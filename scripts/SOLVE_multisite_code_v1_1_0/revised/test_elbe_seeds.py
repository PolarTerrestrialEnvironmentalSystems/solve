"""Offline behavioral tests for bounded, resumable seed preparation."""
import json
import io
import hashlib
import tempfile
import threading
import time
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import prepare_elbe_seeds as seeds


WB={'waterbody_id':'DELW_DEST_MEL06OW17-00','name_original':'ARENDSEE',
    'waterbody_type':'lake','country_code':'DE','river_basin_code':'DE5000',
    'reporting_state':'ST','spatial_states_target':['ST']}


class Search:
    def __init__(self,hits):self.hits=hits;self.calls=[]
    def search(self,query):self.calls.append(query);return self.hits


class Fetch:
    def __init__(self,overrides=None):self.calls=[];self.overrides=overrides or {}
    def __call__(self,url):
        self.calls.append(url)
        return dict(self.overrides.get(url,{'status':'fetched','http_status':200,
                    'content_type':'text/html','final_url':url,
                    'text':('Arendsee Sachsen-Anhalt: Phosphor, Nährstoffe und Wasserqualität. Messdaten und Sanierung. '
                            'Der Forschungsbericht dokumentiert die Probennahme, Messmethoden und zeitlichen Veränderungen. ')*3}))


