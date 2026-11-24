"""Prepared source lists and river identity: synthetic fixtures, no network calls."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_web_enrichment as core
import source_preflight
import start_links
import quality_search
from test_collector import config


class PreparedSeedsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'SERPER_KEY': 'fixture', 'BLABLADOR_KEY': 'fixture'})
        self.env.start()
        self.cfg = config(self.root,
            waterbodies=[dict(id='elbe', name='Elbe', waterbody_kind='river',
                seed_urls=['https://example.org/first?utm_source=seed', 'https://example.org/second'],
                seed_titles={'https://example.org/first?utm_source=seed': 'Elbe river monitoring'},
                preferred_domains=['example.org'])],
            search=dict(provider='serper', strategy='focused'),
            retrieval=dict(enabled=True, crossref_enabled=False, wait_for_search_routes=True),
            scientific_discovery=dict(enabled=False), scientific_sources=[],
            preflight=dict(enabled=True),
            start_links=dict(enabled=True, skip_selection=True, prepared=True,
                             initial_keywords=['Messdaten']),
            limits=dict(max_fetches_per_run=10, max_searches_per_run=10))
        self.r = core.Research(self.cfg, self.root / 'dossier')
        self.profile = self.r.profiles['elbe']

    def tearDown(self):
        self.r.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_plan_has_only_prepared_roots_and_waits_before_adaptive_search(self):
        with patch.object(core, 'api_request', side_effect=AssertionError('Unexpected API')):
            self.r.plan()
            self.r.plan()
            self.assertFalse(start_links.step(self.r, core))
            self.assertIsNone(self.r.next_search())
        self.assertEqual({t['url'] for t in self.r.store.rows('SELECT url FROM tasks')},
                         {'https://example.org/first', 'https://example.org/second'})
        self.assertEqual(self.r.store.rows('SELECT * FROM searches'), [])
        report = start_links.report(self.r)[0]
        self.assertEqual((report['status'], report['selected']), ('prepared_seed_urls', 2))

    def test_adaptive_serper_resumes_after_seeds_and_hits_use_normal_preflight(self):
        self.r.plan()
        self.r.store.db.execute("UPDATE tasks SET status='done'")
        search = self.r.next_search()
        self.assertEqual(search['provider'], 'serper')
        self.assertNotIn(search['family'], {'overview', 'start_pool_initial', 'start_pool_topic', 'start_pool_page'})
        self.r.search_result(search, [dict(url='https://example.org/third', title='Elbe river report')])
        task = self.r.store.one("SELECT * FROM tasks WHERE url='https://example.org/third'")
        self.assertTrue(start_links.allowed(self.r, task))
        with patch.object(source_preflight, 'screen', return_value=False) as screen, \
             patch.object(self.r.fetcher, 'fetch') as fetch:
            self.r.process(task)
        screen.assert_called_once()
        fetch.assert_not_called()

    def test_web_provenance_does_not_revoke_prepared_seed(self):
        self.r.plan()
        sid = self.r.add_search(self.profile, 'Elbe monitoring', 'topic', 'fixture')
        self.r.search_result(self.r.store.one('SELECT * FROM searches WHERE id=?', (sid,)),
                             [dict(url='https://example.org/first', title='Elbe river monitoring')])
        seed = self.r.store.one("SELECT * FROM tasks WHERE url='https://example.org/first'")
        self.assertTrue(start_links.allowed(self.r, seed))
        self.assertEqual(start_links.filter_allowed(self.r, [seed]), [seed])
        self.assertEqual(start_links.selected_count(self.r, self.profile), 0)

    def test_preflight_receives_exact_seed_locator_without_bypassing_review(self):
        body = self.r.bodies[self.profile]
        body['seed_evidence'] = {'https://example.org/first?utm_source=seed': {
            'provider': 'Official catalogue', 'catalogue_verified_waterbody_id': 'elbe',
            'catalogue_pages': [17], 'locator': 'PDF-Seite(n) 17',
            'context_pages': [3],
            'evidence': 'Full document text must never enter this hint'}}
        self.r.plan()
        self.r.enqueue(self.profile, 'https://example.org/unrelated', force=True)
        response = dict(choices=[dict(message=dict(content=json.dumps(dict(action='defer',
            importance=10, confidence=.99, identity='mismatch', category='unrelated', reason='Fixture rejection'))))])
        for url, should_have_hint in [('https://example.org/first', True),
                                      ('https://example.org/unrelated', False)]:
            task = self.r.store.one('SELECT * FROM tasks WHERE url=?', (url,))
            with patch.object(core, 'api_request', return_value=response) as api:
                self.assertFalse(source_preflight.screen(self.r, task, core))
            request = json.loads(api.call_args.args[1]['messages'][1]['content'])
            self.assertEqual('prepared_seed_metadata' in request, should_have_hint)
            if should_have_hint:
                self.assertEqual(request['prepared_seed_metadata']['catalogue_pages'], [17])
                self.assertEqual(request['prepared_seed_metadata']['context_pages'], [3])
                self.assertNotIn('evidence', request['prepared_seed_metadata'])
        seed = self.r.store.one("SELECT * FROM tasks WHERE url='https://example.org/first'")
        body['seed_evidence']['https://example.org/first?utm_source=seed']['catalogue_verified_waterbody_id'] = 'OTHER_RIVER'
        self.assertIsNone(source_preflight.prepared_seed_metadata(self.r, seed, core))
        body['seed_evidence']['https://example.org/first?utm_source=seed']['catalogue_verified_waterbody_id'] = 'elbe'
        self.r.cfg['start_links']['prepared'] = False
        self.assertIsNone(source_preflight.prepared_seed_metadata(self.r, seed, core))

    def test_preparation_replaces_old_selection_but_keeps_preflight_rejection(self):
        self.r.plan()
        self.r.store.db.execute("UPDATE tasks SET status='deferred',reason='start_link_not_selected' WHERE url='https://example.org/first'")
        self.r.store.db.execute("UPDATE tasks SET status='deferred',reason='llm_preflight_deferred' WHERE url='https://example.org/second'")
        self.r.plan()
        self.assertEqual(self.r.store.one("SELECT status FROM tasks WHERE url='https://example.org/first'")['status'], 'pending')
        self.assertEqual(self.r.store.one("SELECT status FROM tasks WHERE url='https://example.org/second'")['status'], 'deferred')

    def test_resume_reuses_completed_seeds_recovers_interrupted_seed_and_disables_old_pool(self):
        self.r.plan()
        self.r.store.db.execute("UPDATE tasks SET status='done' WHERE url='https://example.org/first'")
        self.r.store.db.execute("UPDATE tasks SET status='in_progress' WHERE url='https://example.org/second'")
        old = self.r.add_search(self.profile, 'Elbe', 'overview', 'legacy start pool')
        self.r.store.db.commit()
        original_profile = self.profile
        self.r.close()
        self.r = core.Research(self.cfg, self.root / 'dossier')
        self.assertEqual(self.r.profiles['elbe'], original_profile)
        self.r.cfg['limits']['max_searches_per_run'] = 0
        def complete(task):
            self.assertEqual(task['url'], 'https://example.org/second')
            self.assertEqual(task['reason'], 'recovered_after_interrupt')
            self.r.store.db.execute("UPDATE tasks SET status='done' WHERE id=?", (task['id'],))
            self.r.store.db.commit()
        with patch.object(self.r, 'process', side_effect=complete) as process, \
             patch.object(core, 'api_request', side_effect=AssertionError('Unexpected API')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.r.run()
        process.assert_called_once()
        self.assertEqual(self.r.store.one('SELECT status FROM searches WHERE id=?', (old,))['status'], 'disabled')
        self.assertEqual(len(self.r.store.rows('SELECT * FROM tasks')), 2)

    def test_prepared_serper_cooldown_remains_resumable(self):
        self.r.plan()
        self.r.store.db.execute("UPDATE tasks SET status='done'")
        search = self.r.next_search()
        due = core.time.time() + 120
        self.r.store.db.execute("UPDATE searches SET status='retry',next_try=? WHERE id=?", (due, search['id']))
        self.assertEqual(quality_search.next_search_wakeup(self.r, core), due)
        self.assertIsNone(self.r.next_search())

    def test_configuration_rejects_silent_fallback_and_duplicate_seeds(self):
        for updates in [dict(skip_selection=False), dict(prepared='true')]:
            cfg = json.loads(json.dumps(self.cfg))
            cfg['start_links'].update(updates)
            with self.assertRaises(ValueError):
                core.load_config(cfg)
        for urls in [[], ['not a URL'], ['https://example.org/first', 'https://example.org/first?utm_source=x']]:
            cfg = json.loads(json.dumps(self.cfg))
            cfg['waterbodies'][0]['seed_urls'] = urls
            with self.assertRaises(ValueError):
                core.load_config(cfg)


class RiverIdentityTests(unittest.TestCase):
    def waterbody(self, **overrides):
        return dict(dict(name='Elbe', aliases=[], identifiers=[], waterbody_kind='river',
                         ambiguous_place_name=True, identity_exclusions=['Alte Elbe']), **overrides)

    def review(self, text, **overrides):
        request = dict(waterbody=self.waterbody(**overrides), topics=[dict(name='Water quality')],
                       fragments=[dict(locator='p1', text=text)], links=[], require_topic_evidence=True)
        answer = dict(decision='relevant', identity_status='candidate', topic_hits=['Water quality'],
                      topic_evidence=[dict(topic='Water quality', quote=text, locator='p1')])
        return core.validate_review(answer, request)

    def test_river_and_fliessgewaesser_evidence_accepted_with_exact_identity(self):
        for term in ['Fluss', 'Fließgewässer', 'river', 'stream']:
            text = f'Elbe: {term} monitoring with verified phosphorus observations.'
            self.assertEqual(self.review(text)['decision'], 'relevant')

    def test_city_other_river_and_excluded_arm_remain_unproven(self):
        for text in ['Die Stadt Elbe berichtet über ihre kommunale Geschichte.',
                     'Der Fluss Oder wird seit vielen Jahren untersucht.',
                     'Der Fluss Alte Elbe wird seit vielen Jahren untersucht.',
                     'Elbe präsentiert eine neue Streaming-Plattform für Musik.']:
            with self.assertRaises(ValueError):
                self.review(text)

    def test_river_hint_does_not_weaken_lake_evidence_gate(self):
        with self.assertRaises(ValueError):
            self.review('Elbe river monitoring with phosphorus observations.', waterbody_kind='lake')

    def test_preflight_uses_river_context_without_accepting_town_metadata(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'BLABLADOR_KEY': 'fixture'}):
            cfg = config(directory, waterbodies=[dict(self.waterbody(), id='elbe')],
                         preflight=dict(enabled=True), retrieval=dict(enabled=True))
            r = core.Research(cfg, Path(directory) / 'dossier')
            try:
                profile = r.profiles['elbe']
                response = dict(choices=[dict(message=dict(content=json.dumps(dict(action='download',
                    importance=90, confidence=.95, identity='match', category='authority', reason='Fixture metadata'))))])
                for suffix, label, expected in [('river', 'Elbe: Fließgewässer monitoring', 'priority'),
                                                ('city', 'Stadt Elbe: historischer Ortsbericht', 'explore')]:
                    r.enqueue(profile, 'https://example.org/' + suffix, label=label, force=True)
                    task = r.store.one('SELECT * FROM tasks WHERE url=?', ('https://example.org/' + suffix,))
                    with patch.object(core, 'api_request', return_value=response) as api:
                        source_preflight.screen(r, task, core)
                    sent = json.loads(api.call_args.args[1]['messages'][1]['content'])
                    self.assertEqual(sent['waterbody']['waterbody_kind'], 'river')
                    score = r.store.one('SELECT tier FROM preflight_scores WHERE task_id=?', (task['id'],))
                    self.assertEqual(score['tier'], expected)
            finally:
                r.close()


if __name__ == '__main__':
    unittest.main()
