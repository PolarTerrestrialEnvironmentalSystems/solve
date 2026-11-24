"""Idempotent additive master-data preservation for the isolated Bergwitzsee build."""
import hashlib
import json
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from neo4j import GraphDatabase
from build_postgres import OUT, SOURCE, DBNAME, WATER_BODY_ID, js


def main():
    source = SOURCE/'stammdaten_lhw.json'
    raw = json.loads(source.read_text(encoding='utf-8'))
    props = dict(lhw_stammdaten_json=js(raw),lhw_stammdaten_source=source.name,
        lhw_stammdaten_note='Original LHW master attributes preserved. Units are not inferred; conflicting UTM attribute is explicit.')
    config = dotenv_values(OUT/'runtime/postgres.env')
    neo = dotenv_values(OUT/'runtime/neo4j_preview/preview.env')
    engine = create_engine(config['SOLVE_DATABASE_URL'])
    assert engine.url.database == DBNAME and neo['NEO4J_URI']=='bolt://127.0.0.1:7691'
    registry=dict(source_file=source.name,source_path=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    with engine.begin() as conn:
        for field in props:
            conn.execute(text(f'ALTER TABLE water_kg_2.water_body ADD COLUMN IF NOT EXISTS {field} TEXT'))
        conn.execute(text('INSERT INTO water_raw.source_file VALUES (:source_file,:source_path,:sha256) ON CONFLICT(source_file) DO NOTHING'),registry)
        result=conn.execute(text('UPDATE water_kg_2.water_body SET '+','.join(f'{k}=:{k}' for k in props)+' WHERE water_body_id=:id'),dict(props,id=WATER_BODY_ID))
        assert result.rowcount==1
        pg_payload=conn.execute(text('SELECT lhw_stammdaten_json FROM water_kg_2.water_body WHERE water_body_id=:id'),dict(id=WATER_BODY_ID)).scalar_one()
        assert json.loads(pg_payload)==raw
    with GraphDatabase.driver(neo['NEO4J_URI'],auth=(neo['NEO4J_USER'],neo['NEO4J_PASSWORD'])) as driver:
        with driver.session(database='neo4j') as session:
            got=session.run('MATCH (w:WaterBodyV2 {water_body_id:$id}) SET w += $props RETURN w.lhw_stammdaten_json AS payload',id=WATER_BODY_ID,props=props).single()['payload']
            assert json.loads(got)==raw
    for name in ['POSTGRES_VALIDATION.json','NEO4J_VALIDATION.json']:
        path=OUT/name
        report=json.loads(path.read_text(encoding='utf-8'))
        report['lhw_master_attributes_source_roundtrip_verified']=True
        if name=='POSTGRES_VALIDATION.json' and source.name not in {r['source_file'] for r in report['source_registry']}:
            report['source_registry'].append(registry)
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Original LHW lake master attributes preserved and independently round-tripped in PostgreSQL and Neo4j.')


if __name__=='__main__':
    main()
