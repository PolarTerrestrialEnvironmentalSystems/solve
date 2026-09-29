import unittest
from pydantic import ValidationError
from schemas import Event, Relationship, read_headers
from common import BASE_DIR

class CompactContractTests(unittest.TestCase):
    def event(self):
        return {'event_id':'e1','record_type':'Messung','statement':'Kein Zufluss.',
                'assertion_mode':'reported','polarity':'negated','time_status':'unknown_in_source',
                'values':[{'parameter':'Zufluss','value_original':'0','value_normalized':0}],
                'quality_assessment':{'needs_ontology_extension':False},
                'evidence':[{'quote':'Kein Zufluss.','block_id':'b1','page':1,'field_names':['statement']}]}

    def test_null_omission_preserves_normalized_meaning(self):
        compact=Event.model_validate(self.event())
        verbose=compact.model_dump(mode='json')
        self.assertEqual(compact,Event.model_validate(verbose))
        self.assertIsNone(compact.time_original)
        self.assertIsNone(compact.evidence[0].table_id)
        self.assertEqual(compact.values[0].value_normalized,0)
        self.assertIs(compact.quality_assessment['needs_ontology_extension'],False)
        self.assertEqual(compact.time_status,'unknown_in_source')
        self.assertEqual(compact.polarity,'negated')

    def test_required_fields_remain_required(self):
        for field in ['event_id','record_type','statement','assertion_mode','polarity','evidence']:
            data=self.event(); del data[field]
            with self.assertRaises(ValidationError): Event.model_validate(data)

    def test_unknown_fields_rejected(self):
        data=self.event(); data['invented_field']=42
        with self.assertRaises(ValidationError): Event.model_validate(data)

    def test_export_contract_unchanged(self):
        self.assertEqual(len(read_headers(BASE_DIR/'Event_headers.xlsx',Event)),28)
        self.assertEqual(len(read_headers(BASE_DIR/'relationship_headers.xlsx',Relationship)),30)

    def test_relationship_references_and_evidence_required(self):
        row={'relationship_id':'r1','source_event_id':'e1','target_event_id':'e2',
             'relationship_type':'core:associated_with','statement':'Zusammenhang',
             'relationship_scope':'specific_case','assertion_mode':'reported','polarity':'affirmed',
             'evidence':self.event()['evidence']}
        self.assertEqual(Relationship.model_validate(row).source_event_id,'e1')
        for field in ['source_event_id','target_event_id','evidence']:
            data=dict(row); del data[field]
            with self.assertRaises(ValidationError): Relationship.model_validate(data)
