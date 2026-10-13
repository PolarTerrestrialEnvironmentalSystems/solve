"""Plan by default. --execute alone authorizes connections and database writes.

Full source archive + lossless records + conservative semantic adapters.
No imports or reads from the old SOLVE database.
"""
import argparse
import csv
import io
import json
import math
import os
import re
from collections import Counter
from contextlib import contextmanager
from dbf_numeric import read_geodata
from datetime import datetime
from pathlib import Path
from common import ROOT, VERSION, digest, uid, read_jsonl, write_json, waterbody_id
from waterbody_matching import (WaterbodyMatcher, default_network, reference_files,
    identifiers_from_record, names_from_record, POLICY, RULE_VERSION)

DB_NAME='SOLVE Sachsen-Anhalt'
NEO_DB='solve-sachsen-anhalt'
PARSED_TABLES={'findings','relationships','quantities','evidence','mentions','entity_links','reviews','sources','issues','concepts'}
GEO_CLASSES={'Oberflächenwasserkörper':'solve:WaterBody','Grundwasserkörper':'solve:GroundwaterBody',
 'Punktuelle Maßnahmen':'solve:Intervention','Lineare Maßnahmen':'solve:Intervention',
 'Projektgebiete Gewässerentwicklungskonzepte':'solve:Region',
 'Wasser Einzelproben':'solve:MonitoringStation','Fliessgewässer':'solve:MonitoringStation',
 'Messstellen_Grundwasserbeschaffenheit':'solve:MonitoringStation','Messstellen_Grundwasserstand':'solve:MonitoringStation',
 'Pegel Wasserstand-Durchfluss (geprüfte Daten)':'solve:MonitoringStation'}
# Only byte-identical copies in these documented package pairs share records.
PACKAGE_PAIRS={
 'Physikalisch_Chemisch_Fliessgewaesser_Gesamt':['Physikalisch_Chemisch_Wasser_Einzelproben','Physikalisch_Chemisch_Wasser_Wochenmischproben','Physikalisch_Chemisch_SedimentEinzelproben','Physikalisch_Chemisch_SchwebstoffEinzelproben','Physikalisch_Chemisch_SchwebstoffMonatsMischproben'],
 'Physikalisch_Chemisch_Seen':['Physikalisch_Chemisch_Seen_Wasser','Physikalisch_Chemisch_Seen_Sediment']}


def validate_package(package):
    manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
    if set(manifest['files'])!={k+'.jsonl' for k in PARSED_TABLES}:raise ValueError('Unexpected processed tables')
    for name,sha in manifest['files'].items():
        if digest(package/name)!=sha:raise ValueError('Processed package hash mismatch: '+name)
        if sum(1 for _ in read_jsonl(package/name))!=manifest['counts'][Path(name).stem]:raise ValueError('Processed count mismatch')
    return manifest


