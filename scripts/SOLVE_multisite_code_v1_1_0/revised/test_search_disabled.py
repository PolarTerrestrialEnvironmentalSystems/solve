"""No-search mode preserves crawl/review work and reversibly pauses search queues."""
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import credential_env
import quality_search
import run_web_enrichment as core
import start_links
from test_collector import config


class SearchDisabledTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'BLABLADOR_KEY': 'fixture', 'SERPER_KEY': ''})
        self.env.start()
        self.credentials = patch.object(credential_env, 'load')
        self.credentials.start()
        self.instances = []

    def tearDown(self):
        for r in self.instances:
            r.close()
        self.credentials.stop()
        self.env.stop()
        self.tmp.cleanup()

    def cfg(self):
        return config(self.root,
            waterbodies=[dict(id='lake', name='Example Lake', region='Northshire',
                keywords=['Northshire'], preferred_domains=['example.org'],
                seed_urls=['https://example.org/report'])],
            search=dict(provider='none', strategy='focused'),
            retrieval=dict(enabled=True, crossref_enabled=True, wait_for_search_routes=True),
            scientific_discovery=dict(enabled=True, datacite_enabled=True),
            preflight=dict(enabled=True, base_url='https://llm.example.org/v1'),
            review=dict(mode='api', required=True, require_topic_evidence=True,
                        base_url='https://llm.example.org/v1', api_key_env='BLABLADOR_KEY', model='fixture'),
            start_links=dict(enabled=True, prepared=True, skip_selection=True),
            limits=dict(max_fetches_per_run=10, max_searches_per_run=0,
                        max_tasks_per_run=30, delay_seconds=0))

    def research(self, cfg, dossier='dossier'):
        r = core.Research(cfg, self.root / dossier)
        self.instances.append(r)
        return r

    def close(self, r):
        r.close()
        self.instances.remove(r)

    def test_prepared_none_needs_only_llm_key_and_enqueues_no_searches(self):
        r = self.research(self.cfg())
        with patch.object(core, 'api_request', side_effect=AssertionError('Unexpected API')):
            r.plan()
            for provider in (None, 'serper', 'crossref', 'datacite', 'external'):
                self.assertIsNone(r.add_search(r.profiles['lake'], 'Example Lake', 'review', 'fixture', provider=provider))
            self.assertIsNone(r.next_search())
            self.assertIsNone(quality_search.next_search_wakeup(r, core))
            self.assertFalse(start_links.step(r, core))
        self.assertEqual(r.store.rows('SELECT * FROM searches'), [])
        self.assertEqual(r.store.one('SELECT url FROM tasks')['url'], 'https://example.org/report')
        bad = self.cfg()
        bad['start_links']['prepared'] = False
        with self.assertRaises(ValueError):
            core.load_config(bad)
        with patch.dict(os.environ, {'BLABLADOR_KEY': ''}), self.assertRaisesRegex(ValueError, 'LLM-Vorprüfung'):
            self.research(self.cfg(), 'missing-llm')

    def test_saved_jobs_suspend_and_resume_without_profile_or_retry_changes(self):
        cfg = self.cfg()
        cfg['search'].update(provider='serper', api_key_env='SERPER_KEY')
        with patch.dict(os.environ, {'SERPER_KEY': 'fixture'}):
            r = self.research(cfg)
        profile = r.profiles['lake']
        rows = []
        for i, (provider, status) in enumerate([('serper', 'pending'), ('crossref', 'retry'),
                                               ('external', 'waiting_external'), ('datacite', 'deferred_budget'),
                                               ('serper', 'done')]):
            sid = r.add_search(profile, f'Example Lake query {i}', 'topic', 'saved', provider=provider)
            r.store.db.execute('UPDATE searches SET status=?,attempts=3,next_try=1234567890 WHERE id=?', (status, sid))
            rows.append((sid, status))
        r.enqueue(profile, 'https://example.org/saved-search-result', label='Saved source', force=True,
                  query_id=rows[0][0], branch=rows[0][0])
        task_id = r.store.one('SELECT id FROM tasks')['id']
        r.store.db.commit()
        self.close(r)

        paused_cfg = copy.deepcopy(cfg)
        paused_cfg['search']['provider'] = 'none'
        r = self.research(core.load_config(paused_cfg))
        self.assertEqual(r.profiles['lake'], profile)
        self.assertEqual(r.store.one('SELECT id,status FROM tasks'), dict(id=task_id, status='pending'))
        r.plan()
        for sid, status in rows:
            saved = r.store.one('SELECT * FROM searches WHERE id=?', (sid,))
            self.assertEqual(saved['status'], 'done' if status == 'done' else 'suspended')
            self.assertEqual((saved['attempts'], saved['next_try']), (3, 1234567890))
        self.assertEqual(len(r.store.rows('SELECT * FROM searches')), 5)
        with patch.object(core, 'api_request', side_effect=AssertionError('Unexpected API')):
            for sid, _ in rows:
                r.do_search(r.store.one('SELECT * FROM searches WHERE id=?', (sid,)))
        self.assertEqual((r.search_used, r.search_http_used, r.health_used), (0, 0, 0))
        r.store.db.execute("UPDATE tasks SET status='done'")
        r.store.db.commit()
        with patch.object(core.time, 'sleep', side_effect=AssertionError('No search wait allowed')), \
             patch.object(core, 'api_request', side_effect=AssertionError('Unexpected API')), \
             contextlib.redirect_stdout(io.StringIO()):
            summary = r.run()
        self.assertEqual(summary['stop_reason'], 'no_due_tasks')
        self.assertEqual(summary['search_provider'], 'none')
        self.assertEqual((summary['pending_searches'], summary['suspended_searches']), (0, 4))
        self.assertEqual((r.store.root / 'results/search_requests.jsonl').read_text(), '')
        self.close(r)

        with patch.dict(os.environ, {'SERPER_KEY': 'fixture'}):
            r = self.research(cfg)
        self.assertEqual(r.profiles['lake'], profile)
        self.assertEqual([(x['id'], x['status']) for x in r.store.rows('SELECT id,status FROM searches ORDER BY rowid')], rows)
        self.assertEqual(r.store.rows('SELECT * FROM search_suspensions'), [])
        self.assertEqual(r.next_search()['id'], rows[0][0])

    def test_completed_import_is_not_resurrected_when_search_is_reenabled(self):
        cfg = self.cfg()
        cfg['search']['provider'] = 'external'
        cfg['start_links']['enabled'] = False
        r = self.research(cfg)
        sid = r.add_search(r.profiles['lake'], 'Example Lake', 'topic', 'fixture')
        r.cfg['search']['provider'] = 'none'
        r.sync_search_state()
        r.store.db.execute("UPDATE searches SET status='done' WHERE id=?", (sid,))
        r.cfg['search']['provider'] = 'external'
        r.sync_search_state()
        self.assertEqual(r.store.one('SELECT status FROM searches')['status'], 'done')
        self.assertEqual(r.store.rows('SELECT * FROM search_suspensions'), [])

    def test_none_blocks_all_strategy_dispatchers_and_preserves_direct_seeds(self):
        for strategy in ('baseline', 'staged_greedy', 'staged_epsilon', 'focused'):
            with self.subTest(strategy=strategy):
                cfg = self.cfg()
                cfg['start_links'].update(enabled=False, prepared=False, skip_selection=False)
                cfg['search']['strategy'] = strategy
                r = self.research(core.load_config(cfg), strategy)
                r.plan()
                with patch('focused_search.next_search', side_effect=AssertionError('Dispatcher reached')), \
                     patch('adaptive_search.next_search', side_effect=AssertionError('Dispatcher reached')):
                    self.assertIsNone(r.next_search())
                self.assertEqual(r.store.rows('SELECT * FROM searches'), [])
                self.assertIsNotNone(r.store.one("SELECT * FROM tasks WHERE url='https://example.org/report'"))

    def test_none_still_downloads_seeds_follows_links_and_calls_llm_reviews(self):
        r = self.research(self.cfg())
        def api(url, payload, *args, **kwargs):
            self.assertEqual(url, 'https://llm.example.org/v1/chat/completions')
            req = json.loads(payload['messages'][1]['content'])
            if 'fragments' not in req:
                data = dict(action='download', importance=90, confidence=.9, identity='match',
                            category='science', reason='Monitoring fixture')
            else:
                data = dict(decision='relevant', identity_status='candidate', topic_hits=['Water quality'],
                            topic_evidence=[dict(topic='Water quality', evidence_id=req['fragments'][0]['evidence_id'])])
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(data)}}]}
        def request(url, *args, **kwargs):
            self.assertIn(url, {'https://example.org/report', 'https://example.org/child'})
            link = '<a href="/child">Example Lake phosphorus monitoring report appendix</a>' if url.endswith('/report') else ''
            body = '<html><title>Example Lake monitoring</title><main>Example Lake phosphorus monitoring in Northshire.' + link + '</main></html>'
            return 200, {'content-type': 'text/html'}, body.encode()
        with patch.object(core, 'api_request', side_effect=api), \
             patch.object(r.fetcher, 'validate'), patch.object(r.fetcher, 'robots'), \
             patch.object(r.fetcher, 'request', side_effect=request) as fetch, \
             patch.object(core, 'searxng_search', side_effect=AssertionError('Unexpected search')), \
             contextlib.redirect_stdout(io.StringIO()):
            summary = r.run()
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual((summary['preflight_calls'], summary['review_calls']), (2, 2))
        self.assertEqual((summary['searches'], summary['search_http_calls'], summary['health_calls']), (0, 0, 0))
        self.assertEqual(summary['pending_tasks'], 0)
        self.assertEqual({t['url'] for t in r.store.rows("SELECT url FROM tasks WHERE status='done'")},
                         {'https://example.org/report', 'https://example.org/child'})
        self.assertEqual(r.store.rows('SELECT * FROM searches'), [])


if __name__ == '__main__':
    unittest.main()
