"""Initialize an isolated enrichment runtime seeded from the verified Bergwitzsee dump."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import zipfile

ROOT=Path(__file__).resolve().parents[2]
RUNTIME=ROOT/'outputs/bergwitzsee_ergaenzung_20261102/runtime'
PREVIEW=RUNTIME/'neo4j_preview'
HOME=PREVIEW/'neo4j-community-2026.10.1'
JAVA=Path('C:/Users/jowals001/.Neo4jDesktop2/Cache/runtime/zulu21.50.19-ca-jre21.0.11-win_x64')
SHORT=Path.home()/'.solve-bergwitz-enriched-neo4j-tmp'
OLD=ROOT/'outputs/wasserblick33_20261031/runtime'


def main():
    if HOME.exists() or (PREVIEW/'preview.env').exists():raise RuntimeError('Runtime exists; refusing overwrite.')
    for port in [7478,7692]:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            sock.bind(('127.0.0.1',port))
    archive=OLD/'neo4j_preview/neo4j-community-2026.10.1-windows.zip'
    with archive.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
    if sha!='675655653b16fe2f300dae8686d6afe6fef902ef2550be14d4facd289f6ae295':
        raise RuntimeError('Distribution checksum mismatch')
    PREVIEW.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist(): (PREVIEW/name).resolve().relative_to(PREVIEW.resolve())
        z.extractall(PREVIEW)
    manager=(OLD/'manage_neo4j_preview.py').read_text(encoding='utf-8')
    manager=manager.replace('7475','7478').replace('7688','7692').replace('.solve33-neo4j-tmp','.solve-bergwitz-enriched-neo4j-tmp')
    (RUNTIME/'manage_neo4j_preview.py').write_text(manager,encoding='utf-8')
    password=secrets.token_hex(24)
    secret=PREVIEW/'preview.env'
    with secret.open('x',encoding='utf-8') as f:
        f.write(f'NEO4J_URI=bolt://127.0.0.1:7692\nNEO4J_USER=neo4j\nNEO4J_PASSWORD={password}\nNEO4J_DATABASE=neo4j\n')
    flags=subprocess.CREATE_NO_WINDOW
    identity=subprocess.run(['whoami'],capture_output=True,text=True,creationflags=flags,check=True).stdout.strip()
    subprocess.run(['icacls',str(secret),'/inheritance:r','/grant:r',f'{identity}:F'],stdout=subprocess.DEVNULL,check=True,creationflags=flags)
    SHORT.mkdir(exist_ok=True)
    desired={
        'server.default_listen_address':'127.0.0.1','server.default_advertised_address':'127.0.0.1',
        'server.http.enabled':'true','server.http.listen_address':'127.0.0.1:7478','server.http.advertised_address':'127.0.0.1:7478',
        'server.https.enabled':'false','server.bolt.enabled':'true','server.bolt.listen_address':'127.0.0.1:7692','server.bolt.advertised_address':'127.0.0.1:7692',
        'server.memory.heap.initial_size':'256m','server.memory.heap.max_size':'512m','server.memory.pagecache.size':'128m',
        'dbms.security.auth_enabled':'true','dbms.usage_report.enabled':'false',
        'dbms.fleet_manager.enabled':'false','server.fleet_discovery.enabled':'false'}
    conf=HOME/'conf/neo4j.conf'
    lines=[line for line in conf.read_text(encoding='utf-8').splitlines()
           if line.lstrip().startswith('#') or line.split('=',1)[0].strip() not in desired]
    conf.write_text('\n'.join(lines)+'\n'+'\n'.join(f'{k}={v}' for k,v in desired.items())+
                    f'\nserver.jvm.additional=-Djdk.net.unixdomain.tmpdir={SHORT.as_posix()}\n',encoding='utf-8')
    env=os.environ.copy()
    env.update(JAVA_HOME=str(JAVA),NEO4J_HOME=str(HOME),NEO4J_CONF=str(HOME/'conf'),TEMP=str(SHORT),TMP=str(SHORT))
    with (PREVIEW/'initial-password.log').open('wb') as log:
        subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(HOME/'bin/neo4j-admin.ps1'),'dbms','set-initial-password',password],
                       cwd=HOME,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=flags,check=True,timeout=90)
    # Seed only the newly allocated data directory, never an existing target.
    source_dump=ROOT/'outputs/bergwitzsee_neo4j_v2_events_20261101/export/bergwitzsee-v2-events.dump'
    with source_dump.open('rb') as f:seed_sha=hashlib.file_digest(f,'sha256').hexdigest()
    if seed_sha!='3c5c7dfdff211917612dca8a8787b91353bdff29053a845711542765b917de6b':
        raise RuntimeError('Source dump checksum mismatch')
    seed_dir=RUNTIME/'seed_dump';seed_dir.mkdir()
    import shutil
    shutil.copyfile(source_dump,seed_dir/'neo4j.dump')
    with (PREVIEW/'seed-load.log').open('wb') as log:
        subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(HOME/'bin/neo4j-admin.ps1'),
                        'database','load','neo4j',f'--from-path={seed_dir}','--overwrite-destination=false'],
                       cwd=HOME,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=flags,check=True,timeout=180)
    si=subprocess.STARTUPINFO();si.dwFlags|=subprocess.STARTF_USESHOWWINDOW;si.wShowWindow=0
    with (PREVIEW/'console.log').open('ab') as log:
        proc=subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(HOME/'bin/neo4j.ps1'),'console'],
                              cwd=HOME,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                              startupinfo=si,creationflags=subprocess.CREATE_NEW_CONSOLE)
    state={'home':str(HOME),'community_version':'2026.10.1','java_home':str(JAVA),'short_temp_dir':str(SHORT),
        'sha256':sha,'download_sha256_verified':True,'launcher_pid':proc.pid,'http':'http://127.0.0.1:7478',
        'bolt':'bolt://127.0.0.1:7692','env_file':str(secret),
        'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    (PREVIEW/'preview-status.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
    from neo4j import GraphDatabase
    for _ in range(60):
        if proc.poll() is not None:raise RuntimeError('Neo4j exited; inspect console.log')
        try:
            with GraphDatabase.driver(state['bolt'],auth=('neo4j',password),connection_timeout=2) as d:
                d.verify_connectivity()
            break
        except Exception:time.sleep(2)
    else:raise RuntimeError('Startup timed out')
    sys.path.insert(0,str(RUNTIME))
    import manage_neo4j_preview
    manage_neo4j_preview.status()


if __name__=='__main__':main()
