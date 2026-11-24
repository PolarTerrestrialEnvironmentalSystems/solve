"""Add only absent V2 nodes/edges from enriched PostgreSQL; never update old graph data.

Uses the unchanged original NodeSpec/RelationshipSpec keys and projections.
One transaction applies the complete parameterized delta. Default is plan-only;
--apply writes only to the isolated enrichment runtime, never Neo4j Desktop.
"""
from pathlib import Path
from collections import Counter,defaultdict
from datetime import date,datetime,time,timedelta
import argparse
import hashlib
import importlib.util
import json
import sys
import uuid
from dotenv import dotenv_values
from neo4j import GraphDatabase
from neo4j.spatial import WGS84Point,CartesianPoint
from sqlalchemy import create_engine,inspect,text
from snapshot_baseline import OUT,ROOT,canonical


def load_original():
    path=ROOT/'outputs/bergwitzsee_neo4j_v2_events_20261101/source_scripts/neo4j_v2_import.py'
    spec=importlib.util.spec_from_file_location('additive_original_v2',path)
    m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
    return m


def ident(value):
    if not value.isidentifier():raise ValueError('Unexpected identifier')
    return '`'+value+'`'


def decode(value):
    if isinstance(value,list):return [decode(v) for v in value]
    if not isinstance(value,dict):return value
    if '__type__' not in value:return {k:decode(v) for k,v in value.items()}
    kind=value['__type__']
    if kind=='Point':
        point=WGS84Point(value['coordinates']) if value['srid'] in (4326,4979) else CartesianPoint(value['coordinates'])
        if point.srid!=value['srid']:raise ValueError('Unsupported point SRID')
        return point
    if kind in ('date','Date'):return date.fromisoformat(value['value'])
    if kind in ('datetime','DateTime'):return datetime.fromisoformat(value['value'])
    if kind in ('time','Time'):return time.fromisoformat(value['value'])
    raise ValueError('Unsupported typed property '+kind)


def graph_state(session):
    nodes={r['id']:{'labels':r['labels'],'props':dict(r['props'])} for r in session.run(
        'MATCH(n) RETURN elementId(n) AS id,labels(n) AS labels,properties(n) AS props')}
    edges=[dict(r) for r in session.run('MATCH(a)-[r]->(b) RETURN elementId(r) AS id,elementId(a) AS start,elementId(b) AS end,type(r) AS type,properties(r) AS props')]
    return nodes,edges


