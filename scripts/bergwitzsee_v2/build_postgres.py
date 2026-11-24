"""Load real Bergwitzsee sources into a NEW database with the original V2 DDL.

The original schema/importer are retained. Additional provenance columns and
event/version tables are additive; no dummy rows or guessed measurements enter.
"""
from pathlib import Path
from datetime import datetime
import csv
import hashlib
import importlib.util
import json
import math
import re
import sys
from collections import defaultdict
import psycopg
from psycopg import sql
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import URL
from event_extension import build_extension, WATER_BODY_ID

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'outputs/bergwitzsee_neo4j_v2_events_20261101'
SOURCE = ROOT / 'outputs/bergwitzsee_lhw_20261101/prepared'
BUNDLE = ROOT / 'outputs/wasserblick33_20261031/data/prepared/bundle.json'
DBNAME = 'solve_bergwitzsee_v2_events_20261101'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def js(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def read_csv(name):
    with (SOURCE/name).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f, delimiter=';'))


def number(value):
    if value is None or str(value).strip() == '':
        return None
    n = float(str(value).replace(',', '.'))
    if not math.isfinite(n):
        raise ValueError(f'Nonfinite value {value!r}')
    return n


def insert(conn, table, rows, schema='water_kg_2'):
    if not rows:
        return
    keys = sorted(set().union(*(r.keys() for r in rows)))
    query = text(f'INSERT INTO "{schema}"."{table}" (' + ','.join(f'"{k}"' for k in keys)
                 + ') VALUES (' + ','.join(':'+k for k in keys) + ')')
    for start in range(0, len(rows), 1000):
        conn.execute(query, [{k: r.get(k) for k in keys} for r in rows[start:start+1000]])


