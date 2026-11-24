"""Preserve source rows, normalize entities, and build auditable, conservative links."""
from __future__ import annotations
import argparse
import collections
import csv
import hashlib
from pathlib import Path
from common import load_json, save_json, stable_id, canonical_json, targets_from_events, select_matches

HERE = Path(__file__).resolve().parent
ASSESSMENTS = {
    'swEcologicalStatusOrPotentialValue': ('swEcologicalAssessmentYear', 'swEcologicalAssessmentConfidence'),
    'swChemicalStatusValue': ('swChemicalAssessmentYear', 'swChemicalAssessmentConfidence'),
    'swChemicalStatusValueWithoutUPBT': ('swChemicalAssessmentYear', 'swChemicalAssessmentConfidence')}


def prepare(snapshot, events_path, sources_path, selection_path=None, codebook_path=None):
    manifest = load_json(snapshot / 'manifest.json')
    selection = load_json(selection_path) if selection_path else manifest['selection']
    default_book = HERE / ('codebook.json' if manifest['report_year'] == 2022 else f"codebook_{manifest['report_year']}.json")
    codebook_path = codebook_path or (default_book if default_book.exists() else None)
    codebook = load_json(codebook_path) if codebook_path else {}
    events = load_json(events_path)
    source_rows = load_json(sources_path)
    source_docs = {r['document_id']: r for r in source_rows}
    if len(source_docs) != len(source_rows):
        raise ValueError('Duplicate source document IDs')
    for request in manifest['requests']:
        path = (snapshot / request['file']).resolve()
        if path.parent != snapshot.resolve():
            raise ValueError('Snapshot manifest path must be a direct child file')
        if hashlib.sha256(path.read_bytes()).hexdigest() != request['sha256']:
            raise ValueError('Original response checksum mismatch: ' + request['file'])
    contract = load_json(HERE / 'CONTRACT.json')
    tables = {name: [] for name in contract['tables']}

    def add(table, **values):
        row = {k: values.get(k) for k in contract['tables'][table]['columns']}
        tables[table].append(row)
        return row

    def issue(code, entity_type, entity_id, message, details=None, severity='warning'):
        add('import_issue', issue_id=stable_id('issue', code, entity_type, entity_id), severity=severity,
            entity_type=entity_type, entity_id=entity_id, code=code, message=message, details=details or {})

    by_code = {}
    for layer in manifest['layers']:
        metadata = manifest['layers'][layer]
        role = metadata.get('role', layer)
        path = snapshot / metadata['data_file']
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != metadata['sha256']:
            raise ValueError('Snapshot checksum mismatch: ' + layer)
        features = load_json(path)['features']
        if len(features) != metadata['features']:
            raise ValueError('Snapshot count mismatch: ' + layer)
        source_features = []
        for request in manifest['requests']:
            if request['file'].startswith(layer + '.') and request['file'].endswith('.geojson'):
                source_features.extend(load_json(snapshot / request['file'])['features'])
        sort_features = lambda rows: sorted(rows, key=lambda r: str(r['properties']['OBJECTID']))
        if canonical_json(sort_features(source_features)) != canonical_json(sort_features(features)):
            raise ValueError('Merged layer differs from original response pages: ' + layer)
        dataset_id = stable_id('dataset', layer, metadata['service_url'], actual_hash)
        requests = [r for r in manifest['requests'] if r['file'].startswith(layer + '.')]
        add('dataset', dataset_id=dataset_id, reporting_cycle=manifest['reporting_cycle'],
            source_name=manifest['provider'], source_url=metadata['service_url'],
            retrieved_at=requests[-1]['retrieved_at'], sha256=actual_hash,
            metadata={'requests': requests, 'layer': metadata, 'lineage': manifest['lineage'],
                      'report_year': manifest['report_year'],
                      'field_definitions': load_json(snapshot / metadata['metadata_file']),
                      'geometry_note': manifest.get('geometry_note'),
                      'station_metadata_note': manifest.get('station_metadata_note'),
                      'license_metadata': load_json(snapshot / (layer + '.service_item.json')) if (snapshot / (layer + '.service_item.json')).exists() else None,
                      'codebook': codebook, 'coverage_note': manifest['coverage_note']})
        seen = set()
        for feature in features:
            props = feature['properties']
            if props.get('countryCode') != 'DE' or props.get('cYear') != manifest['report_year']:
                raise ValueError('Unexpected reporting country/year')
            raw_id = stable_id('raw', dataset_id, props['OBJECTID'])
            add('raw_record', raw_id=raw_id, dataset_id=dataset_id, layer=layer,
                source_id=str(props['OBJECTID']), payload=feature)
            if role == 'water_bodies':
                code = props['euSurfaceWaterBodyCode']
                if code in seen or code in by_code:
                    raise ValueError('Multiple features for one waterbody require explicit segment reconciliation: ' + code)
                seen.add(code)
                if props['surfaceWaterBodyCategory'] not in ('LW', 'RW'):
                    raise ValueError('Unexpected category for selected historical waterbody')
                wid = stable_id('wb', dataset_id, code)
                by_code[code] = wid
                add('water_body', water_body_id=wid, dataset_id=dataset_id, eu_code=code,
                    name=props['surfaceWaterBodyName'], category=props['surfaceWaterBodyCategory'],
                    geometry=feature.get('geometry'), raw_id=raw_id, attributes=props)
                add('water_body_identifier', identifier_id=stable_id('identifier', wid, code),
                    water_body_id=wid, scheme='euSurfaceWaterBodyCode', value=code,
                    provenance={'raw_id': raw_id, 'field': 'euSurfaceWaterBodyCode'})
                for parameter, (year_field, confidence_field) in ASSESSMENTS.items():
                    value = props.get(parameter)
                    if value is None:
                        continue
                    domain = codebook.get('domains', {}).get(parameter, {})
                    label = domain.get('values', {}).get(str(value))
                    add('water_body_assessment', assessment_id=stable_id('assessment', wid, parameter),
                        water_body_id=wid, parameter_code=parameter, value_raw=value,
                        value_label=label, unit=None, reporting_cycle=manifest['reporting_cycle'], raw_id=raw_id,
                        metadata={'assessment_year_original': props.get(year_field),
                                  'confidence_original': props.get(confidence_field),
                                  'natural_or_modified': props.get('naturalAWBHMWB'),
                                  'definition': domain, 'scope': 'WRRL waterbody, not automatically each project sub-lake'})
                    if label is None:
                        issue('assessment_code_uninterpreted', 'water_body', wid + ':' + parameter,
                              'Raw assessment value preserved; no verified label for this parameter/code.', {'parameter': parameter, 'value': value})
            elif role == 'stations':
                code = props['thematicIdIdentifier']
                if code in seen:
                    raise ValueError('Duplicate station identifier requires reconciliation: ' + code)
                seen.add(code)
                if props['thematicIdIdentifierScheme'] != 'euMonitoringSiteCode':
                    raise ValueError('Unexpected station identifier scheme')
                sid = stable_id('station', dataset_id, code)
                add('monitoring_station', station_id=sid, dataset_id=dataset_id, eu_code=code,
                    name=props.get('nameText') or props.get('nameTextInternational'),
                    geometry=feature.get('geometry'), raw_id=raw_id, attributes=props)
                ref = props.get('featureOfInterestIdentifier')
                if props.get('featureOfInterestIdentifierScheme') == 'euSurfaceWaterBodyCode' and ref in by_code:
                    add('station_water_body', link_id=stable_id('station_wb', sid, by_code[ref]),
                        station_id=sid, water_body_id=by_code[ref], source_code=ref,
                        method='exact_reported_foreign_key', raw_id=raw_id)
                else:
                    issue('unresolved_station_waterbody', 'monitoring_station', sid,
                          'Original station reference retained; no accepted waterbody FK.', {'source_code': ref})
                revision = str(props.get('inspireIdVersionId') or props.get('beginLifespanVersion') or '')
                if revision[:4].isdigit() and int(revision[:4]) > manifest['report_year']:
                    issue('station_metadata_revised_after_report_year', 'monitoring_station', sid,
                          'Station metadata has a later revision than the service reporting year; do not treat it as an original-era station inventory or sampling date.',
                          {'revision': revision, 'report_year': manifest['report_year']}, severity='info')
            elif role == 'quality':
                code = props['euSurfaceWaterBodyCode']
                if code not in by_code:
                    issue('unresolved_quality_waterbody', 'raw_record', raw_id,
                          'Quality-element record retained without fabricated waterbody FK.', {'source_code': code})
                    continue
                field_info = {f['name']: f for f in load_json(snapshot / metadata['metadata_file']).get('fields', [])}
                for parameter, value in props.items():
                    if not (parameter.startswith('QE') or parameter == 'qeStatusOrPotentialValue') or value is None:
                        continue
                    # Per-component codes are retained exactly; the overall legend
                    # must not be generalized to all biological/chemical elements.
                    domain = codebook.get('domains', {}).get(parameter, {})
                    add('water_body_assessment', assessment_id=stable_id('quality', raw_id, parameter),
                        water_body_id=by_code[code], parameter_code=parameter, value_raw=value,
                        value_label=domain.get('values', {}).get(str(value)), unit=None,
                        reporting_cycle=manifest['reporting_cycle'], raw_id=raw_id,
                        metadata={'parameter_alias': field_info.get(parameter, {}).get('alias'),
                                  'definition': domain, 'scope': 'reported quality element, not an individual measurement',
                                  'missing_marker_original': value if str(value) in ('None', 'Unpopulated', 'Inapplicable', 'Unknown') else None})
            else:
                raise ValueError('Unsupported layer role: ' + role)
    lakes = targets_from_events(events)
    for lake in lakes:
        add('lake', **lake)
    links, candidates = select_matches(lakes, tables['water_body'], selection['decisions'])
    for candidate in candidates:
        candidate['evidence']['reporting_cycle'] = manifest['reporting_cycle']
        if candidate['water_body_id'] is None:
            candidate['candidate_id'] = stable_id('unmatched', candidate['lake_id'], manifest['reporting_cycle'])
    tables['lake_water_body'] = links
    tables['match_candidate'] = candidates
    lake_ids = {r['name']: r['lake_id'] for r in lakes}
    used_sources = set()
    for event in events:
        eid = event['event_id']
        original_time = event.get('time_normalized') or {}
        add('event', event_id=eid, lake_id=lake_ids[event['see']], record_type=event['record_type'],
            statement=event['statement'], time_start=original_time.get('start'), time_end=original_time.get('end'),
            time_precision=event.get('time_precision'), payload=event)
        for i, evidence in enumerate(event['evidence']):
            source_id = evidence['document_id']
            if source_id not in source_docs:
                raise ValueError('Missing source document: ' + source_id)
            used_sources.add(source_id)
            add('event_evidence', evidence_id=stable_id('evidence', eid, i, evidence), event_id=eid,
                source_id=source_id, locator=evidence['locator'], payload=evidence)
    for sid in sorted(used_sources):
        source = source_docs[sid]
        add('source_document', source_id=sid, url=source['url'], title=source.get('title'), metadata=source)
    station_counts = collections.Counter(r['water_body_id'] for r in tables['station_water_body'])
    quality_body_ids = {r['water_body_id'] for r in tables['water_body_assessment'] if r['parameter_code'].startswith('QE')}
    for wb in tables['water_body']:
        if not station_counts[wb['water_body_id']]:
            issue('no_station_in_selected_report', 'water_body', wb['water_body_id'],
                  'No station in this selected reporting layer. Does not establish absence of monitoring.', {'eu_code': wb['eu_code']})
        if any(v.get('role') == 'quality' for v in manifest['layers'].values()) and wb['water_body_id'] not in quality_body_ids:
            issue('no_quality_elements_in_selected_report', 'water_body', wb['water_body_id'],
                  'No per-component quality record returned for this reporting body; no zero/default status substituted.', {'eu_code': wb['eu_code']})
    issue('no_measurement_time_series', 'dataset', 'WISE_WFD' + str(manifest['report_year']),
          'This snapshot contains geometry, reported status and monitoring-site metadata, not station observations/time series.', severity='info')
    issue('event_human_review_open', 'event_collection', 'events490',
          'Lake identity matching does not validate event statements. Original human-review status preserved.', severity='info')
    issue('recipient_copy_access', 'dataset', 'WISE_WFD' + str(manifest['report_year']),
          'Retrieved from official EEA recipient publication. Direct BfG download returned HTTP403; not silently labelled a BfG download.', severity='info')
    for table, rows in tables.items():
        pk = contract['tables'][table]['pk']
        if len(rows) != len({r[pk] for r in rows}):
            raise ValueError('Duplicate primary key in ' + table)
    return {'bundle_version': 1, 'tables': tables, 'provenance': {
        'snapshot_manifest': manifest, 'event_input_sha256': hashlib.sha256(events_path.read_bytes()).hexdigest(),
        'event_source_register_sha256': hashlib.sha256(sources_path.read_bytes()).hexdigest(),
        'selection': selection, 'event_count': len(events), 'review_note': 'No event-to-station/measurement match inferred.'}}