def make_plan(source,package,neo4j_existing_database=None):
    if neo4j_existing_database is not None and (not neo4j_existing_database.strip() or neo4j_existing_database.lower()=='system'):
        raise ValueError('Choose an existing standard Neo4j database, not system')
    manifest=validate_package(package)
    files=[]
    for path in sorted(source.rglob('*')):
        if not path.is_file():continue
        if path.is_symlink():raise ValueError('Symlink not allowed in source inventory')
        files.append(dict(path=path.relative_to(source).as_posix(),sha256=digest(path),bytes=path.stat().st_size))
    if not files:raise ValueError('Empty source directory')
    by_path={f['path']:f for f in files}
    for f in files:
        path=Path(f['path']); f['adapter']='archive'
        if path.suffix.lower()=='.csv':f['adapter']='csv'
        elif path.suffix.lower() in {'.shp','.geojson'}:f['adapter']='geo'
        elif path.suffix.lower()=='.dbf' and path.with_suffix('.shp').as_posix() not in by_path:f['adapter']='dbf'
        f['canonical_path']=f['path']
        if path.parts[0] in PACKAGE_PAIRS:
            for folder in PACKAGE_PAIRS[path.parts[0]]:
                alt=Path(folder,*path.parts[1:]).as_posix()
                if alt in by_path and by_path[alt]['sha256']==f['sha256']:
                    f['canonical_path']=alt;break
    code_hashes={f:digest(Path(__file__).with_name(f)) for f in ['load_database.py','common.py','schema.sql','waterbody_matching.py','dbf_numeric.py']}
    reference=source/'Oberflächenwasserkörper/Oberflächenwasserkörper.shp'
    matching=None
    if reference.exists():
        network=default_network(reference)
        fingerprint=uid(RULE_VERSION,POLICY,[(p.name,digest(p)) for p in reference_files(reference,network)])
        matching=dict(policy=POLICY,reference=str(reference.relative_to(source)),
                      network=str(network.relative_to(source)) if network else None,reference_fingerprint=fingerprint)
        package_fingerprint=manifest.get('waterbody_matching',{}).get('reference_fingerprint')
        if package_fingerprint and package_fingerprint!=fingerprint:
            raise ValueError('Processing and import waterbody reference versions differ; reprocess with the target references')
    neo_target=neo4j_existing_database or NEO_DB
    neo_mode='existing' if neo4j_existing_database else 'create_separate'
    return dict(version=VERSION,database=DB_NAME,neo4j_database=neo_target,neo4j_mode=neo_mode,code_hashes=code_hashes,
        field_definitions=dict(id='user_status_years_v1',authority='explicit_user_definition',
          P='Planung abgeschlossen; Jahre beschreiben Planungszeitraum',B='Planung und Bau abgeschlossen; Jahre beschreiben gemeinsamen Zeitraum'),
        batch_id=uid(VERSION,manifest['batch_id'],files,code_hashes,neo_target,neo_mode),source_root=str(source.resolve()),processed_package=str(package.resolve()),
        processing_manifest=manifest,waterbody_matching=matching,files=files,counts=dict(files=len(files),bytes=sum(f['bytes'] for f in files),
        adapters=dict(Counter(f['adapter'] for f in files)),duplicate_occurrences=sum(f['canonical_path']!=f['path'] for f in files)),
        execution='not_executed',semantics='All files archived; CSV/DBF/geodata rows queryable. Known adapters normalized; other semantics remain source records.')


def decode(data):
    for encoding in ['utf-8-sig','cp1252']:
        try:return data.decode(encoding),encoding
        except UnicodeDecodeError:pass
    raise ValueError('Unsupported text encoding; no lossy replacement permitted')


def numeric(raw,note=''):
    value=raw.strip(); match=re.match(r'^(<=|>=|<|>|≤|≥|~|≈)\s*(.*)$',value)
    qualifier='='
    if match:qualifier,value=match.groups()
    if 'kleiner bestimmungsgrenze' in note.lower():qualifier='<'
    try:
        n=float(value.replace(',','.'))
        if not math.isfinite(n):raise ValueError()
        return n,qualifier
    except ValueError:return None,qualifier if value or note else None


def date(raw):
    for fmt in ['%d.%m.%Y %H:%M:%S','%d.%m.%Y','%Y-%m-%d %H:%M:%S','%Y-%m-%d']:
        try:return datetime.strptime(raw,fmt)
        except (ValueError,TypeError):pass
    return None


def observations(headers,row,path,record_id,metadata=None):
    """Retain parameter forms, source units and column ordinals; no inferred conversion."""
    if 'Zeitpunkt' not in headers:return
    stamp=row[headers.index('Zeitpunkt')]
    family=Path(path).parts[0]
    station=Path(path).stem.removeprefix('daten_')
    for i,h in enumerate(headers):
        match=re.fullmatch(r'(.+) \[([^\]]+)\]',h)
        if not match:continue
        note=row[i+1] if i+1<len(headers) and headers[i+1]=='Hinweise' else ''
        if not row[i].strip() and not note.strip():continue
        number,qualifier=numeric(row[i],note)
        yield (uid(record_id,i),record_id,i+1,'solve:Observation',station,match[1],match[2],row[i],number,qualifier,note,
               stamp,date(stamp),dict(source_family=family,metadata=metadata or {},parameter_mapping='source_term_only',
                                      method_depth_fraction='see_source_metadata'),
               'source_not_individually_reviewed' if number is not None and date(stamp) else 'value_or_time_requires_review')


