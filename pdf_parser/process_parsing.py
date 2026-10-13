"""Offline ontology alignment; never connects to a database or calls an LLM."""
import argparse
import json
from collections import Counter
from pathlib import Path
from common import ROOT, VERSION, digest, uid, legacy_hash, read_jsonl, write_json, write_jsonl, waterbody_id
from waterbody_matching import WaterbodyMatcher, default_network, POLICY, normalize_name

KIND_CLASSES = {'Messung':'solve:Observation', 'Bewertung':'solve:Assessment',
 'Zustand':'solve:State', 'Eigenschaft':'solve:PropertyAssertion', 'Maßnahme':'solve:Intervention',
 'Prozess':'solve:Process', 'Ereignis':'solve:Event'}
DEFAULT_INPUT = ROOT/'parsing_tests/results/volltest_20260923_ohne_thinking/fein'
DEFAULT_OUTPUT = ROOT/'output/processed_parsing'
DEFAULT_REVIEWS = [ROOT/'output/integrationspilot/user_review_decisions.json',
                   ROOT/'output/integration_sachsen_anhalt/classification_review_decisions.json']


def process(input_dir, 
            output_root, 
            ontology_path, 
            review_paths, 
            reference_path=None, 
            network_path=None):
    events=list(read_jsonl(input_dir/'events.jsonl'))
    relations=list(read_jsonl(input_dir/'relationships.jsonl'))
    ontology=json.loads(ontology_path.read_text(encoding='utf-8'))
    parents={c['id']:c.get('parent_class') for c in ontology['classes']}
    def descendant(c, root):
        seen=set()
        while c and c not in seen:
            if c==root: return True
            seen.add(c);c=parents.get(c)
        return False
    def target(c):
        return {'core:SpatialUnit':'solve:Region','core:Document':'solve:SourceVersion',
                'core:Relation':'solve:RelationshipAssertion','core:CausalRelation':'solve:ReportedCausalRelationship'}.get(c,'solve:'+c.split(':',1)[1])
    sources_in=[input_dir/'events.jsonl',input_dir/'relationships.jsonl',ontology_path,*review_paths,
                Path(__file__),Path(__file__).with_name('common.py'),Path(__file__).with_name('waterbody_matching.py')]
    reviews=[]
    reviewed_payloads={}
    for path in review_paths:
        reviews.extend(json.loads(path.read_text(encoding='utf-8')))
        snapshot=path.parent/'classification_candidates.json'
        if snapshot.exists():
            sources_in.append(snapshot)
            reviewed_payloads.update({r['finding_id']:r['original_payload'] for r in json.loads(snapshot.read_text(encoding='utf-8'))})
    by_review={}
    for r in sorted(reviews,key=lambda r:r['reviewed_at']):
        by_review.setdefault(r['finding_id'],[]).append(r)
    matcher=None
    if reference_path:
        matcher=WaterbodyMatcher(reference_path,network_path or default_network(reference_path))
        sources_in.extend(matcher.files)
    inputs=[dict(path=str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p),sha256=digest(p)) for p in sources_in]
    batch=uid(VERSION,inputs)
    out=output_root/batch[:16]
    out.mkdir(parents=True,exist_ok=True)
    tables={k:[] for k in ['findings','relationships','quantities','evidence','mentions','entity_links','reviews','sources','issues','concepts']}
    for c in ontology['classes']:
        tables['concepts'].append(dict(id=target(c['id']),source_concept=c['id'],label=c['label_de'],
            parent=target(c['parent_class']) if c.get('parent_class') else None,definition=c['definition']))
    tables['concepts'].append(dict(id='solve:PropertyAssertion',parent='solve:Finding',label='Eigenschaftsaussage'))
    source_ids={}; event_ids={}
    for e in events:
        doc=e['source_document'];sid='source:'+uid(doc.get('content_hash'),doc.get('document_id'))
        source_ids[sid]=dict(id=sid,original=doc)
        # Include extraction run: a rerun never overwrites an earlier assertion.
        eid='finding:'+uid(sid,e.get('extraction_metadata',{}).get('run_signature'),e['event_id'])
        if e['event_id'] in event_ids: raise ValueError('Duplicate source event ID')
        event_ids[e['event_id']]=eid
    def evidence(owner, values):
        for n,v in enumerate(values or []):
            tables['evidence'].append(dict(id='evidence:'+uid(owner,n),owner_id=owner,original=v,
                                          review_status='source_excerpt_not_independently_reverified'))
    for e in events:
        eid=event_ids[e['event_id']]; kind=e['record_type']; concept=e.get('ontology_concept')
        statement_status=e.get('quality_assessment',{}).get('review_status','needs_review')
        classification_status='needs_review'
        for d in by_review.get(e['event_id'],[]):
            snapshot=reviewed_payloads.get(e['event_id'])
            if legacy_hash(e)!=d['original_payload_hash'] and not (snapshot==e and legacy_hash(snapshot)==d['original_payload_hash']):
                raise ValueError('Review source mismatch: '+e['event_id'])
            kind=d['kind'];concept=d['concept'];classification_status='accepted'
            if d['review_scope']=='statement_and_classification_of_displayed_example':statement_status='accepted'
            tables['reviews'].append(dict(id='review:'+uid(d),finding_id=eid,original=d))
        finding_class=target(concept) if concept in parents and descendant(concept,'core:Finding') else KIND_CLASSES.get(kind,'solve:Finding')
        object_class=target(concept) if concept in parents and descendant(concept,'core:Entity') else None
        mapping_status='reviewed_classification' if classification_status=='accepted' else 'structural_mapping_unreviewed'
        if not concept or concept not in parents:
            tables['issues'].append(dict(id='issue:'+uid(eid,'class'),owner_id=eid,code='missing_or_unknown_source_concept',original=concept))
        expected_source=KIND_CLASSES.get(kind,'solve:Finding').replace('solve:','core:')
        if finding_class!=KIND_CLASSES.get(kind) and not object_class and not descendant(concept,expected_source):
            tables['issues'].append(dict(id='issue:'+uid(eid,'granularity'),owner_id=eid,code='source_kind_and_concept_require_alignment_review',kind=kind,concept=concept,target_class=finding_class))
        doc=e['source_document'];sid='source:'+uid(doc.get('content_hash'),doc.get('document_id'))
        tables['findings'].append(dict(id=eid,source_record_id=e['event_id'],source_id=sid,target_class=finding_class,
          kind=kind,source_concept=concept,object_class=object_class,statement=e['statement'],
          statement_review_status=statement_status,classification_review_status=classification_status,mapping_status=mapping_status,
          warning='Aussage und Klassifikation geprüft; Objekt-/Wertzuordnung separat prüfen' if statement_status=='accepted' else
                  'Klassifikation geprüft; Aussage ungeprüft' if classification_status=='accepted' else 'Ungeprüfte LLM-Extraktion',
          assertion_mode=e.get('assertion_mode'),polarity=e.get('polarity'),implementation_status=e.get('implementation_status'),
          time={k:e.get(k) for k in ['time_original','time_normalized','time_precision','time_status']},
          measurement_context=e.get('measurement_context'),categories=e.get('categories'),original=e))
        evidence(eid,e.get('evidence'))
        for n,q in enumerate(e.get('values') or []):
            tables['quantities'].append(dict(id='quantity:'+uid(eid,n),finding_id=eid,ordinal=n,original=q,
                role='observation_result_candidate' if descendant(concept,'core:Observation') else 'property_or_quantity_candidate',
                review_status='needs_review',normalization_status='parser_normalization_not_verified'))
        mentions=[dict(field='waterbody_references',value=x) for x in e.get('waterbody_references') or []]
        mentions += [dict(field='participants',value=x) for x in e.get('participants') or []]
        if e.get('waterbody_name_original') or e.get('waterbody_identifiers'):
            mentions.append(dict(field='waterbody_name_original',value=dict(name_original=e.get('waterbody_name_original'),identifiers=e.get('waterbody_identifiers'))))
        if (e.get('measurement_context') or {}).get('station'):
            mentions.append(dict(field='measurement_context.station',value=dict(name_original=e['measurement_context']['station'])))
        for n,m in enumerate(mentions):
            mid='mention:'+uid(eid,n)
            mention=dict(id=mid,finding_id=eid,source_field=m['field'],original=m['value'],identity_status='unresolved')
            tables['mentions'].append(mention)
            value=m['value']
            eligible=(m['field'] in {'waterbody_references','waterbody_name_original'} or
                      (m['field']=='participants' and value.get('class_id')=='core:WaterBody'))
            if normalize_name(value.get('role')) in {'planungsgebiet','schutzgebiet','einzugsgebiet'}:
                eligible=False
            if matcher and eligible:
                # Use coordinates only when explicitly attached to this mention.
                geometry=None
                if isinstance(value.get('geometry'),dict):
                    from shapely.geometry import shape
                    geometry=shape(value['geometry'])
                result=matcher.match([value.get('name_original')],value.get('identifiers'),geometry,value.get('crs'))
                mention['waterbody_assignment_status']=result['status']
                mention['assignment_warnings']=result['warnings']
                mention['unmatched_identifiers']=result['unmatched_identifiers']
                for link in result['links']:
                    tables['entity_links'].append(dict(id='link:'+uid(mid,link['target_code']),finding_id=eid,mention_id=mid,
                        reference_source=str(reference_path),**link))
    tables['sources']=list(source_ids.values())
    for r in relations:
        rid='relationship:'+uid(batch,r['relationship_id'])
        a=event_ids.get(r['source_event_id']);b=event_ids.get(r['target_event_id'])
        if not a or not b: raise ValueError('Relationship endpoint missing')
        tables['relationships'].append(dict(id=rid,source_record_id=r['relationship_id'],target_class='solve:RelationshipAssertion',
          source_id=a,target_id=b,predicate=r['relationship_type'],scope=r['relationship_scope'],statement=r['statement'],
          review_status='needs_review',causal_status='reported_not_established',
          endpoint_issue='same_source_and_target' if a==b else 'not_semantically_reviewed',original=r))
        evidence(rid,r.get('evidence'))
    for name,rows in tables.items():
        if len({r['id'] for r in rows})!=len(rows):raise ValueError('Duplicate IDs in '+name)
        write_jsonl(out/(name+'.jsonl'),rows)
    manifest=dict(version=VERSION,batch_id=batch,inputs=inputs,counts={k:len(v) for k,v in tables.items()},
      review_counts=dict(statement_accepted=sum(f['statement_review_status']=='accepted' for f in tables['findings']),
                         classification_accepted=sum(f['classification_review_status']=='accepted' for f in tables['findings'])),
      target_classes=dict(Counter(f['target_class'] for f in tables['findings'])),
      waterbody_matching=dict(policy=POLICY,reference_fingerprint=matcher.fingerprint if matcher else None,
          assignments=dict(Counter(m.get('waterbody_assignment_status','not_applicable') for m in tables['mentions'])),
          link_statuses=dict(Counter(link['assignment_status'] for link in tables['entity_links']))),
      files={k+'.jsonl':digest(out/(k+'.jsonl')) for k in tables},
      limitations=['Strukturelles Ontologiemapping ist keine fachliche Freigabe.',
        'Objekterwähnungen werden nicht allein anhand Namen verschmolzen.',
        'Parameter, Einheiten und Zeitnormalisierung bleiben ohne zusätzliche Prüfung Parserangaben.',
        'Kein LLM, keine Datenbankverbindung und keine Kausalitätsinferenz.'])
    write_json(out/'manifest.json',manifest)
    write_json(output_root/'latest.json',dict(batch_dir=out.name,manifest_sha256=digest(out/'manifest.json')))
    return out,manifest


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',type=Path,default=DEFAULT_INPUT)
    ap.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    ap.add_argument('--ontology',type=Path,default=ROOT/'parsing_tests/ontologie_fein.json')
    ap.add_argument('--reviews',type=Path,nargs='*',default=DEFAULT_REVIEWS)
    ap.add_argument('--waterbodies',type=Path,default=ROOT/'Sachsen_Anhalt/Oberflächenwasserkörper/Oberflächenwasserkörper.shp')
    ap.add_argument('--network',type=Path,help='Gewässernetz with explicit owk_st and alternative codes; detected beside register by default')
    args=ap.parse_args()
    out,m=process(args.input,args.output,args.ontology,args.reviews,args.waterbodies,args.network)
    print(json.dumps(dict(output=str(out),counts=m['counts'],reviews=m['review_counts']),ensure_ascii=True))


if __name__=='__main__': main()