def make_plan(engine,driver):
    original=load_original();imp=original.WaterKgV2Importer(engine,driver,'neo4j',batch_size=1000)
    with driver.session(database='neo4j') as session:
        current,edges=graph_state(session)
    base=json.loads((OUT/'NEO4J_BASELINE.json').read_text(encoding='utf8'))
    if (len(current),len(edges))!=(base['nodes'],base['relationships']):
        raise RuntimeError('Graph is no longer the unchanged baseline; refusing a new delta')
    key_fields=defaultdict(set)
    for spec in original.NODE_SPECS:key_fields[spec.label].update((spec.key_property,*spec.extra_unique))
    for spec in original.RELATIONSHIP_SPECS:
        key_fields[spec.start_label].add(spec.start_property);key_fields[spec.end_label].add(spec.end_property)
    key_fields['GroundwaterBodyVersion'].add('version_id')
    lookup={}
    def register(eid,labels,props):
        for label in labels:
            for key in key_fields[label]:
                if props.get(key) is not None:
                    composite=(label,key,props[key])
                    if composite in lookup and lookup[composite]!=eid:raise RuntimeError('Nonunique graph identity '+str(composite))
                    lookup[composite]=eid
    for eid,row in current.items():register(eid,row['labels'],row['props'])
    existing_edges={(r['type'],r['start'],r['end'],r['props'].get('relationship_key')) for r in edges}
    new_nodes=[];new_edges=[];skipped=Counter();new_counts=Counter();relationship_counts=Counter()
    run_id='bergwitzsee-enrichment-'+uuid.uuid4().hex
    def add_node(label,key,value,props,labels):
        if (label,key,value) in lookup:
            skipped[label]+=1;return
        eid='new:'+hashlib.sha256(json.dumps([label,key,value],ensure_ascii=False).encode()).hexdigest()
        props=dict(props);props[key]=value
        new_nodes.append({'label':label,'key':key,'value':value,'labels':labels,'props':props})
        register(eid,labels,props);new_counts[label]+=1
    for spec in original.NODE_SPECS:
        for row in imp.sql_rows(imp.node_query(spec)):
            key=original.clean_value(spec.key_builder(row))
            if key is None or key=='':raise RuntimeError('Missing node key '+spec.table)
            if (spec.label,spec.key_property,key) in lookup:
                skipped[spec.label]+=1;continue
            props=original.clean_properties(row,set(spec.point_from or ()))
            props.update(graph_version=original.GRAPH_VERSION,import_run_id=run_id)
            if spec.point_from:
                lon,lat=(original.clean_value(row.get(k)) for k in spec.point_from)
                if lon is not None and lat is not None:props['location']=WGS84Point((lon,lat))
            add_node(spec.label,spec.key_property,key,props,[spec.label,'WaterKgV2'])
    extension_rows=[]
    if inspect(engine).has_table('groundwater_body_version',schema='water_kg_extension'):
        with engine.connect() as conn:
            extension_rows=[dict(r) for r in conn.execute(text('SELECT * FROM water_kg_extension.groundwater_body_version')).mappings()]
        for row in extension_rows:
            props=original.clean_properties(row);props['import_run_id']=run_id
            props['valid_time_kind']='reporting_year_not_observation_date'
            add_node('GroundwaterBodyVersion','version_id',row['version_id'],props,['GroundwaterBodyVersion'])
    def add_edge(name,kind,sl,sk,sv,el,ek,ev,props,merge_key=None):
        a=lookup.get((sl,sk,sv));b=lookup.get((el,ek,ev))
        if a is None or b is None:raise RuntimeError('Unresolved relationship endpoint '+str((name,sl,sk,sv,el,ek,ev)))
        signature=(kind,a,b,merge_key)
        if signature in existing_edges:return
        existing_edges.add(signature)
        properties=dict(props);properties.update(graph_version=original.GRAPH_VERSION,import_run_id=run_id)
        if merge_key is not None:properties['relationship_key']=merge_key
        new_edges.append({'type':kind,'start':{'label':sl,'key':sk,'value':sv},'end':{'label':el,'key':ek,'value':ev},
                          'props':properties,'merge_property':'relationship_key' if merge_key is not None else None,
                          'merge_value':merge_key,'mapping':name})
        relationship_counts[name]+=1
    for spec in original.RELATIONSHIP_SPECS:
        for row in imp.sql_rows(spec.query):
            prepared=imp.prepare_relationship_row(spec,row)
            add_edge(spec.name,spec.relationship_type,spec.start_label,spec.start_property,prepared['start'],
                     spec.end_label,spec.end_property,prepared['end'],prepared['properties'],prepared.get('relationship_key'))
    for row in extension_rows:
        add_edge('groundwater_version','HAS_VERSION','GroundwaterBodyV2','groundwater_body_id',row['groundwater_body_id'],
                 'GroundwaterBodyVersion','version_id',row['version_id'],{'reporting_year':row['reporting_year'],'source_url':row['source_url']})
    return {'run_id':run_id,'target':'bolt://127.0.0.1:7692','baseline':{'nodes':base['nodes'],'relationships':base['relationships']},
            'nodes':new_nodes,'relationships':new_edges,'new_node_counts':dict(new_counts),'new_relationship_counts':dict(relationship_counts),
            'existing_nodes_skipped':dict(skipped),'groundwater_version_constraint':bool(extension_rows),
            'original_schema_mappings_reused':True,'existing_entities_never_updated':True}