def write_review(bundle, output):
    tables = bundle['tables']
    bodies = {r['water_body_id']: r for r in tables['water_body']}
    lakes = {r['lake_id']: r for r in tables['lake']}
    evcounts = collections.Counter(e['lake_id'] for e in tables['event'])
    stcounts = collections.Counter(e['water_body_id'] for e in tables['station_water_body'])
    relations = {(r['lake_id'], r['water_body_id']): r['relation_type'] for r in tables['lake_water_body']}
    datasets = {r['dataset_id']: r for r in tables['dataset']}
    columns = ['See', 'lake_id', 'Berichtszyklus', 'WRRL_ID', 'WRRL_Name', 'Status', 'Beziehung', 'Events', 'Gemeldete_Messstellen', 'Methode', 'Begruendung', 'Belege']
    with (output / 'Zuordnung_33_Seen.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f, delimiter=';', quoting=csv.QUOTE_ALL)
        writer.writerow(columns)
        for match in tables['match_candidate']:
            lid, wid = match['lake_id'], match['water_body_id']
            wb = bodies.get(wid, {})
            cycle = datasets[wb['dataset_id']]['reporting_cycle'] if wb else match['evidence'].get('reporting_cycle')
            writer.writerow([lakes[lid]['name'], lid, cycle, wb.get('eu_code'), wb.get('name'), match['status'],
                relations.get((lid, wid), ''), evcounts[lid], stcounts[wid], match['method'], match['reason'], canonical_json(match['evidence'])])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, type=Path, action='append', help='Repeat for multiple reporting cycles.')
    parser.add_argument('--events', required=True, type=Path)
    parser.add_argument('--sources', required=True, type=Path)
    parser.add_argument('--selection', type=Path, help='Override only with one snapshot; otherwise use each stored selection.')
    parser.add_argument('--codebook', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('Output must be new or empty to preserve previous bundles.')
    if args.selection and len(args.snapshot) != 1:
        raise ValueError('A selection override is only valid for a single reporting cycle.')
    snapshots = sorted(args.snapshot, key=lambda p: load_json(p / 'manifest.json')['report_year'])
    bundles = [prepare(snapshot, args.events, args.sources, args.selection, args.codebook) for snapshot in snapshots]
    bundle = bundles[0]
    contract = load_json(HERE / 'CONTRACT.json')
    bundle['provenance'] = {'snapshots': [b['provenance'] for b in bundles]}
    for extra in bundles[1:]:
        for table, rows in extra['tables'].items():
            pk = contract['tables'][table]['pk']
            existing = {r[pk]: r for r in bundle['tables'][table]}
            for row in rows:
                if row[pk] in existing:
                    if row != existing[row[pk]]:
                        raise ValueError('Conflicting row while joining cycles: ' + table + '/' + row[pk])
                else:
                    bundle['tables'][table].append(row)
                    existing[row[pk]] = row
    args.output.mkdir(parents=True, exist_ok=True)
    save_json(args.output / 'bundle.json', bundle)
    write_review(bundle, args.output)
    summary = {table: len(rows) for table, rows in bundle['tables'].items()}
    save_json(args.output / 'counts.json', summary)
    print(canonical_json(summary))


if __name__ == '__main__':
    main()
