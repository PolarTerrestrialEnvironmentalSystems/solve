"""Behavioural regression tests for route health, retrieval and evidence gates."""
import io
import json
import tempfile
import unittest
import contextlib
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.parse import urlsplit,parse_qs
import run_web_enrichment as s
import quality_search as q
import focused_search as f
import source_preflight as p
from test_collector import config


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.cfg=config(self.tmp.name,search=dict(provider='searxng',strategy='focused',delay_seconds=0),
                        retrieval=dict(enabled=True,engines=['google'],crossref_enabled=True),
                        limits=dict(max_searches_per_run=8,max_fetches_per_run=10,delay_seconds=0))
        self.r=s.Research(self.cfg,Path(self.tmp.name)/'dossier')
        self.profile=next(iter(self.r.active))

    def tearDown(self):
        self.r.close();self.tmp.cleanup()

    def healthy_hits(self,cfg,query,**kw):
        if query=='Bergwitzsee':return [dict(url='https://example.org/bergwitzsee',title='Bergwitzsee')],[]
        return [dict(url='https://www.ufz.de/index.php?id=1',title='Lake Arendsee')],[]

    def test_bad_controls_cached_and_do_not_enqueue_junk(self):
        with patch.object(s,'searxng_search',return_value=([dict(url='https://junk.test/',title='Vattenfall')],[])) as api:
            self.assertFalse(q.healthy(self.r,'google',s))
            self.assertFalse(q.healthy(self.r,'google',s))
        self.assertEqual(api.call_count,1)
        self.assertFalse(self.r.store.rows('SELECT * FROM tasks'))
        self.assertEqual(self.r.health_used,1)

    def test_passing_controls_require_both_queries(self):
        with patch.object(s,'searxng_search',side_effect=self.healthy_hits) as api:
            self.assertTrue(q.healthy(self.r,'google',s))
        self.assertEqual(api.call_count,2)

    def test_query_echo_and_engine_routing(self):
        opener=MagicMock()
        opener.open.return_value.__enter__.return_value=io.BytesIO(json.dumps(dict(query='wrong',results=[])).encode())
        with patch.object(s.ur,'build_opener',return_value=opener),self.assertRaisesRegex(ValueError,'echo'):
            s.searxng_search(self.cfg,'Example Lake',engine='google',page=2)
        params=parse_qs(urlsplit(opener.open.call_args.args[0].full_url).query)
        self.assertEqual(params['q'],['!google Example Lake'])
        self.assertEqual(params['pageno'],['2'])

    def test_site_violation_is_quarantined_and_cools_route(self):
        q.mark(self.r,'google',True,dict(reason='fixture'),s)
        sid=self.r.add_search(self.profile,'"Example Lake" site:example.org','authority','test')
        task=self.r.store.one('SELECT * FROM searches WHERE id=?',(sid,))
        with patch.object(s,'searxng_search',return_value=([dict(url='https://wrong.test/report.pdf',title='Example Lake')],[])):
            self.r.do_search(task)
        self.assertEqual(self.r.store.one('SELECT status FROM searches')['status'],'retry')
        self.assertFalse(self.r.store.rows('SELECT * FROM tasks'))
        self.assertTrue(self.r.store.rows("SELECT * FROM events WHERE kind='search_quarantined'"))

    def test_opaque_documents_are_not_rejected_for_missing_lake_title(self):
        hits=[dict(url='https://example.org/download?id=7',title='Regional report')]
        self.assertTrue(q.plausible(hits,self.cfg['waterbodies'][0],'Example Lake',s))

    def test_no_healthy_web_route_keeps_crossref_available(self):
        self.r.plan()
        with patch.object(q,'available',return_value=[]):
            chosen=self.r.next_search()
        self.assertEqual(chosen['provider'],'crossref')

    def test_new_irrelevant_urls_trigger_next_search_direction(self):
        self.r.plan()
        self.r.store.db.execute("UPDATE searches SET status='done',result_count=1,completed=?",(s.utc(),))
        search=self.r.store.one("SELECT * FROM searches WHERE family='overview'")
        self.r.enqueue(self.profile,'https://unrelated.test/',branch=search['id'])
        self.r.store.db.execute("UPDATE tasks SET status='deferred',reason='llm_preflight_deferred'")
        with patch.object(q,'available',return_value=['google']):
            chosen=self.r.next_search()
        self.assertEqual(chosen['family'],'authority')

    def test_rule_only_false_positive_does_not_reward_scheduler(self):
        self.r.enqueue(self.profile,'https://example.org/lake',branch='query')
        task=self.r.store.one('SELECT * FROM tasks')
        self.r.store.db.execute("INSERT INTO urls(url,sha) VALUES(?,?)",(task['url'],'hash'))
        self.r.store.db.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',('r',self.profile,task['id'],'rules_only','{}',None,
             json.dumps(dict(decision='relevant',identity_status='candidate',topic_hits=['Water quality'])),s.utc(),'rules'))
        self.r.store.db.execute("UPDATE tasks SET status='done',review_id='r'")
        stats,covered=f.feedback(self.r,self.profile)
        self.assertEqual(stats['query']['gain'],0);self.assertFalse(covered)

    def test_crossref_metadata_does_not_mix_lake_i_and_ii(self):
        self.r.bodies[self.profile]=dict(self.r.bodies[self.profile],name='Barleber See I')
        opener=MagicMock()
        payload=dict(message=dict(items=[dict(title=['Barleber See II phosphorus'],DOI='10.1234/b'),
                                        dict(title=['Barleber See I phosphorus'],DOI='10.1234/a')]))
        opener.open.return_value.__enter__.return_value=io.BytesIO(json.dumps(payload).encode())
        with patch.object(s.ur,'build_opener',return_value=opener):
            hits=q.crossref_search(self.r,dict(query='Barleber See I',family='science_metadata',profile=self.profile),s)
        self.assertEqual([h['doi'] for h in hits],['10.1234/a'])

    def test_bibliography_keeps_metadata_but_not_quote(self):
        self.r.cfg['retrieval']['bibliography']=True
        self.r.enqueue(self.profile,'https://example.org/report')
        task=self.r.store.one('SELECT * FROM tasks')
        self.r.add_lead(task,dict(sha='abc',url=task['url']),'bibliography','Author (1990) Lake sediment study.','PRIVATE full quote','PDF page 9')
        lead=self.r.store.one('SELECT * FROM leads')
        self.assertEqual(lead['quote'],'')
        self.assertEqual(self.r.store.one('SELECT provider FROM searches')['provider'],'crossref')

    def test_more_pages_have_distinct_ids(self):
        one=self.r.add_search(self.profile,'Example Lake','overview','test',page=1)
        two=self.r.add_search(self.profile,'Example Lake','next_page','test',page=2)
        self.assertNotEqual(one,two)

    def test_content_api_empty_response_is_a_clear_retryable_error(self):
        cfg=dict(self.cfg)
        cfg['review']=dict(self.cfg['review'],base_url='https://api.example.org/v1',model='fixture',api_key_env='TEST_KEY')
        with patch.dict(s.os.environ,{'TEST_KEY':'fixture'}),patch.object(s,'api_request',return_value={'choices':[{'finish_reason':'length','message':{'content':None}}]}),self.assertRaisesRegex(s.FetchProblem,'token_limit'):
            s.review_api(cfg,{})

    def test_end_to_end_preflight_ranking_and_content_review(self):
        self.r.cfg['search']['provider']='none'
        self.r.cfg['limits']['max_searches_per_run']=0
        self.r.cfg['preflight'].update(enabled=True,api_key_env='TEST_KEY',rank_before_download=True)
        self.r.cfg['review'].update(mode='api',required=True,require_topic_evidence=True,api_key_env='TEST_KEY',base_url='https://api.example.org/v1',model='fixture')
        self.r.enqueue(self.profile,'https://example.org/report',label='Example Lake monitoring',priority=90)
        def api(url,payload,*args,**kwargs):
            req=json.loads(payload['messages'][1]['content'])
            if 'fragments' not in req:
                data=dict(action='download',importance=90,confidence=.9,identity='match',category='science',reason='Title mentions monitoring')
            else:
                data=dict(decision='relevant',identity_status='candidate',topic_hits=['Water quality'],
                          topic_evidence=[dict(topic='Water quality',evidence_id=req['fragments'][0]['evidence_id'])])
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(data)}}]}
        body=b'<html><title>Example Lake monitoring</title><main>Example Lake phosphorus monitoring in Northshire.</main></html>'
        with patch.dict(s.os.environ,{'TEST_KEY':'fixture'}),patch.object(s,'api_request',side_effect=api),patch.object(self.r.fetcher,'validate'),patch.object(self.r.fetcher,'robots'),patch.object(self.r.fetcher,'request',return_value=(200,{'content-type':'text/html'},body)),contextlib.redirect_stdout(io.StringIO()):
            summary=self.r.run()
        self.assertEqual(summary['preflight_calls'],1)
        self.assertEqual(summary['review_calls'],1)
        task=self.r.store.one('SELECT * FROM tasks')
        self.assertEqual(task['status'],'done')
        saved=self.r.store.one('SELECT response FROM reviews')['response']
        self.assertEqual(json.loads(saved)['decision'],'relevant')
        self.assertNotIn('phosphorus monitoring in Northshire',saved)

    def test_review_budget_stops_before_new_download(self):
        self.r.cfg['review'].update(required=True,max_calls_per_run=1)
        self.r.api_used=1
        self.r.enqueue(self.profile,'https://example.org/report',priority=90)
        with patch.object(self.r.fetcher,'fetch') as fetch:
            summary=self.r.run()
        fetch.assert_not_called()
        self.assertEqual(summary['stop_reason'],'review_call_budget')

    def test_invalid_evidence_does_not_trip_service_outage_counter(self):
        self.r.cfg['review'].update(mode='api',required=True)
        self.r.enqueue(self.profile,'https://example.org/report',priority=90)
        task=self.r.store.one('SELECT * FROM tasks')
        parsed=dict(title='Example Lake',text='Example Lake phosphorus.',fragments=[dict(locator='p1',text='Example Lake phosphorus.')],links=[],status='ok')
        with patch.object(s,'review_api',side_effect=ValueError('unverified evidence')):
            _,decision,_=self.r.review(task,dict(url=task['url'],sha='hash'),parsed)
        self.assertEqual(self.r.api_errors,0)
        self.assertEqual(self.r.review_validation_errors,1)
        self.assertEqual(decision['decision'],'uncertain')

    def test_review_errors_allow_recovery_but_bound_persistent_outages(self):
        self.r.cfg['review'].update(mode='api', required=True, max_calls_per_run=50)
        self.r.enqueue(self.profile,'https://example.org/report',priority=90)
        task=self.r.store.one('SELECT * FROM tasks')
        parsed=dict(title='Example Lake',text='Example Lake phosphorus.',
                    fragments=[dict(locator='p1',text='Example Lake phosphorus.')],links=[],status='ok')
        good=dict(decision='relevant',identity_status='confirmed',topic_hits=[],reason='fixture')
        outcomes=[s.FetchProblem('api_network_error') for _ in range(3)]
        outcomes += [(good, {})]
        outcomes += [s.FetchProblem('api_network_error') for _ in range(5)]
        with patch.object(s,'review_api',side_effect=outcomes) as api:
            for i in range(9):
                self.r.review(task,dict(url=task['url'],sha=str(i)),parsed)
                if i==2:
                    self.assertFalse(self.r.review_error_limit_reached())
                if i==3:
                    self.assertEqual(self.r.consecutive_api_errors,0)
            self.assertTrue(self.r.review_error_limit_reached())
            self.r.review(task,dict(url=task['url'],sha='after-limit'),parsed)
            self.assertEqual(api.call_count,9)
        self.assertEqual(self.r.api_errors,8)
        self.r.consecutive_api_errors=0
        self.r.api_errors=30
        self.assertTrue(self.r.review_error_limit_reached())

    def test_review_error_budget_rejects_invalid_configuration(self):
        for value in [0, -1, True, 1.5]:
            with self.assertRaises(ValueError):
                config(self.tmp.name,review=dict(max_consecutive_api_errors=value))

    def test_unreviewed_lake_gets_first_content_check_before_extra_hits(self):
        other='other-profile';self.r.active.add(other)
        self.r.bodies[other]=dict(self.r.bodies[self.profile],id='other')
        self.r.enqueue(self.profile,'https://example.org/extra',priority=95)
        first=self.r.store.one('SELECT * FROM tasks')
        self.r.store.db.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',('reviewed',self.profile,first['id'],'complete','{}','{}','{}',s.utc(),'fixture'))
        self.r.store.db.execute("UPDATE tasks SET review_id='reviewed' WHERE id=?",(first['id'],))
        self.r.enqueue(other,'https://example.org/initial',priority=50)
        self.assertEqual(self.r.next_task(set())['profile'],other)
        self.r.body_fetch_attempts[other]=3
        self.assertEqual(self.r.next_task(set())['profile'],self.profile)


