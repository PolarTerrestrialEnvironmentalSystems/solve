"""Reproducible, sequential three-way pilot; secrets stay in process memory."""
from pathlib import Path
import argparse
import importlib.metadata
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
VARIANTS = {'reference': ('baseline','nvidia/Qwen3.8-Flash-Next-NVFP4','off'),
            'compact_qwen': ('compact','nvidia/Qwen3.8-Flash-Next-NVFP4','off'),
            'compact_gptoss': ('compact','openai/gpt-oss-120b','auto')}

def worker(variant, index):
    code, model_root, thinking = VARIANTS[variant]
    sys.path.insert(0,str(ROOT/code))
    from common import load_env, read_json, write_json, digest, file_hash, utc_now, RunLock
    from blablador_client import Settings, BlabladorClient
    from extract_facts import extract_chunk, context_jobs, SYSTEM_PROMPT
    from schemas import Event, Relationship, Extraction, read_headers
    from validate_export import finalize, export_variant
    snapshot = read_json(ROOT/'models.json')
    identity = next(m for m in snapshot['data'] if m.get('root') == model_root and not m['id'].startswith('alias-'))
    env = {}
    load_env(Path(r'C:\Users\jowals001\awi\solve\scripts\graphrag\data\.env'),env)
    settings = Settings(api_key=env['GRAPHRAG_API_KEY2'],model=identity['id'],rpm=30,
                        timeout=300,max_retries=1,max_output_tokens=16000,
                        context_tokens=65536,temperature=0,json_mode='auto',thinking_mode=thinking)
    prepared = read_json(ROOT/'prepared_all.json')
    by_id = {b['block_id']:b for b in prepared['blocks']}
    def span(block_id, start=None, end=None):
        block=dict(by_id[block_id])
        text=block['text']
        begin=text.index(start) if start else 0
        finish=text.index(end,begin) if end else len(text)
        block['text']=text[begin:finish].strip()
        return block
    cases = [{'chunk_id':'pilot_measures','pages':[2],
              'blocks':[span('p0002_b0001','Die Bedeutung des Themas'),
                        span('p0002_b0002',end='Betrachtung des hydrologischen Jahres:')],
              'context_blocks':[]},
             {'chunk_id':'pilot_uncertainty','pages':[3],
              'blocks':[span('p0003_b0001','Bei Betrachtung der Zeitreihe','Zu den extrem trockenen Sommern')],
              'context_blocks':[]},
             {'chunk_id':'pilot_table_2016_2030','pages':[5],
              'blocks':[by_id['p0005_b0009'],by_id['p0005_b0011']],
              'context_blocks':[span('p0003_b0004','Maßeinheit')]}]
    cover = [by_id['p0001_b0001']]
    ontology = read_json(ROOT/code/'ontologie_fein.json')
    headers = {'events':read_headers(ROOT/code/'Event_headers.xlsx',Event),
               'relationships':read_headers(ROOT/code/'relationship_headers.xlsx',Relationship)}
    output = ROOT/'results'/variant
    config = {'variant':variant,'settings':settings.public(),'workers':1,'repetitions':1,
              'context_calls':0,'model_identity':{k:identity.get(k) for k in ['id','root','max_model_len']},
              'pdf':prepared['document'],'cases':cases,'cover':cover,
              'code_hashes':{p.name:file_hash(p) for p in sorted((ROOT/code).glob('*.py'))},
              'schema_hash':digest(Extraction.model_json_schema()),'prompt_hash':digest(SYSTEM_PROMPT),
              'ontology_hash':file_hash(ROOT/code/'ontologie_fein.json'),
              'dependencies':{n:importlib.metadata.version(n) for n in ['pdfplumber','pypdf','pydantic','openpyxl']},
              'python':sys.version}
    signature = digest(config)
    result_file = output/f'case_{index}.json'
    if result_file.exists():
        raise RuntimeError('Existing result; use a fresh results directory for a new benchmark.')
    with RunLock(ROOT/'.pilot.lock'):
        write_json(output/'manifest.json',{'signature':signature,'config':config,'updated_at':utc_now()})
        client = BlabladorClient(settings,output/'logs',ROOT/'api_attempts.jsonl',variant)
        client.configure_models(snapshot)
        started = time.perf_counter()
        print(f'START {variant} case {index+1}: {cases[index]["chunk_id"]}',flush=True)
        try:
            packages = extract_chunk(client,cases[index],cover,ontology,headers,output/'fein',signature,'fein')
        finally:
            client.close()
        elapsed = time.perf_counter()-started
        write_json(result_file,{'index':index,'elapsed_seconds':elapsed,'packages':packages,'finished':utc_now()})
        all_packages = []
        for p in sorted(output.glob('case_*.json')):
            all_packages.extend(read_json(p)['packages'])
        _,extra = context_jobs(all_packages,prepared['blocks'],0)
        events,relationships,review,summary = finalize(all_packages,ontology,prepared['document'],signature,'fein',extra)
        summary.update(pilot=True,scheduled_primary_chunks=3,completed_cases=len(list(output.glob('case_*.json'))))
        export_variant(output/'fein',events,relationships,review,summary,headers)
        print(f'DONE {variant} case {index+1}: {elapsed:.1f}s; cumulative {len(events)} events, {len(relationships)} relationships',flush=True)

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--variant',choices=VARIANTS)
    parser.add_argument('--case',type=int)
    args=parser.parse_args()
    if args.variant:
        worker(args.variant,args.case)
    else:
        orders=[['reference','compact_qwen','compact_gptoss'],
                ['compact_qwen','compact_gptoss','reference'],
                ['compact_gptoss','reference','compact_qwen']]
        for index,order in enumerate(orders):
            for variant in order:
                subprocess.run([sys.executable,'-u',str(Path(__file__)), '--variant',variant,'--case',str(index)],check=True)
