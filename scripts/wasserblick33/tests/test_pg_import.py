import contextlib
import copy
from decimal import Decimal, localcontext
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pg_import
from init_env import create_env


def empty_bundle():
    return {'bundle_version': 1, 'tables': {t: [] for t in pg_import.TABLES}}


def lake_row():
    return {'lake_id': 'lake-1', 'name': 'Beispielsee', 'aliases': [], 'requested_identifiers': []}


class BundleTests(unittest.TestCase):
    def test_complete_empty_source_tables_preserved(self):
        result = pg_import.validate_bundle(empty_bundle())
        self.assertEqual(set(result), set(pg_import.TABLES))
        self.assertTrue(all(n == 0 for n in result.values()))

    def test_missing_table_fails_instead_of_dropping_a_source(self):
        bundle = empty_bundle()
        del bundle['tables']['event']
        with self.assertRaisesRegex(ValueError, 'exactly'):
            pg_import.validate_bundle(bundle)

    def test_unexpected_column_fails_instead_of_losing_original_data(self):
        bundle = empty_bundle()
        row = lake_row()
        row['unmodeled_field'] = 'must be raw payload'
        bundle['tables']['lake'] = [row]
        with self.assertRaisesRegex(ValueError, 'columns'):
            pg_import.validate_bundle(bundle)

    def test_duplicate_ids_rejected(self):
        bundle = empty_bundle()
        bundle['tables']['lake'] = [lake_row(), lake_row()]
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            pg_import.validate_bundle(bundle)

    def test_original_json_survives_and_order_does_not_change_table_hash(self):
        rows = [{'id':'b','payload':{'z':1,'a':[None, 2]}}, {'id':'a','payload':{'parameter':'Phosphor','unit':'mg/l'}}]
        self.assertEqual(pg_import.table_digest(rows, 'id'), pg_import.table_digest(list(reversed(rows)), 'id'))
        changed = copy.deepcopy(rows)
        changed[0]['payload']['z'] = 2
        self.assertNotEqual(pg_import.table_digest(rows, 'id'), pg_import.table_digest(changed, 'id'))

    def test_jsonb_expanded_maxscale_has_same_exact_decimal_hash(self):
        source = [{'id':'source', 'metadata':{'maxScale':1.7976931348623157e308}}]
        stored = [{'id':'source', 'metadata':{'maxScale':int(Decimal('1.7976931348623157e308'))}}]
        self.assertEqual(pg_import.table_digest(source, 'id'), pg_import.table_digest(stored, 'id'))

    def test_numeric_hash_does_not_round_large_integers(self):
        first = 123456789012345678901234567890123456789
        second = first + 1
        with localcontext() as context:
            context.prec = 2
            self.assertNotEqual(pg_import.numeric_canonical(first), pg_import.numeric_canonical(second))
            self.assertEqual(pg_import.numeric_canonical(Decimal('1.23456789012345678901234567890123456789e38')),
                             pg_import.numeric_canonical(first))

    def test_numeric_hash_equivalent_spelling_preserves_json_types(self):
        self.assertEqual(pg_import.numeric_canonical(1), pg_import.numeric_canonical(1.0))
        self.assertEqual(pg_import.numeric_canonical(0), pg_import.numeric_canonical(-0.0))
        self.assertNotEqual(pg_import.numeric_canonical(True), pg_import.numeric_canonical(1))
        self.assertNotEqual(pg_import.numeric_canonical('1'), pg_import.numeric_canonical(1))
        self.assertNotEqual(pg_import.numeric_canonical(1.0000000000000002), pg_import.numeric_canonical(1))

    def test_raw_bytes_hash_distinguishes_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bundle.json'
            path.write_text(json.dumps(empty_bundle()), encoding='utf8')
            _, hash1, counts1, table_hashes1 = pg_import.read_bundle(path)
            path.write_text(json.dumps(empty_bundle(), indent=2), encoding='utf8')
            _, hash2, counts2, table_hashes2 = pg_import.read_bundle(path)
            self.assertNotEqual(hash1, hash2)
            self.assertEqual(counts1, counts2)
            self.assertEqual(table_hashes1, table_hashes2)

    def test_projection_coordinates_not_labeled_as_wgs84(self):
        with self.assertRaisesRegex(ValueError, 'transform'):
            pg_import.validate_geometry({'type':'Point','coordinates':[401463,5887612]}, 'station')

    def test_wgs84_and_nullable_geometry(self):
        pg_import.validate_geometry({'type':'Point','coordinates':[12.4,51.3]}, 'station')
        pg_import.validate_geometry(None, 'station')

    def test_nonfinite_and_third_coordinate_rejected(self):
        for coordinates in [[float('nan'),51], [12,51,100], [True,51]]:
            with self.assertRaises(ValueError):
                pg_import.validate_geometry({'type':'Point','coordinates':coordinates}, 'station')

    def test_explicit_crs_must_be_resolved_before_import(self):
        with self.assertRaisesRegex(ValueError, 'transformed'):
            pg_import.validate_geometry({'type':'Point','coordinates':[12,51],'crs':{'name':'EPSG:25833'}}, 'station')

    def test_nan_json_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bundle.json'
            path.write_text('{"bundle_version":1,"tables":{},"value":NaN}', encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'Invalid JSON number'):
                pg_import.read_bundle(path)

    def test_old_schema_and_sql_injection_rejected(self):
        for name in ['water_kg_2', 'public', 'solve33_x;DROP SCHEMA public', 'solve33_'+'x'*56]:
            with self.assertRaises(ValueError):
                pg_import.validate_schema_name(name)
        self.assertEqual(pg_import.validate_schema_name('solve33_0123_abcd'), 'solve33_0123_abcd')

    def test_dangling_event_lake_reference_rejected(self):
        bundle = empty_bundle()
        bundle['tables']['event'] = [{c:None for c in pg_import.TABLES['event']['columns']}]
        bundle['tables']['event'][0].update(event_id='event-1',lake_id='unverified-lake',record_type='Ereignis',statement='Test',payload={})
        with self.assertRaisesRegex(ValueError, 'foreign key'):
            pg_import.validate_bundle(bundle)

    def test_unmatched_candidate_retained_without_invented_water_body(self):
        bundle = empty_bundle()
        bundle['tables']['lake'] = [lake_row()]
        bundle['tables']['match_candidate'] = [{
            'candidate_id':'candidate-1','lake_id':'lake-1','water_body_id':None,
            'status':'unmatched','method':'no_verified_identifier','reason':'No authoritative match','evidence':{}
        }]
        self.assertEqual(pg_import.validate_bundle(bundle)['match_candidate'], 1)
        bundle['tables']['match_candidate'][0]['status'] = 'accepted'
        with self.assertRaisesRegex(ValueError, 'real water body'):
            pg_import.validate_bundle(bundle)


class CredentialTests(unittest.TestCase):
    def test_independent_secrets_and_never_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                create_env(path)
            text = path.read_text()
            values = dict(line.split('=',1) for line in text.splitlines() if '=' in line)
            self.assertNotEqual(values['POSTGRES_PASSWORD'], values['NEO4J_PASSWORD'])
            self.assertEqual(len(values['POSTGRES_PASSWORD']), 64)
            self.assertEqual(captured.getvalue(), '')
            with self.assertRaises(FileExistsError):
                create_env(path)
            self.assertEqual(path.read_text(), text)


if __name__ == '__main__':
    unittest.main()
