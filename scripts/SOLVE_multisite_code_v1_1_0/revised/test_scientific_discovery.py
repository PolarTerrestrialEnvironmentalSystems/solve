"""General scientific discovery; synthetic metadata, no external API calls."""
import copy
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import focused_search
import quality_search
import run_web_enrichment as core
import scientific_discovery as science
from test_collector import config


def record(doi='10.1594/example', title='Example Lake water measurements', **fields):
    return dict(attributes=dict(doi=doi, titles=[dict(title=title)], **fields))


class ScientificDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = config(self.tmp.name,
            search=dict(provider='searxng', strategy='focused', delay_seconds=0),
            retrieval=dict(enabled=True, crossref_enabled=True, wait_for_search_routes=True),
            scientific_discovery=dict(enabled=True),
            limits=dict(max_searches_per_run=20, max_fetches_per_run=10, delay_seconds=0))
        self.r = core.Research(self.cfg, Path(self.tmp.name)/'dossier')
        self.r.plan()
        self.task = self.r.store.one("SELECT * FROM searches WHERE provider='datacite'")

    def tearDown(self):
        self.r.close()
        self.tmp.cleanup()

    def response(self, payload):
        return patch.object(core.ur, 'build_opener', **{
            'return_value.open.return_value': io.BytesIO(json.dumps(payload).encode())})

    def test_title_or_description_identity_and_metadata_only_storage(self):
        payload = dict(data=[
            record(url='https://data.example.org/lake'),
            record('10.1594/campaign', 'Regional campaign',
                   descriptions=[dict(description='Example Lake PRIVATE_TRANSIENT_DESCRIPTION')]),
            record('10.1594/wrong', 'Different Lake phosphorus'),
            record('10.1594/duplicate', url='https://data.example.org/lake#fragment'),
            record('invalid-doi', url='https://data.example.org/invalid'),
        ])
        with self.response(payload):
            self.r.do_search(self.task)
        stored = self.r.store.one('SELECT * FROM searches WHERE id=?', (self.task['id'],))
        self.assertEqual((stored['status'], stored['result_count']), ('done', 2))
        hits = json.loads(stored['results'])
        self.assertEqual({h['url'] for h in hits}, {'https://data.example.org/lake', 'https://doi.org/10.1594/campaign'})
        self.assertNotIn('PRIVATE_TRANSIENT_DESCRIPTION', stored['results'])
        self.assertTrue(all('descriptions' not in h and 'description' not in h for h in hits))
        self.assertEqual(self.r.search_http_used, 1)
        self.assertEqual(len(self.r.store.rows('SELECT * FROM tasks')), 2)

    def test_query_uses_escaped_literal_name_and_no_authorization(self):
        task = dict(self.task, query='See "Nord" / Süßwasser')
        with self.response(dict(data=[])) as network:
            science.search(self.r, task, core)
        req = network.return_value.open.call_args.args[0]
        params = core.up.parse_qs(core.up.urlsplit(req.full_url).query)
        phrase = science.literal_phrase(task['query'])
        self.assertEqual(params['query'], [f'(titles.title:{phrase} OR descriptions.description:{phrase})'])
        self.assertIn('\\"Nord\\"', params['query'][0])
        self.assertIsNone(req.get_header('Authorization'))

    def test_datacite_works_during_web_outage_and_crossref_backoff(self):
        self.r.store.db.execute("UPDATE searches SET status='retry',next_try=? WHERE provider='crossref'", (time.time()+600,))
        with patch.object(quality_search, 'available', return_value=[]):
            self.assertEqual(self.r.next_search()['provider'], 'datacite')

    def test_backoff_blocks_provider_and_new_queries_and_sets_wakeup(self):
        self.r.cfg['retrieval']['crossref_enabled'] = False
        self.r.store.db.execute("UPDATE searches SET status='retry',next_try=200 WHERE provider='datacite'")
        with patch.object(quality_search.time, 'time', return_value=100), patch.object(quality_search, 'available', return_value=[]):
            self.assertIsNone(self.r.next_search())
            self.assertEqual(quality_search.next_search_wakeup(self.r, core), 200)
            with patch.object(core.ur, 'build_opener') as network, self.assertRaisesRegex(core.FetchProblem, 'datacite_cooldown'):
                science.search(self.r, self.task, core)
            network.assert_not_called()

    def test_rate_limit_saves_retry_after(self):
        error = urllib.error.HTTPError('https://api.datacite.org/dois', 429, 'fixture', {'Retry-After':'120'}, None)
        before = time.time()
        with patch.object(core.ur, 'build_opener', **{'return_value.open.side_effect':error}):
            self.r.do_search(self.task)
        row = self.r.store.one('SELECT * FROM searches WHERE id=?', (self.task['id'],))
        self.assertEqual(row['status'], 'retry')
        self.assertEqual(row['error'], 'datacite_http_429')
        self.assertGreaterEqual(row['next_try'], before+120)
        self.assertEqual(self.r.store.rows('SELECT * FROM tasks'), [])

    def test_global_search_budget_prevents_request(self):
        self.r.search_http_used = self.r.cfg['limits']['max_searches_per_run']
        with patch.object(core.ur, 'build_opener') as network, self.assertRaisesRegex(core.FetchProblem, 'search_call_budget'):
            science.search(self.r, self.task, core)
        network.assert_not_called()

    def test_invalid_response_is_error_not_zero_results(self):
        with self.response(dict(data=dict(unexpected=True))):
            self.r.do_search(self.task)
        row = self.r.store.one('SELECT * FROM searches WHERE id=?', (self.task['id'],))
        self.assertEqual(row['status'], 'error')
        self.assertIsNone(row['result_count'])
        self.assertIn('datacite_invalid_items', row['error'])

    def test_existing_disabled_provider_queue_does_not_run(self):
        self.r.cfg['scientific_discovery']['datacite_enabled'] = False
        self.r.cfg['retrieval']['crossref_enabled'] = False
        with patch.object(quality_search, 'available', return_value=[]):
            self.assertIsNone(self.r.next_search())

    def test_alias_search_follows_empty_initial_search(self):
        self.r.cfg['retrieval']['crossref_enabled'] = False
        self.r.bodies[self.task['profile']]['aliases'] = ['Lake Example']
        self.r.store.db.execute("UPDATE searches SET status='no_results',result_count=0,completed=? WHERE provider='datacite'", (core.utc(),))
        with patch.object(quality_search, 'available', return_value=[]):
            task = self.r.next_search()
        self.assertEqual((task['provider'], task['query']), ('datacite', 'Lake Example'))

    def test_metadata_candidates_still_pass_blablador_preflight(self):
        with self.response(dict(data=[record()])):
            self.r.do_search(self.task)
        task = self.r.store.one('SELECT * FROM tasks')
        self.r.cfg['preflight']['enabled'] = True
        with patch('source_preflight.screen', return_value=False) as screen, patch.object(self.r.fetcher, 'fetch') as fetch:
            self.r.process(task)
        screen.assert_called_once()
        fetch.assert_not_called()

    def test_config_validation(self):
        for change in [None, dict(enabled='yes'), dict(domains=['example.org OR something']),
                       dict(domains=['https://example.org']), dict(max_name_variants=0), dict(max_name_variants=True)]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                core.load_config(dict(self.cfg, scientific_discovery=change))

    def test_generic_settings_preserve_completed_work_and_profiles(self):
        cfg = copy.deepcopy(self.cfg)
        cfg['scientific_discovery']['enabled'] = False
        root = Path(self.tmp.name)/'resume'
        previous = core.Research(cfg, root)
        previous.plan()
        profile = next(iter(previous.active))
        previous.enqueue(profile, 'https://example.org/existing', priority=95)
        previous.store.db.execute("UPDATE tasks SET status='done',reason='ok'")
        previous.store.db.commit()
        previous.close()
        updated = core.Research(self.cfg, root)
        try:
            updated.plan()
            self.assertEqual(updated.active, {profile})
            self.assertEqual(updated.store.one('SELECT status FROM tasks')['status'], 'done')
            self.assertEqual(len(updated.store.rows('SELECT * FROM profiles')), 1)
        finally:
            updated.close()

    def test_all_33_lakes_have_scoped_queries_and_scientific_portals(self):
        project = Path(__file__).resolve().parents[1]
        cfg = core.load_config(project/'inputs/sites_blablador_33_scientific.json')
        old = core.load_config(project/'inputs/sites_blablador_33_v1_4.json')
        self.assertEqual([w['id'] for w in cfg['waterbodies']], [w['id'] for w in old['waterbodies']])
        self.assertEqual([w['name'] for w in cfg['waterbodies']], [w['name'] for w in old['waterbodies']])
        cfg['review']['required'] = False
        cfg['preflight']['enabled'] = False
        cfg.pop('credentials_env_file', None)
        cfg['storage']['shared_cache_dir'] = ''
        with patch.dict(os.environ, {'SERPER_KEY':'synthetic-test-key'}):
            r = core.Research(cfg, Path(self.tmp.name)/'all_lakes')
        try:
            with patch.object(core.ur, 'build_opener') as network:
                r.plan()
                r.plan()
            network.assert_not_called()
            self.assertEqual(len(r.active), 33)
            searches = r.store.rows('SELECT * FROM searches')
            self.assertEqual(len(searches), 99)
            for wb in cfg['waterbodies']:
                profile = r.profiles[wb['id']]
                self.assertEqual({q['provider'] for q in searches if q['profile']==profile}, {cfg['search']['provider'], 'crossref', 'datacite'})
                options = list(focused_search.options(r, wb, set()))
                for domain in science.DEFAULT_DOMAINS:
                    self.assertTrue(any(f'site:{domain}' in query for query, _ in options), (wb['name'], domain))
            curated = r.store.rows("SELECT * FROM tasks WHERE branch LIKE 'scientific_source:%'")
            self.assertEqual(len(curated), 8)
            self.assertEqual({r.bodies[t['profile']]['name'] for t in curated}, {'Arendsee'})
        finally:
            r.close()


if __name__ == '__main__':
    unittest.main()