def assemble(extension):
    tables = defaultdict(list)
    stations, params, units, sections = {}, {}, {}, {}
    registry = {}
    issues = tables['import_issue']

    def source(name, path=None):
        if name not in registry:
            path = path or SOURCE/name
            registry[name] = {'source_file': name, 'source_path': str(path),
                              'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        return name

    def issue(entity, file, row, col, val, code, message):
        issues.append(dict(entity_type=entity, source_file=file, source_row_number=row,
                           source_column=col, source_value=str(val), issue_code=code, message=message))

    def station(code, name, file, original):
        if code not in stations:
            sid = len(stations)+1
            stations[code] = dict(station_id=sid, water_body_id=WATER_BODY_ID, name=name,
                station_origin='observed_only', local_code=code, metadata=[])
            tables['station_alias'].append(dict(alias_id=sid, station_id=sid,
                water_body_id=WATER_BODY_ID, alias_code=code, alias_type='local_code', source_file=file))
            issue('monitoring_station', file, sid, 'coordinates',
                  original, 'UNRESOLVED_COORDINATE_CRS',
                  'Raw coordinates retained; CSV CRS is absent or conflicts with supplied SHP PRJ. No guessed station geometry.')
        stations[code]['metadata'].append(original)
        return stations[code]['station_id']

    def parameter(name, quality):
        key = (name, quality)
        if key not in params:
            params[key] = len(params)+1
            tables['parameter'].append(dict(parameter_id=params[key], name=name, quality_component=quality))
        return params[key]

    def unit(symbol):
        if not symbol:
            return None
        if symbol not in units:
            units[symbol] = len(units)+1
            tables['unit'].append(dict(unit_id=units[symbol], symbol=symbol))
        return units[symbol]

    file = source('messstellen_chemie.csv')
    for r in read_csv(file):
        station(r['station_id'], r['Messstellen-Name'], file, r)
    for topic in ['phytoplankton', 'makrophyten_diatomeen', 'makrozoobenthos']:
        file = source(topic+'_Stammdaten.csv')
        for r in read_csv(file):
            code = r['Mst_Nr'] if topic == 'phytoplankton' else topic+':'+r['ID']
            station(code, r.get('Messstelle') or r['ID'], file, r)

    common = dict(water_body_id=WATER_BODY_ID, monitoring_program='LHW Sachsen-Anhalt')
    for topic, filename in [('water', 'einzeldaten_wasser.csv'), ('sediment', 'einzeldaten_sediment.csv')]:
        file = source(filename)
        for n, r in enumerate(read_csv(file), 2):
            assert r['EU_CD_LW'] == WATER_BODY_ID and r['Gewaesser'] == 'Bergwitzsee'
            exact, bound = number(r['WERT_exakt']), number(r['Grenzwert'])
            assert not (exact is not None and bound is not None)
            assert (r['Vergleichszeichen'] == '=') == (exact is not None)
            tables['measurement'].append(dict(common, measurement_id=len(tables['measurement'])+1,
                source_file=file, source_row_number=n, source_index=int(r['source_csv_record']),
                station_id=stations[r['MESSSTELLE']]['station_id'], source_station_code=r['MESSSTELLE'],
                parameter_id=parameter(r['Parameterbezeichnung'], 'Chemie '+topic), unit_id=unit(r['EINHEIT']),
                measured_at=datetime.strptime(r['DATUM_original'], '%d.%m.%Y %H:%M:%S').date(), value=exact,
                value_original=r['WERT_original'], comparison_operator=r['Vergleichszeichen'], detection_limit=bound,
                measured_at_original=r['DATUM_original'], measured_at_utc=r['DATUM_UTC'], time_precision='second (source UTC)',
                source_record_id=r['record_id'], source_topic=topic,
                source_archive=r['source_archive'], source_member=r['source_member'],
                source_csv_record=int(r['source_csv_record']), source_column=r['source_column'],
                source_url=r['source_url'], source_payload_json=js(r),
                depth_original=r['Tiefe_original'], compartment_original=r['Kompartiment']))
            if r['Parameterbezeichnung'] == 'Sauerstoffsättigung' and r['EINHEIT'] == 'mg/l':
                issue('measurement', file, n, 'EINHEIT', r['EINHEIT'], 'SOURCE_UNIT_QUESTIONABLE',
                      'Source labels oxygen saturation in mg/l. Preserved without inferred correction.')
        if topic == 'sediment':
            issue('dataset', file, 0, 'Kompartiment', 'Wasser', 'SOURCE_COMPARTMENT_CONFLICT',
                  'Sediment archive source header says Wasser. Topic is sediment; original header preserved.')

    for topic in ['phytoplankton', 'makrophyten_diatomeen', 'makrozoobenthos']:
        filename = topic+'_Messreihen.csv'
        # A biological source row contains multiple metrics. Unique normalized row
        # identifiers preserve the old V2 uniqueness contract, original locators stay explicit.
        file = source('derived/'+filename, SOURCE/filename)
        sequence = 0
        for r in read_csv(filename):
            assert r['Gewaesser'] == 'Bergwitzsee'
            code = r['Mst_Nr'] if topic == 'phytoplankton' else topic+':'+r['ID']
            sample = r.get('ProbeNrBio') or r['Probe_Nr']
            day = datetime.strptime(r['Datum'], '%d.%m.%Y %H:%M:%S').date()
            depth = r.get('Tiefe', '')
            sample_key = (topic, code, sample, r['Datum'], depth)
            if sample_key not in sections:
                section_id = len(sections)+1
                sections[sample_key] = section_id
                lo = hi = None
                match = re.fullmatch(r'(\d+)-(\d+) m', depth)
                if match:
                    lo, hi = map(float, match.groups())
                elif re.fullmatch(r'>\d+ m', depth):
                    lo = float(depth[1:-2])
                tables['sampling_section'].append(dict(sampling_section_id=section_id,
                    source_file=file, source_row_number=section_id, water_body_id=WATER_BODY_ID,
                    station_id=stations[code]['station_id'], source_station_code=code,
                    section_code=sample, sampling_year=day.year, depth_from=lo, depth_to=hi,
                    depth_original=depth, depth_lower_open=depth.startswith('>'), sample_date=day,
                    source_payload_json=js(r), source_csv_record=int(r['source_csv_record'])))
            metrics = ['Zellzahl', 'Biovolumen'] if topic == 'phytoplankton' else (
                ['MP_HK', 'Dia_abs', 'Dia_%'] if topic == 'makrophyten_diatomeen' else ['IZ'])
            quality = r.get('BQK') or topic
            for metric in metrics:
                if not r.get(metric, '').strip():
                    continue
                sequence += 1
                value = number(r[metric])
                tables['measurement'].append(dict(common, measurement_id=len(tables['measurement'])+1,
                    source_file=file, source_row_number=sequence, source_index=int(r['source_csv_record']),
                    station_id=stations[code]['station_id'], source_station_code=code,
                    sampling_section_id=sections[sample_key], source_section_code=sample,
                    parameter_id=parameter(r['Taxon'], quality), unit_id=unit('%' if metric == 'Dia_%' else None),
                    measured_at=day, value=value, abundance_type=metric, value_original=r[metric],
                    comparison_operator='=', measured_at_original=r['Datum'], time_precision='day',
                    source_record_id=f'{topic}:{r["source_csv_record"]}:{metric}', source_topic=topic,
                    source_archive=r['source_archive'], source_member=r['source_member'],
                    source_csv_record=int(r['source_csv_record']), source_column=metric, source_url=r['source_url'],
                    source_payload_json=js(r), depth_original=depth, taxon_code=r['DV'], taxon_name=r['Taxon'],
                    value_scale='ordinal' if metric == 'MP_HK' else 'source numeric metric',
                    unit_status='reported percent' if metric == 'Dia_%' else 'not specified in source export'))
    for r in stations.values():
        r['source_payload_json'] = js(r.pop('metadata'))
        r['coordinate_status'] = 'unresolved_crs_no_geometry'
        tables['monitoring_station'].append(r)

    source('wrrl_event_bundle.json', BUNDLE)
    source('stammdaten_lhw.json')
    for i, a in enumerate(extension['tables']['water_body_assessment'], 1):
        try:
            value = number(a['value_raw'])
        except ValueError:
            value = None
        definition = a['metadata'].get('definition', {}).get('values', {})
        valid = value is not None and str(a['value_raw']) in definition
        year = a['metadata'].get('assessment_year_original')
        tables['water_body_assessment'].append(dict(assessment_id=i, source_file='wrrl_event_bundle.json',
            source_row_number=i, water_body_id=WATER_BODY_ID, parameter_id=parameter(a['parameter_code'], 'WRRL'),
            assessment_year=int(year) if year and re.fullmatch(r'\d{4}', str(year)) else None,
            value_raw=value, value_normalized=value if valid else None, is_valid=valid,
            validation_reason=None if valid else 'Missing value or unverified code domain; original preserved',
            source_assessment_id=a['assessment_id'], source_version_id=a['water_body_id'],
            reporting_cycle=a['reporting_cycle'], assessment_year_original=year,
            value_original=str(a['value_raw']), value_label=a['value_label'], source_payload_json=js(a)))
    assert len(tables['measurement']) == 23789
    assert len(stations) == 25 and len(tables['sampling_section']) == 324
    assert sum(r['value'] is None for r in tables['measurement']) == 2627
    return tables, registry


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    runtime = OUT/'runtime'
    runtime.mkdir(exist_ok=True)
    extension = build_extension(BUNDLE)
    (OUT/'BERGWITZSEE_EXTENSION.json').write_text(js(extension), encoding='utf-8')
    tables, registry = assemble(extension)
    pg = load('berg_pg_runtime', ROOT/'outputs/wasserblick33_20261031/runtime/manage_test_pg.py')
    pg.check_marker()
    status = pg.call([pg.PG_BIN/'pg_ctl.exe', '-D', pg.PG_DATA, 'status'], 'berg-pg-status.log', check=False)
    if status.returncode == 3:
        pg.call([pg.PG_BIN/'pg_ctl.exe', '-D', pg.PG_DATA, '-l', pg.PG_LOG, '-o', f'-h 127.0.0.1 -p {pg.PORT}', '-w', 'start'], 'berg-pg-start.log')
    elif status.returncode:
        raise RuntimeError('Unexpected PostgreSQL status')
    password = pg.PWFILE.read_text(encoding='utf-8').strip()
    with psycopg.connect(host='127.0.0.1', port=pg.PORT, user=pg.DBUSER, password=password, dbname='postgres', autocommit=True) as conn:
        assert Path(conn.execute("SELECT current_setting('data_directory')").fetchone()[0]).resolve() == pg.PG_DATA.resolve()
        if conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (DBNAME,)).fetchone():
            raise RuntimeError('Target database exists; refusing replacement')
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(DBNAME)))
    url = URL.create('postgresql+psycopg', username=pg.DBUSER, password=password, host='127.0.0.1', port=pg.PORT, database=DBNAME)
    pg.private_write(runtime/'postgres.env', 'SOLVE_DATABASE_URL='+url.render_as_string(hide_password=False)+'\n')
    engine = create_engine(url, pool_pre_ping=True)
    snapshots = OUT/'source_scripts'
    snapshots.mkdir(exist_ok=True)
    hashes = {}
    for name in ['water_kg_2_import.py', 'neo4j_v2_import.py']:
        content = (ROOT/'scripts'/name).read_bytes()
        (snapshots/name).write_bytes(content)
        hashes[name] = hashlib.sha256(content).hexdigest()
    original = load('berg_original_v2_ddl', snapshots/'water_kg_2_import.py')
    print('Creating original V2 schema and importing actual Bergwitzsee data', flush=True)
    with engine.begin() as conn:
        conn.execute(text('CREATE SCHEMA water_raw'))
        conn.execute(text('CREATE TABLE water_raw.source_file(source_file TEXT PRIMARY KEY,source_path TEXT NOT NULL,sha256 TEXT NOT NULL)'))
        insert(conn, 'source_file', list(registry.values()), 'water_raw')
        for ddl in original.schema_ddl('water_kg_2'):
            conn.execute(text(ddl))
        additions = {
            'water_body': 'snapshot_year INTEGER,source_version_id TEXT,source_payload_json TEXT,geometry_note TEXT,lhw_stammdaten_json TEXT,lhw_stammdaten_source TEXT,lhw_stammdaten_note TEXT',
            'monitoring_station': 'local_code TEXT,source_payload_json TEXT,coordinate_status TEXT',
            'sampling_section': 'depth_original TEXT,depth_lower_open BOOLEAN,sample_date DATE,source_payload_json TEXT,source_csv_record BIGINT',
            'measurement': 'value_original TEXT,comparison_operator TEXT,detection_limit DOUBLE PRECISION,measured_at_original TEXT,measured_at_utc TEXT,time_precision TEXT,source_record_id TEXT,source_topic TEXT,source_archive TEXT,source_member TEXT,source_csv_record BIGINT,source_column TEXT,source_url TEXT,source_payload_json TEXT,depth_original TEXT,compartment_original TEXT,taxon_code TEXT,taxon_name TEXT,value_scale TEXT,unit_status TEXT',
            'water_body_assessment': 'source_assessment_id TEXT UNIQUE,source_version_id TEXT,reporting_cycle TEXT,assessment_year_original TEXT,value_original TEXT,value_label TEXT,source_payload_json TEXT',
        }
        for table, fields in additions.items():
            for field in fields.split(','):
                conn.execute(text(f'ALTER TABLE water_kg_2.{table} ADD COLUMN {field}'))
        latest = max(extension['tables']['water_body'], key=lambda r:r['attributes']['cYear'])
        a = latest['attributes']
        conn.execute(text('''INSERT INTO water_kg_2.water_body
            (water_body_id,segment_code,name,area_km2,lawa_type,water_type,artificial,river_basin_code,ecological_status,chemical_status,
             geometry,snapshot_year,source_version_id,source_payload_json,geometry_note)
            VALUES (:id,:segment,'Bergwitzsee',:area,:type,'LW',true,:basin,:eco,:chem,
            ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(:geometry),4326),25833),:year,:version,:payload,:note)'''),
            dict(id=WATER_BODY_ID,segment='EL03OW07-00',area=a['cArea'],type=a['surfaceWaterBodyTypeCode'],
                 basin=a['euRBDCode'],eco=a['swEcologicalStatusOrPotentialValue'],chem=a['swChemicalStatusValue'],
                 geometry=js(latest['geometry']),year=a['cYear'],version=latest['water_body_id'],payload=js(latest),
                 note='Consolidated EEA reference geometry; not a reconstructed historical shoreline.'))
        conn.execute(text('''UPDATE water_kg_2.water_body SET lhw_stammdaten_json=:payload,
            lhw_stammdaten_source='stammdaten_lhw.json',lhw_stammdaten_note=:note WHERE water_body_id=:id'''),
            dict(id=WATER_BODY_ID, payload=js(json.loads((SOURCE/'stammdaten_lhw.json').read_text(encoding='utf-8'))),
                 note='Original LHW master attributes preserved. Units are not inferred; conflicting UTM attribute is explicit.'))
        insert(conn, 'water_body_identifier', [dict(identifier=WATER_BODY_ID, identifier_type='EU_CD_LW', water_body_id=WATER_BODY_ID),
            dict(identifier='EL03OW07-00', identifier_type='LHW_OWK', water_body_id=WATER_BODY_ID)])
        for table in ['monitoring_station','station_alias','parameter','unit','sampling_section','measurement','water_body_assessment','import_issue']:
            insert(conn, table, tables[table])
        conn.execute(text('CREATE SCHEMA water_events'))
        conn.execute(text('CREATE TABLE water_events.source_document(source_id TEXT PRIMARY KEY,payload JSONB NOT NULL)'))
        conn.execute(text('CREATE TABLE water_events.event(event_id TEXT PRIMARY KEY,water_body_id TEXT NOT NULL REFERENCES water_kg_2.water_body,payload JSONB NOT NULL)'))
        conn.execute(text('CREATE TABLE water_events.event_evidence(evidence_id TEXT PRIMARY KEY,event_id TEXT NOT NULL REFERENCES water_events.event,source_id TEXT NOT NULL REFERENCES water_events.source_document,payload JSONB NOT NULL)'))
        conn.execute(text('CREATE TABLE water_events.water_body_version(version_id TEXT PRIMARY KEY,water_body_id TEXT NOT NULL REFERENCES water_kg_2.water_body,payload JSONB NOT NULL)'))
        for table, key in [('source_document','source_id'),('event','event_id'),('event_evidence','evidence_id'),('water_body_version','version_id')]:
            rows = extension['tables']['water_body' if table == 'water_body_version' else table]
            for row in rows:
                values = {key:row['water_body_id'] if table == 'water_body_version' else row[key], 'payload':js(row)}
                if table in ['event','water_body_version']: values['water_body_id']=WATER_BODY_ID
                if table == 'event_evidence':values.update(event_id=row['event_id'],source_id=row['source_id'])
                cols = list(values)
                vals = ['CAST(:payload AS JSONB)' if k=='payload' else ':'+k for k in cols]
                conn.execute(text(f'INSERT INTO water_events.{table}('+','.join(cols)+') VALUES ('+','.join(vals)+')'),values)
        conn.execute(text('ALTER TABLE water_kg_2.water_body_assessment ADD CONSTRAINT fk_assessment_version FOREIGN KEY(source_version_id) REFERENCES water_events.water_body_version(version_id)'))
    with engine.connect() as conn:
        counts={name:conn.execute(text(f'SELECT count(*) FROM water_kg_2."{name}"')).scalar_one()
                for name in inspect(engine).get_table_names(schema='water_kg_2')}
        assert conn.execute(text('SELECT ST_IsValid(geometry) FROM water_kg_2.water_body')).scalar_one()
        assert conn.execute(text("SELECT count(*) FROM water_kg_2.measurement WHERE comparison_operator != '=' AND value IS NOT NULL")).scalar_one()==0
    report={'status':'passed','database':DBNAME,'host':'127.0.0.1','port':pg.PORT,'original_ddl_sha256':hashes,
            'normalized_counts':counts,'source_registry':list(registry.values()),'event_counts':extension['audit'],
            'scope':'Bergwitzsee actual LHW observations plus historical WRRL reports and researched events; no dummy data',
            'censored_observations':2627,'all_original_tables_retained':True}
    (OUT/'POSTGRES_VALIDATION.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(counts,indent=2),flush=True)
    engine.dispose()


if __name__ == '__main__':
    main()
