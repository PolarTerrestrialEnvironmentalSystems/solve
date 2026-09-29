"""Minimal availability probe, excluded from benchmark timings."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent.parent/'SOLVE_Parsing_Fein_Batch_1.1.0'))
from common import load_env,read_json,write_json
from blablador_client import Settings,BlabladorClient,APIError
env={};load_env(Path(r'C:\Users\jowals001\awi\solve\scripts\graphrag\data\.env'),env)
snapshot=read_json(ROOT/'models.json')
model=next(m for m in snapshot['data'] if m.get('root')=='nvidia/Qwen3.8-Flash-Next-NVFP4' and not m['id'].startswith('alias-'))
settings=Settings(api_key=env['GRAPHRAG_API_KEY2'],model=model['id'],timeout=30,max_retries=0,max_output_tokens=256,rpm=30,thinking_mode='off')
client=BlabladorClient(settings,ROOT/'probe_logs',ROOT/'api_attempts.jsonl','availability_probe')
client.configure_models(snapshot)
try:
    result=client.complete([{'role':'user','content':'Return exactly {"ok":true}.'}],{'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False},purpose='availability_probe',ontology='none',chunk_id='none')
    write_json(ROOT/'probe_result.json',result)
    print('Probe succeeded:',result['choices'][0]['message']['content'])
except APIError as e:
    print('Probe failed:',str(e))
    write_json(ROOT/'probe_failure.json',{'status':e.status,'error':str(e)})
finally:client.close()
