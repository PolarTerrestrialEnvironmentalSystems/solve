"""Build the original Dummy V2 schema in a NEW task-owned database only."""
from pathlib import Path
import importlib.util
import hashlib
import json
import sys
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import URL
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'outputs/dummy_neo4j_v2_20261101'
RUNTIME = OUT / 'runtime'
SOURCE = Path('C:/Users/jowals001/awi/solve/dummy_data')
DBNAME = 'solve_dummy_v2_20261101'


def load(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module


def main():
    RUNTIME.mkdir(parents=True,exist_ok=True)
    old_runtime=ROOT/'outputs/wasserblick33_20261031/runtime'
    pg=load('dummy_pg_runtime',old_runtime/'manage_test_pg.py')
    pg.check_marker()
    status=pg.call([pg.PG_BIN/'pg_ctl.exe','-D',pg.PG_DATA,'status'],'dummy-pg-status.log',check=False)
    if status.returncode==3:
        pg.call([pg.PG_BIN/'pg_ctl.exe','-D',pg.PG_DATA,'-l',pg.PG_LOG,'-o',f'-h 127.0.0.1 -p {pg.PORT}','-w','-t','60','start'],'dummy-pg-start.log')
    elif status.returncode:
        raise RuntimeError('Unexpected PostgreSQL status')
    password=pg.PWFILE.read_text(encoding='utf-8').strip()
    with psycopg.connect(host='127.0.0.1',port=pg.PORT,user=pg.DBUSER,password=password,dbname='postgres',autocommit=True) as conn:
        actual=Path(conn.execute("SELECT current_setting('data_directory')").fetchone()[0]).resolve()
        if actual!=pg.PG_DATA.resolve():raise RuntimeError('Wrong PostgreSQL data directory')
        if conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',(DBNAME,)).fetchone():
            raise RuntimeError('Target database already exists. Refusing replacement.')
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(DBNAME)))
    url=URL.create('postgresql+psycopg',username=pg.DBUSER,password=password,host='127.0.0.1',port=pg.PORT,database=DBNAME)
    pg.private_write(RUNTIME/'postgres.env','SOLVE_DATABASE_URL='+url.render_as_string(hide_password=False)+'\n')
    engine=create_engine(url,pool_pre_ping=True)
    assert not {'water_raw','water_kg_2','water_kg_2_build'} & set(inspect(engine).get_schema_names())
    snapshots=OUT/'source_scripts'
    snapshots.mkdir(exist_ok=True)
    scripts={}
    for name in ['raw_data_import.py','water_kg_2_import.py','neo4j_v2_import.py']:
        content=(ROOT/'scripts'/name).read_bytes()
        (snapshots/name).write_bytes(content)
        scripts[name]=hashlib.sha256(content).hexdigest()
    raw=load('dummy_raw_import',snapshots/'raw_data_import.py')
    normalized=load('dummy_normalized_import',snapshots/'water_kg_2_import.py')
    raw.DATA=SOURCE
    files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCE.iterdir() if p.is_file()}
    print('Importing original top-level dummy files into new PostgreSQL database',flush=True)
    raw_counts=raw.load_raw_sources(engine)
    errors=raw.validate_raw_sources(engine)
    if errors:raise RuntimeError(str(errors))
    print('Raw row/column checks passed; normalizing original V2 schema',flush=True)
    counts=normalized.publish_schema(engine,rebuild=False)
    errors=normalized.validate_schema(engine,'water_kg_2',counts)
    if errors:raise RuntimeError(str(errors))
    report={'database':DBNAME,'host':'127.0.0.1','port':pg.PORT,'source_directory':str(SOURCE),
            'scope':'Original top-level dummy CSV/SHP/XLSX files; fgg_data/apw_data not imported',
            'source_sha256':files,'importer_sha256':scripts,'raw_counts':raw_counts,'normalized_counts':counts,
            'raw_validation_passed':True,'normalized_validation_passed':True}
    (OUT/'POSTGRES_VALIDATION.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(counts,indent=2),flush=True)


if __name__=='__main__':main()
