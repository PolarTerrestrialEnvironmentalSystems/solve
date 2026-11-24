"""Back up the newly created Dummy PostGIS DB (including original polygon data)."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/dummy_neo4j_v2_20261101'


def main():
    url=make_url(dotenv_values(OUT/'runtime/postgres.env')['SOLVE_DATABASE_URL'])
    assert url.database=='solve_dummy_v2_20261101' and url.host=='127.0.0.1' and url.port==55439
    destination=OUT/'postgres';destination.mkdir(exist_ok=True)
    backup=destination/'dummy-waterkg-v2.backup'
    if backup.exists():raise RuntimeError('Backup already exists')
    env=os.environ.copy();env['PGPASSWORD']=url.password
    args=['C:/Program Files/PostgreSQL/18/bin/pg_dump.exe','-h',url.host,'-p',str(url.port),'-U',url.username,
          '-d',url.database,'-Fc','--no-owner','--no-acl','-f',str(backup)]
    with (destination/'backup.log').open('wb') as log:
        subprocess.run(args,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW,check=True,timeout=180)
    with (destination/'contents.txt').open('wb') as log:
        subprocess.run(['C:/Program Files/PostgreSQL/18/bin/pg_restore.exe','--list',str(backup)],stdout=log,stderr=subprocess.STDOUT,
                       creationflags=subprocess.CREATE_NO_WINDOW,check=True,timeout=60)
    with backup.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
    (destination/'BACKUP_VALIDATION.json').write_text(json.dumps({'file':backup.name,'bytes':backup.stat().st_size,'sha256':sha,
        'archive_listing_passed':True,'full_restore_test_performed':False},indent=2),encoding='utf-8')
    print(json.dumps({'backup':str(backup),'bytes':backup.stat().st_size}),flush=True)


if __name__=='__main__':main()
