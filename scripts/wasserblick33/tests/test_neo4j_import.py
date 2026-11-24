import copy
from decimal import Decimal, localcontext
import importlib.util
import json
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'neo4j_import.py'
spec = importlib.util.spec_from_file_location('solve33_neo4j', PATH)
neo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(neo)


def sample():
    tables = {t: [] for t in neo.CONTRACT['tables']}
    def add(table, **kw):
        row = dict.fromkeys(neo.CONTRACT['tables'][table]['columns'])
        row.update(kw)
        tables[table].append(row)
    add('dataset', dataset_id='ds', reporting_cycle='2022-2027', metadata={'source': 'test'})
    add('raw_record', raw_id='raw', dataset_id='ds', layer='lakes', source_id='1', payload={'original': 999})
    add('water_body', water_body_id='wb', dataset_id='ds', eu_code='DELW_DEST_SAL17OW14-00',
        name='Rappbode', category='LW', raw_id='raw', attributes={'status': 999})
    for n, name in enumerate(['Rappbodetalsperre', 'Vorsperre Hassel', 'Vorsperre Rappbode']):
        add('lake', lake_id='lake' + str(n), name=name, aliases=[], requested_identifiers=[])
        add('lake_water_body', link_id='link' + str(n), lake_id='lake' + str(n), water_body_id='wb',
            relation_type='part_of', method='reviewed', evidence={'scope': 'component'})
    add('source_document', source_id='src', url='https://example.org/report.pdf', title='Report', metadata={})
    add('event', event_id='event', lake_id='lake1', record_type='Messung', statement='Measurement',
        time_start='2018', time_end='2018', time_precision='Jahr',
        payload={'values': [{'parameter': 'P', 'value': 0.02, 'unit': 'mg/l'}], 'missing': None})
    for n, locator in enumerate(['PDF-Seite 1', 'PDF-Seite 2']):
        add('event_evidence', evidence_id='ev' + str(n), event_id='event', source_id='src',
            locator=locator, payload={'paraphrase': 'test'})
    add('match_candidate', candidate_id='cand', lake_id='lake1', water_body_id='wb',
        status='review', method='name_only', reason='uncertain', evidence={})
    return tables


def actual(plan):
    nodes = [{'key': n['key'], 'labels': ['Solve33', n['label'], *n.get('additional_labels', [])], 'properties': copy.deepcopy(n['properties'])}
             for n in plan['nodes']]
    edges = copy.deepcopy(plan['edges'])
    return nodes, edges


