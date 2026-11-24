"""Offline preparation checks: input integrity, raw semantics and period-specific IDs.

The optional real-data checks only read local snapshots when all are present.
No network, credentials or database connections are used.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from common import load_json, save_json
from prepare import prepare


class PreparationFixtureTests(unittest.TestCase):
    CODE = 'DELW_DEST_TEST01OW01-00'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='solve33-prepare-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshot = self.root / 'snapshot'
        self.snapshot.mkdir()
        self.events_path = self.root / 'events.json'
        self.sources_path = self.root / 'sources.json'
        self.events = [{'event_id': 'event:1', 'see': 'Fixture Lake',
                        'waterbody_identifiers': [{'id': self.CODE}],
                        'record_type': 'event', 'statement': 'Source statement',
                        'time_normalized': {'start': '2018-01-01', 'end': None},
                        'time_precision': 'year', 'human_review': 'pending',
                        'evidence': [{'document_id': 'source:1', 'locator': 'page 3'}]}]
        self.sources = [{'document_id': 'source:1', 'url': 'https://example.org/report.pdf',
                         'title': 'Fixture report'}]
        self.layers = {
            'water_bodies': [self.feature(1, euSurfaceWaterBodyCode=self.CODE,
                surfaceWaterBodyName='Fixture Lake', surfaceWaterBodyCategory='LW',
                swEcologicalStatusOrPotentialValue='3', swEcologicalAssessmentYear='2018',
                swChemicalStatusValueWithoutUPBT='Unpopulated')],
            'stations': [self.feature(2, thematicIdIdentifier='DESM_TEST1',
                thematicIdIdentifierScheme='euMonitoringSiteCode', nameText='Fixture station',
                featureOfInterestIdentifier=self.CODE,
                featureOfInterestIdentifierScheme='euSurfaceWaterBodyCode')],
            'quality': [self.feature(3, euSurfaceWaterBodyCode=self.CODE,
                QE1_1='3', QE2_1='3', QE3_3='3', QE2_2='4',
                QE1_2='Unknown', QE1_2_1='None', QE1_2_2='Unpopulated',
                QE1_2_3='Inapplicable', QE1_2_4=None)]}
        self.write_fixture()

    @staticmethod
    def feature(object_id, **props):
        return {'type': 'Feature', 'geometry': None,
                'properties': {'OBJECTID': object_id, 'countryCode': 'DE', 'cYear': 2022, **props}}

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def write_fixture(self):
        save_json(self.events_path, self.events)
        save_json(self.sources_path, self.sources)
        manifest = {'report_year': 2022, 'reporting_cycle': '2022-2027',
                    'provider': 'Offline fixture', 'lineage': 'Test data only',
                    'coverage_note': 'Not real source observations', 'selection': {'decisions': {}},
                    'requests': [], 'layers': {}}
        for name, features in self.layers.items():
            body = {'type': 'FeatureCollection', 'features': features}
            for filename in (name + '.000.geojson', name + '.geojson'):
                save_json(self.snapshot / filename, body)
            metadata_file = name + '.metadata.json'
            save_json(self.snapshot / metadata_file, {'fields': [
                {'name': key, 'alias': key, 'domain': None}
                for key in features[0]['properties']]})
            for filename in (name + '.000.geojson', metadata_file):
                manifest['requests'].append({'file': filename, 'sha256': self.sha(self.snapshot / filename),
                    'url': 'https://example.org/' + filename, 'retrieved_at': '2026-10-31T00:00:00Z'})
            manifest['layers'][name] = {'role': name, 'data_file': name + '.geojson',
                'metadata_file': metadata_file, 'features': len(features),
                'sha256': self.sha(self.snapshot / (name + '.geojson')),
                'service_url': 'https://example.org/' + name}
        save_json(self.snapshot / 'manifest.json', manifest)

    def run_prepare(self):
        return prepare(self.snapshot, self.events_path, self.sources_path)

    def test_tampered_original_response_is_rejected(self):
        path = self.snapshot / 'water_bodies.000.geojson'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'Original response checksum mismatch'):
            self.run_prepare()

    def test_rehashed_merged_data_cannot_disagree_with_original_response(self):
        path = self.snapshot / 'water_bodies.geojson'
        body = load_json(path)
        body['features'][0]['properties']['surfaceWaterBodyName'] = 'Changed lake'
        save_json(path, body)
        manifest = load_json(self.snapshot / 'manifest.json')
        manifest['layers']['water_bodies']['sha256'] = self.sha(path)
        save_json(self.snapshot / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'Merged layer differs from original response'):
            self.run_prepare()

    def test_duplicate_source_document_id_is_rejected(self):
        self.sources.append(dict(self.sources[0], url='https://example.org/other.pdf'))
        self.write_fixture()
        with self.assertRaisesRegex(ValueError, 'Duplicate source document IDs'):
            self.run_prepare()

    def test_duplicate_source_object_id_is_rejected(self):
        other = copy.deepcopy(self.layers['water_bodies'][0])
        other['properties']['euSurfaceWaterBodyCode'] = 'DELW_DEST_TEST02OW02-00'
        other['properties']['surfaceWaterBodyName'] = 'Another lake'
        self.layers['water_bodies'].append(other)
        self.write_fixture()
        with self.assertRaisesRegex(ValueError, 'Duplicate primary key in raw_record'):
            self.run_prepare()

    def test_unmatched_station_reference_is_retained_without_fk(self):
        self.layers['stations'][0]['properties']['featureOfInterestIdentifier'] = 'DELW_UNKNOWN'
        self.write_fixture()
        tables = self.run_prepare()['tables']
        self.assertEqual(len(tables['monitoring_station']), 1)
        self.assertEqual(tables['station_water_body'], [])
        self.assertIn('unresolved_station_waterbody', {r['code'] for r in tables['import_issue']})

    def test_qe_numeric_domains_are_component_specific_and_missing_strings_survive(self):
        tables = self.run_prepare()['tables']
        values = {r['parameter_code']: r for r in tables['water_body_assessment']}
        self.assertEqual(values['QE1_1']['value_label'], 'Moderate status or potential')
        self.assertEqual(values['QE2_1']['value_label'], 'Less than good status or potential')
        self.assertEqual(values['QE3_3']['value_label'], 'Less than good status or potential')
        self.assertEqual(values['QE2_2']['value_raw'], '4')
        self.assertIsNone(values['QE2_2']['value_label'])
        for key, raw in [('QE1_2', 'Unknown'), ('QE1_2_1', 'None'),
                         ('QE1_2_2', 'Unpopulated'), ('QE1_2_3', 'Inapplicable')]:
            self.assertEqual(values[key]['value_raw'], raw)
            self.assertIsNone(values[key]['value_label'])
        self.assertNotIn('QE1_2_4', values)
        self.assertEqual(values['swEcologicalStatusOrPotentialValue']['metadata']['assessment_year_original'], '2018')
        self.assertEqual(values['swEcologicalStatusOrPotentialValue']['reporting_cycle'], '2022-2027')
        self.assertEqual(tables['event'][0]['payload'], self.events[0])


class LocalRealSnapshotTests(unittest.TestCase):
    """Optional local read-only integration of actual research inputs, no downloads."""
    @classmethod
    def setUpClass(cls):
        workspace = HERE.parents[1]
        root = workspace / 'outputs' / 'wasserblick33_20261031' / 'data' / 'snapshots'
        research = workspace / 'outputs' / 'seen33_vertiefung_20261030'
        events = research / 'events.json'
        sources = research / 'quellenverzeichnis.json'
        if not all(p.exists() for p in [events, sources, *[root / str(y) / 'manifest.json' for y in (2010, 2016, 2022)]]):
            raise unittest.SkipTest('Local historical source snapshots are absent; no network access attempted.')
        cls.original_events = load_json(events)
        cls.bundles = {year: prepare(root / str(year), events, sources) for year in (2010, 2016, 2022)}

    def test_all_events_and_project_lakes_preserved_each_cycle(self):
        originals = {event['event_id']: event for event in self.original_events}
        for year, bundle in self.bundles.items():
            with self.subTest(year=year):
                tables = bundle['tables']
                self.assertEqual(len(tables['lake']), 33)
                self.assertEqual({row['event_id']: row['payload'] for row in tables['event']}, originals)
                self.assertEqual(len(tables['lake_water_body']), 33)
                self.assertEqual({row['status'] for row in tables['match_candidate']}, {'accepted'})
                self.assertEqual({link['method'] for link in tables['station_water_body']}, {'exact_reported_foreign_key'})

    def test_rappbode_components_remain_distinct_with_period_specific_reporting_units(self):
        for year, bundle in self.bundles.items():
            tables = bundle['tables']
            ids = {r['lake_id'] for r in tables['lake'] if r['name'].startswith('Rappbodetalsperre')}
            links = [r for r in tables['lake_water_body'] if r['lake_id'] in ids]
            with self.subTest(year=year):
                self.assertEqual(len(ids), 3)
                self.assertEqual(len(links), 3)
                self.assertEqual(len({r['water_body_id'] for r in links}), 3 if year == 2010 else 1)
                self.assertEqual({r['relation_type'] for r in links},
                                 {'same_water_body'} if year == 2010 else {'part_of'})

    def test_ecker_has_explicit_source_review_and_no_invented_input_identifier(self):
        for year, bundle in self.bundles.items():
            tables = bundle['tables']
            lake = next(r for r in tables['lake'] if r['name'] == 'Eckertalsperre')
            link = next(r for r in tables['lake_water_body'] if r['lake_id'] == lake['lake_id'])
            body = next(r for r in tables['water_body'] if r['water_body_id'] == link['water_body_id'])
            with self.subTest(year=year):
                self.assertEqual(lake['requested_identifiers'], [])
                self.assertEqual(body['eu_code'], 'DELW_DENI_15008')
                self.assertEqual(link['method'], 'reviewed_official_mapping')
                self.assertTrue(link['evidence']['review']['evidence'])


if __name__ == '__main__':
    unittest.main()
