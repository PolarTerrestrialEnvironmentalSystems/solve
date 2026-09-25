"""Offline adapter, provider migration, rate limits and credential-loading tests."""
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import credential_env
import focused_search
import quality_search
import run_web_enrichment as core
import serper_search
from test_collector import config


class SerperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'SERPER_KEY':'synthetic-key'})
        self.env.start()
        self.cfg = config(self.tmp.name,
            search=dict(provider='serper', strategy='focused', delay_seconds=0, count=20),
            retrieval=dict(enabled=True, crossref_enabled=False, wait_for_search_routes=True),
            limits=dict(max_searches_per_run=20, max_fetches_per_run=10, delay_seconds=0))
        self.r = core.Research(self.cfg, Path(self.tmp.name)/'dossier')
        self.r.plan()
        self.task = self.r.store.one("SELECT * FROM searches WHERE provider='serper'")

    def tearDown(self):
        self.r.close()
        self.env.stop()
        self.tmp.cleanup()

    def response(self, payload):
        return patch.object(core.ur, 'build_opener', **{
            'return_value.open.return_value':io.BytesIO(json.dumps(payload).encode())})

    def test_post_header_payload_normalization_and_no_snippets(self):
        payload = dict(searchParameters=dict(q=self.task['query']), organic=[
            dict(link='https://example.org/lake#section',title='Example Lake',snippet='TRANSIENT_SENTINEL'),
            dict(link='https://example.org/lake',title='duplicate')])
        with self.response(payload) as network:
            self.r.do_search(self.task)
        req = network.return_value.open.call_args.args[0]
        self.assertEqual(req.full_url, 'https://google.serper.dev/search')
        self.assertEqual(req.get_method(), 'POST')
        self.assertEqual(req.get_header('X-api-key'), 'synthetic-key')
        self.assertEqual(json.loads(req.data), dict(q=self.task['query'],num=20,page=1,gl='de',hl='de'))
        row = self.r.store.one('SELECT * FROM searches WHERE id=?',(self.task['id'],))
        self.assertEqual((row['status'],row['result_count']),('done',1))
        self.assertNotIn('TRANSIENT_SENTINEL',row['results'])
        self.assertNotIn('synthetic-key',row['results'])
        self.assertEqual(json.loads(row['results'])[0]['retrieved_by'],'serper')
        self.assertEqual(self.r.search_http_used,1)

    def test_invalid_payloads_are_not_successful_empty_searches(self):
        for payload in [{},dict(message='invalid API key'),dict(organic=None),dict(organic=[dict(link='file:///private')]),
                        dict(organic=[],searchParameters=dict(q='wrong query'))]:
            with self.subTest(payload=payload), self.response(payload), self.assertRaises(ValueError):
                serper_search.search(self.r,self.task,core)

    def test_empty_organic_is_successful_zero_results(self):
        with self.response(dict(organic=[])):
            self.r.do_search(self.task)
        self.assertEqual(self.r.store.one('SELECT status FROM searches')['status'],'no_results')

    def test_rate_limit_and_cross_query_backoff(self):
        exc=urllib.error.HTTPError(serper_search.ENDPOINT,429,'fixture',{'Retry-After':'120'},io.BytesIO())
        with patch.object(core.ur,'build_opener',**{'return_value.open.side_effect':exc}):
            self.r.do_search(self.task)
        exc.close()
        row=self.r.store.one('SELECT * FROM searches WHERE id=?',(self.task['id'],))
        self.assertEqual(row['status'],'retry')
        self.assertGreater(row['next_try'],time.time()+110)
        self.r.add_search(self.task['profile'],'Another query','topic','fixture')
        self.assertIsNone(self.r.next_search())
        self.assertEqual(quality_search.next_search_wakeup(self.r,core),row['next_try'])

    def test_authentication_error_does_not_retry_or_log_body(self):
        exc=urllib.error.HTTPError(serper_search.ENDPOINT,401,'fixture',{},io.BytesIO(b'synthetic-key'))
        with patch.object(core.ur,'build_opener',**{'return_value.open.side_effect':exc}):
            self.r.do_search(self.task)
        exc.close()
        row=self.r.store.one('SELECT * FROM searches WHERE id=?',(self.task['id'],))
        self.assertEqual((row['status'],row['error']),('error','serper_http_401'))

    def test_no_searxng_health_probes_or_old_jobs(self):
        self.r.add_search(self.task['profile'],'old search','overview','fixture',provider='searxng')
        with patch.object(core,'searxng_search') as network:
            task=self.r.next_search()
        network.assert_not_called()
        self.assertEqual(task['provider'],'serper')
        self.assertEqual(self.r.health_used,0)

    def test_pagination_after_verified_gain(self):
        self.r.store.db.execute("UPDATE searches SET status='done',result_count=1,completed=?",(core.utc(),))
        with patch.object(focused_search,'feedback',return_value=({self.task['id']:dict(checked=1,gain=1)},set())):
            task=self.r.next_search()
        self.assertEqual(task['family'],'next_page')
        with self.response(dict(organic=[])) as network:
            serper_search.search(self.r,task,core)
        self.assertEqual(json.loads(network.return_value.open.call_args.args[0].data)['page'],2)

    def test_budget_prevents_network(self):
        self.r.search_http_used=20
        with patch.object(core.ur,'build_opener') as network, self.assertRaisesRegex(core.FetchProblem,'search_call_budget'):
            serper_search.search(self.r,self.task,core)
        network.assert_not_called()

    def test_switch_preserves_profile_and_completed_tasks(self):
        old=dict(self.cfg,search=dict(self.cfg['search'],provider='searxng'))
        r=core.Research(core.load_config(old),Path(self.tmp.name)/'old')
        r.plan()
        profile=next(iter(r.active))
        r.enqueue(profile,'https://example.org/old',priority=95)
        r.store.db.execute("UPDATE tasks SET status='done',reason='ok'")
        r.store.db.commit();r.close()
        r=core.Research(self.cfg,Path(self.tmp.name)/'old')
        try:
            r.plan()
            self.assertEqual(r.active,{profile})
            self.assertEqual(r.store.one('SELECT status FROM tasks')['status'],'done')
            self.assertEqual({x['provider'] for x in r.store.rows('SELECT provider FROM searches')},{'searxng','serper'})
        finally:r.close()

    def test_env_file_allowlist_and_environment_precedence(self):
        path=Path(self.tmp.name)/'.env'
        path.write_text('SERPER_KEY="file-value"\nBLABLADOR_KEY=another-value\nUNRELATED_SECRET=do-not-load\n',encoding='utf-8')
        with patch.dict(os.environ,{'BLABLADOR_KEY':'existing'},clear=True):
            credential_env.load(dict(credentials_env_file=str(path)))
            self.assertEqual(os.environ['SERPER_KEY'],'file-value')
            self.assertEqual(os.environ['BLABLADOR_KEY'],'existing')
            self.assertNotIn('UNRELATED_SECRET',os.environ)

    def test_missing_key_fails_before_creating_dossier(self):
        with patch.dict(os.environ,{},clear=True), self.assertRaisesRegex(ValueError,'SERPER_KEY'):
            core.Research(self.cfg,Path(self.tmp.name)/'missing')
        self.assertFalse((Path(self.tmp.name)/'missing').exists())

    def test_simple_mode_stores_actual_query_and_deduplicates(self):
        self.r.cfg['search']['serper_simple_queries'] = True
        first = self.r.add_search(self.task['profile'],'"Example Lake" site:ufz.de filetype:pdf','authority','fixture')
        second = self.r.add_search(self.task['profile'],'Example Lake ufz.de pdf','authority','fixture')
        self.assertEqual(first, second)
        row = self.r.store.one('SELECT * FROM searches WHERE id=?',(first,))
        self.assertEqual(row['query'],'Example Lake ufz.de pdf')
        with self.response(dict(organic=[],searchParameters=dict(q=row['query']))) as network:
            self.r.do_search(row)
        self.assertEqual(json.loads(network.return_value.open.call_args.args[0].data)['q'],row['query'])


if __name__=='__main__':
    unittest.main()