def connect_admin():
    import psycopg
    from psycopg.conninfo import make_conninfo
    # Never reuse SOLVE_DATABASE_URL or select the legacy database implicitly.
    dsn=os.environ.get('SOLVE_ADMIN_DSN','')
    return psycopg.connect(make_conninfo(dsn,dbname='postgres'),autocommit=True)


def create_target():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    with connect_admin() as conn:
        if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',(DB_NAME,)).fetchone():
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(DB_NAME)))
    return psycopg.connect(make_conninfo(os.environ.get('SOLVE_ADMIN_DSN',''),dbname=DB_NAME))


def save_assignment(conn,batch,owner_type,owner_id,result):
    from psycopg.types.json import Jsonb
    conn.execute('INSERT INTO solve.waterbody_assignment VALUES (%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',
                 (uid(batch,owner_type,owner_id),batch,owner_type,owner_id,Jsonb(result)))


class CsvReader:
    """Strict CSV with a recorded fallback for fully quoted single-line exports."""
    def __init__(self,reader,stream,delimiter,width,quoted_header):
        self.reader=reader;self.stream=stream;self.delimiter=delimiter
        self.width=width;self.quoted_header=quoted_header;self.issues={}
        self.ordinal=1

    @property
    def line_num(self):
        return self.reader.line_num

    def __iter__(self):
        return self

    def __next__(self):
        start=self.stream.tell()
        try:
            row=next(self.reader)
        except csv.Error:
            raw=self.stream.getvalue()[start:self.stream.tell()]
            line=raw.rstrip('\r\n')
            # No multiline repair, column padding, truncation or quote guessing.
            if not self.quoted_header or '\n' in line or '\r' in line or not (line.startswith('"') and line.endswith('"')):
                raise
            fields=line[1:-1].split('"'+self.delimiter+'"')
            if len(fields)!=self.width:
                raise
            row=[field.replace('""','"') for field in fields]
            self.issues[self.ordinal+1]=dict(status='nonstandard_source_quoting',
                method='fully_quoted_single_line_boundaries',raw_csv_record=raw,
                physical_line=self.line_num)
        self.ordinal+=1
        return row


def csv_input(content,path):
    """Detect separators from the header, never from decimal commas in values.

    Use the CSV parser for quoted separators and multiline fields. Keep ordered
    headers, including duplicates and an empty index-column header.
    """
    candidates=[]
    for delimiter in (';',',','\t'):
        stream=io.StringIO(content,newline='')
        reader=csv.reader(stream,delimiter=delimiter,strict=True)
        try:
            headers=next(reader,[])
        except csv.Error:
            continue
        candidates.append((len(headers),delimiter,headers,reader,stream))
    if not candidates:
        raise ValueError(f'Invalid CSV header: {path}')
    width=max(c[0] for c in candidates)
    choices=[c for c in candidates if c[0]==width]
    if width>1 and len(choices)>1:
        raise ValueError(f'Ambiguous CSV delimiter in header: {path}')
    _,delimiter,headers,reader,stream=choices[0]
    raw_header=content[:stream.tell()].rstrip('\r\n')
    quoted_header=raw_header.startswith('"') and raw_header.endswith('"') and len(raw_header[1:-1].split('"'+delimiter+'"'))==len(headers)
    return CsvReader(reader,stream,delimiter,len(headers),quoted_header),headers,delimiter


