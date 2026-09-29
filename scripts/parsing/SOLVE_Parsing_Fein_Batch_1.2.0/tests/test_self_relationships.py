import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from blablador_client import APIError
from common import BASE_DIR, read_json
from extract_facts import extract_chunk
from schemas import Event, Extraction, Relationship
from relationship_guard import CAUSAL_RELATIONSHIP_TYPES
from repair_relationships import validate_repair
from validate_export import finalize

TEXT = 'Die Bewirtschaftung erhöhte den Abfluss.'
BLOCK = {'block_id':'b1','page':1,'text':TEXT}
CHUNK = {'chunk_id':'c1','pages':[1],'blocks':[BLOCK],'context_blocks':[]}
ONTOLOGY = read_json(BASE_DIR/'ontologie_fein.json')


def event(eid='e1', statement=TEXT):
    return Event(event_id=eid,record_type='Prozess',ontology_concept='core:Process',
                 statement=statement,assertion_mode='reported',polarity='affirmed',
                 evidence=[{'block_id':'b1','page':1,'quote':TEXT,'field_names':['statement']}]).model_dump(mode='json')


def edge(source='e1',target='e1',kind='causes',rid='r1'):
    return Relationship(relationship_id=rid,source_event_id=source,target_event_id=target,
                        relationship_type=kind,statement=TEXT,relationship_scope='specific_case',
                        assertion_mode='reported',polarity='affirmed',
                        evidence=event()['evidence']).model_dump(mode='json')


def payload():
    return Extraction(events=[event()],relationships=[edge()]).model_dump(mode='json')


def repaired():
    data=payload()
    data['events'] += [event('e2','Bewirtschaftung des Gewässers.'),event('e3','Erhöhter Abfluss.')]
    data['relationships']=[edge('e2','e3')]
    return data


def response(data,finish='stop'):
    return {'model':'mock','choices':[{'finish_reason':finish,'message':{'content':json.dumps(data)}}]}


def package(data):
    return {'status':'complete','chunk_id':'c1','pages':[1],'result':data,'supplied_blocks':[BLOCK],
            'extracted_at':'2026-09-28T00:00:00Z','raw_response_file':'original.json','response_model':'mock'}


class Client:
    def __init__(self,responses):
        self.responses=iter(responses);self.calls=[];self.format_mode='json_schema'
        self.settings=SimpleNamespace(max_output_tokens=16000,context_tokens=1000000)
    def complete(self,messages,schema,**context):
        self.calls.append(context)
        value=next(self.responses)
        if isinstance(value,Exception):raise value
        return value


