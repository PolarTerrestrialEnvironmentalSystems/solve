"""Run the unchanged original V2 Neo4j importer against the isolated Dummy DB."""
from pathlib import Path
import importlib.util
import json
import sys
from dotenv import dotenv_values
from sqlalchemy import create_engine
from neo4j import GraphDatabase

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/dummy_neo4j_v2_20261101'


def comparable(value):
    if hasattr(value,'to_native'):return value.to_native()
    return value


def verify_properties(mod,imp,driver):
    checked={}
    with driver.session(database='neo4j') as session:
        for spec in mod.NODE_SPECS:
            count=0
            for batch in mod.batches(imp.sql_rows(imp.node_query(spec)),1000):
                expected={mod.clean_value(spec.key_builder(row)):row for row in batch}
                assert len(expected)==len(batch),f'Duplicate source node key in {spec.label}'
                records=session.run(f'UNWIND $keys AS key MATCH (n:{spec.label} {{{spec.key_property}:key}}) RETURN n',keys=list(expected)).data()
                assert len(records)==len(batch),spec.label
                for record in records:
                    actual=dict(record['n'])
                    key=actual[spec.key_property]
                    row=expected[key]
                    props=mod.clean_properties(row,set(spec.point_from or ()))
                    props.update({spec.key_property:key,'graph_version':mod.GRAPH_VERSION,'import_run_id':imp.run_id})
                    location=actual.pop('location',None)
                    assert {k:comparable(v) for k,v in actual.items()}==props,(spec.label,key)
                    if spec.point_from:
                        lon,lat=(mod.clean_value(row.get(c)) for c in spec.point_from)
                        if lon is not None and lat is not None:
                            assert location is not None and location.srid==4326
                            assert abs(location.longitude-lon)<1e-12 and abs(location.latitude-lat)<1e-12
                        else:assert location is None
                    count+=1
            checked[spec.label]=count
            print(f'property check {spec.label}: {count}',flush=True)
    return checked


def main():
    postgres=dotenv_values(OUT/'runtime/postgres.env')
    neo=dotenv_values(OUT/'runtime/neo4j_preview/preview.env')
    if neo['NEO4J_URI']!='bolt://127.0.0.1:7690':raise RuntimeError('Unexpected Neo4j target')
    engine=create_engine(postgres['SOLVE_DATABASE_URL'],pool_pre_ping=True)
    if engine.url.database!='solve_dummy_v2_20261101':raise RuntimeError('Unexpected PostgreSQL database')
    spec=importlib.util.spec_from_file_location('dummy_original_v2',OUT/'source_scripts/neo4j_v2_import.py')
    mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
    with GraphDatabase.driver(neo['NEO4J_URI'],auth=(neo['NEO4J_USER'],neo['NEO4J_PASSWORD'])) as driver:
        driver.verify_connectivity()
        with driver.session(database='neo4j') as session:
            if session.run('MATCH (n) RETURN count(n)').single()[0]:raise RuntimeError('Neo4j target is not empty')
        imp=mod.WaterKgV2Importer(engine,driver,'neo4j',batch_size=2000)
        imp.run(rebuild=False)
        properties=verify_properties(mod,imp,driver)
        with driver.session(database='neo4j') as session:
            nodes=session.run('MATCH (n) RETURN count(n)').single()[0]
            rels=session.run('MATCH ()-[r]->() RETURN count(r)').single()[0]
            constraints=session.run('SHOW CONSTRAINTS YIELD name,type,labelsOrTypes,properties RETURN name,type,labelsOrTypes,properties').data()
            lakes=session.run('MATCH (n:WaterBodyV2) RETURN n.water_body_id AS id,n.name AS name ORDER BY name').data()
            invalid=session.run('MATCH (n:StationAssessmentV2) WHERE n.is_valid=false RETURN count(n)').single()[0]
            staging=session.run('MATCH (n:WaterKgV2Build) RETURN count(n)').single()[0]
            foreign=session.run('MATCH (n) WHERE NOT n:WaterKgV2 RETURN count(n)').single()[0]
            assert staging==0 and foreign==0
        report={'status':'passed','graph_version':'2','source_importer_unchanged':True,'run_id':imp.run_id,
            'nodes':nodes,'relationships':rels,'counts_by_label':imp.source_counts,
            'counts_by_relationship_mapping':imp.relationship_counts,'constraints':constraints,
            'water_bodies':lakes,'invalid_station_assessments_preserved':invalid,
            'staging_nodes':staging,'foreign_nodes':foreign,
            'all_node_properties_verified_against_postgres':properties,
            'node_specs':[{'table':s.table,'label':s.label,'key_property':s.key_property} for s in mod.NODE_SPECS],
            'relationship_specs':[{'name':s.name,'type':s.relationship_type,'from':s.start_label,'to':s.end_label} for s in mod.RELATIONSHIP_SPECS]}
        (OUT/'NEO4J_VALIDATION.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({k:report[k] for k in ['status','nodes','relationships','water_bodies']},indent=2,ensure_ascii=False),flush=True)
    engine.dispose()


if __name__=='__main__':main()
