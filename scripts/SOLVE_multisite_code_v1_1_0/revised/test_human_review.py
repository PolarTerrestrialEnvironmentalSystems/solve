import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import human_review as h
import run_web_enrichment as s
from test_collector import config


class HumanReviewTests(unittest.TestCase):
    def record(self, **values):
        return dict(dict(url='https://example.org/report', task_status='pending',
                         decision='unreviewed', review_status='not_reviewed'), **values)

    def test_access_reasons(self):
        for error, reason in [('http_403', 'automated_access_blocked'),
                              ('robots_restricted', 'automated_access_blocked'),
                              ('http_404', 'source_not_found'),
                              ('network_error', 'technical_access_problem')]:
            with self.subTest(error=error):
                row=h.entry(self.record(task_status='blocked', reason=error))
                self.assertIn(reason, row['human_review_reasons'])

    def test_pending_and_budget_are_not_uncertainty(self):
        for values in [{}, dict(task_status='retry', reason='host_cooldown'),
                       dict(task_status='deferred', reason='low_priority')]:
            self.assertIsNone(h.entry(self.record(**values)))

    def test_uncertain_metadata_and_resolved_content(self):
        assessment=dict(identity='uncertain', category='science', reason='Ambiguous name')
        self.assertIn('preflight_uncertain', h.entry(self.record(), assessment)['human_review_reasons'])
        self.assertIsNone(h.entry(self.record(review_status='complete', decision='relevant',
                                             identity_status='confirmed'), assessment))
        self.assertIsNone(h.entry(self.record(), dict(assessment, category='tourism')))
        self.assertIsNone(h.entry(self.record(decision='irrelevant'), assessment))

    def test_unreadable_and_unreviewed_originals(self):
        row=h.entry(self.record(original_bytes=True, parse_status='ocr_required'))
        self.assertIn('content_not_reliably_readable', row['human_review_reasons'])
        self.assertIn('content_review_unresolved', row['human_review_reasons'])

    def test_export_keeps_provenance_and_task_state_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            r=s.Research(config(root), root/'dossier')
            try:
                profile=next(iter(r.bodies))
                url='https://example.org/report'
                for parent in ['https://example.org/a', 'https://example.org/b']:
                    r.enqueue(profile, url, label='Monitoring', parent=parent, depth=1)
                r.store.db.execute("UPDATE tasks SET status='blocked',reason='http_403'")
                before=r.store.rows('SELECT * FROM tasks')
                with patch.object(s, 'api_request', side_effect=AssertionError('No API calls')):
                    r.export()
                out=root/'dossier/results/lake'
                with (out/'03b_manuelle_pruefung.csv').open(encoding='utf-8-sig', newline='') as f:
                    rows=list(csv.DictReader(f, delimiter=';'))
                self.assertEqual(len(rows), 1)
                self.assertIn('https://example.org/a', rows[0]['parent_urls'])
                self.assertIn('https://example.org/b', rows[0]['parent_urls'])
                self.assertEqual(rows[0]['access_error'], 'http_403')
                self.assertTrue((out/'03_offene_hinweise.csv').exists())
                self.assertEqual(before, r.store.rows('SELECT * FROM tasks'))
            finally:
                r.close()

    def test_empty_csv_has_stable_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'empty.csv'
            s.write_csv(path, [], fields=h.FIELDS)
            self.assertEqual(path.read_text(encoding='utf-8-sig').strip().split(';'), h.FIELDS)
