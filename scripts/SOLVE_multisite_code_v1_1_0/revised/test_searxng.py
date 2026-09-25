"""Offline adapter and queue regression tests for Blablador SearXNG."""
import io
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
from urllib.parse import urlsplit, parse_qs
from pathlib import Path
import run_web_enrichment as s
from test_collector import config

class SearxngTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = config(self.tmp.name, search=dict(provider="searxng", delay_seconds=0, count=2))
    def tearDown(self):
        self.tmp.cleanup()
    def response(self, payload):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = io.BytesIO(json.dumps(payload).encode())
        return opener
    def test_request_normalization_deduplication_and_privacy(self):
        opener = self.response(dict(results=[dict(url="https://example.org/a", title="A", content="PRIVATE"),
                                            dict(url="https://example.org/a"), dict(url="https://example.org/b"),
                                            dict(url="https://example.org/c")], unresponsive_engines=[["brave", "timeout"]]))
        with patch.object(s.ur, "build_opener", return_value=opener):
            hits, warnings = s.searxng_search(self.cfg, 'See Wasserqualität')
        self.assertEqual(len(hits), 2)
        self.assertNotIn("PRIVATE", json.dumps(hits))
        self.assertEqual(warnings, [["brave", "timeout"]])
        args, kwargs = opener.open.call_args
        self.assertEqual(parse_qs(urlsplit(args[0].full_url).query)["q"], ['See Wasserqualität'])
        self.assertEqual(kwargs["timeout"], 45)
    def test_http_429_respects_retry_after(self):
        opener = MagicMock()
        opener.open.side_effect = HTTPError("https://example.org", 429, "rate limit", {"Retry-After":"120"}, None)
        with patch.object(s.ur, "build_opener", return_value=opener), self.assertRaises(s.FetchProblem) as caught:
            s.searxng_search(self.cfg, "lake")
        self.assertTrue(caught.exception.retry)
        self.assertEqual(caught.exception.delay, 120)
    def test_malformed_response_is_not_zero_results(self):
        for payload in ({}, {"results":None}, {"results":[{"url":"file:///tmp/a"}]}, []):
            with self.subTest(payload=payload), patch.object(s.ur, "build_opener", return_value=self.response(payload)), self.assertRaises(ValueError):
                s.searxng_search(self.cfg, "lake")
    def test_queue_partial_failure_and_empty_outcomes(self):
        for hits, warnings, expected in [([], [], "no_results"), ([], [["bing","timeout"]], "retry"),
                ([{"url":"https://example.org/report","title":"Report"}], [["bing","timeout"]], "done")]:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                research = s.Research(self.cfg, Path(tmp)/"dossier")
                try:
                    research.plan()
                    search = research.next_search()
                    with patch.object(s, "searxng_search", return_value=(hits,warnings)), patch.object(s, "brave_search") as brave:
                        research.do_search(search)
                    brave.assert_not_called()
                    self.assertEqual(research.store.one("SELECT status FROM searches WHERE id=?", (search["id"],))["status"], expected)
                    if hits:
                        self.assertIsNotNone(research.store.one("SELECT id FROM tasks WHERE url=?", (hits[0]["url"],)))
                    if warnings:
                        self.assertTrue(research.store.rows("SELECT * FROM events"))
                finally:
                    research.close()
    def test_invalid_search_settings(self):
        for field,value in [("count",0),("timeout_seconds",0),("delay_seconds",-1),("base_url","file:///tmp")]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                config(self.tmp.name, search=dict(provider="searxng", **{field:value}))
    def test_starter_defaults_to_searxng(self):
        import start_research
        with patch.dict(s.os.environ, {}, clear=True):
            cfg = start_research.prepare_input(dict(waterbodies=[dict(name="Example Lake")]))
        self.assertEqual(cfg["search"]["provider"], "searxng")

if __name__ == "__main__":
    unittest.main()
