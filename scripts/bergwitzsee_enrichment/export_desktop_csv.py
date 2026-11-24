"""Generate typed CSVs, exact baseline validator and atomic additive Desktop query.

Every lookup uses the original static label/unique property. No APOC or passwords.
Original graph identities use stable keys, never runtime-specific element IDs.
"""
from collections import defaultdict,Counter
from pathlib import Path
import csv
import gzip
import json
import math
from import_delta import load_original,ident
from snapshot_baseline import OUT

FOLDER='bergwitzsee_enrichment_20261102'
DEST=OUT/'desktop_import'/FOLDER
TYPE_FIELDS=['type','value','separator','length','srid','x','y','z']
BACKSLASH_TOKEN='__SOLVE_BACKSLASH_818eabd064d84cd9__'


def typed(value,prefix=''):
    row={prefix+k:'' for k in TYPE_FIELDS}
    def put(kind,text):row.update({prefix+'type':kind,prefix+'value':text});return row
    if value is None:raise ValueError('Null graph property cannot exist')
    if isinstance(value,bool):return put('bool','true' if value else 'false')
    if isinstance(value,int):return put('int',str(value))
    if isinstance(value,float):
        if not math.isfinite(value):raise ValueError('Nonfinite property')
        return put('float',repr(value))
    if isinstance(value,str):
        assert BACKSLASH_TOKEN not in value
        return put('string',value.replace('\\',BACKSLASH_TOKEN))
    if isinstance(value,dict):
        kind=value['__type__']
        if kind=='Point':
            row[prefix+'srid']=value['srid']
            for key,v in zip(('x','y','z'),value['coordinates']):row[prefix+key]=repr(v)
            return put('point','')
        kind=kind.lower()
        if kind not in ('date','datetime','time','localdatetime','localtime','duration'):
            raise ValueError('Unhandled canonical type '+kind)
        return put(kind,value['value'])
    if isinstance(value,list):
        sep='\u241f'
        while any(sep in str(v) for v in value):sep+='\u241f'
        kinds={typed(v)['type'] for v in value}
        if len(kinds)>1:raise ValueError('Mixed list property')
        kind=next(iter(kinds),'string')
        if kind not in ('string','int','float','bool'):raise ValueError('Unsupported list kind '+kind)
        row[prefix+'separator']=sep;row[prefix+'length']=len(value)
        return put('list_'+kind,sep.join(typed(v)['value'] for v in value))
    raise ValueError('Unhandled graph value '+str(type(value)))


def expression(prefix='',r='row',allowed=None):
    v=f'{r}.{prefix}value';t=f'{r}.{prefix}type'
    def unescape(s):return f'replace({s}, {json.dumps(BACKSLASH_TOKEN)}, '+json.dumps('\\')+')'
    cases={'string':unescape(f'coalesce({v}, "")'),'int':f'toInteger({v})','float':f'toFloat({v})','bool':f'toBoolean({v})'}
    for kind in ('date','datetime','time','localdatetime','localtime','duration'):cases[kind]=f'{kind}({v})'
    xy=f'srid:toInteger({r}.{prefix}srid),x:toFloat({r}.{prefix}x),y:toFloat({r}.{prefix}y)'
    cases['point']=f'CASE WHEN coalesce({r}.{prefix}z, "") = "" THEN point({{{xy}}}) ELSE point({{{xy},z:toFloat({r}.{prefix}z)}}) END'
    for kind,convert in [('string',unescape('item')),('int','toInteger(item)'),('float','toFloat(item)'),('bool','toBoolean(item)')]:
        cases['list_'+kind]=f'CASE WHEN toInteger({r}.{prefix}length)=0 THEN [] ELSE [item IN split(coalesce({v},""),{r}.{prefix}separator) | {convert}] END'
    if allowed is not None:cases={k:v for k,v in cases.items() if k in allowed}
    if not cases:return 'null'
    return 'CASE '+t+' '+ ' '.join('WHEN '+json.dumps(k)+' THEN '+v for k,v in cases.items())+' ELSE null END'


