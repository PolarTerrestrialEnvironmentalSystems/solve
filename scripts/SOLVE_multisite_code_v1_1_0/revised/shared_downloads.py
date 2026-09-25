"""Shared successful URL -> original-byte cache. No fuzzy title-based equivalence."""
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


@contextmanager
def locked(root, core):
    try:
        with core.WorkspaceLock(root):
            yield
    except RuntimeError as error:
        if 'bereits bearbeitet' in str(error):
            raise core.FetchProblem('shared_cache_busy', retry=True, delay=2) from None
        raise


def import_source(fetcher, row, source_root):
    row=dict(row)
    relative=Path(row['path'])
    if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0]!='archive':
        return None
    source=Path(source_root)/relative
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest()!=row['sha']:
        return None
    target=fetcher.store.root/relative
    target.parent.mkdir(parents=True,exist_ok=True)
    if source.resolve()!=target.resolve():
        shutil.copy2(source,target)
    columns=list(row)
    fetcher.store.db.execute('INSERT OR REPLACE INTO urls ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',[row[k] for k in columns])
    fetcher.store.db.commit()
    return row


class SharedDownloads:
    def __init__(self, root):
        self.root=Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.root/'downloads.sqlite')) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS sources(url TEXT PRIMARY KEY,payload TEXT NOT NULL)')

    def lookup(self, fetcher, url):
        with closing(sqlite3.connect(self.root/'downloads.sqlite')) as db, db:
            entry=db.execute('SELECT payload FROM sources WHERE url=?',(url,)).fetchone()
        return import_source(fetcher,json.loads(entry[0]),self.root) if entry else None

    def publish(self, fetcher, rows=None):
        # Include known redirect aliases, so a different lake/dossier can reuse them.
        if rows is None:
            rows=fetcher.store.rows("SELECT * FROM urls WHERE status='ok' AND original=1 AND origin='http'")
        with closing(sqlite3.connect(self.root/'downloads.sqlite')) as db, db:
            for row in rows:
                if row['status'] != 'ok' or not row['original'] or row['origin'] != 'http':
                    continue
                source=fetcher.store.root/row['path']
                if not source.is_file():
                    continue
                if hashlib.sha256(source.read_bytes()).hexdigest() != row['sha']:
                    continue
                target=self.root/row['path']
                target.parent.mkdir(parents=True,exist_ok=True)
                if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != row['sha']:
                    shutil.copy2(source,target)
                db.execute('INSERT OR REPLACE INTO sources VALUES(?,?)',(row['url'],json.dumps(row)))
