import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import parallel_crawler as parallel
import run_web_enrichment as core
from test_collector import config


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = config(self.root, waterbodies=[
            dict(id='one', name='First Lake'), dict(id='two', name='Second Lake')])

    def tearDown(self):
        self.tmp.cleanup()

    def test_budget_partition_and_unchanged_profile_inputs(self):
        self.cfg['credentials_env_file'] = 'C:/private/keys.env'
        self.cfg['scientific_sources'] = [dict(waterbody_id='one', url='https://example.org/report', title='Report')]
        parts = parallel.split_config(self.cfg, 2)
        for section, key in [('limits', 'max_tasks_per_run'), ('review', 'max_calls_per_run'),
                             ('preflight', 'max_calls_per_run')]:
            self.assertEqual(sum(p[section][key] for p in parts), self.cfg[section][key])
        self.assertEqual(parts[0]['waterbodies'], self.cfg['waterbodies'][:1])
        self.assertEqual(parts[1]['scientific_sources'], [])
        self.assertFalse(parts[0].get('credentials_env_file'))
        self.assertEqual(parts[0]['context'], self.cfg['context'])
        self.assertEqual(parts[0]['topics'], self.cfg['topics'])

    def test_import_run_two_processes_resume_and_keep_original(self):
        original = self.root / 'old'
        research = core.Research(self.cfg, original)
        try:
            html = self.root / 'fixture.html'
            html.write_text('<html><title>Monitoring</title><p>First Lake Second Lake phosphorus monitoring.</p></html>')
            manifest = self.root / 'import.jsonl'
            manifest.write_text(json.dumps(dict(url='https://example.org/report',
                waterbody_ids=['one', 'two'], original_file=str(html), mime_type='text/html')) + '\n')
            research.import_content(manifest)
            research.plan()
            tasks_before = research.store.rows('SELECT * FROM tasks')
            searches_before = research.store.rows('SELECT * FROM searches')
            profiles = dict(research.profiles)
        finally:
            research.close()
        target = self.root / 'parallel'
        parallel.prepare(target, self.cfg, 2, original)
        for i, wb in enumerate(['one', 'two'], 1):
            folder = target / f'group-{i:02d}'
            copied = core.Research(core.load_config(folder / 'config.json'), folder)
            try:
                self.assertEqual(copied.profiles[wb], profiles[wb])
                self.assertEqual(copied.store.rows('SELECT * FROM tasks'), tasks_before)
                self.assertEqual(copied.store.rows('SELECT * FROM searches'), searches_before)
            finally:
                copied.close()
        for attempt in range(2):
            result = subprocess.run([sys.executable, str(Path(parallel.__file__)), 'run',
                '--workspace', str(target), '--workers', '2'], capture_output=True, timeout=40)
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))
        for i, wb in enumerate(['one', 'two'], 1):
            folder = target / f'group-{i:02d}'
            self.assertTrue((folder / 'results' / wb).is_dir())
            self.assertFalse((folder / 'results' / ('two' if wb == 'one' else 'one')).exists())
            self.assertTrue((folder / 'last_run.json').is_file())
            with contextlib.closing(sqlite3.connect(folder / 'research.sqlite')) as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], 'ok')
                self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0], 2)
        with contextlib.closing(sqlite3.connect(original / 'research.sqlite')) as db:
            db.row_factory = sqlite3.Row
            self.assertEqual([dict(r) for r in db.execute('SELECT * FROM tasks')], tasks_before)
        with self.assertRaisesRegex(ValueError, 'existiert bereits'):
            parallel.prepare(target, self.cfg, 2, original)

    def test_import_refuses_running_source(self):
        original = self.root / 'old'
        store = core.Store(original)
        store.close()
        with core.WorkspaceLock(original), self.assertRaisesRegex(RuntimeError, 'bereits bearbeitet'):
            parallel.prepare(self.root / 'new', self.cfg, 2, original)

    def test_interrupt_is_forwarded_to_all_active_workers(self):
        target = self.root / 'parallel'
        parallel.prepare(target, self.cfg, 2)
        class Process:
            pid = 42
            def __init__(self):
                self.stopped = False
            def poll(self):
                return 130 if self.stopped else None
            def send_signal(self, sig):
                self.stopped = True
        processes = [Process(), Process()]
        with patch.object(parallel.subprocess, 'Popen', side_effect=processes), \
             patch.object(parallel.time, 'sleep', side_effect=KeyboardInterrupt):
            self.assertEqual(parallel.run(target, 2), 130)
        self.assertTrue(all(p.stopped for p in processes))
        report = json.loads((target / 'parallel_last_run.json').read_text())
        self.assertTrue(report['interrupted'])
        self.assertEqual(len(report['workers']), 2)

    def test_mismatched_source_profiles_are_rejected(self):
        source = self.root / 'old'
        research = core.Research(self.cfg, source)
        research.close()
        changed = dict(self.cfg, context='Different research')
        with self.assertRaisesRegex(ValueError, 'Gewässerprofilen'):
            parallel.prepare(self.root / 'parallel', changed, 2, source)

    def test_cooperative_stop_saves_summary_and_exports(self):
        research = core.Research(self.cfg, self.root / 'dossier')
        try:
            research.parallel_stop_requested = True
            with patch.object(research, 'process') as process:
                summary = research.run()
            self.assertEqual(summary['stop_reason'], 'interrupted')
            process.assert_not_called()
            saved = research.store.one('SELECT ended,summary FROM runs')
            self.assertTrue(saved['ended'])
            self.assertEqual(json.loads(saved['summary'])['stop_reason'], 'interrupted')
            self.assertTrue((research.store.root / 'results/RECHERCHEBERICHT.md').is_file())
        finally:
            research.close()

    def test_two_fetch_processes_download_once_and_share_host_delay(self):
        hits = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/robots.txt':
                    body, mime = b'User-agent: *\nAllow: /\n', 'text/plain'
                else:
                    hits.append(self.path)
                    body, mime = b'%PDF-1.4\nfixture', 'application/pdf'
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        code = '''
import sys
from pathlib import Path
import run_web_enrichment as c
root=Path(sys.argv[1])
cfg=c.load_config({'waterbodies':[{'id':'test','name':'Test Lake'}],
    'topics':[{'name':'Water quality','keywords':['monitoring']}],
    'storage':{'shared_cache_dir':sys.argv[2],'shared_cache_wait_seconds':10},
    'limits':{'delay_seconds':.1}})
store=c.Store(root)
try:
    fetcher=c.Fetcher(store,cfg,allow_private=True)
    fetcher.fetch(sys.argv[3])
finally:
    store.close()
'''
        processes = []
        try:
            url = f'http://127.0.0.1:{server.server_port}/source.pdf'
            for i in range(2):
                processes.append(subprocess.Popen([sys.executable, '-c', code, str(self.root / str(i)),
                    str(self.root / 'cache'), url], cwd=Path(core.__file__).parent,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            for proc in processes:
                stdout, stderr = proc.communicate(timeout=20)
                self.assertEqual(proc.returncode, 0, stderr.decode(errors='replace'))
            self.assertEqual(hits, ['/source.pdf'])
            with contextlib.closing(sqlite3.connect(self.root / 'cache/downloads.sqlite')) as db:
                self.assertGreater(db.execute('SELECT count(*) FROM hosts').fetchone()[0], 0)
        finally:
            for proc in processes:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_thirty_real_workers_for_thirty_three_lakes_offline(self):
        cfg = config(self.root, waterbodies=[dict(id=f'lake-{i:02d}', name=f'Lake {i}') for i in range(33)],
                     preflight=dict(max_calls_per_run=300),
                     review=dict(mode='rules', max_calls_per_run=300),
                     limits=dict(max_fetches_per_run=0, max_searches_per_run=0,
                                 max_tasks_per_run=300, delay_seconds=0))
        target = self.root / 'thirty'
        parallel.prepare(target, cfg, 30)
        entries = json.loads((target / 'parallel.json').read_text())['groups']
        self.assertEqual(sorted(len(e['waterbodies']) for e in entries), [1]*27+[2]*3)
        result = subprocess.run([sys.executable, str(Path(parallel.__file__)), 'run',
                                 '--workspace', str(target), '--workers', '30'],
                                capture_output=True, timeout=100)
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))
        report = json.loads((target / 'parallel_last_run.json').read_text())
        self.assertEqual(len(report['workers']), 30)
        for entry in entries:
            folder = target / entry['directory']
            summary = json.loads((folder / 'last_run.json').read_text())
            self.assertEqual(summary['api_calls'], 0)
            self.assertEqual(summary['fetch_attempts'], 0)
            self.assertEqual(summary['search_http_calls'], 0)
            for wb in entry['waterbodies']:
                self.assertTrue((folder / 'results' / wb).is_dir())


if __name__ == '__main__':
    unittest.main()
