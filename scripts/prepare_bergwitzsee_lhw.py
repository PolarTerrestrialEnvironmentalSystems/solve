"""Reproducible, conservative preparation of manually exported public GLD data.

Inputs are untouched portal ZIPs. No live database is modified.
Python dependencies: pyshp, pyproj (only for explicitly declared SHP geometry).
"""
from pathlib import Path
import csv
import io
import json
import re
import hashlib
import zipfile
from datetime import datetime
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/bergwitzsee_lhw_20261101'
PREP = OUT / 'prepared'
URL = 'https://gld.lhw-sachsen-anhalt.de/'
CREDIT = 'Quelle: © Landesbetrieb für Hochwasserschutz und Wasserwirtschaft Sachsen-Anhalt (LHW), 2026 (Daten verändert)'
EU = 'DELW_DEST_EL03OW07-00'


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def decode(data):
    try:
        return data.decode('utf-8-sig'), 'utf-8-sig'
    except UnicodeDecodeError:
        return data.decode('cp1252'), 'cp1252'


def read_rows(data):
    text, enc = decode(data)
    return [r for r in csv.reader(io.StringIO(text, newline=None), delimiter=';') if r], enc


def csv_output(name, rows, fields=None):
    # Relational interchange file, not a formatted workbook. Raw values kept as text.
    fields = fields or list(dict.fromkeys(k for r in rows for k in r))
    with (PREP / name).open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter=';')
        writer.writeheader()
        writer.writerows(rows)


def isodate(value):
    return datetime.strptime(value, '%d.%m.%Y %H:%M:%S').isoformat()


def values(value):
    m = re.fullmatch(r'\s*([<>]=?|[≤≥])?\s*([+-]?\d+(?:[,.]\d+)?(?:[eE][+-]?\d+)?)\s*', value)
    if not m:
        return '', '', ''
    qualifier, number = m.groups()
    number = number.replace(',', '.')
    # A below-detection bound is NOT a measured concentration.
    return qualifier or '=', number if not qualifier else '', number if qualifier else ''


