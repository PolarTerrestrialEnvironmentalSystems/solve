"""Import unchanged original V2 model, then add direct events and report versions."""
from pathlib import Path
import json
from dotenv import dotenv_values
from sqlalchemy import create_engine
from neo4j import GraphDatabase
from build_postgres import load, OUT, ROOT, DBNAME
from import_extensions import import_extensions, NODE_TABLES


def main():
    pg = dotenv_values(OUT/'runtime/postgres.env')
    neo = dotenv_values(OUT/'runtime/neo4j_preview/preview.env')
    assert neo['NEO4J_URI'] == 'bolt://127.0.0.1:7691'
    engine = create_engine(pg['SOLVE_DATABASE_URL'], pool_pre_ping=True)
    assert engine.url.database == DBNAME
    original = load('berg_original_neo4j_v2', OUT/'source_scripts/neo4j_v2_import.py')
    verifier = load('original_property_verifier', ROOT/'scripts/dummy_v2/import_graph.py')
    extension = json.loads((OUT/'BERGWITZSEE_EXTENSION.json').read_text(encoding='utf-8'))
    with GraphDatabase.driver(neo['NEO4J_URI'], auth=(neo['NEO4J_USER'], neo['NEO4J_PASSWORD'])) as driver:
        driver.verify_connectivity()
        with driver.session(database='neo4j') as session:
            if session.run('MATCH (n) RETURN count(n)').single()[0]:
                raise RuntimeError('Target graph is not empty; refusing replacement')
        imp = original.WaterKgV2Importer(engine, driver, 'neo4j', batch_size=2000)
        imp.run(rebuild=False)
        properties = verifier.verify_properties(original, imp, driver)
        ext_report = import_extensions(driver, 'neo4j', extension)
        with driver.session(database='neo4j') as session:
            nodes = session.run('MATCH (n) RETURN count(n)').single()[0]
            rels = session.run('MATCH ()-[r]->() RETURN count(r)').single()[0]
            counts = dict(imp.source_counts)
            counts.update(ext_report['node_counts'])
            lakes = session.run('MATCH (w:WaterBodyV2) RETURN w.water_body_id AS id,w.name AS name').data()
            assert lakes == [{'id':'DELW_DEST_EL03OW07-00','name':'Bergwitzsee'}]
            assert session.run('MATCH (n:WaterKgV2Build) RETURN count(n)').single()[0] == 0
            assert session.run('MATCH (e:Event) WHERE NOT (:WaterBodyV2)-[:HAS_EVENT]->(e) RETURN count(e)').single()[0] == 0
            constraints = session.run('SHOW CONSTRAINTS YIELD name,type,labelsOrTypes,properties RETURN name,type,labelsOrTypes,properties').data()
            relationships = session.run('MATCH ()-[r]->() RETURN type(r) AS type,count(r) AS count ORDER BY type').data()
        report = dict(status='passed', nodes=nodes, relationships=rels, counts_by_label=counts,
            water_bodies=lakes, constraints=constraints, relationship_counts=relationships,
            original_importer_unchanged=True, original_schema_extended_only=True,
            all_original_properties_verified_against_postgres=properties,
            original_relationship_mappings=imp.relationship_counts, extension_validation=ext_report,
            node_specs=[{'table':s.table,'label':s.label,'key_property':s.key_property} for s in original.NODE_SPECS]
                       +[{'table':table,'label':label,'key_property':key} for table,(label,key) in NODE_TABLES.items()],
            relationship_specs=[{'name':s.name,'type':s.relationship_type,'from':s.start_label,'to':s.end_label} for s in original.RELATIONSHIP_SPECS])
        (OUT/'NEO4J_VALIDATION.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:report[k] for k in ['status','nodes','relationships','water_bodies','extension_validation']},indent=2),flush=True)
    engine.dispose()


if __name__ == '__main__':
    main()