def csv_rows(reader,headers,path):
    try:
        for ordinal,row in enumerate(reader,2):
            if len(row)!=len(headers):
                raise ValueError(f'CSV width mismatch {path}: record {ordinal}, physical line {reader.line_num}; expected {len(headers)} fields, got {len(row)}')
            yield ordinal,row
    except csv.Error as error:
        raise ValueError(f'Invalid CSV quoting {path}: physical line {reader.line_num}: {error}') from error


def import_csv(conn,path,file_id,dataset_id,relative_path,matcher=None,batch=None):
    from psycopg.types.json import Jsonb
    content,encoding=decode(path.read_bytes())
    reader,headers,delimiter=csv_input(content,path)
    conn.execute('UPDATE ingest.dataset SET headers=%s WHERE id=%s',(Jsonb(dict(columns=headers,encoding=encoding,delimiter=delimiter)),dataset_id))
    md={};metadata_issues={}
    if path.name.startswith('daten_'):
        mp=path.with_name('metadaten_'+path.name[6:])
        if mp.exists():
            mr,mh,_=csv_input(decode(mp.read_bytes())[0],mp)
            md={r[0]:r[1:] for _,r in csv_rows(mr,mh,mp) if r}
            metadata_issues=mr.issues
    matching_metadata={key:values[0] for key,values in md.items() if values}
    if matcher and (names_from_record(matching_metadata) or identifiers_from_record(matching_metadata)):
        # Metadata coordinates have no explicit CRS: do not guess one.
        result=matcher.match_record(matching_metadata,historical=any(re.fullmatch(r'20\d{2}',p) for p in Path(relative_path).parts))
        result['source_metadata']=matching_metadata
        save_assignment(conn,batch,'dataset',dataset_id,result)
    n=0;buffer=[]
    # COPY raw and observations in alternating bounded batches.
    def flush(rows):
        with conn.cursor().copy('COPY ingest.record(id,dataset_id,ordinal,payload) FROM STDIN') as cp:
            for rid,ordinal,row in rows:
                payload=dict(columns=headers,values=row)
                if ordinal in reader.issues:payload['csv_parse_issue']=reader.issues[ordinal]
                cp.write_row((rid,dataset_id,ordinal,Jsonb(payload)))
        with conn.cursor().copy('COPY solve.observation(id,record_id,ordinal,target_class,station_code,parameter_original,unit_original,value_original,value_numeric,qualifier,note_original,time_original,observed_at,context,quality_status) FROM STDIN') as cp:
            for rid,ordinal,row in rows:
                for obs in observations(headers,row,relative_path,rid,md):
                    obs=list(obs);obs[-2]=Jsonb(obs[-2]);cp.write_row(obs)
                if path.name.lower()=='messwerte.csv' and 'Taxon' in headers:
                    values=dict(zip(headers,row));stamp=values.get('Datum')
                    cp.write_row((uid(rid,'bio'),rid,0,'solve:BiologicalObservation',values.get('Mst_Nr_Bio',values.get('ID')),None,None,None,None,None,None,
                      stamp,date(stamp),Jsonb(dict(values=values,source_family=path.parent.name,taxon_mapping='unreviewed',quantity_fields='original_columns')),'biological_row_mapping_requires_review'))
    for n,row in csv_rows(reader,headers,path):
        buffer.append((uid(dataset_id,n),n,row))
        if len(buffer)>=1000:flush(buffer);buffer=[]
    if buffer:flush(buffer)
    if reader.issues or metadata_issues:
        conn.execute('UPDATE ingest.dataset SET headers=headers || %s WHERE id=%s',
            (Jsonb(dict(csv_parse_issue_count=len(reader.issues),metadata_csv_issues=metadata_issues)),dataset_id))
    return max(0,n-1)