def main():
    PREP.mkdir(parents=True, exist_ok=True)
    manifest = {'retrieval_date': '2026-11-01', 'portal': URL,
        'method': 'Public browser export: Seen -> selected topic -> Expertenrecherche -> Gewässer = Bergwitzsee -> Messreihen Export, Gesamtzeitraum, CSV. All parameters for chemistry/sediment.',
        'download_urls_session_bound': True,
        'source_credit': CREDIT,
        'archives': [], 'counts': {}, 'limitations': [
            'Only the records published in this portal export; no claim of all measurements ever collected.',
            'All available dates selected. Gaps between sampling years are retained.',
            'Original numeric qualifiers, missing values, taxon names, source units and timestamps retained.',
            'No taxon-count or biovolume units invented where CSV does not state them.',
            'Local station and transect identifiers must be scoped by topic and waterbody. Equal transect numbers across biology topics do not identify equal locations.',
            'No station-level WRRL assessment, catchment polygon, protected area, groundwater or paleo dataset obtained in this package.',
            'EEA historical geometries are consolidated reference geometries, not evidence of historical shorelines.',
            'LHW raw SHP is a complete 72-object topic export: the map filter did not restrict export; prepared SHP contains only Bergwitzsee.',
        ]}
    chemistry, stations, quality = [], [], []
    for p in sorted((OUT / 'raw').glob('*.zip')):
        with zipfile.ZipFile(p) as z:
            assert z.testzip() is None, p
            entry = {'file': str(p.relative_to(OUT)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size, 'members': []}
            for n in z.namelist():
                if n.endswith('/'):
                    continue
                data = z.read(n)
                # Extract original member bytes with checked paths; never overwrite raw archives.
                dest = (OUT / 'original_export' / p.stem / n).resolve()
                dest.relative_to((OUT / 'original_export').resolve())
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                member = {'name': n, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                entry['members'].append(member)
                if not n.endswith('.csv'):
                    continue
                rows, enc = read_rows(data)
                member['encoding_used'] = enc
                member['nonempty_csv_records'] = len(rows)
                if p.stem in ['wasser_messreihen', 'sediment_messreihen'] and n.endswith('_messreihen.csv'):
                    metadata_name = n.rsplit('/', 1)[0] + '/Metadaten.csv'
                    metadata_rows, _ = read_rows(z.read(metadata_name))
                    meta = dict(metadata_rows[1:])
                    assert meta['Name des Gewässers'] == 'Bergwitzsee'
                    assert meta['Oberflächenwasserkörper Code'] == 'EL03OW07-00'
                    sid = meta['Messstellen-Nummer']
                    stations.append({'topic': p.stem, 'station_id': sid, **meta, 'source_archive':p.name, 'source_member':metadata_name})
                    h = next(i for i,r in enumerate(rows) if r[0]=='Zeitpunkt')
                    assert h == 2
                    for ri, row in enumerate(rows[h+1:], h+2):
                        assert len(row) == len(rows[h]), (n,ri)
                        for col, raw in enumerate(row[1:],1):
                            if not raw.strip():
                                continue
                            header = rows[h][col]
                            m = re.fullmatch(r'(.*) \[(.*)\]', header)
                            parameter, unit = m.groups() if m else (header, '')
                            qualifier, measured, limit = values(raw)
                            chemistry.append({'record_id':f'{p.stem}:{sid}:{ri}:{col}',
                                'Gewaesser':'Bergwitzsee', 'EU_CD_LW':EU, 'OWK_original':'EL03OW07-00',
                                'MESSSTELLE':sid, 'Messstellenname':meta['Messstellen-Name'],
                                'DATUM_original':row[0], 'DATUM_UTC':isodate(row[0])+'Z',
                                'Kompartiment':rows[0][col], 'Stoffgruppe':rows[1][col],
                                'Parameterbezeichnung':parameter, 'EINHEIT':unit,
                                'WERT_original':raw, 'Vergleichszeichen':qualifier,
                                'WERT_exakt':measured, 'Grenzwert':limit,
                                'Tiefe_original':meta['Tiefe der Probenahme'],
                                'Probenart':meta['Probenart'], 'source_archive':p.name,
                                'source_member':n, 'source_csv_record':ri, 'source_column':col+1,
                                'source_url':URL})
                    if 'Sauerstoffsättigung [mg/l]' in rows[h]:
                        quality.append({'code':'suspicious_source_unit','archive':p.name,'member':n,
                            'message':'Sauerstoffsättigung is labelled mg/l in source; preserved, not silently changed to percent.'})
                elif p.stem in ['phytoplankton','makrophyten_diatomeen','makrozoobenthos']:
                    assert all(len(r)==len(rows[0]) for r in rows[1:])
                    records = [dict(zip(rows[0], r)) for r in rows[1:]]
                    assert all(r['Gewaesser']=='Bergwitzsee' for r in records)
                    for i,r in enumerate(records,2):
                        r.update(source_archive=p.name, source_member=n, source_csv_record=i, source_url=URL)
                    csv_output(p.stem+'_'+Path(n).name, records)
                    if n=='Messreihen.csv':
                        ds=[isodate(r['Datum']) for r in records]
                        manifest['counts'][p.stem]={'records':len(records), 'date_start':min(ds),'date_end':max(ds),
                            'taxa':len({r['Taxon'] for r in records}),
                            'distinct_source_probe_numbers':len({r.get('ProbeNrBio',r.get('Probe_Nr')) for r in records})}
                        if p.stem=='makrophyten_diatomeen':
                            manifest['counts'][p.stem]['by_BQK']=dict(Counter(r['BQK'] for r in records))
            manifest['archives'].append(entry)
    for name in ['wasser','sediment']:
        records=[r for r in chemistry if r['source_archive']==name+'_messreihen.zip']
        csv_output('einzeldaten_'+name+'.csv', records)
        manifest['counts'][name]={'nonempty_values':len(records), 'parameters':len({r['Parameterbezeichnung'] for r in records}),
            'stations':len({r['MESSSTELLE'] for r in records}),
            'date_start':min(r['DATUM_UTC'] for r in records), 'date_end':max(r['DATUM_UTC'] for r in records),
            'qualified_bounds':sum(bool(r['Grenzwert']) for r in records),
            'unparsed_nonempty':sum(not r['Vergleichszeichen'] for r in records)}
    csv_output('messstellen_chemie.csv', stations)

    import shapefile
    from pyproj import Transformer
    source = OUT / 'original_export/stammdaten_geodaten/Stammdaten/Stammdaten'
    enc=source.with_suffix('.cpg').read_text().strip()
    reader=shapefile.Reader(str(source), encoding=enc)
    selected=[sr for sr in reader.iterShapeRecords() if sr.record.as_dict()['gewaesser'].casefold()=='bergwitzsee']
    assert len(selected)==1
    geo=PREP / 'geodaten'
    geo.mkdir(exist_ok=True)
    with shapefile.Writer(str(geo/'bergwitzsee_stammdaten'), shapeType=reader.shapeType,encoding=enc) as writer:
        writer.fields=reader.fields[1:]
        for sr in selected:
            writer.shape(sr.shape);writer.record(*sr.record)
    for ext in ['.prj','.cpg']:
        (geo/('bergwitzsee_stammdaten'+ext)).write_bytes(source.with_suffix(ext).read_bytes())
    props=selected[0].record.as_dict()
    assert props['owk_nr']=='EL03OW07-00'
    save_json(PREP/'stammdaten_lhw.json', {'source':URL,'attributes_original':props,
        'shape_geometry_crs':'EPSG:25832 (from supplied PRJ)', 'attribute_utm_conflict':props['utm']})
    csv_output('stammdaten_lhw.csv',[props])
    transform=Transformer.from_crs(source.with_suffix('.prj').read_text(),4326,always_xy=True)
    point=transform.transform(*selected[0].shape.points[0])
    assert 12<point[0]<13 and 51<point[1]<52
    save_json(geo/'bergwitzsee_stammdaten_wgs84.geojson',{'type':'FeatureCollection','features':[
        {'type':'Feature','geometry':{'type':'Point','coordinates':point},'properties':{**props,'source_url':URL,'transformation':'PRJ EPSG:25832 to EPSG:4326; utm attribute untouched'}}]})
    quality.append({'code':'conflicting_crs_attribute','message':'LHW Stammdaten SHP PRJ declares ETRS89 / UTM 32N; attribute utm is 33. Point geometries transformed ONLY using supplied PRJ. Biology CSV coordinates not transformed or silently relabelled.'})
    quality.append({'code':'topic_scoped_transect_ids','message':'Bergwitzsee_1 etc. refer to different locations in Makrophyten/Diatomeen and Makrozoobenthos. Join keys must include topic.'})

    bundle=json.loads((ROOT/'outputs/wasserblick33_20261031/data/prepared/bundle.json').read_text(encoding='utf-8'))['tables']
    wbs=[r for r in bundle['water_body'] if r['eu_code']==EU]
    assert len(wbs)==3
    ids={r['water_body_id'] for r in wbs}
    assessments=[r for r in bundle['water_body_assessment'] if r['water_body_id'] in ids]
    dataset_ids={r['dataset_id'] for r in wbs}
    raw_ids={r['raw_id'] for r in wbs+assessments}
    raw_records=[r for r in bundle['raw_record'] if r['raw_id'] in raw_ids]
    dataset_ids.update(r['dataset_id'] for r in raw_records)
    save_json(PREP/'wrrl_historie.json',{'water_body':wbs,'assessments':assessments,
        'raw_records':raw_records,'datasets':[r for r in bundle['dataset'] if r['dataset_id'] in dataset_ids]})
    csv_output('wasserkoerper_bewertung.csv',[{k:(json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v) for k,v in r.items()} for r in assessments])
    for wb in wbs:
        save_json(geo/f"lakewaterbody_{wb['attributes']['cYear']}.geojson",{'type':'FeatureCollection','features':[
            {'type':'Feature','geometry':wb['geometry'],'properties':wb['attributes']}]})
    manifest['counts']['wrrl']={'versions':len(wbs),'assessment_entries':len(assessments)}
    save_json(PREP/'gewaesser_zuordnung.json', {
        'project_lake':'Bergwitzsee','local_lake_number':'13',
        'LHW_OWK':'EL03OW07-00','EEA_euSurfaceWaterBodyCode':EU,
        'method':'Exact full regional code, corroborated by lake name and the LHW Stammdaten record. No fuzzy-name-only match.',
        'evidence':[
            {'file':'stammdaten_lhw.json','fields':['gewaesser','owk_nr']},
            {'file':'wrrl_historie.json','fields':['water_body[].eu_code','water_body[].name']},
            {'file':'messstellen_chemie.csv','fields':['Name des Gewässers','Oberflächenwasserkörper Code']}
        ],
        'station_matching':'Chemistry -> phytoplankton only when local Mst_Nr is identical within Bergwitzsee. No inferred EU station code.',
        'transect_matching':'Use topic + Gewaesser + Transekt; do not merge between Macrophytes/Diatoms and Macrozoobenthos.'})
    assert len({r['record_id'] for r in chemistry})==len(chemistry)
    assert all(not (r['Grenzwert'] and r['WERT_exakt']) for r in chemistry)
    assert all(r['EU_CD_LW']==EU for r in chemistry)
    assert len(shapefile.Reader(str(geo/'bergwitzsee_stammdaten'),encoding=enc))==1
    save_json(PREP/'qualitaetshinweise.json',quality)
    save_json(OUT/'manifest.json',manifest)
    print(json.dumps(manifest['counts'],ensure_ascii=True,indent=2))


if __name__=='__main__':
    main()
