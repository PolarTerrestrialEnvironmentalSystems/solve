"""Read-only independent review of PostgreSQL-to-Neo4j additions."""
from pathlib import Path
from datetime import date,datetime
import json
import hashlib
import importlib.util
import sys
from collections import Counter
from dotenv import dotenv_values
from sqlalchemy import create_engine,text
from neo4j import GraphDatabase

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/bergwitzsee_ergaenzung_20261102'
WB='DELW_DEST_EL03OW07-00'
BATCH='bergwitzsee_official_enrichment_20261102'


def normal(v):
    if hasattr(v,'iso_format'):return v.iso_format()
    if isinstance(v,(date,datetime)):return v.isoformat()
    if isinstance(v,dict):return {k:normal(x) for k,x in v.items()}
    if isinstance(v,(list,tuple)):return [normal(x) for x in v]
    return v


def main():
    plan=json.loads((OUT/'GRAPH_DELTA.json').read_text(encoding='utf8'))
    baseline=json.loads((OUT/'NEO4J_BASELINE.json').read_text(encoding='utf8'))
    config=dotenv_values(OUT/'runtime/neo4j_preview/preview.env')
    assert config['NEO4J_URI']=='bolt://127.0.0.1:7692'
    engine=create_engine(dotenv_values(OUT/'runtime/postgres.env')['SOLVE_DATABASE_URL'])
    assert engine.url.database=='solve_bergwitzsee_v2_enriched_20261102'
    props_checked=0;matched=[]
    with engine.connect() as pg, GraphDatabase.driver(config['NEO4J_URI'],auth=(config['NEO4J_USER'],config['NEO4J_PASSWORD'])) as driver:
        with driver.session(database='neo4j',default_access_mode='READ') as neo:
            new_nodes=neo.run('MATCH(n) WHERE n.import_run_id=$run RETURN labels(n) AS labels,properties(n) AS props',run=plan['run_id']).data()
            new_edges=neo.run('MATCH(a)-[r]->(b) WHERE r.import_run_id=$run RETURN labels(a) AS al,properties(a) AS ap,type(r) AS type,properties(r) AS props,labels(b) AS bl,properties(b) AS bp',run=plan['run_id']).data()
            assert len(new_nodes)==len(plan['nodes'])==47
            assert len(new_edges)==len(plan['relationships'])==104
            for expected in plan['nodes']:
                rows=[n for n in new_nodes if expected['label'] in n['labels'] and n['props'].get(expected['key'])==expected['value']]
                assert len(rows)==1,(expected['label'],expected['key'],expected['value'])
                row=rows[0]
                assert set(row['labels'])==set(expected['labels'])
                for k,v in expected['props'].items():
                    if isinstance(v,dict) and '__type__' in v:
                        assert normal(row['props'].get(k))==v['value'],(expected['label'],k)
                    else:
                        assert normal(row['props'].get(k))==normal(v),(expected['label'],k)
            for expected in plan['relationships']:
                a,b=expected['start'],expected['end']
                matches=[r for r in new_edges if r['type']==expected['type'] and a['label'] in r['al']
                    and b['label'] in r['bl'] and r['ap'].get(a['key'])==a['value'] and r['bp'].get(b['key'])==b['value']
                    and r['props'].get('relationship_key')==expected['props'].get('relationship_key')]
                assert len(matches)==1,(expected['mapping'],a,b)
                assert matches[0]['props']==expected['props']

            for table,label,key,expected_count in [
                ('measurement','MeasurementV2','measurement_id',14),
                ('water_body_assessment','WaterBodyAssessmentV2','assessment_id',8)]:
                rows=pg.execute(text(f'SELECT * FROM water_kg_2.{table} WHERE enrichment_batch=:b'),{'b':BATCH}).mappings().all()
                nodes=[n['props'] for n in new_nodes if label in n['labels']]
                assert len(rows)==len(nodes)==expected_count
                byid={n[key]:n for n in nodes}
                assert len(byid)==expected_count
                for row in rows:
                    node=byid[row[key]]
                    for field,value in row.items():
                        assert normal(node.get(field))==normal(value),(table,row[key],field)
                        props_checked+=1
                matched.append({'table':table,'label':label,'count':expected_count,'ids':[r[key] for r in rows],'all_pg_properties_equal':True})
            measures=[n['props'] for n in new_nodes if 'MeasurementV2' in n['labels']]
            censored=[r for r in measures if r.get('comparison_operator')=='<']
            assert len(censored)==7
            assert all(r.get('value') is None and r['reported_censoring_bound']==10 and r['limit_kind']=='reported_censoring_bound; analytical_limit_type_unspecified' for r in censored),[(r.get('reported_censoring_bound'),r.get('limit_kind')) for r in censored]
            for m in measures:
                edge_types=Counter(r['type'] for r in new_edges if ('MeasurementV2' in r['al'] and r['ap'].get('measurement_id')==m['measurement_id']) or ('MeasurementV2' in r['bl'] and r['bp'].get('measurement_id')==m['measurement_id']))
                assert edge_types==Counter({'RECORDED':1,'FOR_WATERBODY':1,'SAMPLED_IN':1,'MEASURES_PARAMETER':1,'HAS_UNIT':1})

            pa=neo.run('MATCH(w:WaterBodyV2 {water_body_id:$w})-[r:OVERLAPS_PROTECTED_AREA]->(p:ProtectedAreaV2) RETURN p.protected_area_id AS id,properties(r) AS props',w=WB).data()
            assert {r['id'] for r in pa}=={'ST:lau_lsg_lsa:LSG0035WB_','ST:lau_nup_lsa:NUP0003LSA'}
            gw=neo.run('MATCH(w:WaterBodyV2 {water_body_id:$w})-[r:OVERLAPS]->(g:GroundwaterBodyV2) RETURN g.groundwater_body_id AS id,g.name AS name,g.horizon AS horizon,g.source_horizons AS source_horizons,properties(r) AS props',w=WB).data()
            assert len(gw)==1 and gw[0]['id']=='DEGB_DEST_EL-3-1'
            assert gw[0]['horizon'] is None and gw[0]['source_horizons']=='2'
            assert gw[0]['name']=='Elbtal und Moränenlandschaft Dübener Heide'
            assert all('not a hydrological dependency' in r['props']['relation_method'] for r in pa)
            assert 'no hydraulic exchange inferred' in gw[0]['props']['determination_method']
            versions=neo.run('MATCH(g:GroundwaterBodyV2 {groundwater_body_id:$g})-[r:HAS_VERSION]->(v:GroundwaterBodyVersion) RETURN properties(v) AS props',g=gw[0]['id']).data()
            assert sorted(r['props']['reporting_year'] for r in versions)==[2010,2016,2022]
            sqlversions={r['version_id']:dict(r) for r in pg.execute(text('SELECT * FROM water_kg_extension.groundwater_body_version')).mappings()}
            for r in versions:
                node=r['props']
                for k,v in sqlversions[node['version_id']].items():
                    assert normal(node.get(k))==normal(v),(node['version_id'],k)
            assert neo.run('MATCH(:GroundwaterBodyVersion)-[r]-(:GroundwaterBodyVersion) RETURN count(r)').single()[0]==0
            event_count=neo.run('MATCH(w:WaterBodyV2 {water_body_id:$w})-[r:HAS_EVENT]->(e:Event) RETURN count(r),count(DISTINCT e)',w=WB).single()
            assert list(event_count)==[73,73]
            assert neo.run('MATCH(:Event)-[r]-(:MeasurementV2) RETURN count(r)').single()[0]==0
            assert not any('Event' in r['al'] or 'Event' in r['bl'] for r in new_edges)

            old_import=ROOT/'outputs/bergwitzsee_neo4j_v2_events_20261101/source_scripts/neo4j_v2_import.py'
            spec=importlib.util.spec_from_file_location('peer_old_kg_schema',old_import)
            module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
            original_classes={r.label for r in module.NODE_SPECS}
            assert len(original_classes)==21
            allowed=set(baseline['counts_by_label'])|original_classes|{'GroundwaterBodyVersion'}
            labels={r['label'] for r in neo.run('CALL db.labels() YIELD label RETURN label')}
            assert labels<=allowed,labels-allowed
            constraints=neo.run('SHOW CONSTRAINTS YIELD name,type,labelsOrTypes,properties RETURN name,type,labelsOrTypes,properties').data()
            constraints_by_name={r['name']:r for r in constraints}
            assert all(constraints_by_name.get(r['name'])==r for r in baseline['constraints'])
            assert constraints_by_name['groundwater_version_id']['properties']==['version_id']
            counts={'nodes':neo.run('MATCH(n) RETURN count(n)').single()[0],
                    'relationships':neo.run('MATCH()-[r]->() RETURN count(r)').single()[0]}
            assert counts=={'nodes':25537,'relationships':98577}
    engine.dispose()
    preservation_path=OUT/'ADDITIVE_VALIDATION.json'
    preservation=json.loads(preservation_path.read_text(encoding='utf8'))
    assert preservation['status']=='passed' and preservation['total_errors']==0
    assert preservation['checked_original_nodes']==25490 and preservation['checked_original_relationships']==98473
    report={'status':'passed','access_mode':'read-only','target':'bolt://127.0.0.1:7692',
        'counts':counts,'new_nodes_checked':47,'new_relationships_checked':104,
        'postgres_graph_comparisons':matched,'postgres_properties_compared':props_checked,
        'censored_measurements_checked':7,'protected_area_overlaps':2,'groundwater_overlaps':1,
        'parallel_groundwater_versions':[2010,2016,2022],'waterbody_event_edges':73,
        'event_measurement_edges':0,'new_event_edges':0,'original_v2_schema_classes_retained':21,
        'only_new_class_outside_original_model':'GroundwaterBodyVersion',
        'original_constraints_preserved':True,'original_preservation_report':preservation_path.name,
        'original_preservation_report_sha256':hashlib.sha256(preservation_path.read_bytes()).hexdigest(),
        'original_nodes_preserved':25490,'original_relationships_preserved':98473,
        'notes':['Newly populated GroundwaterBodyV2 and ProtectedAreaV2 are existing original V2 classes.',
            'Overlap relations carry explicit spatial-only semantics.',
            'Groundwater report years are not observation dates; assessed periods remain separate.',
            'Censored values remain absent as exact numeric values; explicit source bounds are retained.']}
    (OUT/'KG_PEER_AUDIT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k not in ['postgres_graph_comparisons','notes']}))


if __name__=='__main__':main()
