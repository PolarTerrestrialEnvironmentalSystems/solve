from pathlib import Path
import sys
import json
sys.path.insert(0, str(Path(__file__).parent / 'baseline'))
from common import load_env, write_json
from blablador_client import Settings, BlabladorClient
from pypdf import PdfReader

ROOT = Path(__file__).parent
PDF = Path(r'C:\Users\jowals001\awi\solve\scripts\SOLVE_multisite_code_v1_1_0\dossier_33_seen_serper_v1_6_2_20260925\archive\pdf\d1367f5d9f0e19451126eec4ad6cb76920c5f0d51ac3f7de4736e74f4252ee39.pdf')
if __name__ == '__main__':
    reader = PdfReader(PDF)
    pages = [{'page': i+1, 'text': p.extract_text() or ''} for i,p in enumerate(reader.pages)]
    write_json(ROOT / 'pdf_text.json', pages)
    print('PDF pages:',len(pages))
    for p in pages:
        print(p['page'],len(p['text']),p['text'][:220].replace('\n',' '))
    secrets = {}
    load_env(Path(r'C:\Users\jowals001\awi\solve\scripts\graphrag\data\.env'), secrets)
    settings = Settings(api_key=secrets['GRAPHRAG_API_KEY2'],rpm=30,timeout=60,max_retries=0)
    settings.validate()
    client = BlabladorClient(settings, ROOT/'catalog_logs', ROOT/'api_attempts.jsonl', 'catalog')
    try:
        snapshot = client.models()
        write_json(ROOT/'models.json',snapshot)
        print(json.dumps(snapshot,ensure_ascii=False))
    except Exception as exc:
        print('Catalog failed:',type(exc).__name__,str(exc).replace(settings.api_key,'[REDACTED]'))
    finally:
        client.close()
