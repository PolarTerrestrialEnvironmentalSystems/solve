from pathlib import Path
import json
import hashlib
import zipfile
import sys
ROOT=Path(__file__).resolve().parent.parent
SOURCE=ROOT/'SOLVE_Parsing_Fein_Batch_1.1.0'
sys.path.insert(0,str(SOURCE))
from common import load_env
report=json.loads((SOURCE/'TEST_REPORT.json').read_text(encoding='utf-8'))
report.update(release='1.1.0',parser_version='fine-batch-1.1.0',offline_tests_passed=41,
              live_api_calls_for_release_tests=2,live_repair_tests=1,live_repair_accepted=1,
              historical_cases_replayed=19,historical_self_relationships_quarantined=6)
report['scope'] += ['causal self-edge quarantine before and after namespace resolution/deduplication',
                   'bounded conservative semantic repair and audited withdrawal',
                   'failed repair preserves facts and checkpoint resume avoids new calls',
                   'six real regression fixtures and one accepted live-repair fixture']
(SOURCE/'TEST_REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
files=sorted(p for p in SOURCE.rglob('*') if p.is_file() and '__pycache__' not in p.parts
             and p.name!='RELEASE_SHA256.json' and p.suffix not in {'.pyc','.pyo'}
             and 'results' not in p.relative_to(SOURCE).parts)
assert not any(p.name=='.env' for p in files)
secrets={};load_env(Path(r'C:\Users\jowals001\awi\solve\scripts\graphrag\data\.env'),secrets)
key=secrets['GRAPHRAG_API_KEY2'].encode('utf-8')
assert all(key not in p.read_bytes() for p in files),'Secret found; release refused.'
hashes={p.relative_to(SOURCE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
(SOURCE/'RELEASE_SHA256.json').write_text(json.dumps(hashes,indent=2)+'\n',encoding='utf-8')
files.append(SOURCE/'RELEASE_SHA256.json')
archive=ROOT/'SOLVE_Parsing_Fein_Batch_1.1.0.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for p in files:z.write(p,(Path(SOURCE.name)/p.relative_to(SOURCE)).as_posix())
verification=ROOT/'parsing_model_comparison/guard_release_unpacked'
verification.mkdir(exist_ok=False)
with zipfile.ZipFile(archive) as z:z.extractall(verification)
print(f'Packaged {len(files)} files; secret scan passed; extracted verification copy.')
print('ZIP SHA256:',hashlib.sha256(archive.read_bytes()).hexdigest())
