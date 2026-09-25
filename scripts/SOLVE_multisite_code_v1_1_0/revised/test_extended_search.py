"""Provider outages and waiting are simulated; these are not research results."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_web_enrichment as s
import quality_search as q
from test_collector import config


class ExtendedSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = config(self.tmp.name, search=dict(provider='searxng', strategy='focused', delay_seconds=0),
                     retrieval=dict(enabled=True, engines=['google'], crossref_enabled=True, wait_for_search_routes=True),
                     limits=dict(max_searches_per_run=20, max_fetches_per_run=10, max_seconds_per_run=65, delay_seconds=0))
        self.r = s.Research(cfg, Path(self.tmp.name)/'dossier')
        self.profile = next(iter(self.r.active))
        self.r.plan()

    def tearDown(self):
        self.r.close()
        self.tmp.cleanup()

    def finish_crossref(self):
        self.r.store.db.execute("UPDATE searches SET status='no_results',result_count=0,completed=? WHERE provider='crossref'", (s.utc(),))

    def test_failed_web_queue_does_not_prevent_new_crossref_queries(self):
        self.finish_crossref()
        for n in range(15):
            self.r.add_search(self.profile, f'Example Lake web {n}', 'overview', 'fixture')
        with patch.object(q, 'available', return_value=[]):
            task = self.r.next_search()
        self.assertEqual(task['provider'], 'crossref')
        self.assertEqual(task['family'], 'science_topic')

    def test_review_query_can_be_searched_at_crossref_without_web(self):
        self.finish_crossref()
        query = 'Example Lake groundwater study'
        self.r.add_search(self.profile, query, 'review', 'fixture')
        with patch.object(q, 'available', return_value=[]):
            task = self.r.next_search()
        self.assertEqual((task['provider'], task['query']), ('crossref', query))

    def test_disabled_crossref_does_not_run_existing_queue(self):
        self.r.cfg['retrieval']['crossref_enabled'] = False
        with patch.object(q, 'available', return_value=[]):
            self.assertIsNone(self.r.next_search())

    def test_crossref_backoff_blocks_new_variants(self):
        with patch.object(q.time, 'time', return_value=100):
            self.r.store.db.execute("UPDATE searches SET status='retry',next_try=200 WHERE provider='crossref'")
            with patch.object(q, 'available', return_value=[]):
                self.assertIsNone(self.r.next_search())
            self.assertEqual(q.next_search_wakeup(self.r, s), 200)
            with patch.object(s.ur, 'build_opener') as network, self.assertRaisesRegex(s.FetchProblem, 'crossref_cooldown'):
                q.crossref_search(self.r, {}, s)
            network.assert_not_called()

    def test_exhausted_exploration_does_not_freeze_new_search_directions(self):
        search = self.r.store.one("SELECT * FROM searches WHERE provider='crossref'")
        self.r.store.db.execute("UPDATE searches SET status='done',result_count=4,completed=? WHERE id=?", (s.utc(), search['id']))
        self.r.enqueue(self.profile, 'https://example.org/unknown', branch=search['id'])
        self.r.store.db.execute("UPDATE tasks SET status='pending',reason='preflight_exploration_budget'")
        self.r.exploration_used[self.profile] = self.r.cfg['preflight']['exploration_per_waterbody']
        with patch.object(q, 'available', return_value=[]):
            self.assertEqual(self.r.next_search()['provider'], 'crossref')

    def test_already_attempted_unresolved_review_does_not_freeze_search(self):
        search = self.r.store.one("SELECT * FROM searches WHERE provider='crossref'")
        self.r.store.db.execute("UPDATE searches SET status='done',result_count=1,completed=? WHERE id=?", (s.utc(), search['id']))
        self.r.enqueue(self.profile, 'https://example.org/review', branch=search['id'])
        self.r.attempted_tasks = {self.r.store.one('SELECT id FROM tasks')['id']}
        with patch.object(q, 'available', return_value=[]):
            self.assertIsNotNone(self.r.next_search())

    def test_web_plateau_does_not_stop_crossref(self):
        self.finish_crossref()
        for n in range(4):
            self.r.add_search(self.profile, f'Example Lake web {n}', 'overview', 'fixture')
        self.r.store.db.execute("UPDATE searches SET status='no_results',result_count=0,completed=? WHERE provider='searxng'", (s.utc(),))
        with patch.object(q, 'available', return_value=['google']):
            self.assertEqual(self.r.next_search()['provider'], 'crossref')

    def test_route_can_recover_after_cooldown_in_same_run(self):
        self.r.cfg['retrieval']['cooldown_seconds'] = 100
        with patch.object(q.time, 'time', return_value=100), patch.object(s, 'searxng_search', return_value=([], [])):
            self.assertFalse(q.healthy(self.r, 'google', s))
        def hits(cfg, query, **kwargs):
            return [dict(url='https://www.ufz.de/Bergwitzsee-Arendsee', title='Bergwitzsee Lake Arendsee')], []
        with patch.object(q.time, 'time', return_value=201), patch.object(s, 'searxng_search', side_effect=hits):
            self.assertTrue(q.healthy(self.r, 'google', s))
        self.assertEqual(self.r.health_used, 3)

    def test_no_wait_when_health_or_search_budget_is_exhausted(self):
        self.finish_crossref()
        q.mark(self.r, 'google', False, dict(reason='fixture'), s)
        self.r.health_used = self.r.cfg['retrieval']['max_health_calls_per_run']
        self.assertIsNone(q.next_search_wakeup(self.r, s))
        self.r.health_used = 0
        self.r.search_http_used = self.r.cfg['limits']['max_searches_per_run']
        self.assertIsNone(q.next_search_wakeup(self.r, s))
        with self.assertRaisesRegex(s.FetchProblem, 'search_call_budget'):
            q.crossref_search(self.r, {}, s)

    def test_wait_is_interruptible_and_stays_inside_run_budget(self):
        clock = [100.0]
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds
        with patch.object(s.time, 'time', side_effect=lambda: clock[0]), patch.object(s.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(s.time, 'sleep', side_effect=sleep), patch.object(self.r, 'next_task', return_value=None), patch.object(self.r, 'next_search', return_value=None), patch.object(q, 'next_search_wakeup', return_value=200), contextlib.redirect_stdout(io.StringIO()):
            summary = self.r.run()
        self.assertEqual(summary['stop_reason'], 'run_time_budget')
        self.assertEqual(sum(sleeps), 65)
        self.assertLessEqual(max(sleeps), 30)
        with patch.object(self.r, 'next_task', return_value=None), patch.object(self.r, 'next_search', return_value=None), patch.object(q, 'next_search_wakeup', return_value=s.time.time()+100), patch.object(s.time, 'sleep', side_effect=KeyboardInterrupt), contextlib.redirect_stdout(io.StringIO()):
            summary = self.r.run()
        self.assertEqual(summary['stop_reason'], 'interrupted')

    def test_run_resumes_work_after_wait(self):
        clock = [100.0]
        search = self.r.store.one("SELECT * FROM searches WHERE provider='crossref'")
        with patch.object(s.time, 'time', side_effect=lambda: clock[0]), patch.object(s.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(s.time, 'sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0]+seconds)), patch.object(self.r, 'next_task', return_value=None), patch.object(self.r, 'next_search', side_effect=[None, search, None]), patch.object(q, 'next_search_wakeup', side_effect=[130, None]), patch.object(self.r, 'do_search') as perform, contextlib.redirect_stdout(io.StringIO()):
            self.r.run()
        perform.assert_called_once_with(search)

    def test_one_bad_route_does_not_claim_all_routes_are_down(self):
        self.r.cfg['retrieval']['engines'] = ['google', 'brave']
        q.mark(self.r, 'google', False, dict(reason='fixture'), s)
        q.mark(self.r, 'brave', True, dict(reason='fixture'), s)
        self.assertFalse(q.routes_unavailable(self.r, s))


if __name__ == '__main__':
    unittest.main()
