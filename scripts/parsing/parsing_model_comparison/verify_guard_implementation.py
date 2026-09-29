from pathlib import Path
import sys
import copy
ROOT=Path(__file__).resolve().parent
CODE=ROOT.parent/'SOLVE_Parsing_Fein_Batch_1.1.0'
sys.path.insert(0,str(CODE))
from common import read_json,write_json,load_env
from schemas import Extraction,Event,Relationship,read_headers
from validate_export import finalize,export_variant
from repair_relationships import repair_causal_relationships
from extract_facts import build_messages
from blablador_client import Settings,BlabladorClient

OUT=ROOT/'guard_implementation_validation'
ontology=read_json(CODE/'ontologie_fein.json')
if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sources=list((ROOT/'repeated_self_relations/results').glob('*/result.json'))
    sources += [ROOT/'results/compact_qwen/case_0.json']
    checks=[]
    for path in sources:
        packages=read_json(path)['packages']
        events,rels,review,summary=finalize(packages,ontology,{},'replay','fein')
        remaining=[r for r in rels if r['source_event_id']==r['target_event_id']]
        assert not remaining
        checks.append({'source':str(path),'exported_events':len(events),'exported_relationships':len(rels),
                       'self_relationships_quarantined':sum('causal_self_relationship' in r.get('reasons',[]) for r in review)})
    assert sum(c['self_relationships_quarantined'] for c in checks)==6
    write_json(OUT/'offline_replay.json',{'cases':checks,'all_6_known_self_relationships_quarantined':True})
    print('Replay: 19 stored cases, all 6 known causal self relationships quarantined.',flush=True)
    if '--live' not in sys.argv:raise SystemExit(0)
    destination=OUT/'live_repair'
    if destination.exists():raise RuntimeError('Live test output exists; no automatic repeat.')
    config=read_json(ROOT/'results/compact_qwen/manifest.json')['config']
    original=read_json(ROOT/'results/compact_qwen/case_0.json')['packages'][0]
    parsed=Extraction.model_validate(original['result'])
    headers={'events':read_headers(CODE/'Event_headers.xlsx',Event),
             'relationships':read_headers(CODE/'relationship_headers.xlsx',Relationship)}
    messages=build_messages(config['cases'][0],config['cover'],ontology,headers)
    env={};load_env(Path(r'C:\Users\jowals001\awi\solve\scripts\graphrag\data\.env'),env)
    settings=Settings(**config['settings'],api_key=env['GRAPHRAG_API_KEY2'])
    client=BlabladorClient(settings,destination/'logs',OUT/'api_attempts.jsonl','guard_live_repair')
    try:
        snapshot=client.models();client.configure_models(snapshot)
        write_json(destination/'models.json',snapshot)
        candidate,audit,repaired_path=repair_causal_relationships(client,parsed,messages,
            config['cases'][0],config['cover'],ontology,destination,'fein',Path(original['raw_response_file']),[])
    finally:client.close()
    package={**original,'result':candidate.model_dump(mode='json'),'relationship_review':audit}
    if repaired_path:package['raw_response_file']=str(repaired_path)
    events,rels,review,summary=finalize([package],ontology,config['pdf'],'guard_live','fein')
    assert not any(r['source_event_id']==r['target_event_id'] for r in rels)
    export_variant(destination/'fein',events,rels,review,summary,headers)
    write_json(destination/'outcome.json',{'audit':audit,'summary':summary,'no_causal_self_relationship_exported':True})
    print('Live repair:',audit[0]['status'],audit[0].get('reason',''),'; exported relationships:',len(rels),flush=True)
