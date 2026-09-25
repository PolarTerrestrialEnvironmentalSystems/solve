"""Import already archived originals into the shared index without HTTP calls."""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import run_web_enrichment as s
from shared_downloads import SharedDownloads, locked


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dossier',required=True)
    p.add_argument('--cache',default=str(Path(__file__).resolve().parent.parent/'download_cache'))
    args=p.parse_args()
    root=Path(args.dossier).resolve()
    dbpath=root/'research.sqlite'
    if not dbpath.is_file():
        raise SystemExit('Dossier database not found')
    cache=SharedDownloads(args.cache)
    with closing(sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        store=SimpleNamespace(root=root,rows=lambda sql:[dict(row) for row in db.execute(sql)])
        with locked(cache.root,s):
            cache.publish(SimpleNamespace(store=store))
    with closing(sqlite3.connect(cache.root/'downloads.sqlite')) as db:
        count=db.execute('SELECT COUNT(*) FROM sources').fetchone()[0]
    print(json.dumps(dict(dossier=str(root),cache=str(cache.root),indexed_urls=count,network_calls=0)))


if __name__=='__main__':
    main()
