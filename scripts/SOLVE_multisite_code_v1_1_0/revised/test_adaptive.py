import tempfile
import unittest
from pathlib import Path
import run_web_enrichment as s
import adaptive_search as a
from test_collector import config


class AdaptiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = config(self.tmp.name, search=dict(provider='searxng', strategy='staged_greedy', delay_seconds=0))
        self.r = s.Research(self.cfg, Path(self.tmp.name) / 'dossier')

    def tearDown(self):
        self.r.close()
        self.tmp.cleanup()

    def test_roman_numerals_are_distinct(self):
        wb = dict(self.cfg['waterbodies'][0], name='Barleber See I')
        self.assertFalse(s.identity_matches(wb, 'Barleber See II phosphorus'))
        self.assertTrue(s.identity_matches(wb, 'Barleber See I: phosphorus'))

    def test_parent_reservoir_not_credited_for_child_label(self):
        wb = dict(self.cfg['waterbodies'][0], name='Rappbodetalsperre', identity_exclusions=['Rappbodetalsperre/Vorsperre Hassel'])
        self.assertFalse(s.identity_matches(wb, 'Rappbodetalsperre/Vorsperre Hassel phosphorus'))

    def test_distant_topic_and_town_are_not_relevant(self):
        wb = self.cfg['waterbodies'][0]
        for text in ['Example Lake is mentioned.\n\nAnother lake has phosphorus.', 'Example Lake ' + ('x ' * 300) + 'phosphorus']:
            self.assertFalse(s.local_identity_topics(wb, self.cfg['topics'], text))
        wb = dict(wb, name='Arendsee', ambiguous_place_name=True)
        self.assertFalse(s.local_identity_topics(wb, self.cfg['topics'], 'Stadt Arendsee phosphorus'))

    def test_one_seed_then_document_stage(self):
        self.r.plan()
        self.assertEqual(len(self.r.store.rows('SELECT * FROM searches')), 1)
        q = self.r.next_search()
        self.assertEqual(q['family'], 'overview')
        self.r.search_result(q, [])
        self.assertEqual(self.r.next_search()['family'], 'document_body')

    def test_wait_for_source_evaluation(self):
        self.r.plan()
        q = self.r.next_search()
        self.r.search_result(q, [dict(url='https://example.org/report')])
        self.assertIsNone(self.r.next_search())

    def test_errors_do_not_count_as_negative_evidence(self):
        self.r.plan()
        q = self.r.next_search()
        self.r.search_result(q, [], error='timeout')
        self.assertFalse(a.feedback(self.r)[0][q['id']]['informative'])

    def test_partial_search_not_negative_evidence(self):
        self.r.plan()
        q = self.r.next_search()
        self.r.search_result(q, [])
        self.r.store.event('search_engine_failures', dict(search_id=q['id'], engines=[['bing', 'timeout']]))
        self.assertFalse(a.feedback(self.r)[0][q['id']]['informative'])

    def test_source_credit_deduplicates_bytes(self):
        self.r.plan()
        q = self.r.next_search()
        self.r.search_result(q, [dict(url='https://example.org/a'), dict(url='https://example.org/b')])
        parsed = dict(title='Report', text='Example Lake phosphorus monitoring.', fragments=[], links=[], status='ok', warnings=[])
        for t in self.r.store.rows('SELECT * FROM tasks'):
            self.r.store.db.execute('INSERT INTO urls(url,sha,status) VALUES(?,?,?)', (t['url'], 'same-hash', 'ok'))
            key, _, _ = self.r.review(t, dict(url=t['url'], sha='same-hash'), parsed)
            self.r.store.db.execute("UPDATE tasks SET status='done',review_id=? WHERE id=?", (key, t['id']))
        self.r.store.db.commit()
        stat = a.feedback(self.r)[0][q['id']]
        self.assertEqual(stat['documents'], 1)
        self.assertEqual(stat['gain'], 5)

    def test_exploration_is_recorded(self):
        self.r.plan()
        for _ in range(2):
            self.r.search_result(self.r.next_search(), [])
        self.r.cfg['search'].update(strategy='staged_epsilon', epsilon=1)
        q = self.r.next_search()
        self.assertIn('Erkundung', q['reason'])
        self.assertEqual(self.r.next_search()['id'], q['id'])

    def test_plateau_pauses_queries(self):
        self.r.plan()
        for _ in range(4):
            q = self.r.next_search()
            self.assertIsNotNone(q)
            self.r.search_result(q, [])
        self.assertIsNone(self.r.next_search())