class SelfRelationshipTests(unittest.TestCase):
    def test_all_six_historical_self_relationships_quarantined(self):
        packages=read_json(BASE_DIR/'tests/fixtures/historical_self_relationships.json')
        rejected=0
        for package in packages:
            _,rels,review,_=finalize([package],ONTOLOGY,{},'historical','fein')
            self.assertFalse(any(r['source_event_id']==r['target_event_id'] for r in rels))
            rejected+=sum('causal_self_relationship' in r.get('reasons',[]) for r in review)
        self.assertEqual(rejected,6)

    def test_saved_successful_live_repair_remains_valid(self):
        packages=read_json(BASE_DIR/'tests/fixtures/historical_self_relationships.json')
        original=next(p for p in packages if p['chunk_id']=='pilot_measures')
        candidate=Extraction.model_validate(read_json(BASE_DIR/'tests/fixtures/live_repair_candidate.json'))
        validate_repair(Extraction.model_validate(original['result']),candidate,['r2'],
                        original['chunk_id'],[],ONTOLOGY,{b['block_id']:b for b in original['supplied_blocks']})
        result={**original,'result':candidate.model_dump(mode='json')}
        _,rels,_,_=finalize([result],ONTOLOGY,{},'live_fixture','fein')
        self.assertEqual(len(rels),2)
        self.assertFalse(any(r['source_event_id']==r['target_event_id'] for r in rels))

    def finalize(self,data):
        return finalize([package(data)],ONTOLOGY,{},'signature','fein')

    def test_all_six_causal_types_quarantined(self):
        for kind in CAUSAL_RELATIONSHIP_TYPES:
            with self.subTest(kind=kind):
                data=payload();data['relationships'][0]['relationship_type']=kind
                events,rels,review,_=self.finalize(data)
                self.assertEqual(len(events),1);self.assertEqual(rels,[])
                rejected=next(r for r in review if r['kind']=='rejected_relationship')
                self.assertIn('causal_self_relationship',rejected['reasons'])

    def test_distinct_valid_edge_kept(self):
        self.assertEqual(len(self.finalize(repaired())[1]),1)

    def test_noncausal_policy_unchanged(self):
        data=payload();data['relationships'][0]['relationship_type']='core:associated_with'
        self.assertEqual(len(self.finalize(data)[1]),1)

    def test_same_event_after_deduplication_quarantined(self):
        data=payload();data['events'].append(event('e2'));data['relationships']=[edge('e1','e2')]
        events,rels,review,_=self.finalize(data)
        self.assertEqual(len(events),1);self.assertFalse(rels)
        self.assertTrue(any('causal_self_relationship' in r.get('reasons',[]) for r in review))

    def test_namespace_alias_quarantined(self):
        data=payload();data['relationships']=[edge('e1','c1:e1')]
        self.assertFalse(self.finalize(data)[1])

    def test_unknown_topics_do_not_hide_self_reason(self):
        data=payload();data['events'][0]['categories']=['not-an-id']
        _,rels,review,_=self.finalize(data)
        self.assertFalse(rels)
        self.assertTrue(any('causal_self_relationship' in r.get('reasons',[]) for r in review))

    def run_extraction(self,answers):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        client=Client(answers);out=Path(temp.name)
        packages=extract_chunk(client,CHUNK,[],ONTOLOGY,{},out,'signature','fein')
        return client,out,packages

    def test_one_repair_and_resume_without_another_call(self):
        client,out,packages=self.run_extraction([response(payload()),response(repaired())])
        self.assertEqual(len(client.calls),2)
        self.assertEqual(client.calls[1]['purpose'],'causal_self_relationship_repair')
        p=packages[0]
        self.assertEqual(p['relationship_review'][0]['status'],'accepted')
        self.assertNotEqual(p['raw_response_file'],p['original_raw_response_file'])
        self.assertTrue(Path(p['original_raw_response_file']).exists())
        again=extract_chunk(client,CHUNK,[],ONTOLOGY,{},out,'signature','fein')
        self.assertEqual(again,packages);self.assertEqual(len(client.calls),2)
        self.assertEqual(len(finalize(packages,ONTOLOGY,{},'signature','fein')[1]),1)

    def test_unchanged_self_loop_fails_closed_without_losing_events(self):
        client,_,packages=self.run_extraction([response(payload()),response(payload())])
        self.assertEqual(len(client.calls),2)
        events,rels,review,_=finalize(packages,ONTOLOGY,{},'signature','fein')
        self.assertEqual(len(events),1);self.assertFalse(rels)
        self.assertTrue(any(r.get('status')=='rejected' for r in review))

    def test_api_failure_preserves_original_and_quarantines(self):
        client,_,packages=self.run_extraction([response(payload()),APIError(503,'unavailable')])
        self.assertEqual(len(client.calls),2)
        events,rels,_,_=finalize(packages,ONTOLOGY,{},'signature','fein')
        self.assertEqual(len(events),1);self.assertFalse(rels)

    def test_truncated_repair_does_not_split_original(self):
        client,_,packages=self.run_extraction([response(payload()),response(repaired(),'length')])
        self.assertEqual(len(client.calls),2);self.assertEqual(len(packages),1)
        self.assertEqual(packages[0]['result'],payload())

    def test_no_repair_when_unneeded(self):
        client,_,_=self.run_extraction([response(repaired())])
        self.assertEqual(len(client.calls),1)

    def test_context_budget_skips_repair_without_losing_data(self):
        with patch('extract_facts.estimated_tokens',side_effect=[1,1000000]):
            client,_,packages=self.run_extraction([response(payload())])
        self.assertEqual(len(client.calls),1)
        self.assertEqual(packages[0]['relationship_review'][0]['status'],'skipped')
        self.assertFalse(finalize(packages,ONTOLOGY,{},'signature','fein')[1])

    def test_withdrawal_audited(self):
        candidate=payload();candidate['relationships']=[]
        _,_,packages=self.run_extraction([response(payload()),response(candidate)])
        review=packages[0]['relationship_review'][0]
        self.assertEqual(review['withdrawn_relationship_ids'],['r1'])
        self.assertEqual(len(packages[0]['result']['events']),1)

    def validate(self,candidate,original=None):
        validate_repair(Extraction.model_validate(original or payload()),Extraction.model_validate(candidate),
                        ['r1'],'c1',[],ONTOLOGY,{'b1':BLOCK})

    def test_copying_same_event_with_new_id_rejected(self):
        candidate=payload();candidate['events'].append(event('e2'));candidate['relationships']=[edge('e1','e2')]
        with self.assertRaises(ValueError):self.validate(candidate)

    def test_changed_existing_event_rejected(self):
        candidate=repaired();candidate['events'][0]['statement']='Something else'
        with self.assertRaisesRegex(ValueError,'original_events'):self.validate(candidate)

    def test_fabricated_quote_or_unknown_topic_rejected(self):
        for field in ['quote','topic']:
            candidate=repaired()
            if field=='quote':candidate['events'][1]['evidence'][0]['quote']='Not in source'
            else:candidate['events'][1]['categories']=['invented']
            with self.assertRaises(ValueError):self.validate(candidate)

    def test_unaffected_relationship_must_survive(self):
        original=payload();original['relationships'].append(edge(kind='core:associated_with',rid='r2'))
        with self.assertRaisesRegex(ValueError,'unaffected_relationships'):self.validate(repaired(),original)

    def test_change_to_noncausal_type_not_allowed_as_escape(self):
        candidate=repaired();candidate['relationships'][0]['relationship_type']='core:associated_with'
        with self.assertRaisesRegex(ValueError,'relationship_type'):self.validate(candidate)

    def test_uncertainty_and_negation_preserved(self):
        candidate=repaired();candidate['relationships'][0]['assertion_mode']='observed'
        with self.assertRaisesRegex(ValueError,'relationship_status'):self.validate(candidate)

    def test_unused_new_events_rejected(self):
        candidate=repaired();candidate['events'].append(event('e4','Unused assertion'))
        with self.assertRaisesRegex(ValueError,'unused_event'):self.validate(candidate)

    def test_repair_duplicate_ids_rejected(self):
        candidate=repaired();candidate['events'].append(copy.deepcopy(candidate['events'][1]))
        with self.assertRaisesRegex(ValueError,'duplicate_repair_ids'):self.validate(candidate)
