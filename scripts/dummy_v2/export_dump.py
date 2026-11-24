"""Dump Dummy V2, perform a fresh offline restore, then restart its own runtime."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import time
from dotenv import dotenv_values
from neo4j import GraphDatabase

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/dummy_neo4j_v2_20261101'
RUNTIME=OUT/'runtime'
sys.path.insert(0,str(RUNTIME))
import manage_neo4j_preview as manager


def counts():
    config=dotenv_values(RUNTIME/'neo4j_preview/preview.env')
    expected=json.loads((OUT/'NEO4J_VALIDATION.json').read_text(encoding='utf-8'))
    with GraphDatabase.driver(config['NEO4J_URI'],auth=(config['NEO4J_USER'],config['NEO4J_PASSWORD'])) as driver:
        with driver.session(database='neo4j') as session:
            n=session.run('MATCH(n) RETURN count(n)').single()[0]
            r=session.run('MATCH()-[r]->() RETURN count(r)').single()[0]
            assert (n,r)==(expected['nodes'],expected['relationships'])
            for spec in expected['node_specs']:
                actual=session.run(f"MATCH(n:{spec['label']}) RETURN count(n)").single()[0]
                assert actual==expected['counts_by_label'][spec['label']]
    return {'nodes':n,'relationships':r}


def main():
    state=manager.marker();home=Path(state['home'])
    delivery=OUT/'export';verify=RUNTIME/'dump_restore_check'
    if delivery.exists() or verify.exists():raise RuntimeError('Export target already exists; refusing overwrite')
    delivery.mkdir();verify.mkdir()
    env=os.environ.copy()
    env.update(JAVA_HOME=state['java_home'],NEO4J_HOME=str(home),NEO4J_CONF=str(home/'conf'),TEMP=state['short_temp_dir'],TMP=state['short_temp_dir'])
    report={'before':counts(),'neo4j_version':'2026.10.1','system_database_included':False,'commands':[]}
    def admin(args,logname,timeout=300):
        print(logname,flush=True)
        start=time.monotonic()
        with (verify/logname).open('wb') as log:
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(home/'bin/neo4j-admin.ps1'),*args],
                                  cwd=home,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                                  creationflags=subprocess.CREATE_NO_WINDOW,timeout=timeout)
        report['commands'].append({'args':args,'exit_code':result.returncode,'seconds':round(time.monotonic()-start,2)})
        if result.returncode:raise RuntimeError('Inspect '+str(verify/logname))
    manager.stop()
    try:
        manager.check_free_ports()
        admin(['database','dump','neo4j',f'--to-path={delivery}','--overwrite-destination=false'],'dump.log')
        admin(['database','load','neo4j',f'--from-path={delivery}','--info'],'info.log')
        confdir=verify/'conf';confdir.mkdir()
        data=verify/'data';txns=verify/'transactions'
        (confdir/'neo4j.conf').write_text('\n'.join([
            f'server.directories.data={data.as_posix()}',f'server.directories.transaction.logs.root={txns.as_posix()}',
            f'server.directories.logs={(verify/"logs").as_posix()}',
            'server.memory.heap.initial_size=256m','server.memory.heap.max_size=768m','server.memory.pagecache.size=256m',
            f'server.jvm.additional=-Djdk.net.unixdomain.tmpdir={Path(state["short_temp_dir"]).as_posix()}',
            'dbms.usage_report.enabled=false','dbms.fleet_manager.enabled=false','server.fleet_discovery.enabled=false'])+'\n',encoding='utf-8')
        env['NEO4J_CONF']=str(confdir)
        admin(['database','load','neo4j',f'--from-path={delivery}','--overwrite-destination=false'],'restore.log')
        admin(['database','check','neo4j',f'--from-path-data={data/"databases"}',f'--from-path-txn={txns}',
               '--check-graph=true','--check-indexes=true','--check-counts=true','--check-property-owners=true',
               '--threads=2','--max-off-heap-memory=256m',f'--report-path={verify}'],'consistency.log',timeout=600)
    finally:
        manager.start()
    report['after_restart']=counts()
    assert report['before']==report['after_restart']
    dest=delivery/'dummy-waterkg-v2.dump'
    origin=delivery/'neo4j.dump'
    origin.resolve().relative_to(delivery.resolve())
    if dest.exists():raise RuntimeError('Dump destination exists')
    origin.rename(dest)
    with dest.open('rb') as f:report['sha256']=hashlib.file_digest(f,'sha256').hexdigest()
    report.update(status='passed',bytes=dest.stat().st_size,dump=dest.name,restored_store_consistency_passed=True)
    (delivery/'EXPORT_VALIDATION.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (delivery/'dummy-waterkg-v2.dump.sha256').write_text(report['sha256']+'  '+dest.name+'\n',encoding='ascii')
    print(json.dumps({'status':report['status'],'dump':str(dest),'bytes':report['bytes']}),flush=True)


if __name__=='__main__':main()