def import_geo(conn,path,dataset_id,matcher=None,batch=None):
    import geopandas as gpd
    from psycopg.types.json import Jsonb
    df,dbf_issues,gdal_warnings=read_geodata(path)
    spatial=isinstance(df,gpd.GeoDataFrame) and 'geometry' in df.columns
    crs=str(df.crs) if spatial and df.crs else None
    attributes=df.drop(columns='geometry') if spatial else df
    rows=json.loads(attributes.to_json(orient='records',date_format='iso'))
    for position,issues in dbf_issues.items():
        if '_dbf_parse_issues' in rows[position]:raise ValueError('Reserved DBF provenance key collision')
        rows[position]['_dbf_parse_issues']=issues
    conn.execute('UPDATE ingest.dataset SET headers=%s,crs_original=%s WHERE id=%s',(Jsonb(list(attributes.columns)),crs,dataset_id))
    transformed=df.to_crs(25833) if crs else None
    category=GEO_CLASSES.get(path.parent.name,'solve:SourceRecord')
    for n,raw in enumerate(rows,1):
        rid=uid(dataset_id,n)
        conn.execute('INSERT INTO ingest.record VALUES (%s,%s,%s,%s)',(rid,dataset_id,n,Jsonb(raw)))
        code=raw.get('owk_st') if path.parent.name=='Oberflächenwasserkörper' else None
        geometry=transformed.geometry.iloc[n-1] if transformed is not None else None
        wkt=geometry.wkt if geometry is not None and not geometry.is_empty else None
        status='source_record_mapping_open' if category=='solve:SourceRecord' else 'source_data_not_individually_reviewed'
        if spatial and not crs:status='missing_crs_original_geometry_archived'
        if geometry is not None and not geometry.is_valid:status='invalid_geometry_not_repaired'
        if n-1 in dbf_issues:status='dbf_values_require_review'
        if gdal_warnings:
            raw['_gdal_read_warnings']=gdal_warnings
            if status=='source_data_not_individually_reviewed':status='gdal_read_warnings_require_review'
        conn.execute('INSERT INTO solve.feature VALUES (%s,%s,%s,%s,%s,ST_GeomFromText(%s,25833),%s,%s)',
          (rid,rid,category,code,waterbody_id(code) if code else None,wkt,status,Jsonb(raw)))
        if (matcher and path.parent.name not in {'Oberflächenwasserkörper','Gewässernetz'}
                and 'Grundwasser' not in path.parent.name
                and (names_from_record(raw) or identifiers_from_record(raw)
                     or category in {'solve:MonitoringStation','solve:Intervention'})):
            match_geometry=geometry if transformed is not None else df.geometry.iloc[n-1] if spatial else None
            result=matcher.match_record(raw,match_geometry,'EPSG:25833' if transformed is not None else None,
                historical=any(re.fullmatch(r'20\d{2}',p) for p in path.parts))
            save_assignment(conn,batch,'feature',rid,result)
    return len(rows)


