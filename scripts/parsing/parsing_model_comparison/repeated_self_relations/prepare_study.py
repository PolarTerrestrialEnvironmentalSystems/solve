from pathlib import Path
import sys
import json
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'baseline'))
from common import write_json,load_env
from prepare_document import prepare_document
from blablador_client import Settings,BlabladorClient
import pypdfium2 as pdfium
PDF=Path(r'C:\Users\jowals001\awi\solve\scripts\SOLVE_multisite_code_v1_1_0\dossier_33_seen_serper_v1_6_2_20260925\archive\pdf\1bfed581c2f2d2d5047c584035c526a2eb9c75315447f09b9d482f053671f44d.pdf')
if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    prepared=prepare_document(PDF,ROOT/'prepared','10,11,17',2000)
    write_json(ROOT/'prepared_all.json',prepared)
    for b in prepared['blocks']:
        print(b['block_id'],len(b['text']),repr(b['text'][:130]))
    doc=pdfium.PdfDocument(str(PDF))
    for n in [9,10,16]:
        page=doc[n]; bitmap=page.render(scale=1.25)
        bitmap.to_pil().save(ROOT/f'page_{n+1}.png'); bitmap.close(); page.close()
    doc.close()
    env={}; load_env(Path(r'C:\Users\jowals001\awi\solve\scripts\graphrag\data\.env'),env)
    client=BlabladorClient(Settings(api_key=env['GRAPHRAG_API_KEY2'],rpm=30,timeout=60,max_retries=0),ROOT/'catalog_logs',ROOT/'api_attempts.jsonl','catalog')
    try:
        snapshot=client.models(); write_json(ROOT/'models.json',snapshot)
        print('Reference model:',[{'id':m['id'],'root':m['root']} for m in snapshot['data'] if m.get('root')=='nvidia/Qwen3.8-Flash-Next-NVFP4'])
    finally: client.close()
