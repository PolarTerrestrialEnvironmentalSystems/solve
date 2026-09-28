import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import run_web_enrichment as s
import source_preflight as pf
from test_collector import config


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(s.os.environ,{'TEST_LLM_KEY':'fixture-not-a-secret'})
        self.env.start()
        self.cfg=config(self.tmp.name,preflight=dict(enabled=True,api_key_env='TEST_LLM_KEY'))
        self.r=s.Research(self.cfg,Path(self.tmp.name)/'dossier')
        self.profile=next(iter(self.r.active))
        self.r.enqueue(self.profile,'https://example.org/report',label='Lake monitoring',force=True)
        self.task=self.r.store.one('SELECT * FROM tasks')

    def tearDown(self):
        self.r.close();self.env.stop();self.tmp.cleanup()

    def response(self,action='download',confidence=.95):
        return dict(choices=[dict(message=dict(content=json.dumps(dict(action=action,importance=80,confidence=confidence,
            identity='uncertain',category='unknown',reason='Only metadata available'))))])

    def test_defer_prevents_any_download(self):
        with patch.object(s,'api_request',return_value=self.response('defer')),patch.object(self.r.fetcher,'fetch') as fetch:
            self.r.process(self.task)
        fetch.assert_not_called()
        self.assertEqual(self.r.store.one('SELECT reason FROM tasks')['reason'],'llm_preflight_deferred')

    def test_uncertain_rejection_does_not_discard_source(self):
        with patch.object(s,'api_request',return_value=self.response('defer',.3)):
            self.assertTrue(pf.screen(self.r,self.task,s))

    def test_accept_before_fetch_and_cached_decision(self):
        with patch.object(s,'api_request',return_value=self.response()) as api:
            self.assertTrue(pf.screen(self.r,self.task,s))
            self.assertTrue(pf.screen(self.r,self.task,s))
        self.assertEqual(api.call_count,1)
        request=api.call_args.args[1]['messages'][1]['content']
        self.assertNotIn('fixture-not-a-secret',request)
        self.assertNotIn('fragments',request)

    def test_api_failure_defers_without_fetch(self):
        with patch.object(s,'api_request',side_effect=s.FetchProblem('api_http_429',retry=True,delay=120)),patch.object(self.r.fetcher,'fetch') as fetch:
            self.r.process(self.task)
        fetch.assert_not_called()
        self.assertEqual(self.r.store.one('SELECT status FROM tasks')['status'],'pending')
        self.assertGreater(self.r.preflight_next_try,s.time.time()+100)

    def test_invalid_response_and_budget_do_not_download(self):
        with patch.object(s,'api_request',return_value={'choices':[]}),patch.object(self.r.fetcher,'fetch') as fetch:
            self.r.process(self.task)
        fetch.assert_not_called()
        self.r.preflight_errors=0;self.r.preflight_next_try=0
        self.r.preflight_used=self.cfg['preflight']['max_calls_per_run']
        with patch.object(s,'api_request') as api,self.assertRaises(s.FetchProblem):
            pf.screen(self.r,self.task,s)
        api.assert_not_called()

    def test_isolated_and_start_link_errors_do_not_disable_download_screening(self):
        self.r.preflight_errors=3
        self.r.consecutive_preflight_errors=2
        self.r.start_link_errors=10
        self.r.cfg['limits']['max_fetches_per_run']=10
        self.assertIsNotNone(self.r.next_task(set()))
        with patch.object(s,'api_request',return_value=self.response()):
            self.assertTrue(pf.screen(self.r,self.task,s))
        self.assertEqual(self.r.preflight_errors,3)
        self.assertEqual(self.r.consecutive_preflight_errors,0)

    def test_persistent_preflight_outage_has_explicit_stop_reason(self):
        self.r.preflight_errors=5
        self.r.consecutive_preflight_errors=5
        with patch.object(s,'api_request') as api:
            result=self.r.run()
        api.assert_not_called()
        self.assertEqual(result['stop_reason'],'preflight_api_errors')
        self.assertEqual(result['consecutive_preflight_errors'],5)
        self.r.consecutive_preflight_errors=0
        self.r.preflight_errors=30
        self.assertTrue(self.r.preflight_error_limit_reached())

    def test_temporary_preflight_pause_waits_and_resumes_eligible_task(self):
        self.r.cfg['limits']['max_fetches_per_run']=10
        clock=[1000.0]
        self.r.preflight_next_try=1002.0
        def process(task):
            self.r.store.db.execute("UPDATE tasks SET status='done' WHERE id=?",(task['id'],))
        with patch.object(s.time,'time',side_effect=lambda:clock[0]), \
             patch.object(s.time,'sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)) as sleep, \
             patch.object(self.r,'next_search',return_value=None), \
             patch.object(self.r,'process',side_effect=process) as worker:
            self.r.run()
        sleep.assert_called_once_with(2.0)
        worker.assert_called_once()


class DownloadCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.cfg=config(self.root,storage=dict(persist_extracted_text=False,shared_cache_dir=str(self.root/'common')),
                        limits=dict(max_fetches_per_run=10,delay_seconds=0))
        self.a=s.Store(self.root/'a');self.b=s.Store(self.root/'b')
        self.fa=s.Fetcher(self.a,self.cfg);self.fb=s.Fetcher(self.b,self.cfg)

    def tearDown(self):
        self.a.close();self.b.close();self.tmp.cleanup()

    def seed(self,fetcher,url='https://example.org/report.pdf'):
        sha,path,_=fetcher.save_bytes(b'%PDF-1.4\nfixture','application/pdf')
        fetcher.store.db.execute("INSERT INTO urls(url,final_url,status,sha,path,origin,mime) VALUES(?,?,'ok',?,?,'http','application/pdf')",(url,url,sha,path))
        fetcher.store.db.commit()
        return fetcher.store.one('SELECT * FROM urls WHERE url=?',(url,))

    def test_same_url_across_dossiers_no_network(self):
        row=self.seed(self.fa)
        self.fa.fetch(row['url'])  # Publishes existing original to common cache.
        with patch.object(self.fb,'request',side_effect=AssertionError('Unexpected network')):
            cached=self.fb.fetch(row['url']+'?utm_source=other#page=2')
        self.assertEqual(cached['sha'],row['sha'])
        self.assertTrue((self.b.root/cached['path']).exists())
        self.assertEqual(self.fb.used,0)

    def test_redirect_to_cached_target_does_not_download_body(self):
        row=self.seed(self.fa);self.fa.fetch(row['url'])
        with patch.object(self.fb,'validate'),patch.object(self.fb,'robots'),patch.object(self.fb,'request',return_value=(302,{'location':row['url']},b'')) as request:
            result=self.fb.fetch('https://example.org/new-download-link')
        self.assertEqual(request.call_count,1)
        self.assertEqual(result['sha'],row['sha'])
        self.assertEqual(result['url'],'https://example.org/new-download-link')

    def test_explicit_refresh_bypasses_shared_cache(self):
        row=self.seed(self.fa);self.fa.fetch(row['url'])
        with patch.object(self.fb,'_fetch_local',return_value=row) as network:
            self.fb.fetch(row['url'],refresh=True)
        network.assert_called_once()

    def test_unknown_mirror_not_guessed_by_title(self):
        row=self.seed(self.fa);self.fa.fetch(row['url'])
        self.assertIsNone(self.fb.cached_source('https://different.example/report.pdf'))

    def test_missing_cached_bytes_not_reused(self):
        row=self.seed(self.fa);self.fa.fetch(row['url'])
        (self.fa.shared.root/row['path']).unlink()
        self.assertIsNone(self.fb.cached_source(row['url']))

    def test_concurrent_cache_lock_retries_instead_of_redownloading(self):
        row=self.seed(self.fa);self.fa.fetch(row['url'])
        with s.WorkspaceLock(self.fa.shared.root),self.assertRaises(s.FetchProblem) as caught:
            self.fb.fetch(row['url'])
        self.assertTrue(caught.exception.retry)
        self.assertEqual(str(caught.exception),'shared_cache_busy')

    def test_corrupt_cache_bytes_are_repaired_from_valid_original(self):
        row=self.seed(self.fa);self.fa.fetch(row['url'])
        (self.fa.shared.root/row['path']).write_bytes(b'corrupt')
        self.assertIsNone(self.fb.cached_source(row['url']))
        self.fa.fetch(row['url'])
        self.assertEqual(self.fb.fetch(row['url'])['sha'],row['sha'])
