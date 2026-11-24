"""Append verified official Bergwitzsee data to the isolated V2 clone.

No existing rows are changed. Unavailable original classes remain empty.
Every numeric qualifier, assessment scope and source locator is retained.
"""
from pathlib import Path
from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'outputs/bergwitzsee_ergaenzung_20261102'
BASE = ROOT/'outputs/bergwitzsee_neo4j_v2_events_20261101'
WB = 'DELW_DEST_EL03OW07-00'
BATCH = 'bergwitzsee_official_enrichment_20261102'
DBNAME = 'solve_bergwitzsee_v2_enriched_20261102'
SCHEMA = 'water_kg_2'


def js(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def quoted(x):
    return '"'+x.replace('"', '""')+'"'


def read(name):
    return json.loads((OUT/'data'/name).read_text(encoding='utf8'))


def main():
    config = dotenv_values(OUT/'runtime/postgres.env')
    engine = create_engine(config['SOLVE_DATABASE_URL'])
    assert engine.url.database == DBNAME
    old_engine = create_engine(dotenv_values(BASE/'runtime/postgres.env')['SOLVE_DATABASE_URL'])
    geo, mon = read('geodata/prepared_geodata.json'), read('monitoring/monitoring_supplement.json')
    assert geo['water_body_id'] == mon['waterbody_id'] == WB
    new = defaultdict(list)
    registry = {}
    next_ids = {}

    # Capture original columns as well as row hashes: adding NULL provenance
    # columns must not make the preservation check depend on the new schema.
    original = {}
    with old_engine.connect() as c:
        tables = c.execute(text("SELECT table_schema,table_name FROM information_schema.tables WHERE table_type='BASE TABLE' AND table_schema IN ('water_raw','water_kg_2','water_events') ORDER BY 1,2")).all()
        for schema, table in tables:
            cols = c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema=:s AND table_name=:t ORDER BY ordinal_position"), {'s':schema,'t':table}).scalars().all()
            q = f'SELECT md5(row_to_json(r)::text) FROM (SELECT {",".join(map(quoted,cols))} FROM {quoted(schema)}.{quoted(table)}) r'
            original[(schema,table)] = (q, Counter(c.execute(text(q)).scalars()))

    with engine.begin() as conn:
        if conn.execute(text("SELECT count(*) FROM water_raw.source_file WHERE source_file LIKE 'enrichment_20261102/%'")).scalar_one():
            raise RuntimeError('Supplement already loaded; refusing a second append')

        def columns(table, definitions):
            for name, typ in definitions.items():
                conn.execute(text(f'ALTER TABLE water_kg_2.{quoted(table)} ADD COLUMN IF NOT EXISTS {quoted(name)} {typ}'))

        provenance = {k:'TEXT' for k in ['source_url','source_sha256','source_locator','enrichment_batch']}
        for table in ['groundwater_body','protected_area','monitoring_station','sampling_section','measurement','water_body_assessment']:
            columns(table, dict(provenance, source_payload_json='TEXT'))
        columns('groundwater_body', {'source_file':'TEXT','snapshot_year':'INTEGER','source_horizons':'TEXT',
            'geometry_geojson':'TEXT','name_resolution_json':'TEXT','quantitative_status_label':'TEXT',
            'chemical_status_label':'TEXT','geometry_note':'TEXT'})
        columns('protected_area', {'source_area_code':'TEXT','geometry_geojson':'TEXT','geometry_note':'TEXT'})
        columns('water_body_assessment', {'assessment_scope':'TEXT','assessment_caveat':'TEXT'})
        columns('monitoring_station', {'identity_scope':'TEXT','reported_coordinates_json':'TEXT'})
        columns('measurement', {'reported_censoring_bound':'DOUBLE PRECISION','limit_kind':'TEXT'})
        conn.execute(text('CREATE SCHEMA IF NOT EXISTS water_kg_extension'))
        conn.execute(text('''CREATE TABLE water_kg_extension.groundwater_body_version (
          version_id TEXT PRIMARY KEY,
          groundwater_body_id TEXT NOT NULL REFERENCES water_kg_2.groundwater_body,
          reporting_year INTEGER NOT NULL,
          attributes_json TEXT NOT NULL,source_url TEXT NOT NULL,source_sha256 TEXT NOT NULL,
          geometry_geojson TEXT NOT NULL,quantitative_status TEXT,chemical_status TEXT,
          quantitative_status_label TEXT,chemical_status_label TEXT,
          quantitative_assessment_period TEXT,chemical_assessment_period TEXT,
          source_name TEXT,geometry_note TEXT,enrichment_batch TEXT NOT NULL,
          UNIQUE(groundwater_body_id,reporting_year))'''))

        def next_id(table, pk):
            if table not in next_ids:
                next_ids[table] = conn.execute(text(f'SELECT coalesce(max({quoted(pk)}),0) FROM water_kg_2.{quoted(table)}')).scalar_one()
            next_ids[table] += 1
            return next_ids[table]

        def insert(table, row, schema=SCHEMA, geometry=None):
            # This original table uses GENERATED ALWAYS AS IDENTITY.
            generated_issue = table == 'import_issue' and schema == SCHEMA
            if generated_issue:
                row.pop('issue_id', None)
            keys = list(row)
            vals = [':'+k for k in keys]
            params = dict(row)
            if geometry is not None:
                keys.append('geometry')
                vals.append('ST_SetSRID(ST_GeomFromGeoJSON(:_geometry),25833)')
                params['_geometry'] = js(geometry)
            suffix = ' RETURNING issue_id' if generated_issue else ''
            result = conn.execute(text(f'INSERT INTO {quoted(schema)}.{quoted(table)} ({",".join(map(quoted,keys))}) VALUES ({",".join(vals)})'+suffix), params)
            if generated_issue:
                row['issue_id'] = result.scalar_one()
            new[schema+'.'+table].append(row)

        def source(folder, relative, sha):
            path = OUT/'data'/folder/relative
            assert path.is_file(), str(path)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == sha, str(path)
            identity = 'enrichment_20261102/'+folder+'/'+relative.replace('\\','/')
            if identity not in registry:
                row = dict(source_file=identity,source_path=str(path),sha256=sha)
                insert('source_file', row, 'water_raw')
                registry[identity] = row
            return identity

        def prov(source_info, payload, locator):
            return dict(source_url=source_info['url'],source_sha256=source_info['sha256'],
                source_payload_json=js(payload),source_locator=locator,enrichment_batch=BATCH)

        for r in geo['protected_areas']:
            assert r['spatial_evidence']['intersection_area_m2'] > 0
            identity = r['stable_identity_candidate']
            file = source('geodata',r['source_file'],r['source']['sha256'])
            insert('protected_area', dict(protected_area_id=identity,name=r['name'],area_type=r['area_type'],
                member_state_code='DE',source_file=file,source_area_code=r['source_area_code'],
                url=r['source_properties'].get('rechtsgrun'),metadata_reference=r['source_properties'].get('info_konta'),
                geometry_geojson=js(r['geometry']),geometry_note='Official current protected-area polygon; snapshot '+r['source']['retrieved_at'][:10]+'.',
                **prov(r['source'],r['source_properties'],'WFS feature '+r['source_properties']['gml_id'])),
                geometry=r['geometry_25833'])
            s=r['spatial_evidence']
            insert('water_body_protected_area',dict(water_body_id=WB,protected_area_id=identity,
                relation_method='PostGIS/reference-polygon intersection; EPSG:25833; not a hydrological dependency',
                overlap_area_km2=s['intersection_area_m2']/1e6,water_body_overlap_ratio=s['waterbody_overlap_fraction']))

        names = {r['groundwater_body_id']:r for r in geo['groundwater_name_resolution']}
        by_id = defaultdict(list)
        for r in geo['groundwater_versions']:
            by_id[r['groundwater_body_id']].append(r)
            source('geodata',r['source_file'],r['source']['sha256'])
            source('geodata',f"raw/groundwater_{r['reporting_year']}_status.geojson",r['status_source']['sha256'])
        for gid, versions in by_id.items():
            r=max(versions,key=lambda v:v['reporting_year'])
            assert len(r['status_records'])==1
            a,st = r['source_properties'],r['status_records'][0]
            resolution=names[gid]
            source('geodata',resolution['source_file'],resolution['source']['sha256'])
            labels={v['field']:v['label'] for v in r['status_labels']}
            insert('groundwater_body',dict(groundwater_body_id=gid,name=resolution['name'],
                aquifer_type=a.get('aquiferMediaType'),geological_formation=st.get('geologicalFormation'),
                river_basin_code=a.get('relatedZoneIdentifier'),source_horizons=a.get('horizons'),
                quantitative_status=st.get('gwQuantitativeStatusValue'),chemical_status=st.get('gwChemicalStatusValue'),
                quantitative_status_label=labels['gwQuantitativeStatusValue'],chemical_status_label=labels['gwChemicalStatusValue'],
                source_file=source('geodata',r['source_file'],r['source']['sha256']),
                snapshot_year=r['reporting_year'],geometry_geojson=js(r['geometry']),
                geometry_note='EEA consolidated reference geometry; not proof of historical boundary changes.',
                name_resolution_json=js(resolution),**prov(r['source'],{'geometry_attributes':a,'status_records':r['status_records'],
                'status_source':r['status_source'],'status_labels':r['status_labels']},gid)), geometry=r['geometry_25833'])
            s=r['spatial_evidence']
            insert('water_body_groundwater_body',dict(water_body_id=WB,groundwater_body_id=gid,
                overlap_area_km2=s['intersection_area_m2']/1e6,water_body_overlap_ratio=s['waterbody_overlap_fraction'],
                determination_method='EPSG:25833 horizontal reference-polygon intersection; no hydraulic exchange inferred'))
            for v in sorted(versions,key=lambda v:v['reporting_year']):
                assert len(v['status_records'])==1
                st=v['status_records'][0]; labels={x['field']:x['label'] for x in v['status_labels']}
                insert('groundwater_body_version',dict(version_id=f"{gid}:{v['reporting_year']}",groundwater_body_id=gid,
                    reporting_year=v['reporting_year'],attributes_json=js({k:val for k,val in v.items() if k not in ['geometry','geometry_25833']}),
                    source_url=v['source']['url'],source_sha256=v['source']['sha256'],geometry_geojson=js(v['geometry']),
                    quantitative_status=st.get('gwQuantitativeStatusValue'),chemical_status=st.get('gwChemicalStatusValue'),
                    quantitative_status_label=labels['gwQuantitativeStatusValue'],chemical_status_label=labels['gwChemicalStatusValue'],
                    quantitative_assessment_period=st.get('gwQuantitativeAssessmentYear'),
                    chemical_assessment_period=st.get('gwChemicalAssessmentYear'),source_name=v['source_properties'].get('nameText'),
                    geometry_note='EEA consolidated reference geometry; reporting year is not a measurement date.',
                    enrichment_batch=BATCH), 'water_kg_extension')

        sources={s['file']:s for s in mon['sources']}
        source_keys={name:source('monitoring',name,r['sha256']) for name,r in sources.items()}
        params={(r.name,r.quality_component):r.parameter_id for r in conn.execute(text('SELECT * FROM water_kg_2.parameter'))}
        units={r.symbol:r.unit_id for r in conn.execute(text('SELECT * FROM water_kg_2.unit'))}

        def parameter(name,quality):
            key=(name,quality)
            if key not in params:
                params[key]=next_id('parameter','parameter_id')
                insert('parameter',dict(parameter_id=params[key],name=name,quality_component=quality))
            return params[key]

        def unit(symbol):
            if symbol not in units:
                units[symbol]=next_id('unit','unit_id')
                insert('unit',dict(unit_id=units[symbol],symbol=symbol))
            return units[symbol]

        site=mon['monitoring_site']; sid=next_id('monitoring_station','station_id')
        code=site['site_key']
        # Two reported sublocations are not assigned to individual 2026 samples.
        # Keep the old profile coordinates as evidence, not exact sample geometry.
        insert('monitoring_station',dict(station_id=sid,water_body_id=WB,name=site['name'],station_origin='observed_only',
            local_code=code,identity_scope=site['identity_scope'],coordinate_status='profile_coordinates_only; sample_subsite_unresolved',
            reported_coordinates_json=js({k:site[k] for k in ['reported_x','reported_y','reported_crs_text','coordinate_interpretation']}),
            **prov(sources[site['source_file']],site,'PDF page '+str(site['source_pdf_page']))))
        insert('station_alias',dict(alias_id=next_id('station_alias','alias_id'),station_id=sid,water_body_id=WB,
            alias_code=code,alias_type='local_code',source_file=source_keys[site['source_file']]))
        samples={}
        for n,sample in enumerate(mon['samples'],1):
            section_id=next_id('sampling_section','sampling_section_id')
            samples[sample['sample_id']]=(section_id,sample)
            insert('sampling_section',dict(sampling_section_id=section_id,source_file=source_keys[sample['source_file']],
                source_row_number=n,water_body_id=WB,station_id=sid,source_station_code=code,
                section_code=sample['sample_id'],sampling_year=int(sample['date'][:4]),sample_date=date.fromisoformat(sample['date']),
                **prov(sources[sample['source_file']],sample,sample['source_locator'])))
        for n,r in enumerate(mon['measurements'],1):
            section_id,sample=samples[r['sample_id']]
            assert (r['value'] is None)==r['censored']
            insert('measurement',dict(measurement_id=next_id('measurement','measurement_id'),
                source_file=source_keys[r['source_file']],source_row_number=n,source_record_id=r['measurement_key'],
                water_body_id=WB,station_id=sid,sampling_section_id=section_id,source_station_code=code,
                source_section_code=r['sample_id'],parameter_id=parameter(r['parameter'],'Badegewässerüberwachung'),
                unit_id=unit(r['unit']),measured_at=date.fromisoformat(sample['date']),value=r['value'],
                value_original=r['value_text'],comparison_operator=r['qualifier'],detection_limit=r['limit_value'],
                reported_censoring_bound=r['limit_value'],
                limit_kind='reported_censoring_bound; analytical_limit_type_unspecified' if r['censored'] else None,
                time_precision='day',measured_at_original=sample['date'],method=r['method'],
                monitoring_program='MS Sachsen-Anhalt: EU-Badegewässerüberwachung',source_topic='bathing_water',
                comment=site['caveat'],**prov(sources[r['source_file']],r,r['source_locator'])))

        counters=Counter()
        for r in mon['bathing_water_assessments']+mon['uba_waterbody_assessments']:
            file=r['source_file']; counters[file]+=1
            scope=r['assessment_scope']
            quality='Badegewässerqualität' if scope=='bathing_water' else 'UBA 2016: WRRL-Gewässerbewertung'
            locator=r.get('source_locator') or 'PDF page '+str(r['source_pdf_page'])
            insert('water_body_assessment',dict(assessment_id=next_id('water_body_assessment','assessment_id'),
                source_file=source_keys[file],source_row_number=counters[file],source_assessment_id=r['assessment_key'],
                water_body_id=WB,parameter_id=parameter(r['assessment_type'],quality),assessment_year=r['assessment_year'],
                value_raw=r['value_numeric'],value_normalized=None,is_valid=True,
                value_original=r['value_text'],value_label=r['value_text'],method=r.get('method'),
                assessment_scope=scope,assessment_caveat=r.get('caveat') or r.get('period_note'),
                validation_reason='Verified source value; textual class not converted into an invented numerical scale.',
                **prov(sources[file],r,locator)))

        def issue(entity,file,row,column,value,code,message):
            insert('import_issue',dict(issue_id=next_id('import_issue','issue_id'),entity_type=entity,
                source_file=file,source_row_number=row,source_column=column,source_value=value,issue_code=code,message=message))
        issue('monitoring_station',source_keys[site['source_file']],2,'monitoring_site',code,
            'BATHING_SUBSITE_AMBIGUITY',site['caveat'])
        for n,r in enumerate(mon['missing_values'],1):
            issue('measurement',source_keys[r['source_file']],n,r['parameter'],r['value_text'],
                'SOURCE_VALUE_MISSING',r['source_locator']+'; no numerical measurement created.')

        # All original rows and fields must remain byte-for-byte equivalent as
        # PostgreSQL JSON, including legacy geometry values and event tables.
        preservation=[]
        for (schema,table),(query,expected) in original.items():
            actual=Counter(conn.execute(text(query)).scalars())
            missing=expected-actual
            if missing:raise AssertionError(f'Original rows changed or removed: {schema}.{table}')
            preservation.append(dict(table=schema+'.'+table,original_rows=sum(expected.values()),unchanged=True))
        bad_geom=conn.execute(text('''SELECT count(*) FROM (
          SELECT geometry FROM water_kg_2.protected_area UNION ALL
          SELECT geometry FROM water_kg_2.groundwater_body) g
          WHERE NOT ST_IsValid(geometry) OR ST_SRID(geometry)<>25833''')).scalar_one()
        assert bad_geom==0
        assert sum(r['value'] is None for r in new['water_kg_2.measurement'])==7
        assert len(new['water_kg_2.measurement'])==14
        assert conn.execute(text('SELECT count(*) FROM water_events.event')).scalar_one()==73

    # Deliberately include exact inserted records as a reproducible import manifest.
    manifest={'status':'passed','batch':BATCH,'database':engine.url.database,
        'inserted_counts':{t:len(rows) for t,rows in new.items()},'inserted_rows':dict(new),
        'original_preservation':preservation,'original_data_unchanged':True,
        'limitations':mon['not_acquired']+[
            'Catchment and planning-unit polygons not obtained through a public authorized endpoint.',
            'No verified individual-substance WRRL list; ChemicalStatusV2/PollutantV2 remain unpopulated.',
            'No sediment-core series; Paleo* and CoreLocationV2 remain unpopulated.',
            'Spatial protected-area overlap is not a ProtectedAreaAssessmentV2.',
            'Bathing-water classes apply to the bathing water, not an individual station assessment.']}
    (OUT/'POSTGRES_DELTA.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2,default=str),encoding='utf8')
    (OUT/'POSTGRES_VALIDATION.json').write_text(json.dumps({k:v for k,v in manifest.items() if k!='inserted_rows'},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(manifest['inserted_counts'],ensure_ascii=False))
    engine.dispose();old_engine.dispose()


if __name__=='__main__':
    main()
