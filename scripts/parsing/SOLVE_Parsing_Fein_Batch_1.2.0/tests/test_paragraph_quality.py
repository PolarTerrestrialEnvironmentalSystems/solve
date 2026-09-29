import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import BASE_DIR, read_json
from schemas import Extraction, extraction_schema
from prepare_document import make_chunks
from extract_facts import split_chunk
from quality_repair import comparison_gaps, repair_quality, validate_candidate
from blablador_client import APIError

ONTOLOGY = read_json(BASE_DIR/'ontologie_fein.json')
TEXT = 'Der Abfluss stieg von 20,0 m³/s (1924-1953) auf 38,8 m³/s (1955-1993).'
BLOCK = {'block_id':'b1', 'page':1, 'text':TEXT}
CHUNK = {'chunk_id':'q1', 'pages':[1], 'blocks':[BLOCK], 'context_blocks':[]}

def parsed(complete=False):
    values=[{'parameter':'Abfluss', 'value_original':'20,0', 'unit_original':'m³/s'},
            {'parameter':'Abfluss', 'value_original':'38,8', 'unit_original':'m³/s'}]
    if complete:
        for v, period in zip(values, ['1924-1953', '1955-1993']):
            start,end=period.split('-')
            v.update(time_original=period, time_normalized={'start':start, 'end':end})
    return Extraction.model_validate({'events':[{'event_id':'e1','record_type':'Messung',
        'statement':'Der Abfluss stieg von 20,0 auf 38,8 m³/s.', 'assertion_mode':'reported',
        'polarity':'affirmed', 'ontology_concept':'core:Observation', 'values':values,
        'evidence':[{'quote':TEXT,'block_id':'b1','page':1,'field_names':['statement','values']}]}], 'relationships':[]})

class Client:
    def __init__(self, candidate=None, error=None):
        self.candidate,self.error,self.calls=candidate,error,0
        self.settings=SimpleNamespace(max_output_tokens=16000,context_tokens=1000000)
    def complete(self,*args,**kwargs):
        self.calls+=1
        if self.error: raise self.error
        return {'choices':[{'finish_reason':'stop','message':{'content':self.candidate.model_dump_json()}}]}

