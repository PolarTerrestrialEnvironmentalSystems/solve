"""Read-only snapshot of every original graph node/property/relationship for additive validation."""
from pathlib import Path
from collections import Counter
import gzip
import hashlib
import json
from dotenv import dotenv_values
from neo4j import GraphDatabase

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/bergwitzsee_ergaenzung_20261102'


def canonical(value):
    if hasattr(value,'srid'):
        return {'__type__':'Point','srid':value.srid,'coordinates':list(value)}
    if isinstance(value,dict):return {k:canonical(v) for k,v in sorted(value.items())}
    if isinstance(value,(list,tuple)):return [canonical(x) for x in value]
    if hasattr(value,'iso_format'):return {'__type__':type(value).__name__,'value':value.iso_format()}
    if hasattr(value,'isoformat'):return {'__type__':type(value).__name__,'value':value.isoformat()}
    return value


def main():
    config=dotenv_values(OUT/'runtime/neo4j_preview/preview.env')
    assert config['NEO4J_URI']=='bolt://127.0.0.1:7692'
    destination=OUT/'baseline_graph.jsonl.gz'
    if destination.exists():raise RuntimeError('Baseline already exists; refusing overwrite')
    label_counts=Counter();type_counts=Counter();digest=hashlib.sha256()
    counts={'nodes':0,'relationships':0}
    with GraphDatabase.driver(config['NEO4J_URI'],auth=(config['NEO4J_USER'],config['NEO4J_PASSWORD'])) as driver:
        with driver.session(database='neo4j') as session,gzip.open(destination,'wt',encoding='utf8',newline='\n') as out:
            for row in session.run('MATCH(n) RETURN elementId(n) AS id,labels(n) AS labels,properties(n) AS props ORDER BY id'):
                value={'kind':'node','id':row['id'],'labels':sorted(row['labels']),'props':canonical(row['props'])}
                line=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'
                out.write(line);digest.update(line.encode());counts['nodes']+=1;label_counts.update(value['labels'])
            for row in session.run('MATCH(a)-[r]->(b) RETURN elementId(r) AS id,elementId(a) AS start,elementId(b) AS end,type(r) AS type,properties(r) AS props ORDER BY id'):
                value={'kind':'relationship',**row.data()};value['props']=canonical(value['props'])
                line=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'
                out.write(line);digest.update(line.encode());counts['relationships']+=1;type_counts.update([value['type']])
            water=session.run('MATCH(n:WaterBodyV2) RETURN n.water_body_id AS id,n.name AS name').data()
            assert water==[{'id':'DELW_DEST_EL03OW07-00','name':'Bergwitzsee'}]
            constraints=session.run('SHOW CONSTRAINTS YIELD name,type,labelsOrTypes,properties RETURN name,type,labelsOrTypes,properties').data()
    assert counts=={'nodes':25490,'relationships':98473}
    report={'status':'passed',**counts,'counts_by_label':dict(label_counts),'counts_by_relationship_type':dict(type_counts),
        'water_bodies':water,'snapshot':destination.name,'snapshot_sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),
        'canonical_contents_sha256':digest.hexdigest(),'constraints':constraints,'all_properties_preserved_in_snapshot':True}
    (OUT/'NEO4J_BASELINE.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'status':'passed',**counts,'snapshot':str(destination)}))


if __name__=='__main__':main()
