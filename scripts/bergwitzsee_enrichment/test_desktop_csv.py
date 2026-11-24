"""Exercise Desktop CSV exact validator and importer, then roll back all test writes."""
import json
import shutil
import time
from dotenv import dotenv_values
from neo4j import GraphDatabase
from import_delta import ident,decode
from snapshot_baseline import OUT,canonical
from export_desktop_csv import DEST,FOLDER


def comparable(value):
    if isinstance(value,list):return [comparable(v) for v in value]
    if isinstance(value,dict):return {k:(v.lower() if k=='__type__' else comparable(v)) for k,v in value.items()}
    return value


def main():
    cfg=dotenv_values(OUT/'runtime/neo4j_preview/preview.env')
    assert cfg['NEO4J_URI']=='bolt://127.0.0.1:7692'
    target=OUT/'runtime/neo4j_preview/neo4j-community-2026.10.1/import'/FOLDER
    target.mkdir(parents=True,exist_ok=True)
    for p in DEST.glob('*.csv'):shutil.copy2(p,target/p.name)
    plan=json.loads((OUT/'GRAPH_DELTA.json').read_text(encoding='utf8'))
    validators=[p.read_text(encoding='utf8') for p in sorted(DEST.glob('01_part_*_validate_baseline.cypher'))]
    assert len(validators)==4
    apply=(DEST/'03_apply_additive_delta.cypher').read_text(encoding='utf8')
    report={'status':'running','port':7692,'test_writes_rolled_back':False}
    with GraphDatabase.driver(cfg['NEO4J_URI'],auth=(cfg['NEO4J_USER'],cfg['NEO4J_PASSWORD'])) as driver:
        with driver.session(database='neo4j') as session:
            # Older query-generator revisions can retain very large compiled
            # plans in this task-owned test runtime. Clear only its plan cache.
            session.run('CALL db.clearQueryCaches()').consume()
            for query in [*validators,apply]:session.run('EXPLAIN '+query).consume()
            print('Four baseline checks and import query compile.',flush=True)
            # Validate the count guard's actual database semantics, including no
            # creation surviving a rejected transaction. Use a distinct test label.
            try:
                with session.begin_transaction() as tx:
                    tx.run('CREATE(n:SolveRollbackProbe {test:"csv-import"}) RETURN CASE WHEN false THEN true ELSE (1/0=0) END AS must_abort').consume()
                    raise AssertionError('Division by zero did not abort')
            except Exception as exc:
                if getattr(exc,'code',None)!='Neo.ClientError.Statement.ArithmeticError':raise
                report['count_guard_error_code']=exc.code
            assert session.run('MATCH(n:SolveRollbackProbe) RETURN count(n)').single()[0]==0
            tx=session.begin_transaction(timeout=900)
            try:
                tx.run('MATCH()-[r]->() WHERE r.import_run_id=$run DELETE r',run=plan['run_id']).consume()
                tx.run('MATCH(n) WHERE n.import_run_id=$run DETACH DELETE n',run=plan['run_id']).consume()
                start=time.monotonic();print('Checking every baseline entity/property inside rollback transaction...',flush=True)
                results=[]
                for query in validators:
                    result=tx.run(query).single().data();results.append(result)
                    print(json.dumps(result),flush=True);assert result['part_exact_match']
                baseline={'baseline_exact_match':all(r['part_exact_match'] for r in results),'parts':results,'property_and_entity_checks':sum(r['property_and_entity_checks'] for r in results)}
                report['baseline']=baseline
                report['baseline_seconds']=round(time.monotonic()-start,2)
                assert baseline['baseline_exact_match']
                start=time.monotonic();result=tx.run(apply).single().data();report['import']=result
                report['import_seconds']=round(time.monotonic()-start,2)
                print(json.dumps(result),flush=True);assert result['expected_counts']
                checks=0
                for n in plan['nodes']:
                    query=f'MATCH(n:{ident(n["label"])} {{{ident(n["key"])}:$key}}) RETURN labels(n) AS labels, properties(n) AS props'
                    row=tx.run(query,key=decode(n['value'])).single()
                    assert sorted(row['labels'])==sorted(n['labels'])
                    assert comparable(canonical(row['props']))==comparable(n['props']),(n['label'],n['value'])
                    checks+=1
                for r in plan['relationships']:
                    a,b=r['start'],r['end'];query=(f'MATCH(a:{ident(a["label"])} {{{ident(a["key"])}:$a}})-[r:{ident(r["type"])}]->(b:{ident(b["label"])} {{{ident(b["key"])}:$b}}) RETURN properties(r) AS props')
                    found=tx.run(query,a=decode(a['value']),b=decode(b['value'])).data()
                    assert any(comparable(canonical(x['props']))==comparable(r['props']) for x in found),(r['type'],a,b)
                    checks+=1
                report['exact_new_entity_property_checks']=checks
            finally:
                tx.rollback();report['test_writes_rolled_back']=True
            counts=session.run('MATCH(n) WITH count(n) AS nodes CALL () { MATCH()-[r]->() RETURN count(r) AS relationships } RETURN nodes,relationships').single().data()
            assert counts=={'nodes':25537,'relationships':98577}
            report['after_rollback']=counts
            session.run('CALL db.clearQueryCaches()').consume()
            verified=[]
            for path in sorted(DEST.glob('05_part_*_verify_new_properties.cypher')):
                result=session.run(path.read_text(encoding='utf8')).single().data()
                assert result['exact_new_entities'],result
                verified.append(result)
            assert len(verified)==4
            report['desktop_new_properties_check']=verified
            report['status']='passed'
    (OUT/'DESKTOP_CSV_TEST.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