class ParagraphQualityTests(unittest.TestCase):
    def test_soft_target_finishes_crossing_paragraph(self):
        blocks=[{'block_id':str(i),'page':1,'text':'x'*n} for i,n in enumerate([3500,2000,900])]
        chunks=make_chunks(blocks,4000)
        self.assertEqual([sum(len(b['text']) for b in c['blocks']) for c in chunks],[5500,900])
        self.assertEqual([b for c in chunks for b in c['blocks']],blocks)

    def test_long_paragraph_preserved_even_during_recovery(self):
        blocks=[{'block_id':'long','page':1,'text':'word '*1600}]
        chunk=make_chunks(blocks,4000)[0]
        self.assertEqual(chunk['blocks'],blocks)
        self.assertEqual(split_chunk(chunk),[])

    def test_table_row_preserved(self):
        blocks=[{'block_id':'t','page':1,'kind':'table_row','text':'0 | '*1500}]
        self.assertEqual(make_chunks(blocks,4000)[0]['blocks'],blocks)

    def test_exact_target_and_empty(self):
        blocks=[{'block_id':str(i),'page':1,'text':'x'*2000} for i in range(3)]
        self.assertEqual([len(c['blocks']) for c in make_chunks(blocks,4000)],[2,1])
        self.assertEqual(make_chunks([],4000),[])

    def test_schema_constrains_ids_and_keeps_null_class(self):
        schema=extraction_schema(ONTOLOGY)['$defs']
        self.assertEqual(set(schema['Event']['properties']['categories']['items']['enum']),{t['id'] for t in ONTOLOGY['topics']})
        self.assertIn({'type':'null'},schema['Event']['properties']['ontology_concept']['anyOf'])
        self.assertIn('enum',schema['Relationship']['properties']['relationship_type'])
        a=extraction_schema(ONTOLOGY)
        a['$defs']['Event']['properties']['categories']['items']['enum'].clear()
        self.assertTrue(extraction_schema(ONTOLOGY)['$defs']['Event']['properties']['categories']['items']['enum'])

    def test_quantity_periods_roundtrip(self):
        event=parsed(True).events[0]
        self.assertEqual(event.values[0].time_normalized.start,'1924')
        self.assertEqual(event.values[1].time_normalized.start,'1955')
        self.assertEqual(comparison_gaps(parsed(True),[BLOCK]),[])

    def test_period_only_in_quote_is_missing(self):
        self.assertEqual(len(comparison_gaps(parsed(),[BLOCK])),1)

    def test_cross_block_comparison_detected(self):
        before,after=TEXT.split('auf ')
        blocks=[{**BLOCK,'text':before},{**BLOCK,'block_id':'b2','text':'auf '+after}]
        self.assertEqual(len(comparison_gaps(parsed(),blocks)),1)

    def test_reservoir_volume_not_paired_with_later_flow(self):
        text='Stauraum von 272,43 Mio. m³. Der Abfluss stieg von 2,7 m³/s (1922-1965) auf 8,0 m³/s.'
        gaps=comparison_gaps(parsed(),[{**BLOCK,'text':text}])
        self.assertEqual([(g['from'],g['to']) for g in gaps],[('2,7','8,0')])

    def test_comparison_does_not_cross_sentence(self):
        text='Ein Stauraum von 272,43 Mio. m³ ist vorhanden. Der Abfluss steigt auf 8,0 m³/s.'
        self.assertEqual(comparison_gaps(parsed(),[{**BLOCK,'text':text}]),[])

    def run_repair(self, original, client, chunk=CHUNK):
        with tempfile.TemporaryDirectory() as tmp:
            return repair_quality(client,original,[],chunk,[],ONTOLOGY,Path(tmp),'fein',Path('original.json'))

    def test_clean_output_makes_no_request(self):
        client=Client()
        original=parsed(True)
        result,audit,path=self.run_repair(original,client)
        self.assertIs(result,original)
        self.assertEqual(client.calls,0)

    def test_one_supported_supplement_accepted(self):
        original=parsed()
        candidate=original.model_copy(deep=True)
        extra=parsed(True).events[0]
        extra.event_id='e2'
        candidate.events.append(extra)
        client=Client(candidate)
        result,audit,path=self.run_repair(original,client)
        self.assertEqual(audit[0]['status'],'accepted')
        self.assertEqual(len(result.events),2)
        self.assertEqual(client.calls,1)

    def test_original_facts_cannot_be_rewritten(self):
        original=parsed()
        candidate=parsed(True)
        with self.assertRaisesRegex(ValueError,'changed_facts'):
            validate_candidate(original,candidate,ONTOLOGY,{'b1':BLOCK})

    def test_api_failure_keeps_original(self):
        original=parsed();client=Client(error=APIError(502,'unavailable'))
        result,audit,path=self.run_repair(original,client)
        self.assertIs(result,original)
        self.assertEqual(audit[0]['status'],'rejected')
        self.assertEqual(client.calls,1)

    def test_budget_skips_request(self):
        client=Client()
        with patch('extract_facts.estimated_tokens',return_value=1000000):
            _,audit,_=self.run_repair(parsed(),client)
        self.assertEqual(audit[0]['status'],'skipped')
        self.assertEqual(client.calls,0)

    def test_invalid_topic_correction_keeps_facts(self):
        original=parsed(True);original.events[0].categories=['Hydrologie']
        candidate=parsed(True);candidate.events[0].categories=['topic:hydrology']
        _,audit,_=self.run_repair(original,Client(candidate))
        self.assertEqual(audit[0]['status'],'accepted')

    def test_block_specific_quotes_fix_combined_quote(self):
        original=parsed(True)
        before,after=TEXT.split('auf ')
        blocks=[{**BLOCK,'text':before},{**BLOCK,'block_id':'b2','text':'auf '+after}]
        candidate=original.model_copy(deep=True)
        evidence=original.events[0].evidence[0]
        candidate.events[0].evidence=[evidence.model_copy(update={'quote':b['text'],'block_id':b['block_id']}) for b in blocks]
        chunk={**CHUNK,'blocks':blocks}
        _,audit,_=self.run_repair(original,Client(candidate),chunk)
        self.assertEqual(audit[0]['status'],'accepted')

    def test_invented_quote_rejected(self):
        candidate=parsed(True)
        candidate.events[0].evidence[0].quote='Invented evidence'
        with self.assertRaisesRegex(ValueError,'invalid_evidence'):
            validate_candidate(parsed(True),candidate,ONTOLOGY,{'b1':BLOCK})

if __name__=='__main__': unittest.main()
