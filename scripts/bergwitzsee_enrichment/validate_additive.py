"""Verify every pre-existing graph entity/property survived the enrichment unchanged."""
from pathlib import Path
import gzip
import json
from dotenv import dotenv_values
from neo4j import GraphDatabase
from snapshot_baseline import canonical, OUT


def main():
    base=json.loads((OUT/'NEO4J_BASELINE.json').read_text(encoding='utf8'))
    config=dotenv_values(OUT/'runtime/neo4j_preview/preview.env')
    assert config['NEO4J_URI']=='bolt://127.0.0.1:7692'
    expected_nodes={};expected_rels={}
    with gzip.open(OUT/base['snapshot'],'rt',encoding='utf8') as source:
        for line in source:
            row=json.loads(line)
            (expected_nodes if row['kind']=='node' else expected_rels)[row['id']]=row
    errors=[];checked_nodes=checked_rels=0
    with GraphDatabase.driver(config['NEO4J_URI'],auth=(config['NEO4J_USER'],config['NEO4J_PASSWORD'])) as driver:
        with driver.session(database='neo4j') as session:
            for row in session.run('MATCH(n) RETURN elementId(n) AS id,labels(n) AS labels,properties(n) AS props'):
                old=expected_nodes.pop(row['id'],None)
                if old is None:continue
                checked_nodes+=1;props=canonical(row['props'])
                if not set(old['labels']).issubset(row['labels']):errors.append({'node':row['id'],'problem':'old label removed'})
                for key,value in old['props'].items():
                    if key not in props or props[key]!=value:errors.append({'node':row['id'],'property':key,'problem':'original property changed'})
            for row in session.run('MATCH(a)-[r]->(b) RETURN elementId(r) AS id,elementId(a) AS start,elementId(b) AS end,type(r) AS type,properties(r) AS props'):
                old=expected_rels.pop(row['id'],None)
                if old is None:continue
                checked_rels+=1;props=canonical(row['props'])
                if any(row[key]!=old[key] for key in ('start','end','type')):errors.append({'relationship':row['id'],'problem':'original endpoints/type changed'})
                for key,value in old['props'].items():
                    if key not in props or props[key]!=value:errors.append({'relationship':row['id'],'property':key,'problem':'original property changed'})
            if expected_nodes:errors.append({'problem':'original nodes missing','count':len(expected_nodes)})
            if expected_rels:errors.append({'problem':'original relationships missing','count':len(expected_rels)})
            constraints=session.run('SHOW CONSTRAINTS YIELD name,type,labelsOrTypes,properties RETURN name,type,labelsOrTypes,properties').data()
            current_by_name={r['name']:r for r in constraints}
            for old in base['constraints']:
                if current_by_name.get(old['name'])!=old:errors.append({'constraint':old['name'],'problem':'original constraint changed'})
            count=session.run('MATCH(n) RETURN count(n)').single()[0]
            relationships=session.run('MATCH()-[r]->() RETURN count(r)').single()[0]
    report={'status':'passed' if not errors else 'failed','checked_original_nodes':checked_nodes,
            'checked_original_relationships':checked_rels,'original_properties_unchanged':not errors,
            'nodes_now':count,'relationships_now':relationships,'errors':errors[:100],'total_errors':len(errors)}
    (OUT/'ADDITIVE_VALIDATION.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='errors'}))
    if errors:raise SystemExit(1)


if __name__=='__main__':main()
