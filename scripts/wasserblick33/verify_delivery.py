"""Verify shipped data, source responses, event preservation and temporal graph."""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
from pathlib import Path
from common import load_json, save_json
from pg_import import read_bundle


def verify(data):
    bundle, bundle_hash, counts, _ = read_bundle(data / 'prepared/bundle.json')
    tables = bundle['tables']
    original = load_json(data / 'inputs/events.json')
    original_by_id = {r['event_id']: r for r in original}
    assert len(original_by_id) == len(original) == len(tables['event']) == 490
    for row in tables['event']:
        assert row['payload'] == original_by_id[row['event_id']], row['event_id']
    assert counts['lake'] == 33 and counts['lake_water_body'] == 99
    assert counts['water_body'] == 95 and counts['monitoring_station'] == 246
    assert {m['status'] for m in tables['match_candidate']} == {'accepted'}
    checked = 0
    for snapshot in sorted((data / 'snapshots').iterdir()):
        if not snapshot.is_dir():
            continue
        manifest = load_json(snapshot / 'manifest.json')
        for request in manifest['requests']:
            path = (snapshot / request['file']).resolve()
            assert path.parent == snapshot.resolve()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == request['sha256'], str(path)
            checked += 1
    geometry_count = 0
    from shapely.geometry import shape
    for table in ('water_body', 'monitoring_station'):
        for row in tables[table]:
            if row['geometry'] is not None:
                geometry = shape(row['geometry'])
                assert geometry.is_valid and not geometry.is_empty, row
                geometry_count += 1
    graph = load_json(data / 'prepared/graph_plan.json')
    assert graph['table_counts'] == counts
    assert graph['additional_label_counts']['WaterBodyVersion33'] == 95
    assert graph['additional_label_counts']['MonitoringStationVersion33'] == 246
    assert graph['edge_counts']['HAS_VERSION'] == 341
    assert graph['node_counts']['Lake33'] == 33
    assert graph['node_counts']['ReportingUnit33'] == 33
    assert graph['node_counts']['MonitoringStationIdentity33'] == 222
    assert len({n['key'] for n in graph['nodes']}) == len(graph['nodes'])
    node_index = {n['key']: n for n in graph['nodes']}
    assert all(e['source'] in node_index and e['target'] in node_index for e in graph['edges'])
    for edge in graph['edges']:
        if node_index[edge['source']]['label'] == 'Event33':
            assert node_index[edge['target']]['label'] in ('Lake33', 'SourceDocument33')
    comparison_path = data / 'validation/postgres_to_graph_comparison.json'
    comparison = load_json(comparison_path) if comparison_path.exists() else None
    if comparison is not None:
        assert comparison['status'] == 'passed'
        assert comparison['graph_sha256'] == graph['graph_sha256']
        assert comparison['postgres']['bundle_sha256'] == bundle_hash
    result = {'status': 'passed', 'bundle_sha256': bundle_hash, 'tables': counts,
              'original_event_payloads_exactly_preserved': 490,
              'source_response_hashes_verified': checked,
              'valid_nonempty_WGS84_geometries': geometry_count,
              'graph_nodes': len(graph['nodes']), 'graph_relationships': len(graph['edges']),
              'graph_sha256': graph['graph_sha256'],
              'graph_source': 'validated PostgreSQL projection compared to offline plan' if comparison else 'offline plan; no live comparison report',
              'human_review_of_event_claims': 'open',
              'live_neo4j_and_docker_execution': 'not performed in this environment',
              'historical_2004_baseline': 'not acquired; explicit coverage gap',
              'station_measurement_time_series': 'not provided by these source services'}
    save_json(data / 'validation/delivery_checks.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path(__file__).with_name('data'))
    args = parser.parse_args()
    print(json.dumps(verify(args.data), ensure_ascii=False, indent=2))
