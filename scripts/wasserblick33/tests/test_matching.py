import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import code_matches, select_matches, targets_from_events


class MatchingTests(unittest.TestCase):
    def lake(self, name='Barleber See I', code='MEL03OW21-00', ident='lake1'):
        return {'lake_id': ident, 'name': name, 'aliases': [], 'requested_identifiers': [{'id': code}]}

    def wb(self, name='Barleber See I', code='DELW_DEST_MEL03OW21-00', ident='wb1'):
        return {'water_body_id': ident, 'name': name, 'eu_code': code, 'raw_id': 'raw1'}

    def test_historical_full_prefix_and_state_local(self):
        self.assertTrue(code_matches('DE_LW_DEST_EL03OW07-00', 'DELW_DEST_EL03OW07-00'))
        self.assertTrue(code_matches('EL03OW07-00', 'DELW_DEST_EL03OW07-00'))
        self.assertFalse(code_matches('EL03OW07-00', 'DELW_DEBB_EL03OW07-00'))
        self.assertFalse(code_matches('EL03OW07-00', 'DERW_DEST_EL03OW07-00'))

    def test_roman_numeral_conflict_is_review(self):
        links, candidates = select_matches([self.lake()], [self.wb(name='Barleber See II')])
        self.assertEqual(links, [])
        self.assertEqual(candidates[0]['status'], 'review')

    def test_name_only_does_not_create_fk(self):
        lake = self.lake(); lake['requested_identifiers'] = []
        links, candidates = select_matches([lake], [self.wb()])
        self.assertFalse(links)
        self.assertEqual(candidates[0]['status'], 'review')

    def test_unique_correlated_id_and_name(self):
        links, candidates = select_matches([self.lake()], [self.wb()])
        self.assertEqual(len(links), 1)
        self.assertEqual(candidates[0]['status'], 'accepted')

    def test_duplicate_candidates_never_automatch(self):
        links, candidates = select_matches([self.lake()], [self.wb(), self.wb(ident='wb2')])
        self.assertFalse(links)
        self.assertEqual({c['status'] for c in candidates}, {'review'})

    def test_shared_body_has_three_separate_member_lakes(self):
        names = ['Rappbodetalsperre', 'Vorsperre Hassel', 'Vorsperre Rappbode']
        lakes = [self.lake(n, 'SAL17OW14-00', str(i)) for i, n in enumerate(names)]
        body = self.wb('TS Rappbode', 'DELW_DEST_SAL17OW14-00')
        decisions = {n: {'eu_code': body['eu_code'], 'relation_type': 'part_of', 'reviewed_by': 'test',
                        'evidence': [{'url': 'https://example.org/official', 'reason': 'fixture'}]} for n in names}
        links, _ = select_matches(lakes, [body], decisions)
        self.assertEqual(len(links), 3)
        self.assertEqual(len({l['lake_id'] for l in links}), 3)
        self.assertEqual({l['relation_type'] for l in links}, {'part_of'})

    def test_shared_mixed_identifier_forms_remain_parts(self):
        lakes = [self.lake('same', 'SAL17OW14-00', '1'), self.lake('same', 'DELW_DEST_SAL17OW14-00', '2')]
        links, _ = select_matches(lakes, [self.wb('same', 'DELW_DEST_SAL17OW14-00')])
        self.assertEqual({l['relation_type'] for l in links}, {'part_of'})

    def test_unknown_manual_decision_rejected(self):
        with self.assertRaises(ValueError):
            select_matches([self.lake()], [self.wb()], {'wrong lake': {}})

    def test_review_requires_evidence(self):
        with self.assertRaises(ValueError):
            select_matches([self.lake()], [self.wb()], {'Barleber See I': {'eu_code': self.wb()['eu_code'], 'relation_type': 'same_water_body'}})

    def test_duplicate_event_ids_fail_instead_of_drop(self):
        e = {'event_id': 'a', 'see': 'lake', 'waterbody_identifiers': []}
        with self.assertRaises(ValueError):
            targets_from_events([e, e])


if __name__ == '__main__':
    unittest.main()
