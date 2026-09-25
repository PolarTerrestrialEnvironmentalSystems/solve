"""Synthetic entry-pool tests: no external calls or document downloads."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_web_enrichment as core
import start_links
from test_collector import config


class StartLinksTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'SERPER_KEY':'fake','BLABLADOR_KEY':'fake'})
        self.env.start()
        self.cfg=config(self.tmp.name,search=dict(provider='serper',strategy='focused',serper_simple_queries=True),
            retrieval=dict(enabled=True,crossref_enabled=False),preflight=dict(enabled=True),
            start_links=dict(enabled=True,target=2),limits=dict(max_searches_per_run=10,max_fetches_per_run=10))
        self.r=core.Research(self.cfg,Path(self.tmp.name)/'dossier');self.r.plan()
        self.task=self.r.store.one('SELECT * FROM searches')
        self.profile=self.task['profile']

    def tearDown(self):
        self.r.close();self.env.stop();self.tmp.cleanup()

    def hits(self,count=3):
        self.r.search_result(self.task,[dict(url=f'https://example.org/report{i}',title=f'Example Lake report {i}') for i in range(count)])
        return start_links.candidates(self.r,self.profile)

    def api(self, categories=None):
        def response(url,payload,headers,**kwargs):
            request=json.loads(payload['messages'][1]['content'])
            items=[]
            for i,candidate in enumerate(request['candidates']):
                cat=categories[i] if categories else 'science'
                items.append(dict(id=candidate['id'],action='accept',category=cat,identity='match',
                    importance=90,confidence=0.9,reason='Synthetic metadata assessment'))
            return dict(choices=[dict(message=dict(content=json.dumps(dict(decisions=items))))])
        return patch.object(core,'api_request',side_effect=response)

    def test_tourism_never_counts_even_if_model_says_accept(self):
        tasks=self.hits()
        with self.api(['tourism','science','navigation']):
            start_links.step(self.r,core)
        self.assertEqual(start_links.selected_count(self.r,self.profile),1)
        self.assertFalse(start_links.allowed(self.r,tasks[0]))
        self.assertTrue(start_links.allowed(self.r,tasks[1]))
        self.assertFalse(start_links.allowed(self.r,tasks[2]))

    def test_unreviewed_or_rejected_root_cannot_download(self):
        tasks=self.hits(1)
        with patch.object(self.r.fetcher,'fetch') as fetch:
            self.assertIsNone(self.r.next_task(set()))
            self.r.process(tasks[0])
        fetch.assert_not_called()

    def test_exactly_twenty_selected_and_no_more_searches(self):
        self.r.cfg['start_links']['target']=20
        self.hits(25)
        with self.api():
            self.assertTrue(start_links.step(self.r,core))
        self.assertEqual(start_links.selected_count(self.r,self.profile),20)
        with patch.object(self.r,'do_search') as search:
            self.assertFalse(start_links.step(self.r,core))
        search.assert_not_called()
        self.assertEqual(start_links.report(self.r)[0]['status'],'target_reached')

    def test_refill_paginates_without_download_gain(self):
        self.hits(3)
        with self.api(['tourism','navigation','science']):
            start_links.step(self.r,core)
        with patch.object(self.r,'do_search') as search:
            self.assertTrue(start_links.step(self.r,core))
        task=search.call_args.args[0]
        self.assertEqual(task['family'],'start_pool_page')
        self.assertEqual(self.r.store.one('SELECT page FROM search_options WHERE search_id=?',(task['id'],))['page'],2)

    def test_empty_page_moves_to_topic_query(self):
        self.r.search_result(self.task,[])
        with patch.object(self.r,'do_search') as search:
            start_links.step(self.r,core)
        self.assertEqual(search.call_args.args[0]['query'],'Example Lake Monitoring')

    def test_duplicate_url_not_counted_twice(self):
        tasks=self.hits(1)
        sid=self.r.add_search(self.profile,'Example Lake Monitoring','start_pool_topic','fixture')
        self.r.search_result(self.r.store.one('SELECT * FROM searches WHERE id=?',(sid,)),[dict(url=tasks[0]['url'],title='duplicate')])
        self.assertEqual(len(start_links.candidates(self.r,self.profile)),1)
        with self.api():start_links.step(self.r,core)
        self.assertEqual(start_links.selected_count(self.r,self.profile),1)

    def test_invalid_ids_do_not_partially_approve_batch(self):
        self.hits(2)
        with patch.object(core,'api_request',return_value=dict(choices=[dict(message=dict(content='{"decisions":[]}'))])):
            start_links.step(self.r,core)
        self.assertEqual(start_links.selected_count(self.r,self.profile),0)
        self.assertEqual(start_links.report(self.r)[0]['status'],'llm_review_error')

    def test_budget_stop_is_visible_and_no_unreviewed_fetch(self):
        self.hits(2)
        self.r.preflight_used=self.r.cfg['preflight']['max_calls_per_run']
        with patch.object(core,'api_request') as api:
            self.assertFalse(start_links.step(self.r,core))
        api.assert_not_called()
        self.assertEqual(start_links.report(self.r)[0]['status'],'preflight_call_budget')
        self.assertIsNone(self.r.next_task(set()))

    def test_search_limit_terminates_refill(self):
        self.r.search_result(self.task,[])
        self.r.cfg['start_links']['max_searches']=1
        self.r.start_pool_attempts={self.task['id']}
        with patch.object(self.r,'do_search') as search:
            self.assertFalse(start_links.step(self.r,core))
        search.assert_not_called()
        self.assertEqual(start_links.report(self.r)[0]['status'],'start_search_limit')

    def test_resume_reuses_decisions_and_exports_explanations(self):
        self.hits(2)
        with self.api():start_links.step(self.r,core)
        self.r.close()
        self.r=core.Research(self.cfg,Path(self.tmp.name)/'dossier')
        with patch.object(core,'api_request') as api:
            self.assertFalse(start_links.step(self.r,core))
        api.assert_not_called()
        self.r.export()
        report=json.loads((Path(self.tmp.name)/'dossier/results/startlink_auswahl.json').read_text(encoding='utf-8'))
        self.assertEqual(report[0]['selected'],2)
        self.assertTrue(all(x['reason'] for x in report[0]['candidates']))

    def test_curated_and_followed_links_keep_existing_gates(self):
        self.r.enqueue(self.profile,'https://example.org/curated',priority=94)
        task=self.r.store.one("SELECT * FROM tasks WHERE url='https://example.org/curated'")
        self.assertTrue(start_links.allowed(self.r,task))
        task=self.hits(1)[0]
        self.assertTrue(start_links.allowed(self.r,dict(task,depth=1)))

    def test_initial_keywords_are_queued_before_selection(self):
        self.r.cfg['start_links']['initial_keywords']=['Messdaten','Gewässermaßnahmen']
        self.r.plan()
        queries=self.r.store.rows("SELECT query FROM searches WHERE family='start_pool_initial' ORDER BY rowid")
        self.assertEqual([q['query'] for q in queries],['Example Lake Messdaten','Example Lake Gewässermaßnahmen'])
        self.hits(1)
        with patch.object(core,'api_request') as api,patch.object(self.r,'do_search') as search:
            start_links.step(self.r,core)
        api.assert_not_called()
        self.assertEqual(search.call_args.args[0]['query'],'Example Lake Messdaten')

    def test_ambiguous_city_sensor_portal_needs_water_context(self):
        self.r.bodies[self.profile]['ambiguous_place_name']=True
        self.r.search_result(self.task,[dict(url='https://sensornetwork.example/city/Arendsee',title='Sensordaten Arendsee (Altmark)')])
        with self.api():start_links.step(self.r,core)
        self.assertEqual(start_links.selected_count(self.r,self.profile),0)
        row=start_links.report(self.r)[0]['candidates'][0]
        self.assertEqual(row['identity'],'uncertain')

    def test_keyword_changes_preserve_prior_selection_policy(self):
        policy=self.r.start_pool_policy
        self.r.cfg['start_links']['initial_keywords']=['Messdaten']
        start_links.initialize(self.r,core)
        self.assertEqual(self.r.start_pool_policy,policy)

    def test_invalid_json_retries_smaller_batches_without_raw_output(self):
        self.r.cfg['start_links']['target']=4
        self.hits(4)
        sizes=[]
        def response(url,payload,headers,**kwargs):
            items=json.loads(payload['messages'][1]['content'])['candidates']
            sizes.append(len(items))
            if len(sizes)==1:
                return dict(choices=[dict(finish_reason='stop',message=dict(content='PRIVATE_RESPONSE_SENTINEL invalid json'))])
            decisions=[dict(id=x['id'],action='accept',category='science',identity='match',importance=90,confidence=.9,reason='fixture') for x in items]
            return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(dict(decisions=decisions))))])
        with patch.object(core,'api_request',side_effect=response):
            start_links.step(self.r,core)
        self.assertEqual(sizes,[4,2,2])
        self.assertEqual(start_links.selected_count(self.r,self.profile),4)
        events=self.r.store.rows("SELECT detail FROM events WHERE kind='start_links_review_retry'")
        self.assertIn('start_links_invalid_json',events[0]['detail'])
        self.assertNotIn('PRIVATE_RESPONSE_SENTINEL',str(events))

    def test_exhausted_retry_is_bounded_and_diagnostic(self):
        self.hits(1)
        with patch.object(core,'api_request',return_value=dict(choices=[dict(finish_reason='length',message=dict(content='truncated'))])) as api:
            start_links.step(self.r,core)
        self.assertEqual(api.call_count,3)
        error=self.r.store.one("SELECT detail FROM events WHERE kind='start_links_review_error'")
        self.assertEqual(json.loads(error['detail'])['code'],'start_links_response_token_limit')
        self.assertEqual(start_links.selected_count(self.r,self.profile),0)

    def test_retry_respects_preflight_budget(self):
        self.hits(2)
        self.r.cfg['preflight']['max_calls_per_run']=1
        with patch.object(core,'api_request',return_value=dict(choices=[dict(message=dict(content='bad'))])) as api:
            start_links.step(self.r,core)
        self.assertEqual(api.call_count,1)
        self.assertEqual(start_links.report(self.r)[0]['status'],'preflight_call_budget')

    def test_local_ids_map_back_to_real_task_ids(self):
        self.r.enqueue(self.profile,'https://example.org/curated-before-web',priority=94)
        tasks=self.hits(2)
        self.assertNotEqual(tasks[0]['id'],1)
        seen=[]
        def response(url,payload,headers,**kwargs):
            entries=json.loads(payload['messages'][1]['content'])['candidates']
            seen.extend(x['id'] for x in entries)
            ds=[dict(id=x['id'],action='accept',category='science',identity='match',importance=90,confidence=.9,reason='fixture') for x in entries]
            return dict(choices=[dict(message=dict(content=json.dumps(dict(decisions=ds))))])
        with patch.object(core,'api_request',side_effect=response):start_links.step(self.r,core)
        self.assertEqual(seen,[1,2])
        self.assertEqual({x['url'] for x in start_links.rows(self.r,self.profile)},{t['url'] for t in tasks})


if __name__=='__main__':unittest.main()
