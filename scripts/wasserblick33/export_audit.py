"""Human-readable joins and coverage reports; no new identity inference."""
from __future__ import annotations
import argparse
import collections
import csv
from pathlib import Path
from common import load_json, save_json


def export(bundle_path, output):
    bundle = load_json(bundle_path)
    t = bundle['tables']
    output.mkdir(parents=True, exist_ok=True)
    datasets = {r['dataset_id']: r for r in t['dataset']}
    bodies = {r['water_body_id']: r for r in t['water_body']}
    lakes = {r['lake_id']: r for r in t['lake']}
    stations = {r['station_id']: r for r in t['monitoring_station']}
    lake_links = collections.defaultdict(list)
    for link in t['lake_water_body']:
        lake_links[link['lake_id']].append(link)
    counts = {}
    for ds in t['dataset']:
        cycle = ds['reporting_cycle']
        counts.setdefault(cycle, {'water_bodies': 0, 'stations': 0, 'assessments': 0})
        counts[cycle]['water_bodies'] += sum(w['dataset_id'] == ds['dataset_id'] for w in t['water_body'])
        counts[cycle]['stations'] += sum(w['dataset_id'] == ds['dataset_id'] for w in t['monitoring_station'])
    for a in t['water_body_assessment']:
        counts[a['reporting_cycle']]['assessments'] += 1
    cycles = sorted(counts)

    def write(name, header, rows):
        with (output / name).open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream, delimiter=';', quoting=csv.QUOTE_ALL)
            writer.writerow(header)
            writer.writerows(rows)

    rows = []
    for event in t['event']:
        mapped = {}
        for link in lake_links[event['lake_id']]:
            wb = bodies[link['water_body_id']]
            mapped[datasets[wb['dataset_id']]['reporting_cycle']] = wb['eu_code'] + ' (' + link['relation_type'] + ')'
        p = event['payload']
        rows.append([event['event_id'], lakes[event['lake_id']]['name'], event['record_type'], event['statement'],
                     p.get('time_original'), p.get('quellen_url'), p.get('fundstelle'),
                     *[mapped.get(cycle, '') for cycle in cycles], p.get('quality_assessment', {}).get('review_status')])
    write('Event_Zuordnung.csv', ['event_id', 'See', 'Eintragsart', 'Aussage', 'Event_Zeit_original', 'Quelle',
          'Fundstelle', *['WRRL_Kontext_' + c for c in cycles], 'Event_Human_Review'], rows)
    station_rows = []
    for link in t['station_water_body']:
        station, wb = stations[link['station_id']], bodies[link['water_body_id']]
        ds = datasets[station['dataset_id']]
        station_rows.append([ds['reporting_cycle'], station['eu_code'], station['name'], wb['eu_code'], wb['name'],
                             link['method'], ds['source_url'], station['attributes'].get('inspireIdVersionId')])
    write('Messstellen_Zuordnung.csv', ['Berichtszyklus', 'Messstellen_EU_ID', 'Messstellenname', 'Wasserkörper_EU_ID',
          'Wasserkörpername', 'FK_Methode', 'Quelldienst', 'Metadaten_Versionsdatum'], station_rows)
    unknown = collections.Counter(str(a['value_raw']) for a in t['water_body_assessment']
                                  if str(a['value_raw']) in ('None', 'Unknown', 'Unpopulated', 'Inapplicable'))
    report = {'cycles': counts, 'project_lakes': len(lakes), 'events': len(t['event']),
              'event_evidence': len(t['event_evidence']), 'source_documents': len(t['source_document']),
              'lake_version_links': len(t['lake_water_body']),
              'unique_reported_waterbody_identifiers': len({w['eu_code'] for w in t['water_body']}),
              'unique_reported_station_identifiers': len({w['eu_code'] for w in t['monitoring_station']}),
              'assessment_special_raw_strings': dict(unknown),
              'assessment_verified_labels': sum(a['value_label'] is not None for a in t['water_body_assessment']),
              'issue_codes': dict(collections.Counter(i['code'] for i in t['import_issue'])),
              'no_event_to_station_or_measurement_links_inferred': True,
              'original_event_payloads_preserved': True,
              'historical_geometry_is_consolidated_reference_not_old_shoreline': True}
    save_json(output / 'coverage.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report = export(args.bundle, args.output)
    print('Exported event/station associations and coverage for', report['project_lakes'], 'project lakes.')