class EvidenceTests(unittest.TestCase):
    def request(self,text):
        return dict(waterbody=dict(name='Arendsee',aliases=[],identifiers=[],ambiguous_place_name=True),
                    topics=[dict(name='History')],fragments=[dict(locator='page 1',text=text)],links=[],require_topic_evidence=True)

    def result(self,quote):
        return dict(decision='relevant',identity_status='candidate',topic_hits=['History'],evidence_quotes=[quote],
                    topic_evidence=[dict(topic='History',quote=quote,locator='page 1')])

    def test_city_history_not_accepted_as_lake_history(self):
        text='Die Stadt Arendsee erhielt im Jahr 1260 das Stadtrecht.'
        with self.assertRaisesRegex(ValueError,'Stadt'):
            s.validate_review(self.result(text),self.request(text))

    def test_topic_evidence_requires_correct_locator(self):
        text='Der See Arendsee wurde im Jahr 1995 limnologisch untersucht.'
        result=self.result(text);result['topic_evidence'][0]['locator']='invented page'
        with self.assertRaisesRegex(ValueError,'Fundstelle'):
            s.validate_review(result,self.request(text))

    def test_valid_lake_evidence_keeps_locator_without_storing_quote(self):
        text='Der See Arendsee wurde im Jahr 1995 limnologisch untersucht.'
        out=s.private_decision(s.validate_review(self.result(text),self.request(text)))
        self.assertEqual(out['decision'],'relevant')
        self.assertEqual(out['evidence_locations'][0]['locator'],'page 1')
        self.assertNotIn(text,json.dumps(out,ensure_ascii=False))

    def test_selected_evidence_id_uses_original_text(self):
        text='Der See Arendsee wurde im Jahr 1995 limnologisch untersucht.'
        request=self.request(text);request['fragments'][0]['evidence_id']='e1'
        response=self.result('not a literal quote')
        response['topic_evidence']=[dict(topic='History',evidence_id='e1')]
        out=s.validate_review(response,request)
        self.assertEqual(out['evidence_quotes'],[text])
        response['topic_evidence'][0]['evidence_id']='fake'
        with self.assertRaisesRegex(ValueError,'Beleg-ID'):
            s.validate_review(response,request)

    def test_bad_optional_reference_is_discarded_not_followed(self):
        text='Der See Arendsee wurde im Jahr 1995 limnologisch untersucht.'
        result=self.result(text)
        result['references']=[dict(kind='bibliography',quote='invented source passage',citation='invented',locator='page 1')]
        out=s.validate_review(result,self.request(text))
        self.assertEqual(out['decision'],'relevant')
        self.assertFalse(out['references'])
        self.assertTrue(out['validation_warnings'])

    def test_bad_optional_query_does_not_discard_valid_evidence(self):
        text='Der See Arendsee wurde im Jahr 1995 limnologisch untersucht.'
        result=self.result(text)
        result['queries']=['phosphorus monitoring', 'Arendsee Phosphor', None, 'Arendsee '+('x'*401)]
        out=s.validate_review(result,self.request(text))
        self.assertEqual(out['decision'],'relevant')
        self.assertEqual(out['queries'],['Arendsee Phosphor'])
        self.assertEqual(len(out['validation_warnings']),3)
        result['queries']='invalid optional field'
        out=s.validate_review(result,self.request(text))
        self.assertEqual(out['decision'],'relevant')
        self.assertEqual(out['queries'],[])
        result['topic_evidence'][0]['locator']='invented page'
        with self.assertRaises(ValueError):
            s.validate_review(result,self.request(text))

    def test_passage_selection_finds_later_lake_mentions(self):
        import evidence_review
        text=('Navigation other topics. '*400)+'Der See Arendsee wurde 1995 untersucht.'
        parsed=dict(fragments=[dict(locator='HTML text',text=text)])
        out=evidence_review.passages(parsed,self.request(text)['waterbody'],[dict(name='History',keywords=['1995'])],2000,s)
        self.assertIn('Arendsee',out[0]['text'])
        self.assertIn(out[0]['text'],text)

    def test_town_flag_is_sent_and_unknown_gets_exploration_priority(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(s.os.environ,{'TEST_KEY':'fixture'}):
            cfg=config(tmp,waterbodies=[dict(id='lake',name='Arendsee',ambiguous_place_name=True)],
                       retrieval=dict(enabled=True),preflight=dict(enabled=True,api_key_env='TEST_KEY',rank_before_download=True))
            r=s.Research(cfg,Path(tmp)/'dossier')
            try:
                profile=next(iter(r.active));r.enqueue(profile,'https://arendsee.example/unknown.pdf',label='arendsee.info')
                task=r.store.one('SELECT * FROM tasks')
                response=dict(action='download',importance=88,confidence=.98,identity='match',category='science',reason='Metadata')
                with patch.object(s,'api_request',return_value=dict(choices=[dict(message=dict(content=json.dumps(response)))])) as api,patch.object(r.fetcher,'fetch') as fetch:
                    r.process(task)
                request=json.loads(api.call_args.args[1]['messages'][1]['content'])
                self.assertTrue(request['waterbody']['ambiguous_place_name'])
                self.assertEqual(r.store.one('SELECT tier FROM preflight_scores')['tier'],'explore')
                self.assertEqual(r.store.one('SELECT reason FROM tasks')['reason'],'preflight_ready')
                fetch.assert_not_called()
            finally:r.close()


if __name__=='__main__':unittest.main()