def apply_plan(driver,plan):
    groups=defaultdict(list);relgroups=defaultdict(list)
    for n in plan['nodes']:groups[(n['label'],n['key'],tuple(n['labels']))].append(n)
    for r in plan['relationships']:
        a,b=r['start'],r['end']
        relgroups[(r['type'],a['label'],a['key'],b['label'],b['key'],r['merge_property'])].append(r)
    with driver.session(database='neo4j') as session:
        if plan['groundwater_version_constraint']:
            session.run('CREATE CONSTRAINT groundwater_version_id IF NOT EXISTS FOR (v:GroundwaterBodyVersion) REQUIRE v.version_id IS UNIQUE').consume()
        def transaction(tx):
            nc=tx.run('MATCH(n) RETURN count(n)').single()[0];rc=tx.run('MATCH()-[r]->() RETURN count(r)').single()[0]
            if (nc,rc)!=(plan['baseline']['nodes'],plan['baseline']['relationships']):raise RuntimeError('Baseline counts changed; no data written')
            for (label,key,labels),rows in groups.items():
                labels_set=':'.join(ident(x) for x in labels)
                query=f'UNWIND $rows AS row MERGE(n:{ident(label)} {{{ident(key)}:row.value}}) ON CREATE SET n:{labels_set}, n += row.props RETURN count(n)'
                count=tx.run(query,rows=rows).single()[0]
                if count!=len(rows):raise RuntimeError('Node import mismatch')
            for (kind,sl,sk,el,ek,merge_property),rows in relgroups.items():
                marker=f' {{{ident(merge_property)}:row.merge_value}}' if merge_property else ''
                query=(f'UNWIND $rows AS row MATCH(a:{ident(sl)} {{{ident(sk)}:row.start.value}}) '
                       f'MATCH(b:{ident(el)} {{{ident(ek)}:row.end.value}}) MERGE(a)-[r:{ident(kind)}{marker}]->(b) '
                       'ON CREATE SET r += row.props RETURN count(r)')
                if tx.run(query,rows=rows).single()[0]!=len(rows):raise RuntimeError('Edge import mismatch')
            n=tx.run('MATCH(n) RETURN count(n)').single()[0];r=tx.run('MATCH()-[r]->() RETURN count(r)').single()[0]
            if n!=nc+len(plan['nodes']) or r!=rc+len(plan['relationships']):raise RuntimeError('Final delta counts mismatch')
            return {'nodes':n,'relationships':r}
        totals=session.execute_write(transaction)
        counts={r['label']:r['count'] for r in session.run('MATCH(n) UNWIND labels(n) AS label RETURN label,count(*) AS count ORDER BY label')}
        return {**totals,'counts_by_label':counts}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    pg=dotenv_values(OUT/'runtime/postgres.env');neo=dotenv_values(OUT/'runtime/neo4j_preview/preview.env')
    assert neo['NEO4J_URI']=='bolt://127.0.0.1:7692'
    engine=create_engine(pg['SOLVE_DATABASE_URL'],pool_pre_ping=True)
    assert engine.url.database=='solve_bergwitzsee_v2_enriched_20261102'
    with GraphDatabase.driver(neo['NEO4J_URI'],auth=(neo['NEO4J_USER'],neo['NEO4J_PASSWORD'])) as driver:
        plan=make_plan(engine,driver)
        (OUT/'GRAPH_DELTA.json').write_text(json.dumps(canonical(plan),ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps({'new_nodes':len(plan['nodes']),'new_relationships':len(plan['relationships']),'counts':plan['new_node_counts']}),flush=True)
        if args.apply:
            totals=apply_plan(driver,plan)
            report={'status':'passed',**totals,'new_nodes':len(plan['nodes']),'new_relationships':len(plan['relationships']),
                    'delta_run_id':plan['run_id'],'new_node_counts':plan['new_node_counts'],'new_relationship_counts':plan['new_relationship_counts'],
                    'original_schema_mappings_reused':True,'existing_entities_never_updated':True}
            (OUT/'NEO4J_VALIDATION.json').write_text(json.dumps(report,indent=2),encoding='utf8')
            print(json.dumps(report),flush=True)
    engine.dispose()


if __name__=='__main__':main()