def keyexpr(prefix):
    # All stable node keys in this graph are either strings or integers.
    return f'CASE row.{prefix}type WHEN "int" THEN toInteger(row.{prefix}value) ELSE replace(coalesce(row.{prefix}value,""),'+json.dumps(BACKSLASH_TOKEN)+','+json.dumps('\\')+') END'


def csvfile(name,rows,fields):
    path=DEST/(name+'.csv');rows=list(rows)
    with path.open('w',encoding='utf8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='raise');writer.writeheader();writer.writerows(rows)
    return path.name,len(rows)


def load(name):return f'LOAD CSV WITH HEADERS FROM "file:///{FOLDER}/{name}.csv" AS row'
def keyfields(prefix):return [prefix+k for k in TYPE_FIELDS]
def metadata(props):return {'property_count':len(props)}


def main():
    DEST.mkdir(parents=True,exist_ok=True)
    original=load_original()
    keys={s.label:s.key_property for s in original.NODE_SPECS}
    keys.update(Dataset='dataset_id',RawRecord='raw_id',SourceDocument='source_id',Event='event_id',
                EventEvidence='evidence_id',WaterBodyVersion='water_body_id',MonitoringStationVersion='station_id',
                ImportIssue='issue_id',GroundwaterBodyVersion='version_id')
    nodegroups=defaultdict(list);edges=[];nodes={};identity_seen=set()
    with gzip.open(OUT/'baseline_graph.jsonl.gz','rt',encoding='utf8') as f:
        for line in f:
            row=json.loads(line)
            if row['kind']=='relationship':edges.append(row);continue
            candidates=[label for label in row['labels'] if label in keys]
            assert len(candidates)==1,candidates
            label=candidates[0];key=keys[label];value=row['props'][key]
            assert isinstance(value,(str,int)) and not isinstance(value,bool)
            identity=(label,key,value);assert identity not in identity_seen;identity_seen.add(identity)
            row['identity']=identity;nodes[row['id']]=row;nodegroups[(label,key)].append(row)
    edgegroups=defaultdict(list);edge_seen=set()
    for row in edges:
        a=nodes[row['start']]['identity'];b=nodes[row['end']]['identity']
        signature=(a,b,row['type']);assert signature not in edge_seen;edge_seen.add(signature)
        row['a']=a;row['b']=b
        edgegroups[(row['type'],a[0],a[1],b[0],b[1])].append(row)
    checks=[];stats={'baseline_nodes':len(nodes),'baseline_relationships':len(edges),'files':[]}
    expected=expression()
    def write(name,rows,fields):
        filename,count=csvfile(name,rows,fields);stats['files'].append({'file':filename,'rows':count})
    def check(name,body):
        wanted=next(f['rows'] for f in stats['files'] if f['file']==name+'.csv')
        checks.append(body+'\nRETURN '+json.dumps(name)+' AS check, count(*) AS checks, sum(bad)+abs(count(*)-'+str(wanted)+') AS errors')
    for index,((label,key),rows) in enumerate(sorted(nodegroups.items())):
        stem=f'baseline_n{index:02d}'
        masters=[];props=[]
        for row in rows:
            k=typed(row['identity'][2],'k_')
            masters.append({**k,**metadata(row['props']),'labels':'|'.join(sorted(row['labels']))})
            for prop,val in row['props'].items():props.append({**k,'property':prop,**typed(val)})
        write(stem,masters,keyfields('k_')+['property_count','labels'])
        write(stem+'_props',props,keyfields('k_')+['property']+TYPE_FIELDS)
        expected=expression(allowed={p['type'] for p in props})
        match=f'OPTIONAL MATCH(n:{ident(label)} {{{ident(key)}:{keyexpr("k_")}}})'
        check(stem,load(stem)+'\n'+match+'\nWITH CASE WHEN n IS NOT NULL AND size(keys(n))=toInteger(row.property_count) AND size(labels(n))=size(split(row.labels,"|")) AND all(l IN split(row.labels,"|") WHERE l IN labels(n)) THEN 0 ELSE 1 END AS bad')
        check(stem+'_props',load(stem+'_props')+'\n'+match+'\nWITH n,row,'+expected+' AS expected\nWITH CASE WHEN n IS NOT NULL AND n[row.property]=expected AND valueType(n[row.property])=valueType(expected) THEN 0 ELSE 1 END AS bad')
    for index,((kind,sl,sk,el,ek),rows) in enumerate(sorted(edgegroups.items())):
        stem=f'baseline_r{index:02d}';masters=[];props=[]
        for row in rows:
            k={**typed(row['a'][2],'a_'),**typed(row['b'][2],'b_')}
            masters.append({**k,**metadata(row['props'])})
            for prop,val in row['props'].items():props.append({**k,'property':prop,**typed(val)})
        fields=keyfields('a_')+keyfields('b_')
        write(stem,masters,fields+['property_count']);write(stem+'_props',props,fields+['property']+TYPE_FIELDS)
        expected=expression(allowed={p['type'] for p in props})
        match=f'OPTIONAL MATCH(a:{ident(sl)} {{{ident(sk)}:{keyexpr("a_")}}})\nOPTIONAL MATCH(b:{ident(el)} {{{ident(ek)}:{keyexpr("b_")}}})\nOPTIONAL MATCH(a)-[r:{ident(kind)}]->(b)'
        check(stem,load(stem)+'\n'+match+'\nWITH CASE WHEN r IS NOT NULL AND size(keys(r))=toInteger(row.property_count) THEN 0 ELSE 1 END AS bad')
        check(stem+'_props',load(stem+'_props')+'\n'+match+'\nWITH r,row,'+expected+' AS expected\nWITH CASE WHEN r IS NOT NULL AND r[row.property]=expected AND valueType(r[row.property])=valueType(expected) THEN 0 ELSE 1 END AS bad')
    validation='// Read-only: full stable identities, labels, property types/values and endpoint equality.\nCALL () {\n'+ '\nUNION ALL\n'.join(checks)+'\n}\nWITH sum(checks) AS property_and_entity_checks,sum(errors) AS original_entity_mismatches,collect({check:check,errors:errors}) AS sections\nCALL () { MATCH(n) RETURN count(n) AS nodes }\nCALL () { MATCH()-[r]->() RETURN count(r) AS relationships }\nRETURN nodes,relationships,property_and_entity_checks,original_entity_mismatches, nodes=25490 AND relationships=98473 AND original_entity_mismatches=0 AS baseline_exact_match,[s IN sections WHERE s.errors>0] AS failed_sections;\n'
    (DEST/'01_validate_baseline.cypher').write_text(validation,encoding='utf8')
    chunk_size=math.ceil(len(checks)/4)
    for part,start in enumerate(range(0,len(checks),chunk_size),1):
        chunk=checks[start:start+chunk_size]
        part_query='// Read-only baseline validation part '+str(part)+' of 4. All four must pass before import.\nCALL () {\n'+'\nUNION ALL\n'.join(chunk)+'\n}\nWITH sum(checks) AS property_and_entity_checks,sum(errors) AS original_entity_mismatches,collect({check:check,errors:errors}) AS sections\nCALL () { MATCH(n) RETURN count(n) AS nodes }\nCALL () { MATCH()-[r]->() RETURN count(r) AS relationships }\nRETURN '+str(part)+' AS part,nodes,relationships,property_and_entity_checks,original_entity_mismatches, nodes=25490 AND relationships=98473 AND original_entity_mismatches=0 AS part_exact_match,[s IN sections WHERE s.errors>0] AS failed_sections;\n'
        (DEST/f'01_part_{part}_validate_baseline.cypher').write_text(part_query,encoding='utf8')
    plan=json.loads((OUT/'GRAPH_DELTA.json').read_text(encoding='utf8'))
    run=json.dumps(plan['run_id']);ng=defaultdict(list);rg=defaultdict(list);phases=[];cleanup=[];delta_checks=[]
    for n in plan['nodes']:ng[(n['label'],n['key'],tuple(n['labels']))].append(n)
    for r in plan['relationships']:
        a,b=r['start'],r['end'];rg[(r['type'],a['label'],a['key'],b['label'],b['key'],r['merge_property'])].append(r)
    def phase(body):phases.append('CALL () {\n'+body+'\n}')
    for index,((label,key,labels),rows) in enumerate(sorted(ng.items())):
        stem=f'delta_n{index:02d}';masters=[];props=[]
        for n in rows:
            k=typed(n['value'],'k_');masters.append({**k,**metadata(n['props']),'labels':'|'.join(sorted(n['labels']))})
            for prop,val in n['props'].items():props.append({**k,'property':prop,**typed(val)})
        write(stem,masters,keyfields('k_')+['property_count','labels']);write(stem+'_props',props,keyfields('k_')+['property']+TYPE_FIELDS)
        expected=expression(allowed={p['type'] for p in props})
        pattern=f'(n:{ident(label)} {{{ident(key)}:{keyexpr("k_")}}})'
        phase(load(stem)+'\nMERGE'+pattern+'\nON CREATE SET n:'+':'.join(ident(l) for l in labels)+', n._solve_enrichment_tmp='+run)
        phase(load(stem+'_props')+'\nMATCH'+pattern+'\nWHERE n._solve_enrichment_tmp='+run+'\nSET n[row.property]='+expected)
        cleanup.append('MATCH(n:'+ident(label)+') WHERE n._solve_enrichment_tmp='+run+' REMOVE n._solve_enrichment_tmp')
        delta_checks.append(load(stem)+'\nOPTIONAL MATCH'+pattern+'\nWITH CASE WHEN n IS NOT NULL AND size(keys(n))=toInteger(row.property_count) AND size(labels(n))=size(split(row.labels,"|")) AND all(l IN split(row.labels,"|") WHERE l IN labels(n)) THEN 0 ELSE 1 END AS bad\nRETURN '+json.dumps(stem)+' AS check,count(*) AS checks,sum(bad)+abs(count(*)-'+str(len(masters))+') AS errors')
        delta_checks.append(load(stem+'_props')+'\nOPTIONAL MATCH'+pattern+'\nWITH n,row,'+expected+' AS expected\nWITH CASE WHEN n IS NOT NULL AND n[row.property]=expected AND valueType(n[row.property])=valueType(expected) THEN 0 ELSE 1 END AS bad\nRETURN '+json.dumps(stem+'_props')+' AS check,count(*) AS checks,sum(bad) AS errors')
    for index,((kind,sl,sk,el,ek,mergeprop),rows) in enumerate(sorted(rg.items())):
        stem=f'delta_r{index:02d}';masters=[];props=[]
        for r in rows:
            k={**typed(r['start']['value'],'a_'),**typed(r['end']['value'],'b_')}
            if mergeprop:k.update(typed(r['merge_value'],'m_'))
            masters.append({**k,**metadata(r['props'])})
            for prop,val in r['props'].items():props.append({**k,'property':prop,**typed(val)})
        fields=keyfields('a_')+keyfields('b_')+(keyfields('m_') if mergeprop else [])
        write(stem,masters,fields+['property_count']);write(stem+'_props',props,fields+['property']+TYPE_FIELDS)
        expected=expression(allowed={p['type'] for p in props})
        endpoints=f'MATCH(a:{ident(sl)} {{{ident(sk)}:{keyexpr("a_")}}})\nMATCH(b:{ident(el)} {{{ident(ek)}:{keyexpr("b_")}}})'
        marker=' {'+ident(mergeprop)+':'+keyexpr('m_')+'}' if mergeprop else ''
        pattern='(a)-[r:'+ident(kind)+marker+']->(b)'
        phase(load(stem)+'\n'+endpoints+'\nMERGE'+pattern+'\nON CREATE SET r._solve_enrichment_tmp='+run)
        phase(load(stem+'_props')+'\n'+endpoints+'\nMATCH'+pattern+'\nWHERE r._solve_enrichment_tmp='+run+'\nSET r[row.property]='+expected)
        fullpattern=f'(a:{ident(sl)} {{{ident(sk)}:{keyexpr("a_")}}})-[r:{ident(kind)}{marker}]->(b:{ident(el)} {{{ident(ek)}:{keyexpr("b_")}}})'
        delta_checks.append(load(stem)+'\nOPTIONAL MATCH'+fullpattern+'\nWITH CASE WHEN r IS NOT NULL AND size(keys(r))=toInteger(row.property_count) THEN 0 ELSE 1 END AS bad\nRETURN '+json.dumps(stem)+' AS check,count(*) AS checks,sum(bad)+abs(count(*)-'+str(len(masters))+') AS errors')
        delta_checks.append(load(stem+'_props')+'\nOPTIONAL MATCH'+fullpattern+'\nWITH r,row,'+expected+' AS expected\nWITH CASE WHEN r IS NOT NULL AND r[row.property]=expected AND valueType(r[row.property])=valueType(expected) THEN 0 ELSE 1 END AS bad\nRETURN '+json.dumps(stem+'_props')+' AS check,count(*) AS checks,sum(bad) AS errors')
    for body in cleanup:phase(body)
    phase('MATCH()-[r]->() WHERE r._solve_enrichment_tmp='+run+' REMOVE r._solve_enrichment_tmp')
    query='// One atomic transaction; only new entities receive properties. Baseline must validate immediately before execution.\nCALL () { MATCH(n) RETURN count(n) AS before_nodes }\nCALL () { MATCH()-[r]->() RETURN count(r) AS before_relationships }\nWITH before_nodes,before_relationships WHERE before_nodes=25490 AND before_relationships=98473\n'+ '\n'.join(phases)+'\nCALL () { MATCH(n) RETURN count(n) AS after_nodes }\nCALL () { MATCH()-[r]->() RETURN count(r) AS after_relationships }\nRETURN after_nodes,after_relationships,CASE WHEN after_nodes=25537 AND after_relationships=98577 THEN true ELSE (1/0=0) END AS expected_counts,after_nodes-before_nodes AS added_nodes,after_relationships-before_relationships AS added_relationships;\n'
    (DEST/'02_add_constraint.cypher').write_text('CREATE CONSTRAINT groundwater_version_id IF NOT EXISTS FOR (v:GroundwaterBodyVersion) REQUIRE v.version_id IS UNIQUE;\n',encoding='utf8')
    (DEST/'03_apply_additive_delta.cypher').write_text(query,encoding='utf8')
    (DEST/'04_check_result.cypher').write_text('MATCH(n) WITH count(n) AS nodes CALL () { MATCH()-[r]->() RETURN count(r) AS relationships } CALL () { MATCH(w:WaterBodyV2)-[:HAS_EVENT]->(e:Event) RETURN count(DISTINCT e) AS directly_linked_events } RETURN nodes,relationships,directly_linked_events;\n',encoding='utf8')
    for part,start in enumerate(range(0,len(delta_checks),math.ceil(len(delta_checks)/4)),1):
        chunk=delta_checks[start:start+math.ceil(len(delta_checks)/4)]
        (DEST/f'05_part_{part}_verify_new_properties.cypher').write_text('CALL () {\n'+'\nUNION ALL\n'.join(chunk)+'\n}\nRETURN sum(checks) AS checks,sum(errors) AS new_entity_mismatches,sum(errors)=0 AS exact_new_entities,[s IN collect({check:check,errors:errors}) WHERE s.errors>0] AS failed_sections;\n',encoding='utf8')
    (DEST/'05_verify_new_properties.cypher').write_text('// Execute the four 05_part_* files individually to bound query-planner memory.\nRETURN "Run all four 05_part_*_verify_new_properties.cypher files" AS instruction;\n',encoding='utf8')
    stats.update(status='generated',file_url_directory='file:///'+FOLDER+'/',delta_nodes=len(plan['nodes']),delta_relationships=len(plan['relationships']),
                 baseline_property_rows=sum(f['rows'] for f in stats['files'] if f['file'].startswith('baseline') and '_props' in f['file']),
                 csv_total_bytes=sum(p.stat().st_size for p in DEST.glob('*.csv')))
    (DEST/'MANIFEST.json').write_text(json.dumps(stats,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in stats.items() if k!='files'}))


if __name__=='__main__':main()
