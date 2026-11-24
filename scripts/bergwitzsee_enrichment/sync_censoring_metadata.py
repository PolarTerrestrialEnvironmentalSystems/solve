"""Add corrected limit semantics to exactly the seven newly imported measurements."""
import json
from dotenv import dotenv_values
from neo4j import GraphDatabase
from sqlalchemy import create_engine,text
from snapshot_baseline import OUT


def main():
    path=OUT/'GRAPH_DELTA.json'
    plan=json.loads(path.read_text(encoding='utf8'))
    candidates=[n for n in plan['nodes'] if n['label']=='MeasurementV2']
    assert len(candidates)==14
    pg=dotenv_values(OUT/'runtime/postgres.env')
    engine=create_engine(pg['SOLVE_DATABASE_URL'])
    with engine.connect() as conn:
        rows=conn.execute(text('SELECT measurement_id,reported_censoring_bound,limit_kind FROM water_kg_2.measurement WHERE measurement_id=ANY(:ids)'),
                          {'ids':[n['props']['measurement_id'] for n in candidates]}).mappings().all()
    engine.dispose()
    by_id={r['measurement_id']:dict(r) for r in rows};updates=[]
    for n in candidates:
        row=by_id[n['props']['measurement_id']]
        extra={k:row[k] for k in ('limit_kind','reported_censoring_bound') if row[k] is not None}
        n['props'].update(extra)
        if extra:updates.append({'key':n['value'],'props':extra})
    assert len(updates)==7
    cfg=dotenv_values(OUT/'runtime/neo4j_preview/preview.env')
    assert cfg['NEO4J_URI']=='bolt://127.0.0.1:7692'
    with GraphDatabase.driver(cfg['NEO4J_URI'],auth=(cfg['NEO4J_USER'],cfg['NEO4J_PASSWORD'])) as driver:
        def apply(tx):
            count=tx.run('UNWIND $rows AS row MATCH(n:MeasurementV2 {source_key:row.key}) WHERE n.import_run_id=$run SET n += row.props RETURN count(n)',rows=updates,run=plan['run_id']).single()[0]
            assert count==7
        with driver.session(database='neo4j') as session:session.execute_write(apply)
    path.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf8')
    print('Updated exactly seven new measurements and final GRAPH_DELTA metadata.')


if __name__=='__main__':main()