def execute(plan,postgres_only=False):
    from psycopg.types.json import Jsonb
    source=Path(plan['source_root']);package=Path(plan['processed_package']);batch=plan['batch_id']
    # Recheck BEFORE the first database connection.
    validate_package(package)
    for f in plan['files']:
        if digest(source/f['path'])!=f['sha256']:raise ValueError('Source changed since planning')
    if not postgres_only:
        check_neo4j(plan)
    matching=plan.get('waterbody_matching')
    matcher=WaterbodyMatcher(source/matching['reference'],source/matching['network'] if matching['network'] else None) if matching else None
    companion_hashes={}
    for f in plan['files']:
        companion_hashes.setdefault(Path(f['path']).with_suffix(''),[]).append(f['sha256'])
    with create_target() as conn:
        if conn.info.dbname!=DB_NAME:raise ValueError('Wrong target database')
        conn.execute((Path(__file__).with_name('schema.sql')).read_text(encoding='utf-8'))
        conn.execute("INSERT INTO ingest.batch(id,status,manifest) VALUES (%s,'loading',%s) ON CONFLICT(id) DO UPDATE SET status='loading'",(batch,Jsonb(plan)))
        conn.commit()
        file_ids={}
        for f in plan['files']:
            fid=uid(f['path'],f['sha256']);file_ids[f['path']]=fid
            with conn.transaction():
                if not conn.execute('SELECT 1 FROM ingest.blob WHERE sha256=%s',(f['sha256'],)).fetchone():
                    data=(source/f['path']).read_bytes()
                    import hashlib
                    if hashlib.sha256(data).hexdigest()!=f['sha256']:raise ValueError('Source changed during load')
                    conn.execute('INSERT INTO ingest.blob VALUES (%s,%s) ON CONFLICT DO NOTHING',(f['sha256'],data))
                conn.execute('INSERT INTO ingest.file VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',(fid,f['path'],f['sha256'],f['bytes']))
                conn.execute('INSERT INTO ingest.batch_file VALUES (%s,%s) ON CONFLICT DO NOTHING',(batch,fid))
        for f in plan['files']:
            if f['adapter']=='archive' or f['canonical_path']!=f['path']:continue
            fid=file_ids[f['path']]
            # Sidecar changes produce a new dataset version too.
            stem=Path(f['path']).with_suffix('')
            companions=list(companion_hashes[stem])
            source_path=Path(f['path'])
            if source_path.name.startswith('daten_'):
                meta=source_path.with_name('metadaten_'+source_path.name[6:]).with_suffix('')
                companions.extend(companion_hashes.get(meta,[]))
            did=uid(fid,companions,VERSION,plan['code_hashes'],matching['reference_fingerprint'] if matching else None)
            with conn.transaction():
                old=conn.execute('SELECT complete FROM ingest.dataset WHERE id=%s',(did,)).fetchone()
                if old and old[0]:
                    conn.execute('INSERT INTO ingest.batch_dataset VALUES (%s,%s) ON CONFLICT DO NOTHING',(batch,did))
                    # Reused dataset versions retain their assignments in the new batch.
                    for owner_type,owner_id,payload in conn.execute('''SELECT DISTINCT ON (a.owner_type,a.owner_id)
                        a.owner_type,a.owner_id,a.payload FROM solve.waterbody_assignment a
                        WHERE (a.owner_type='dataset' AND a.owner_id=%s) OR
                        (a.owner_type='feature' AND a.owner_id IN (SELECT id FROM ingest.record WHERE dataset_id=%s))
                        ORDER BY a.owner_type,a.owner_id,a.id''',(did,did)).fetchall():
                        save_assignment(conn,batch,owner_type,owner_id,payload)
                    continue
                conn.execute('INSERT INTO ingest.dataset(id,file_id,family) VALUES (%s,%s,%s)',(did,fid,Path(f['path']).parts[0]))
                path=source/f['path']
                n=import_csv(conn,path,fid,did,f['path'],matcher,batch) if f['adapter']=='csv' else import_geo(conn,path,did,matcher,batch)
                conn.execute('UPDATE ingest.dataset SET complete=true,row_count=%s WHERE id=%s',(n,did))
                conn.execute('INSERT INTO ingest.batch_dataset VALUES (%s,%s) ON CONFLICT DO NOTHING',(batch,did))
        with conn.transaction():
            for table in sorted(PARSED_TABLES):
                for row in read_jsonl(package/(table+'.jsonl')):
                    conn.execute('INSERT INTO solve.processed VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(uid(batch,table,row['id']),batch,table,row['id'],Jsonb(row)))
            count=conn.execute('SELECT count(*) FROM ingest.batch_file WHERE batch_id=%s',(batch,)).fetchone()[0]
            if count!=len(plan['files']):raise ValueError('Incomplete file coverage')
            count=conn.execute('SELECT count(*) FROM solve.processed WHERE batch_id=%s',(batch,)).fetchone()[0]
            if count!=sum(plan['processing_manifest']['counts'].values()):raise ValueError('Incomplete processed package')
            conn.execute("UPDATE ingest.batch SET status='postgres_complete' WHERE id=%s",(batch,))
        if not postgres_only:
            project_neo4j(conn,batch,plan.get('neo4j_database',NEO_DB),plan.get('neo4j_mode')=='existing')
            conn.execute("UPDATE ingest.batch SET status='complete' WHERE id=%s",(batch,))
            conn.commit()


def neo4j_driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(os.getenv('NEO4J_URI','bolt://localhost:7687'),auth=(os.getenv('NEO4J_USER','neo4j'),os.environ['NEO4J_PASSWORD']))


def check_neo4j(plan):
    """Read-only preflight before PostgreSQL writes; not a write-permission test."""
    driver=neo4j_driver()
    try:
        driver.verify_connectivity()
        if plan.get('neo4j_mode')=='existing':
            with driver.session(database=plan['neo4j_database']) as session:
                session.run('RETURN 1 AS connection_ok').consume()
        else:
            with driver.session(database='system') as session:
                row=session.run("CALL dbms.components() YIELD name, edition WHERE name = 'Neo4j Kernel' RETURN edition").single()
                if not row or row['edition'].lower()!='enterprise':
                    raise ValueError('Separate Neo4j database creation requires Enterprise. For Community use --neo4j-existing-database neo4j, or --postgres-only.')
    finally:
        driver.close()


@contextmanager
def neo4j_target(database,existing):
    if database.lower()=='system' or not database.strip():
        raise ValueError('A standard Neo4j database is required')
    if not existing and database!=NEO_DB:
        raise ValueError('Unexpected database creation target')
    driver=neo4j_driver()
    try:
        if not existing:
            with driver.session(database='system') as session:
                session.run('CREATE DATABASE `solve-sachsen-anhalt` IF NOT EXISTS WAIT').consume()
        with driver.session(database=database) as session:
            yield session
    finally:
        driver.close()


def project_neo4j(conn,batch,database=NEO_DB,existing=False):
    with neo4j_target(database,existing) as session:
        session.run('CREATE CONSTRAINT solve_record_id IF NOT EXISTS FOR (n:SOLVERecord) REQUIRE n.id IS UNIQUE').consume()
        cur=conn.execute("SELECT table_name,record_id,payload FROM solve.processed WHERE batch_id=%s",(batch,))
        for table,rid,payload in cur:
            node=dict(id=uid(batch,rid),logical_id=rid,batch_id=batch,kind=table,target_class=payload.get('target_class'),
                statement=payload.get('statement'),review_status=payload.get('statement_review_status',payload.get('review_status')),
                classification_review_status=payload.get('classification_review_status'),warning=payload.get('warning'),sql_database=DB_NAME)
            session.run('MERGE (n:SOLVERecord {id:$id}) SET n += $props',id=node['id'],props={k:v for k,v in node.items() if v is not None}).consume()
        for rid,payload in conn.execute("SELECT record_id,payload FROM solve.processed WHERE batch_id=%s",(batch,)):
            fields={'finding_id':'ABOUT_FINDING','source_id':'SOURCE_REFERENCE','target_id':'TARGET_REFERENCE','owner_id':'EVIDENCE_FOR','mention_id':'MENTION_REFERENCE'}
            for field,kind in fields.items():
                if payload.get(field):
                    session.run('MATCH (a:SOLVERecord {id:$a}),(b:SOLVERecord {id:$b}) MERGE (a)-[r:SOLVE_LINK {kind:$kind}]->(b)',
                                a=uid(batch,rid),b=uid(batch,payload[field]),kind=kind).consume()
        for rid,entity_id,cls in conn.execute('SELECT f.id,f.entity_id,f.target_class FROM solve.feature f JOIN ingest.record r ON r.id=f.record_id JOIN ingest.batch_dataset bd ON bd.dataset_id=r.dataset_id WHERE bd.batch_id=%s',(batch,)):
            session.run('MERGE (n:SOLVERecord {id:$id}) SET n.batch_id=$batch,n.logical_id=$logical,n.kind="feature",n.target_class=$cls,n.sql_database=$db',id=uid(batch,'feature',rid),batch=batch,logical=entity_id or rid,cls=cls,db=DB_NAME).consume()
        for rid,payload in conn.execute("SELECT record_id,payload FROM solve.processed WHERE batch_id=%s AND table_name='entity_links'",(batch,)):
            session.run('MATCH (a:SOLVERecord {id:$id}),(b:SOLVERecord {batch_id:$batch,logical_id:$target}) MERGE (a)-[r:SOLVE_LINK {kind:$kind}]->(b) SET r.status=$status,r.method=$method,r.assignment_status=$assignment_status',
                id=uid(batch,rid),batch=batch,target=payload['target_entity_id'],
                kind='ASSIGNED_WATERBODY' if payload.get('assignment_status')=='rule_assigned' else 'POSSIBLE_WATERBODY',
                status=payload['status'],method=payload['method'],assignment_status=payload.get('assignment_status','candidate')).consume()
        for aid,owner_type,owner_id,payload in conn.execute('SELECT id,owner_type,owner_id,payload FROM solve.waterbody_assignment WHERE batch_id=%s',(batch,)):
            session.run('MERGE (n:SOLVERecord {id:$id}) SET n.batch_id=$batch,n.kind="waterbody_assignment",n.owner_type=$owner_type,n.owner_id=$owner_id,n.assignment_status=$status,n.sql_database=$db',
                id=aid,batch=batch,owner_type=owner_type,owner_id=owner_id,status=payload['status'],db=DB_NAME).consume()
            if owner_type=='feature':
                session.run('MATCH (a:SOLVERecord {id:$a}),(b:SOLVERecord {id:$b}) MERGE (a)-[:SOLVE_LINK {kind:"ASSIGNMENT_FOR"}]->(b)',a=aid,b=uid(batch,'feature',owner_id)).consume()
            for link in payload['links']:
                session.run('MATCH (a:SOLVERecord {id:$a}),(b:SOLVERecord {batch_id:$batch,logical_id:$target}) MERGE (a)-[r:SOLVE_LINK {kind:$kind}]->(b) SET r.status=$status,r.method=$method,r.assignment_status=$assignment_status',
                    a=aid,batch=batch,target=link['target_entity_id'],status=link['status'],method=link['method'],assignment_status=link['assignment_status'],
                    kind='ASSIGNED_WATERBODY' if link['assignment_status']=='rule_assigned' else 'POSSIBLE_WATERBODY').consume()


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path,default=ROOT/'Sachsen_Anhalt')
    ap.add_argument('--processed',type=Path)
    ap.add_argument('--plan',type=Path,default=ROOT/'output/migration_plan.json')
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--postgres-only',action='store_true',help='Explicitly defer Neo4j; keep PostgreSQL status postgres_complete')
    ap.add_argument('--neo4j-existing-database',help='Explicitly use this existing standard database without CREATE DATABASE (Community: neo4j)')
    args=ap.parse_args(argv)
    if args.processed is None:
        base=ROOT/'output/processed_parsing';latest=json.loads((base/'latest.json').read_text(encoding='utf-8'))
        args.processed=base/latest['batch_dir']
        if digest(args.processed/'manifest.json')!=latest['manifest_sha256']:raise ValueError('Latest manifest changed')
    plan=make_plan(args.source,args.processed,args.neo4j_existing_database)
    args.plan.parent.mkdir(parents=True,exist_ok=True);write_json(args.plan,plan)
    print(json.dumps(dict(database=DB_NAME,counts=plan['counts'],execute=args.execute),ensure_ascii=True),flush=True)
    if args.execute:
        execute(plan,args.postgres_only)
        plan['execution']='postgres_complete' if args.postgres_only else 'complete';write_json(args.plan,plan)
    else:print('Plan only. No database connection, database creation or import performed.')


if __name__=='__main__':main()
