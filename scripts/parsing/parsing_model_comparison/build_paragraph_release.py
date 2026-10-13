"""Package the verified 1.2.0 release, scan for credentials, verify distribution."""
from pathlib import Path
import json, hashlib, zipfile, shutil, sys, subprocess
BASE=Path(__file__).resolve().parent
SOURCE=BASE.parent/'SOLVE_Parsing_Fein_Batch_1.2.0'
STUDY=BASE/'paragraph_quality_study'
sys.path.insert(0,str(SOURCE))
from common import load_env
assert (STUDY/'Testbericht.md').exists()
summary=json.loads((STUDY/'verified_summary.json').read_text(encoding='utf-8'))
assert len(summary['runs'])==2
shutil.copyfile(STUDY/'Testbericht.md',SOURCE/'LIVE_TEST_REPORT.md')
(SOURCE/'TEST_REPORT.json').write_text(json.dumps({'release':'1.2.0','parser_version':'fine-batch-1.2.0',
    'schema_version':'1.1.0','offline_tests_passed':59,'live_validation':summary},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
files=sorted(p for p in SOURCE.rglob('*') if p.is_file() and '__pycache__' not in p.parts
             and p.name!='RELEASE_SHA256.json' and p.suffix not in {'.pyc','.pyo'})
assert not any(p.name=='.env' for p in files)
env={};load_env(Path(r'C:\Users\jowals001\awi\solve\scripts\graphrag\data\.env'),env)
key=env['GRAPHRAG_API_KEY2'].encode('utf-8')
assert all(key not in p.read_bytes() for p in files),'Credential scan failed'
hashes={p.relative_to(SOURCE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
(SOURCE/'RELEASE_SHA256.json').write_text(json.dumps(hashes,indent=2)+'\n',encoding='utf-8')
files.append(SOURCE/'RELEASE_SHA256.json')
archive=SOURCE.parent/(SOURCE.name+'.zip')
assert not archive.exists()
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for p in files: z.write(p,(Path(SOURCE.name)/p.relative_to(SOURCE)).as_posix())
unpacked=BASE/'paragraph_release_unpacked'
unpacked.mkdir(exist_ok=False)
with zipfile.ZipFile(archive) as z:z.extractall(unpacked)
target=unpacked/SOURCE.name
subprocess.run([sys.executable,'verify_release.py'],cwd=target,check=True)
with (BASE/'paragraph_release_unpacked_tests.txt').open('w',encoding='utf-8') as log:
    subprocess.run([sys.executable,'-X','utf8','-m','unittest','discover','-s','tests','-v'],cwd=target,stdout=log,stderr=subprocess.STDOUT,check=True)
destination=Path('Y:/p_SOLVE/code/parsing')/SOURCE.name
destination_zip=destination.parent/archive.name
assert not destination.exists() and not destination_zip.exists()
shutil.copytree(target,destination,ignore=shutil.ignore_patterns('__pycache__'))
shutil.copyfile(archive,destination_zip)
assert all(hashlib.sha256((destination/name).read_bytes()).hexdigest()==sha for name,sha in hashes.items())
assert hashlib.sha256(destination_zip.read_bytes()).digest()==hashlib.sha256(archive.read_bytes()).digest()
print(json.dumps({'files':len(files),'zip_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
                  'published':str(destination),'archive':str(destination_zip),'tests_passed':59},indent=2))
