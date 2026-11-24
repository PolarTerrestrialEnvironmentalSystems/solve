"""Create a NEW enrichment DB via pg_dump/pg_restore; leave all existing DBs intact."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from dotenv import dotenv_values
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/bergwitzsee_ergaenzung_20261102'
RUNTIME=OUT/'runtime'
BASE=ROOT/'outputs/bergwitzsee_neo4j_v2_events_20261101'
NAME='solve_bergwitzsee_v2_enriched_20261102'


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module


def fingerprint(connection):
    tables=connection.execute("SELECT table_schema,table_name FROM information_schema.tables WHERE table_type='BASE TABLE' AND table_schema IN ('water_raw','water_kg_2','water_events') ORDER BY table_schema,table_name").fetchall()
    result={}
    for schema,table in tables:
        count=0;digest=hashlib.sha256()
        query=sql.SQL('SELECT md5(row_to_json(t)::text) AS h FROM {}.{} t ORDER BY h').format(sql.Identifier(schema),sql.Identifier(table))
        for row in connection.execute(query):
            digest.update((row[0]+'\n').encode());count+=1
        result[schema+'.'+table]={'rows':count,'ordered_row_hash_sha256':digest.hexdigest()}
    return result


def main():
    RUNTIME.mkdir(parents=True,exist_ok=True)
    pg=load('enrichment_owned_pg',ROOT/'outputs/wasserblick33_20261031/runtime/manage_test_pg.py')
    pg.check_marker()
    status=pg.call([pg.PG_BIN/'pg_ctl.exe','-D',pg.PG_DATA,'status'],'enrichment-pg-status.log',check=False)
    if status.returncode==3:
        pg.call([pg.PG_BIN/'pg_ctl.exe','-D',pg.PG_DATA,'-l',pg.PG_LOG,'-o',f'-h 127.0.0.1 -p {pg.PORT}','-w','-t','60','start'],'enrichment-pg-start.log')
    elif status.returncode:raise RuntimeError('Unexpected cluster status')
    old=make_url(dotenv_values(BASE/'runtime/postgres.env')['SOLVE_DATABASE_URL'])
    assert old.database=='solve_bergwitzsee_v2_events_20261101' and old.host=='127.0.0.1' and old.port==55439
    kwargs=dict(host=old.host,port=old.port,user=old.username,password=old.password)
    with psycopg.connect(**kwargs,dbname='postgres',autocommit=True) as conn:
        assert Path(conn.execute("SELECT current_setting('data_directory')").fetchone()[0]).resolve()==pg.PG_DATA.resolve()
        if conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',(NAME,)).fetchone():
            raise RuntimeError('Enrichment database exists; refusing replacement')
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(NAME)))
    backup=RUNTIME/'source_before_enrichment.backup'
    if backup.exists():raise RuntimeError('Source snapshot already exists; refusing overwrite')
    env=os.environ.copy();env['PGPASSWORD']=old.password
    def run(args,logname):
        with (RUNTIME/logname).open('wb') as log:
            subprocess.run([str(a) for a in args],env=env,stdout=log,stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW,check=True,timeout=180)
    shared=['-h',old.host,'-p',str(old.port),'-U',old.username]
    run([pg.PG_BIN/'pg_dump.exe',*shared,'-d',old.database,'-Fc','--no-owner','--no-acl','-f',backup],'source_pg_dump.log')
    run([pg.PG_BIN/'pg_restore.exe',*shared,'-d',NAME,'--no-owner','--no-acl','--exit-on-error',backup],'clone_pg_restore.log')
    with psycopg.connect(**kwargs,dbname=old.database) as a,psycopg.connect(**kwargs,dbname=NAME) as b:
        before=fingerprint(a);after=fingerprint(b)
    assert before==after,'PostgreSQL clone differs from original source'
    pg.private_write(RUNTIME/'postgres.env','SOLVE_DATABASE_URL='+old.set(database=NAME).render_as_string(hide_password=False)+'\n')
    report={'status':'passed','source_database':old.database,'target_database':NAME,
        'source_dump_sha256':hashlib.sha256(backup.read_bytes()).hexdigest(),
        'full_row_fingerprints_equal':True,'tables':before}
    (OUT/'POSTGRES_BASELINE.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({'status':'passed','database':NAME,'tables':len(before),'rows':sum(r['rows'] for r in before.values())}))


if __name__=='__main__':main()
