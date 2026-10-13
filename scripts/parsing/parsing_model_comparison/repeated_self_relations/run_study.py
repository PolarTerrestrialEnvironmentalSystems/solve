"""Repeated paired Qwen experiment, without changing either extraction prompt."""
from pathlib import Path
import sys
import json
import time
import subprocess
import argparse
ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent

def setup():
    sys.path.insert(0,str(BASE/'baseline'))
    from common import read_json,write_json,digest,file_hash
    prepared=read_json(ROOT/'prepared_all.json')
    blocks={b['block_id']:b for b in prepared['blocks']}
    def span(key,start=None,end=None):
        b=dict(blocks[key]); text=b['text']; begin=text.index(start) if start else 0
        finish=text.index(end,begin) if end else len(text)
        b['text']=text[begin:finish].strip(); return b
    cases=[{'chunk_id':'moldau_qm364','pages':[10],
            'blocks':[span('p0010_b0002',end='Dieser Wert liegt')],'context_blocks':[]},
           {'chunk_id':'eger_elbe','pages':[11],
            'blocks':[span('p0011_b0003','Die Talsperren im Einzugsgebiet der Eger','Nach Untersuchungen')],'context_blocks':[]},
           {'chunk_id':'seasonal_climate','pages':[17],
            'blocks':[span('p0017_b0003','Es wird auch zukünftig'),span('p0017_b0004',end='Von dem Einzugsgebiet')],
            'context_blocks':[]}]
    plan={'repetitions':3,'variants':['reference','compact'],'cases':cases,'cover':[],
          'document':prepared['document'],'pdf_sha256':prepared['document'].get('sha256'),
          'selection':'Three explicit causal passages; selected before new model responses.',
          'unit':'One passage x one repetition x one prompt; fresh response checkpoints per unit.',
          'primary_metric':'Runs with at least one source_event_id == target_event_id in schema-valid packages and final exports.',
          'secondary_metric':'Self edges / all edges; zero-edge runs and failed runs reported separately.',
          'limitations':'Three passages from one document; repeated samples are not independent documents. Temperature zero is not a guarantee of identical responses.'}
    path=ROOT/'study_plan.json'
    if path.exists():
        assert read_json(path)==plan,'Study plan changed.'
    else: write_json(path,plan)
    return plan

def worker(variant,case_index,repeat):
    code=BASE/('baseline' if variant=='reference' else 'compact')
    sys.path.insert(0,str(code))
    from common import load_env,read_json,read_jsonl,write_json,digest,file_hash,utc_now,RunLock
    from blablador_client import Settings,BlabladorClient
    from extract_facts import extract_chunk,context_jobs,SYSTEM_PROMPT
    from schemas import Event,Relationship,Extraction,read_headers
    from validate_export import finalize,export_variant
    plan=read_json(ROOT/'study_plan.json'); case=plan['cases'][case_index]
    snapshot=read_json(ROOT/'models.json')
    identity=next(m for m in snapshot['data'] if m.get('root')=='nvidia/Qwen3.8-Flash-Next-NVFP4' and not m['id'].startswith('alias-'))
    env={}; load_env(Path(r'C:\Users\jowals001\awi\solve\scripts\graphrag\data\.env'),env)
    settings=Settings(api_key=env['GRAPHRAG_API_KEY2'],model=identity['id'],rpm=30,timeout=300,max_retries=1,
                      max_output_tokens=16000,context_tokens=65536,temperature=0,json_mode='auto',thinking_mode='off')
    name=f'r{repeat+1}_{case["chunk_id"]}_{variant}'
    output=ROOT/'results'/name
    if output.exists(): raise RuntimeError('Run folder exists: refusing benchmark cache reuse.')
    config={'variant':variant,'repeat':repeat+1,'case_index':case_index,'case':case,'cover':plan['cover'],
            'model_identity':{k:identity.get(k) for k in ['id','root','max_model_len']},'settings':settings.public(),
            'code_hashes':{p.name:file_hash(p) for p in sorted(code.glob('*.py'))},'prompt_hash':digest(SYSTEM_PROMPT),
            'schema_hash':digest(Extraction.model_json_schema()),'ontology_hash':file_hash(code/'ontologie_fein.json'),
            'plan_hash':digest(plan),'controller_hash':file_hash(Path(__file__))}
    signature=digest(config)
    ontology=read_json(code/'ontologie_fein.json')
    headers={'events':read_headers(code/'Event_headers.xlsx',Event),'relationships':read_headers(code/'relationship_headers.xlsx',Relationship)}
    with RunLock(ROOT/'.study.lock'):
        write_json(output/'manifest.json',{'signature':signature,'started':utc_now(),'config':config})
        client=BlabladorClient(settings,output/'logs',ROOT/'api_attempts.jsonl',name)
        client.configure_models(snapshot)
        started=time.perf_counter(); print('START',name,flush=True)
        try:
            packages=extract_chunk(client,case,plan['cover'],ontology,headers,output/'fein',signature,'fein')
        finally: client.close()
        elapsed=time.perf_counter()-started
        _,extra=context_jobs(packages,case['blocks'],0)
        events,rels,review,summary=finalize(packages,ontology,plan['document'],signature,'fein',extra)
        export_variant(output/'fein',events,rels,review,summary,headers)
        rawrels=[r for p in packages if p['status']=='complete' for r in p['result']['relationships']]
        self_raw=[r for r in rawrels if r['source_event_id']==r['target_event_id']]
        self_final=[r for r in rels if r['source_event_id']==r['target_event_id']]
        logs=read_jsonl(output/'logs/api_requests.jsonl')
        finishes=[r for r in logs if r['event']=='request_finished']
        result={'name':name,'variant':variant,'repeat':repeat+1,'case':case['chunk_id'],'elapsed_seconds':elapsed,
                'packages':packages,'all_packages_complete':all(p['status']=='complete' for p in packages),
                'raw_events':sum(len(p['result']['events']) for p in packages if p['status']=='complete'),
                'raw_relationships':len(rawrels),'raw_self_relationships':len(self_raw),'raw_self_examples':self_raw,
                'exported_events':len(events),'exported_relationships':len(rels),'exported_self_relationships':len(self_final),
                'exported_self_examples':self_final,'http_attempts':sum(r['event']=='request_started' for r in logs),
                'completion_tokens':sum((r.get('usage') or {}).get('completion_tokens',0) for r in finishes),
                'finished':utc_now()}
        write_json(output/'result.json',result)
        print(f'DONE {name}: {elapsed:.1f}s; raw self {len(self_raw)}/{len(rawrels)}, export self {len(self_final)}/{len(rels)}',flush=True)

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(); p.add_argument('--variant',choices=['reference','compact']); p.add_argument('--case',type=int); p.add_argument('--repeat',type=int)
    args=p.parse_args()
    if args.variant: worker(args.variant,args.case,args.repeat)
    else:
        plan=setup()
        for repeat in range(plan['repetitions']):
            for index in range(len(plan['cases'])):
                order=['reference','compact'] if (repeat+index)%2==0 else ['compact','reference']
                for variant in order:
                    subprocess.run([sys.executable,'-u',str(Path(__file__)),'--variant',variant,'--case',str(index),'--repeat',str(repeat)],check=True)
