"""Download the selected official WISE WFD2022 report with full request provenance.

The national WasserBLIcK download is currently inaccessible from this client.
This adapter uses the EEA's published German report, not a live BfG endpoint.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from common import load_json, save_json

BASE = 'https://water.discomap.eea.europa.eu/arcgis/rest/services/WISE_WFD/'
def layer_specs(year):
    prefix = BASE + f'WFD{year}_'
    specs = {'water_bodies': {'url': prefix + 'SurfaceWaterBody_WM/MapServer/' + ('7' if year == 2010 else '4'),
                             'role': 'water_bodies', 'field': 'euSurfaceWaterBodyCode'}}
    if year == 2010:
        specs['historical_river_bodies'] = {'url': prefix + 'SurfaceWaterBody_WM/MapServer/2',
                                          'role': 'water_bodies', 'field': 'euSurfaceWaterBodyCode'}
    specs['stations'] = {'url': prefix + 'MonitoringSite_WM/MapServer/0', 'role': 'stations', 'field': 'featureOfInterestIdentifier'}
    specs['quality_elements'] = {'url': prefix + 'QualityElements_WM/MapServer/' + ('7' if year == 2010 else '4'),
                                'role': 'quality', 'field': 'euSurfaceWaterBodyCode'}
    if year == 2010:
        specs['historical_river_quality'] = {'url': prefix + 'QualityElements_WM/MapServer/2',
                                             'role': 'quality', 'field': 'euSurfaceWaterBodyCode'}
    return specs


CYCLES = {2010: '2009-2015', 2016: '2016-2021', 2022: '2022-2027'}


class Snapshot:
    def __init__(self, root):
        self.root = Path(root)
        if self.root.exists() and any(self.root.iterdir()):
            raise ValueError('Use a new empty snapshot directory; existing downloads are immutable.')
        self.root.mkdir(parents=True, exist_ok=True)
        self.requests = []

    def get(self, url, params, name):
        url += '?' + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={'User-Agent': 'SOLVE-WRRL-research/1.0', 'Accept': 'application/json'})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    body = response.read()
                    final_url = response.url
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 502, 503, 504) or attempt == 2:
                    raise
                time.sleep(min(30, 2 ** (attempt + 1)))
        data = json.loads(body.decode('utf-8-sig'))
        if 'error' in data:
            raise RuntimeError('ArcGIS query failed: ' + json.dumps(data['error']))
        path = self.root / name
        path.write_bytes(body)
        self.requests.append({'file': name, 'url': url, 'final_url': final_url,
            'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)})
        return data

    def layer(self, name, spec, where):
        url = spec['url']
        metadata = self.get(url, {'f': 'json'}, name + '.metadata.json')
        self.get(url.rsplit('/', 1)[0] + '/info/iteminfo', {'f': 'json'}, name + '.service_item.json')
        count = self.get(url + '/query', {'f': 'json', 'where': where, 'returnCountOnly': 'true'}, name + '.count.json')['count']
        ids_data = self.get(url + '/query', {'f': 'json', 'where': where, 'returnIdsOnly': 'true'}, name + '.ids.json')
        ids = sorted(ids_data.get('objectIds') or [])
        if len(ids) != count or len(set(ids)) != count:
            raise ValueError('ArcGIS count/object-ID mismatch for ' + name)
        id_field = ids_data['objectIdFieldName']
        features = []
        for offset in range(0, len(ids), 100):
            data = self.get(url + '/query', {'f': 'geojson', 'objectIds': ','.join(map(str, ids[offset:offset+100])),
                'outFields': '*', 'outSR': '4326', 'returnGeometry': 'true', 'orderByFields': id_field},
                f'{name}.{offset//100:03d}.geojson')
            if data.get('exceededTransferLimit'):
                raise ValueError('Truncated ArcGIS response for ' + name)
            features.extend(data['features'])
        received = [f['properties'][id_field] for f in features]
        if sorted(received) != ids:
            raise ValueError('Missing, duplicate or unexpected source feature IDs for ' + name)
        save_json(self.root / (name + '.geojson'), {'type': 'FeatureCollection', 'features': features})
        return {'features': count, 'where': where, 'service_url': url,
                'role': spec['role'],
                'geometry_crs': 'EPSG:4326', 'metadata_file': name + '.metadata.json',
                'data_file': name + '.geojson',
                'sha256': hashlib.sha256((self.root / (name + '.geojson')).read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', type=Path)
    parser.add_argument('--year', type=int, choices=list(CYCLES), default=2022)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    selection_path = args.selection or Path(__file__).with_name('selection.json' if args.year == 2022 else f'selection_{args.year}.json')
    selection = load_json(selection_path)
    codes = sorted(set(d['eu_code'] for d in selection['decisions'].values()))
    if not codes or any(not c.replace('_', '').replace('-', '').isalnum() for c in codes):
        raise ValueError('Invalid EU-code selection')
    literals = ','.join("'" + c + "'" for c in codes)
    snap = Snapshot(args.output)
    layers = {}
    for name, spec in layer_specs(args.year).items():
        layers[name] = snap.layer(name, spec, "countryCode='DE' AND " + spec['field'] + ' IN (' + literals + ')')
    returned = {f['properties']['euSurfaceWaterBodyCode'] for name, spec in layers.items() if spec['role'] == 'water_bodies'
                for f in load_json(args.output / spec['data_file'])['features']}
    if returned != set(codes):
        raise ValueError('Official lake coverage mismatch: ' + str(set(codes) - returned))
    save_json(args.output / 'manifest.json', {'snapshot_version': 1, 'reporting_cycle': CYCLES[args.year],
        'report_year': args.year, 'provider': f'European Environment Agency, WISE WFD{args.year}; German national reporting',
        'lineage': 'BfG/Länder reporting; access via official EEA recipient publication, not direct WasserBLIcK download.',
        'national_download_status': 'Direct BfG endpoints returned HTTP 403 during discovery on 2026-10-31.',
        'layers': layers, 'requests': snap.requests,
        'selection': selection,
        'geometry_note': 'EEA harmonized reference geometry; older-cycle objects may use newer geometry where identities were retained. Not a reconstruction of historical shorelines.',
        'station_metadata_note': 'Historical service entries can contain later revised station metadata. Report year is not a sampling date or a guarantee of original-era station geometry.',
        'coverage_note': 'Reported waterbody geometry/status and monitoring-site metadata; no station measurement time series.'})
    print(json.dumps({'output': str(args.output), 'water_bodies': len(returned),
                      'stations': layers['stations']['features']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