class GraphTests(unittest.TestCase):
    def test_shared_waterbody_keeps_three_lakes_and_local_event(self):
        plan = neo.build_graph(sample(), 'test')
        self.assertEqual(plan['node_counts']['Lake33'], 3)
        self.assertEqual(plan['node_counts']['WaterBody33'], 1)
        self.assertEqual(plan['edge_counts']['PART_OF'], 3)
        about = [r for r in plan['edges'] if r['type'] == 'ABOUT_LAKE']
        self.assertEqual(len(about), 1)
        self.assertEqual(about[0]['target'], neo.node_key('test', 'lake', 'lake1'))

    def test_duplicate_ids_and_dangling_fk_fail(self):
        t = sample()
        t['lake'].append(copy.deepcopy(t['lake'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            neo.build_graph(t, 'test')
        t = sample()
        t['event'][0]['lake_id'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'Dangling'):
            neo.build_graph(t, 'test')

    def test_original_rows_nulls_and_values_preserved(self):
        tables = sample()
        plan = neo.build_graph(tables, 'test')
        event = next(n for n in plan['nodes'] if n['label'] == 'Event33')
        self.assertEqual(json.loads(event['properties']['row_json']), tables['event'][0])
        raw = next(n for n in plan['nodes'] if n['label'] == 'RawRecord33')
        self.assertEqual(json.loads(raw['properties']['payload_json'])['original'], 999)
        evidence = [r for r in plan['edges'] if r['type'] == 'SUPPORTED_BY']
        self.assertEqual(len(evidence), 2)
        self.assertEqual({r['properties']['locator'] for r in evidence}, {'PDF-Seite 1', 'PDF-Seite 2'})

    def test_candidate_not_accepted_link(self):
        tables = sample()
        tables['lake_water_body'] = []
        plan = neo.build_graph(tables, 'test')
        self.assertEqual(plan['edge_counts']['CANDIDATE_FOR'], 1)
        self.assertNotIn('REPRESENTS', plan['edge_counts'])
        self.assertNotIn('PART_OF', plan['edge_counts'])

    def test_namespace_isolation_and_order_independent_fingerprint(self):
        tables = sample()
        plan = neo.build_graph(tables, 'one')
        plan2 = neo.build_graph(tables, 'two')
        self.assertFalse({n['key'] for n in plan['nodes']} & {n['key'] for n in plan2['nodes']})
        flipped = {k: list(reversed(v)) for k, v in tables.items()}
        self.assertEqual(neo.snapshot_id(tables), neo.snapshot_id(flipped))
        self.assertEqual(plan, neo.build_graph(flipped, 'one'))

    def test_full_property_and_endpoint_verification(self):
        plan = neo.build_graph(sample(), 'test')
        nodes, edges = actual(plan)
        neo.compare_actual_graph(plan, nodes, edges)
        nodes[0]['properties']['injected'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'properties'):
            neo.compare_actual_graph(plan, nodes, edges)
        nodes, edges = actual(plan)
        edges[0]['target'] = edges[0]['source']
        with self.assertRaisesRegex(ValueError, 'relationship differs'):
            neo.compare_actual_graph(plan, nodes, edges)

    def test_source_table_shape_and_run_identifier(self):
        tables = sample()
        del tables['monitoring_station']
        with self.assertRaisesRegex(ValueError, 'Table set'):
            neo.build_graph(tables, 'test')
        with self.assertRaisesRegex(ValueError, 'run_id'):
            neo.build_graph(sample(), 'untrusted`label')
        t = sample()
        t['lake_water_body'][0]['relation_type'] = 'fuzzy_match'
        with self.assertRaisesRegex(ValueError, 'accepted'):
            neo.build_graph(t, 'test')

    def test_bundle_cannot_be_applied(self):
        with self.assertRaises(SystemExit) as e:
            neo.main(['--bundle', 'unused.json', '--apply'])
        self.assertEqual(e.exception.code, 2)

    def test_only_dedicated_postgres_schema_allowed(self):
        self.assertEqual(neo.validate_schema_name('solve33_123abc'), 'solve33_123abc')
        for name in ['public', 'water_kg_2', 'solve33_x; DROP SCHEMA public', 'solve33_']:
            with self.assertRaises(ValueError):
                neo.validate_schema_name(name)

    def test_existing_snapshot_is_checked_before_activation(self):
        plan = neo.build_graph(sample(), 'test')

        class Result(list):
            def consume(self):
                return None
            def single(self):
                return self[0] if self else None

        class Tx:
            def __init__(self, incomplete=False):
                self.queries = []
                self.incomplete = incomplete
            def run(self, query, **params):
                self.queries.append(query)
                if 'RETURN r.graph_sha256 AS hash' in query:
                    return Result([{'hash': plan['graph_sha256']}])
                if 'labels(n)' in query:
                    nodes, _ = actual(plan)
                    return Result(nodes[:-1] if self.incomplete else nodes)
                if 'type(r) AS type' in query:
                    return Result(actual(plan)[1])
                return Result()

        tx = Tx()
        self.assertEqual(neo.import_transaction(tx, plan)['status'], 'already_present_verified')
        self.assertTrue(any('active_run_id' in q for q in tx.queries))
        self.assertFalse(any('CREATE (n:' in q for q in tx.queries))
        tx = Tx(incomplete=True)
        with self.assertRaisesRegex(ValueError, 'counts differ'):
            neo.import_transaction(tx, plan)
        self.assertFalse(any('active_run_id' in q for q in tx.queries))

    def test_same_code_has_parallel_versions_with_separate_periods(self):
        tables = sample()
        tables['dataset'][0].update(reporting_cycle='2009-2015', retrieved_at='2026-10-31T10:00:00Z', metadata={'report_year': 2010})
        tables['water_body'][0]['attributes']['cYear'] = 2010
        ds = copy.deepcopy(tables['dataset'][0])
        ds.update(dataset_id='ds2022', reporting_cycle='2022-2027', retrieved_at='2026-10-31T11:00:00Z', metadata={'report_year': 2022})
        tables['dataset'].append(ds)
        raw = copy.deepcopy(tables['raw_record'][0])
        raw.update(raw_id='raw2022', dataset_id='ds2022')
        tables['raw_record'].append(raw)
        body = copy.deepcopy(tables['water_body'][0])
        body.update(water_body_id='wb2022', dataset_id='ds2022', raw_id='raw2022', attributes={'cYear': 2022, 'status': 3})
        tables['water_body'].append(body)
        plan = neo.build_graph(tables, 'parallel')
        self.assertEqual(plan['node_counts']['ReportingUnit33'], 1)
        self.assertEqual(plan['node_counts']['WaterBody33'], 2)
        self.assertEqual(plan['additional_label_counts']['WaterBodyVersion33'], 2)
        self.assertEqual(plan['edge_counts']['HAS_VERSION'], 2)
        versions = [n for n in plan['nodes'] if n['label'] == 'WaterBody33']
        self.assertEqual({n['properties']['report_year'] for n in versions}, {2010, 2022})
        self.assertEqual({n['properties']['period_start_year'] for n in versions}, {2009, 2022})
        self.assertEqual({n['properties']['retrieved_at'] for n in versions}, {'2026-10-31T10:00:00Z', '2026-10-31T11:00:00Z'})
        for n in versions:
            self.assertEqual(n['properties']['valid_time_kind'], 'reporting_period_not_observation_date')
            self.assertNotIn('valid_from_date', n['properties'])
        self.assertEqual({json.loads(n['properties']['row_json'])['attributes']['status'] for n in versions}, {999, 3})
        neo.compare_actual_graph(plan, *actual(plan))

    def test_changed_predam_codes_are_not_merged_with_current_main_code(self):
        tables = sample()
        for number in [13, 18]:
            row = copy.deepcopy(tables['water_body'][0])
            row.update(water_body_id='old' + str(number), eu_code=f'DEST_SAL17OW{number}-00', name='historical pre-dam')
            tables['water_body'].append(row)
        plan = neo.build_graph(tables, 'history')
        identities = [n for n in plan['nodes'] if n['label'] == 'ReportingUnit33']
        self.assertEqual(len(identities), 3)
        self.assertEqual(len({n['properties']['identity_id'] for n in identities}), 3)
        self.assertEqual(plan['node_counts']['Lake33'], 3)
        self.assertFalse(any(e['type'] in {'SAME_AS', 'NEXT_VERSION'} for e in plan['edges']))
        other = neo.build_graph(tables, 'second_import_run')
        self.assertEqual({n['properties']['identity_id'] for n in identities},
                         {n['properties']['identity_id'] for n in other['nodes'] if n['label'] == 'ReportingUnit33'})

    def test_stations_group_by_exact_official_code_not_name(self):
        tables = sample()
        cols = neo.CONTRACT['tables']['monitoring_station']['columns']
        for sid, code in [('station1', 'DESM_1'), ('station2', 'DESM_1'), ('station3', 'DESM_2'), ('station4', None)]:
            row = dict.fromkeys(cols)
            row.update(station_id=sid, dataset_id='ds', raw_id='raw', eu_code=code,
                       name='Identical display name', attributes={})
            tables['monitoring_station'].append(row)
        plan = neo.build_graph(tables, 'stations')
        self.assertEqual(plan['node_counts']['MonitoringStationIdentity33'], 2)
        self.assertEqual(plan['additional_label_counts']['MonitoringStationVersion33'], 4)
        station_versions = [n for n in plan['nodes'] if n['label'] == 'MonitoringStation33']
        no_code = next(n for n in station_versions if n['properties']['station_id'] == 'station4')
        self.assertFalse(any(e['type'] == 'HAS_VERSION' and e['target'] == no_code['key'] for e in plan['edges']))

    def test_unknown_period_not_invented_and_conflicts_rejected(self):
        ds = sample()['dataset'][0]
        ds['reporting_cycle'] = 'historical, exact period unknown'
        props = neo.version_properties({'attributes': {}}, ds)
        self.assertNotIn('report_year', props)
        self.assertNotIn('period_start_year', props)
        self.assertNotIn('period_end_year', props)
        ds['metadata'] = {'report_year': 2010}
        with self.assertRaisesRegex(ValueError, 'disagree'):
            neo.version_properties({'attributes': {'cYear': 2022}}, ds)

    def test_jsonb_expanded_integer_matches_original_large_float_exactly(self):
        before = sample()
        value = 1.7976931348623157e308
        before['dataset'][0]['metadata']['license_metadata'] = {'maxScale': value}
        after = copy.deepcopy(before)
        after['dataset'][0]['metadata']['license_metadata']['maxScale'] = int(Decimal(str(value)))
        with localcontext() as context:
            context.prec = 2  # Hashing must not accidentally round using context precision.
            self.assertEqual(neo.snapshot_id(before), neo.snapshot_id(after))
            plan_before = neo.build_graph(before, neo.snapshot_id(before))
            plan_after = neo.build_graph(after, neo.snapshot_id(after))
        self.assertEqual(plan_before, plan_after)
        self.assertEqual(plan_before['graph_sha256'], plan_after['graph_sha256'])
        ds_before = next(n for n in plan_before['nodes'] if n['label'] == 'Dataset33')
        ds_after = next(n for n in plan_after['nodes'] if n['label'] == 'Dataset33')
        self.assertEqual(ds_before['properties']['row_json'], ds_after['properties']['row_json'])
        # Arbitrary large integers retain all digits; no blanket float conversion.
        huge = 10 ** 100 + 123456789
        encoded = neo.canonical({'exact': huge})
        recovered = json.loads(encoded, parse_float=Decimal)['exact']
        self.assertEqual(recovered, Decimal(huge))

    def test_large_scalar_values_are_lossless_json_not_overflowing_neo4j_ints(self):
        value = 1.7976931348623157e308
        expanded = int(Decimal(str(value)))
        before = neo.row_properties({'value_raw': value, 'flag': True})
        after = neo.row_properties({'value_raw': expanded, 'flag': True})
        self.assertEqual(before, after)
        self.assertNotIn('value_raw', before)
        self.assertIn('value_raw_json', before)
        self.assertIs(before['flag'], True)


if __name__ == '__main__':
    unittest.main()
