"""Scientific entry points must preserve resume state and normal review gates."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import focused_search
import run_web_enrichment as s
from test_collector import config


class ScientificSourcesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = config(self.tmp.name, search=dict(provider='searxng', strategy='focused'),
                          retrieval=dict(enabled=True))
        self.source = dict(waterbody_id='lake', url='https://papers.example.org/lake-list',
                           title='Example Lake research publications', search_domain='papers.example.org')
        self.root = Path(self.tmp.name)/'dossier'

    def tearDown(self):
        self.tmp.cleanup()

    def test_adding_catalog_keeps_profile_and_completed_tasks(self):
        r = s.Research(self.cfg, self.root)
        profile = next(iter(r.active))
        r.enqueue(profile, 'https://example.org/old')
        r.store.db.execute("UPDATE tasks SET status='done',reason='ok'")
        r.close()
        cfg = s.load_config(dict(self.cfg, scientific_sources=[self.source]))
        r = s.Research(cfg, self.root)
        try:
            r.plan()
            r.plan()
            self.assertEqual(r.active, {profile})
            self.assertEqual(r.store.one("SELECT status FROM tasks WHERE url='https://example.org/old'")['status'], 'done')
            self.assertEqual(len(r.store.rows('SELECT * FROM profiles')), 1)
            rows = r.store.rows('SELECT * FROM tasks WHERE url=?', (self.source['url'],))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['source_hint'].strip(), self.source['title'])
            self.assertTrue(rows[0]['branch'].startswith('scientific_source:'))
        finally:
            r.close()

    def test_catalog_sources_still_require_preflight(self):
        cfg = s.load_config(dict(self.cfg, scientific_sources=[self.source]))
        r = s.Research(cfg, self.root)
        try:
            r.plan()
            task = r.store.one('SELECT * FROM tasks WHERE url=?', (self.source['url'],))
            r.cfg['preflight']['enabled'] = True
            with patch('source_preflight.screen', return_value=False) as screen, patch.object(r.fetcher, 'fetch') as fetch:
                r.process(task)
            screen.assert_called_once()
            fetch.assert_not_called()
            self.assertEqual(r.store.one('SELECT reason FROM tasks WHERE id=?', (task['id'],))['reason'], 'llm_preflight_deferred')
        finally:
            r.close()

    def test_domains_beyond_first_three_and_waterbody_scope(self):
        other = dict(self.source, waterbody_id='other', search_domain='other.example.org')
        cfg = copy.deepcopy(self.cfg)
        cfg['waterbodies'][0]['preferred_domains'] = ['one.example.org','two.example.org','three.example.org']
        cfg['waterbodies'].append(dict(id='other', name='Other Lake'))
        cfg['scientific_sources'] = [self.source, other]
        r = s.Research(s.load_config(cfg), self.root)
        try:
            r.plan()
            rows = r.store.rows('SELECT * FROM tasks WHERE url=?', (self.source['url'],))
            self.assertEqual({row['profile'] for row in rows}, set(r.active))
            variants = [query for query, family in focused_search.options(r, r.cfg['waterbodies'][0], set())]
            self.assertIn('"Example Lake" site:papers.example.org', variants)
            self.assertFalse(any('other.example.org' in query for query in variants))
        finally:
            r.close()

    def test_explicit_entry_reactivates_low_priority_but_not_llm_rejection(self):
        r = s.Research(s.load_config(dict(self.cfg, scientific_sources=[self.source])), self.root)
        try:
            profile = next(iter(r.active))
            r.enqueue(profile, self.source['url'], priority=1)
            r.plan()
            self.assertEqual(r.store.one('SELECT status FROM tasks')['status'], 'pending')
            r.store.db.execute("UPDATE tasks SET status='deferred',reason='llm_preflight_deferred'")
            r.plan()
            self.assertEqual(r.store.one('SELECT reason FROM tasks')['reason'], 'llm_preflight_deferred')
        finally:
            r.close()

    def test_rejects_unknown_waterbody_invalid_url_and_domain_operator(self):
        for change in [dict(waterbody_id='missing'), dict(url='file:///secret'), dict(title=''), dict(search_domain='example.org OR unrelated')]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                s.load_config(dict(self.cfg, scientific_sources=[dict(self.source, **change)]))

    def test_url_normalization_does_not_reopen_llm_rejections(self):
        source = dict(self.source, url=self.source['url']+'?z=2&a=1#section')
        r = s.Research(s.load_config(dict(self.cfg, scientific_sources=[source])), self.root)
        try:
            r.plan()
            r.store.db.execute("UPDATE tasks SET status='deferred',reason='llm_preflight_deferred'")
            r.plan()
            tasks = r.store.rows('SELECT * FROM tasks')
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]['reason'], 'llm_preflight_deferred')
        finally:
            r.close()


if __name__ == '__main__':
    unittest.main()