class SeedTests(unittest.TestCase):
    def test_identity_is_not_city_author_or_tourism(self):
        row={'url':'https://example.org/report','title':'Arendsee Phosphor und Gewässergüte','snippet':'Messdaten'}
        self.assertEqual(seeds.relevance(WB,row)['status'],'eligible')
        self.assertEqual(seeds.relevance(WB,dict(row,title='Arendsee Hotel am See'))['reason'],'tourism_or_unrelated_title')
        self.assertEqual(seeds.relevance(WB,dict(row,title='Arendsee Stadt Apotheke'))['status'],'rejected')
        self.assertEqual(seeds.relevance(WB,dict(row,title='Author Zachary Arendsee swine influenza',snippet='virus'))['reason'],'no_water_science_topic')
        self.assertEqual(seeds.relevance(WB,dict(row,title='Gardensee Phosphor'))['reason'],'waterbody_identity_uncertain')
        self.assertEqual(seeds.relevance(WB,dict(row,title='Unsere Gewässer – Anglerverein'),body='Arendsee Wasserstand und Karten. Auch Spree und Havel.',trusted_content=True)['reason'],'generic_waterbody_directory')
        self.assertEqual(seeds.relevance(WB,dict(row,title='Arendsee'),body='Start Tourismus & Freizeit Arendsee, Hochwasser, historische Staumauer.',trusted_content=True)['reason'],'tourism_background_not_technical_start_source')
        self.assertEqual(seeds.relevance(WB,dict(row,url='https://lhw.sachsen-anhalt.de/arendsee',title='Arendsee Badewasserqualität und Blaualgenwarnung'),body='Tourismus & Freizeit Arendsee. Badewasserqualität und Blaualgenwarnung.',trusted_content=True)['status'],'eligible')

    def test_scope_qualifier_and_name_ambiguity(self):
        w=dict(WB,waterbody_id='DELW_DEMV_2300102',name_original='SCHAALSEE ZARRENTINER BECKEN')
        self.assertIsNone(seeds.identity(w,'Schaalsee Phosphor Maßnahmen'))
        self.assertEqual(seeds.identity(w,'Schaalsee Zarrentiner Becken Gewässergüte'),'exact_name')
        self.assertIsNone(seeds.identity(dict(WB,_ambiguous_name=True),'Arendsee Gewässergüte'))
        self.assertEqual(seeds.identity(dict(WB,_ambiguous_name=True),WB['waterbody_id']),'exact_identifier')
        self.assertEqual(seeds.identity(w,'Schaalsee, davon Zarrentiner Becken'),'exact_name')
        self.assertIsNone(seeds.identity(dict(WB,_requires_region=True),'Arendsee Wasserqualität'))
        self.assertEqual(seeds.identity(dict(WB,_requires_region=True),'Arendsee Wasserqualität Sachsen-Anhalt'),'exact_name')
        small=dict(WB,waterbody_id='DELW_DEBB80001582849239',name_original='SCHWERINER SEE',spatial_states_target=['BB'])
        self.assertIsNone(seeds.identity(small,'Schweriner See in Mecklenburg-Vorpommern, Phosphor'))
        self.assertEqual(seeds.identity(small,'Schweriner See in Brandenburg, Phosphor'),'exact_name')
        self.assertIsNone(seeds.identity(small,'Schweriner See Phosphor. '+('Unabhängiger Text. '*100)+' Verlag Brandenburg.'))
        saxony=dict(WB,_requires_region=True,spatial_states_target=['SN'])
        self.assertIsNone(seeds.identity(saxony,'Arendsee Sachsen-Anhalt Phosphor'))
        self.assertEqual(seeds.identity(saxony,'Arendsee Sachsen Phosphor'),'exact_name')

    def test_search_homepage_secret_and_zip_do_not_become_seeds(self):
        self.assertIsNone(seeds.canonical('https://example.org/report?api_key=do-not-save'))
        self.assertIsNone(seeds.canonical('http://127.0.0.1/admin'))
        self.assertEqual(seeds.url_problem('https://example.org/'),'generic_homepage')
        self.assertEqual(seeds.url_problem('https://example.org/search?q=Arendsee'),'generic_search_url')
        self.assertEqual(seeds.url_problem('https://example.org/Arendsee.zip'),'non_html_pdf_data')
        self.assertEqual(seeds.canonical('https://example.org/doc?a=1&utm_campaign=x#p2'),'https://example.org/doc?a=1')

    def test_template_is_hypothesis_not_identity_evidence(self):
        wb=dict(WB,waterbody_id='DELW_DEBB800015828419',name_original='SCHARMUETZELSEE')
        template=seeds.template_candidates(wb)[0]
        with tempfile.TemporaryDirectory() as tmp:
            result=seeds.Collector(tmp,Search([]),Fetch(),target=1,max_searches=0).process(wb)
        self.assertEqual(len(result['accepted']),0)
        self.assertTrue(template['template_is_hypothesis_until_content_verified'])

    def test_legacy_match_requires_name_and_state_then_real_content(self):
        legacy={'waterbodies':[{'id':'old','name':'Arendsee','region':'Brandenburg','seed_urls':['https://example.org/a'],
                              'seed_titles':{'https://example.org/a':'Arendsee Messdaten und Sanierung'}}]}
        self.assertEqual(seeds.legacy_candidates(WB,legacy),[])
        legacy['waterbodies'][0]['region']='Sachsen-Anhalt'
        self.assertEqual(len(seeds.legacy_candidates(WB,legacy)),1)
        with tempfile.TemporaryDirectory() as tmp:
            search=Search([])
            result=seeds.Collector(tmp,search,Fetch(),legacy,target=1,max_searches=0).process(WB)
            self.assertEqual(len(result['accepted']),1)
            self.assertEqual(search.calls,[])

    def test_resume_does_not_repeat_paid_search_or_download(self):
        hits=[{'url':'https://example.org/a','title':'Arendsee Gewässergüte','snippet':'Messdaten','provider':'serper'}]
        with tempfile.TemporaryDirectory() as tmp:
            search=Search(hits);fetch=Fetch()
            collector=seeds.Collector(tmp,search,fetch,target=1,max_searches=6)
            first=collector.process(WB);second=collector.process(WB)
            self.assertEqual(first['seed_urls'],second['seed_urls'])
            self.assertEqual(len(search.calls),1)
            self.assertEqual(len(fetch.calls),1)
            self.assertEqual(second['status'],'target_reached')

    def test_blocked_and_duplicate_redirects_do_not_fill_target(self):
        hits=[{'url':'https://example.org/'+s,'title':'Arendsee Wasserqualität','snippet':'Messdaten','provider':'serper'} for s in ('a','b','blocked')]
        override={'https://example.org/b':{'status':'fetched','http_status':200,'final_url':'https://example.org/a','text':'Arendsee Wasserqualität Messdaten'},
                  'https://example.org/blocked':{'status':'human_review','reason':'http_403','http_status':403}}
        with tempfile.TemporaryDirectory() as tmp:
            result=seeds.Collector(tmp,Search(hits),Fetch(override),target=2,max_searches=1).process(WB)
        self.assertEqual(len(result['accepted']),1)
        self.assertEqual(len(result['human_review']),1)
        self.assertTrue(any(r['status']=='duplicate' for r in result['rejected']))
        self.assertEqual(result['shortfall'],1)

    def test_interrupted_paid_call_is_reserved_and_never_repeated(self):
        class Interrupted:
            def search(self,q):raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyboardInterrupt):seeds.Collector(tmp,Interrupted(),Fetch(),target=1,max_searches=1).process(WB)
            search=Search([])
            result=seeds.Collector(tmp,search,Fetch(),target=1,max_searches=1).process(WB)
            self.assertEqual(search.calls,[])
            self.assertEqual(result['search_calls'][0]['status'],'reserved')
            self.assertEqual(result['shortfall'],1)

    def test_large_official_pdf_only_accepted_with_verified_id(self):
        url='https://example.org/arendsee.pdf'
        fetch=Fetch({url:{'status':'human_review','reason':'pdf_too_large_for_identity_sample',
                         'format':'pdf','http_status':200,'final_url':url}})
        for validated in (False,True):
            candidate={'url':url,'title':'Arendsee WRRL Steckbrief'}
            if validated:candidate.update(catalogue_verified_waterbody_id=WB['waterbody_id'],evidence_basis='Exact published official catalogue link')
            with tempfile.TemporaryDirectory() as tmp:
                result=seeds.Collector(tmp,Search([]),fetch,target=1,max_searches=0,candidate_map={WB['waterbody_id']:[candidate]}).process(WB)
                self.assertEqual(len(result['accepted']),int(validated))

    def test_full_six_query_budget_is_honest_shortfall(self):
        with tempfile.TemporaryDirectory() as tmp:
            search=Search([])
            result=seeds.Collector(tmp,search,Fetch(),target=10,max_searches=6).process(WB)
        self.assertEqual(len(search.calls),6)
        self.assertEqual(result['status'],'shortfall')
        self.assertEqual(result['shortfall'],10)
        self.assertEqual(len(set(search.calls)),6)

    def test_http_cache_persists_across_collector_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=seeds.PublicFetcher(cache_dir=tmp,host_delay=0)
            with patch.object(a,'fetch',return_value={'status':'fetched','text':'Arendsee Messdaten'}) as fetch:
                a('https://example.org/data');a('https://example.org/data')
                self.assertEqual(fetch.call_count,1)
            b=seeds.PublicFetcher(cache_dir=tmp,host_delay=0)
            with patch.object(b,'fetch',side_effect=AssertionError('cache miss')):
                self.assertEqual(b('https://example.org/data')['text'],'Arendsee Messdaten')

    def test_serper_auth_failure_stops_all_lakes_without_secret_output(self):
        client=seeds.SerperClient('PRIVATE_TEST_KEY',delay=0)
        class Opener:
            calls=0
            def open(self,*a,**kw):
                self.calls+=1
                raise urllib.error.HTTPError('https://google.serper.dev/search',402,'PRIVATE_TEST_KEY',{},io.BytesIO(b'PRIVATE_TEST_KEY'))
        opener=Opener()
        with patch.object(seeds.ur,'build_opener',return_value=opener):
            for _ in range(3):
                with self.assertRaisesRegex(seeds.ApiError,'^serper_http_402$'):client.search('Arendsee')
        self.assertEqual(opener.calls,1)

    def test_serper_429_shares_cooldown_and_caps_retries(self):
        client=seeds.SerperClient('PRIVATE_TEST_KEY',delay=0)
        headers=Message();headers['Retry-After']='17'
        class Opener:
            calls=0
            def open(self,*a,**kw):
                self.calls+=1
                raise urllib.error.HTTPError('https://google.serper.dev/search',429,'secret error',headers,None)
        opener=Opener();attempts=[]
        with patch.object(seeds.ur,'build_opener',return_value=opener),patch.object(client,'_wait'):
            with self.assertRaisesRegex(seeds.ApiError,'^serper_http_429$'):
                client.search('Arendsee',on_attempt=lambda:attempts.append(1),max_attempts=3)
        self.assertEqual(opener.calls,3)
        self.assertEqual(len(attempts),3)
        self.assertGreater(client.cooldown_until,time.monotonic()+15)

    def test_retries_count_towards_per_lake_http_budget(self):
        client=seeds.SerperClient('PRIVATE_TEST_KEY',delay=0)
        headers=Message();headers['Retry-After']='1'
        class Opener:
            calls=0
            def open(self,*a,**kw):
                self.calls+=1
                raise urllib.error.HTTPError('https://google.serper.dev/search',429,'secret',headers,None)
        opener=Opener()
        with tempfile.TemporaryDirectory() as tmp,patch.object(seeds.ur,'build_opener',return_value=opener),patch.object(client,'_wait'):
            result=seeds.Collector(tmp,client,Fetch(),target=10,max_searches=2).process(WB)
        self.assertEqual(opener.calls,2)
        self.assertEqual(result['search_http_calls'],2)
        self.assertEqual(result['status'],'search_unavailable')

    def test_eight_workers_still_at_most_two_serper_calls(self):
        client=seeds.SerperClient('PRIVATE_TEST_KEY',delay=0)
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,n):time.sleep(.02);return b'{"organic":[]}'
        active=0;maximum=0;lock=threading.Lock()
        class Opener:
            def open(self,*a,**kw):
                class Counted(Response):
                    def __enter__(self):
                        nonlocal active,maximum
                        with lock:active+=1;maximum=max(maximum,active)
                        return self
                    def __exit__(self,*a):
                        nonlocal active
                        with lock:active-=1
                return Counted()
        with patch.object(seeds.ur,'build_opener',return_value=Opener()):
            with seeds.concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(client.search,['Arendsee']*8))
        self.assertLessEqual(maximum,2)

    def test_transient_windows_replace_error_retries_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'lake.json';real=seeds.os.replace;attempts=[]
            def replace(a,b):
                attempts.append(1)
                if len(attempts)<3:raise PermissionError('simulated sharing violation')
                return real(a,b)
            with patch.object(seeds.os,'replace',side_effect=replace),patch.object(seeds.time,'sleep'):
                seeds.atomic_json(path,{'waterbody_id':WB['waterbody_id']})
            self.assertEqual(len(attempts),3)
            self.assertEqual(json.loads(path.read_text())['waterbody_id'],WB['waterbody_id'])

    def test_new_policy_rechecks_previous_accepted_tourism_page(self):
        url='https://example.org/arendsee'
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'waterbodies'/(seeds.file_id(WB['waterbody_id'])+'.json')
            seeds.atomic_json(path,{'schema_version':seeds.VERSION,'waterbody_id':WB['waterbody_id'],'name':'Arendsee','search_calls':[],
                                   'candidates':[{'url':url,'final_url':url,'title':'Arendsee','status':'accepted'}]})
            fetch=Fetch({url:{'status':'fetched','final_url':url,'text':'Tourismus & Freizeit Arendsee Hochwasser'}})
            result=seeds.Collector(tmp,Search([]),fetch,target=1,max_searches=0).process(WB)
            self.assertEqual(result['accepted'],[])
            self.assertEqual(result['human_review'][0]['reason'],'tourism_background_not_technical_start_source')

    def test_real_pilot_tourism_domains_are_never_accepted(self):
        urls=['https://www.komoot.com/de-de/guide/3799603/seen-rund-um-den-platkowsee',
              'https://www.reiseland-brandenburg.de/poi/uckermark/badestellen/',
              'https://www.brandenburg-tourism.com/poi/lakes/',
              'https://www.seen.de/grosser-warthesee/',
              'https://www.alleangeln.de/gewaesser/gro%C3%9Fer-warthesee']
        for url in urls:
            result=seeds.relevance(WB,{'url':url,'title':'Arendsee Wasserqualität und Messdaten'},
                                   body='Arendsee Phosphor Sediment Wasserqualität Messdaten',trusted_content=True)
            self.assertEqual(result['status'],'rejected',url)
        self.assertEqual(seeds.relevance(WB,{'url':'https://wave-bite.com/arendsee','title':'Arendsee Monitoring'})['status'],'human_review')

    def test_bare_water_word_and_distant_footer_are_insufficient(self):
        row={'url':'https://example.org/arendsee','title':'Arendsee Gewässer','snippet':''}
        self.assertEqual(seeds.relevance(WB,row)['reason'],'no_water_science_topic')
        body='Arendsee '+('Ausführlicher allgemeiner Text. '*100)+' Phosphor Sediment Monitoring'
        self.assertEqual(seeds.relevance(WB,row,body=body,trusted_content=True)['reason'],'identity_and_topic_not_in_same_context')
        result=seeds.relevance(WB,{'url':'https://kulturfeste.de/orte/arendsee/','title':'Arendsee'},
                               body=('Arendsee Wasserqualität, Phosphor, Sedimente, Messdaten und Sanierung. '*10),trusted_content=True)
        self.assertEqual(result['reason'],'unknown_publisher_without_technical_source_title')

    def test_explicit_file_key_overrides_stale_process_environment(self):
        with patch.dict(seeds.os.environ,{'BLABLADOR_KEY':'stale-test-value'}),patch('dotenv.dotenv_values',return_value={'GRAPHRAG_API_KEY':'fresh-test-value'}):
            key,source=seeds.llm_key_from_file_or_environment('not-a-real-env-file')
            self.assertEqual(key,'fresh-test-value')
            self.assertEqual(source,'GRAPHRAG_API_KEY (explicit env file)')

    def test_technical_mining_history_is_in_scope(self):
        result=seeds.relevance(WB,{'url':'https://example.org/report','title':'Arendsee Rekultivierungsbericht und Seegenese'},
            body=('Arendsee: Seegenese und Bathymetrie. Der Bericht untersucht Bergbau, Flutung und Rekultivierung sowie die historische Gewässerbewirtschaftung. '*4),trusted_content=True)
        self.assertEqual(result['status'],'eligible')

    def test_posthoc_veto_is_fail_closed_resumable_and_never_searches(self):
        url='https://example.org/arendsee'
        class Reviewer:
            model='alias-fast';disabled_reason=None
            def __init__(self):self.calls=0
            def review(self,wb,rows):
                self.calls+=1
                if self.calls==1:raise seeds.ApiError('llm_http_429')
                return {0:{'action':'accept','identity':'match','category':'research','reason':'specific report'}}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'waterbodies'/(seeds.file_id(WB['waterbody_id'])+'.json')
            seeds.atomic_json(path,{'schema_version':seeds.VERSION,'policy_version':seeds.POLICY_VERSION,
                'waterbody_id':WB['waterbody_id'],'name':'Arendsee','status':'target_reached','search_calls':[],
                'search_http_calls':0,'candidates':[{'url':url,'final_url':url,'title':'Arendsee Messdaten','status':'accepted'}]})
            seeds.atomic_json(root/'http_cache'/(hashlib.sha256(url.encode()).hexdigest()+'.json'),
                {'url':url,'result':{'text':'Arendsee Messdaten Sediment und Wasserqualität'}})
            reviewer=Reviewer();search=Search([])
            collector=seeds.Collector(root,search,Fetch(),target=1,max_searches=0,reviewer=reviewer)
            first=collector.review_existing(WB)
            self.assertEqual(first['accepted'],[])
            self.assertEqual(first['human_review'][0]['reason'],'llm_content_review_unavailable')
            second=collector.review_existing(WB);third=collector.review_existing(WB)
            self.assertEqual(len(second['accepted']),1)
            self.assertEqual(len(third['accepted']),1)
            self.assertEqual(reviewer.calls,2)
            self.assertEqual(search.calls,[])

    def test_partial_llm_batch_preserves_valid_items_and_retries_missing_once(self):
        reviewer=seeds.LLMReviewer('dummy-test-key',delay=0)
        accepted={'action':'accept','identity':'match','category':'research','reason':'specific report'}
        hits=[{'url':'https://example.org/a'},{'url':'https://example.org/b'},{'url':'https://example.org/c'}]
        outcomes=[seeds.ApiError('llm_incomplete_response',partial_decisions={1:accepted}),
                  {0:accepted},seeds.ApiError('llm_incomplete_response')]
        with patch.object(reviewer,'_review_once',side_effect=outcomes) as request:
            actual=reviewer.review(WB,hits)
        self.assertEqual(request.call_count,3)
        self.assertEqual(request.call_args_list[1].args[1],[hits[0]])
        self.assertEqual(request.call_args_list[2].args[1],[hits[2]])
        self.assertEqual(actual[1]['review_recovery'],'batch_partial_kept')
        self.assertEqual(actual[0]['review_recovery'],'single_retry_completed')
        self.assertEqual(actual[2]['action'],'uncertain')
        self.assertEqual(actual[2]['review_recovery'],'single_retry_failed')

    def test_three_invalid_json_outputs_pause_global_llm_review(self):
        reviewer=seeds.LLMReviewer('PRIVATE_TEST_KEY',delay=0)
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,n):return b'{"choices":[{"finish_reason":"stop","message":{"content":"not valid JSON"}}]}'
        class Opener:
            calls=0
            def open(self,*a,**kw):self.calls+=1;return Response()
        opener=Opener()
        with patch.object(seeds.ur,'build_opener',return_value=opener):
            for i in range(3):
                with self.assertRaisesRegex(seeds.ApiError,'^llm_invalid_response$'):
                    reviewer.review(WB,[{'url':'https://example.org/a'}])
            with self.assertRaisesRegex(seeds.ApiError,'circuit_open'):
                reviewer.review(WB,[{'url':'https://example.org/a'}])
        self.assertEqual(opener.calls,3)
        self.assertTrue(reviewer.stop_event.is_set())

    def test_official_identifier_exemption_requires_verified_template_url(self):
        wb=dict(WB,waterbody_id='DELW_DEBB800015828419',name_original='SCHARMUETZELSEE',spatial_states_target=['BB'])
        official=seeds.template_candidates(wb)[0]['url']
        class Reviewer:
            model='alias-fast';disabled_reason=None
            def review(self,*a):raise AssertionError('No remote LLM call needed in this fixture')
        for url,expected in [(official,1),('https://example.org/unverified.pdf',0)]:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);path=root/'waterbodies'/(seeds.file_id(wb['waterbody_id'])+'.json')
                seeds.atomic_json(path,{'schema_version':seeds.VERSION,'policy_version':seeds.POLICY_VERSION,
                    'waterbody_id':wb['waterbody_id'],'name':wb['name_original'],'search_calls':[],
                    'search_http_calls':0,'candidates':[{'url':url,'final_url':url,'status':'accepted',
                        'provider':'verified_land_template','identity_method':'exact_identifier',
                        'evidence':wb['waterbody_id']+' WRRL Wasserqualität','http':{'http_status':200}}]})
                result=seeds.Collector(root,Search([]),Fetch(),target=1,max_searches=0,reviewer=Reviewer()).review_existing(wb)
                self.assertEqual(len(result['accepted']),expected)


if __name__=='__main__':unittest.main()
