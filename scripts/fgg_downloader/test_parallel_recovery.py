"""Offline recovery regressions. Unmocked portal requests are forbidden."""
import datetime as dt
import http.cookiejar
import json
import sqlite3
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from downloader import Downloader, State, format_progress
from parallel_download import (ParallelRun, WorkerDownloader, WorkerState,
                               claim_next, main, prepare_queue)
from parallel_recovery import (UnavailableSavedExport, backup_recovery_state,
                               defer_saved_export, digest, inspect_saved_page,
                               require_recovery_state, session_key, unavailable_marker)
from portal import Page, Portal, PortalError, UncertainExport

ERROR_HTML = "<html><body>Es ist ein Fehler aufgetreten. java.lang.NullPointerException</body></html>"
BASE = "https://www.elbe-datenportal.de"


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name).resolve()
        self.state = State(self.output)
        self.addCleanup(self.state.db.close)
        prepare_queue(self.state)
        with self.state.db:
            self.state.db.execute("INSERT INTO topics VALUES ('Hydro','Hydrologie','url',31,'date')")
        self.network = patch.object(Portal, "request", side_effect=AssertionError("No live portal requests in tests"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.logging = patch.object(Downloader, "log")
        self.logging.start()
        self.addCleanup(self.logging.stop)

    def add_job(self, label):
        with self.state.db:
            return self.state.add_job("Hydro", {"gewaehltMessstelle": label}, 1)

    def saved_job(self, slot=1):
        job_id = self.add_job(str(slot))
        job = claim_next(self.state, slot)
        self.assertEqual(job["id"], job_id)
        directory = Downloader(self.state, accept_terms=True).job_directory(job)
        started = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat()
        self.state.update(job_id, status="waiting", attempts=1, directory=str(directory),
                          details=json.dumps({"refresh_url": f"{BASE}/poll/{slot}", "started_at": started}))
        cookie = self.state.directory / f"parallel_session_{slot:02d}.cookies"
        http.cookiejar.LWPCookieJar().save(str(cookie))
        return job_id, cookie

    def record(self, job_id):
        return self.state.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def controller(self, enabled=True, workers=2):
        return ParallelRun(self.state, workers=workers, accept_terms=True,
                           defer_unavailable_exports=enabled)

    def run_error_recovery(self, run, html=ERROR_HTML):
        with patch.object(Portal, "get_page", side_effect=lambda url: Page(url, html)), \
             patch.object(Portal, "enter", side_effect=AssertionError("no fresh session needed")), \
             patch.object(Portal, "submit", side_effect=AssertionError("no duplicate submission")), \
             patch.object(run.legacy, "initialize"), patch.object(run.legacy, "discover_stations"):
            run.run()

    def test_opt_in_preserves_state_cookies_raw_files_and_reports_the_gap(self):
        job_id, cookie = self.saved_job(30)
        original = dict(self.record(job_id))
        original_cookie = cookie.read_bytes()
        raw = Path(original["directory"]) / "untouched.csv"
        raw.write_bytes(b"original partial or historical evidence")
        self.run_error_recovery(self.controller())
        record = self.record(job_id)
        self.assertEqual(record["status"], "deferred_uncertain")
        for field in ("filters", "expected", "attempts", "rows", "links", "directory"):
            self.assertEqual(record[field], original[field])
        self.assertEqual(cookie.read_bytes(), original_cookie)
        self.assertEqual(raw.read_bytes(), b"original partial or historical evidence")
        self.assertFalse(self.state.db.execute("SELECT * FROM parallel_claims").fetchall())
        self.assertEqual(self.state.db.execute("SELECT slot FROM parallel_owners").fetchone()[0], 30)
        rotated = WorkerState(self.output, 30)
        self.addCleanup(rotated.db.close)
        self.assertNotEqual(rotated.cookie_path(), cookie)
        self.assertFalse(rotated.cookie_path().exists())
        receipt = json.loads((Path(record["directory"]) / json.loads(record["details"])["deferral_receipt"]).read_text())
        self.assertFalse(receipt["resubmitted"])
        self.assertEqual(receipt["export_cancelled"], "unknown")
        evidence = Path(record["directory"]) / receipt["evidence"]
        self.assertEqual(digest(evidence), receipt["evidence_sha256"])
        self.assertEqual(json.loads(evidence.read_text())["html"], ERROR_HTML)
        report = self.state.report()
        self.assertEqual((report["deferred_exports"], report["deferred_expected_rows"]), (1, 1))
        self.assertEqual((report["downloaded_rows"], report["completed_exports"]), (0, 0))
        self.assertFalse(report["all_complete"])
        self.assertIn(job_id, [p["id"] for p in report["problems"]])
        self.assertIn("Zurückgestellte Exporte: 1", format_progress(report))
        backups = list((self.state.directory / "recovery_backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / cookie.name).read_bytes(), original_cookie)
        connection = sqlite3.connect(backups[0] / "jobs.sqlite")
        try:
            self.assertEqual(connection.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0], "waiting")
        finally:
            connection.close()

    def test_default_still_stops_without_deferral_or_rotation(self):
        job_id, cookie = self.saved_job()
        with self.assertRaises(PortalError):
            self.run_error_recovery(self.controller(enabled=False))
        self.assertEqual(self.record(job_id)["status"], "uncertain")
        self.assertIsNone(self.state.meta(session_key(1)))
        self.assertTrue(cookie.exists())
        self.assertFalse((self.state.directory / "recovery_backups").exists())

    def test_explicit_session_lost_page_can_also_be_deferred(self):
        job_id, _ = self.saved_job()
        self.run_error_recovery(self.controller(), "<p>Sitzung verloren</p>")
        self.assertEqual(self.record(job_id)["status"], "deferred_uncertain")

    def test_ordinary_error_page_cannot_be_deferred(self):
        job_id, _ = self.saved_job()
        with self.assertRaises(PortalError):
            self.run_error_recovery(self.controller(), "<p>Maintenance, please try later</p>")
        self.assertEqual(self.record(job_id)["status"], "uncertain")
        self.assertIsNone(self.state.meta(session_key(1)))

    def test_new_recent_missing_naive_and_future_timestamps_are_not_deferred(self):
        job_id, _ = self.saved_job()
        current = dt.datetime.now(dt.timezone.utc)
        for stamp in (None, "invalid", current.isoformat(),
                      (current - dt.timedelta(minutes=59)).isoformat(),
                      (current + dt.timedelta(days=1)).isoformat(),
                      (current - dt.timedelta(days=2)).replace(tzinfo=None).isoformat()):
            with self.subTest(stamp=stamp):
                self.state.update(job_id, status="waiting", details=json.dumps(
                    {"refresh_url": f"{BASE}/poll/1", "started_at": stamp}))
                with self.assertRaises(PortalError):
                    self.run_error_recovery(self.controller())
                self.assertEqual(self.record(job_id)["status"], "uncertain")
                self.assertIsNone(self.state.meta(session_key(1)))

    def test_refresh_or_any_result_link_prevents_error_classification(self):
        for suffix in ('<meta http-equiv="refresh" content="5;url=/poll/1">',
                       '<a href="/FisFggElbe/ausgabe/result.csv">CSV</a>',
                       '<a href="/FisFggElbe/ausgabe/result.zip">result</a>'):
            with self.subTest(suffix=suffix):
                self.assertIsNone(unavailable_marker(Page(BASE, ERROR_HTML + suffix)))

    def test_missing_cookie_or_poll_context_is_never_deferred(self):
        for missing in ("cookie", "refresh"):
            with self.subTest(missing=missing):
                job_id, cookie = self.saved_job(1 if missing == "cookie" else 2)
                if missing == "cookie":
                    cookie.unlink()  # Only this test's temporary empty fixture.
                else:
                    self.state.update(job_id, details="{}")
                with self.assertRaises(UncertainExport):
                    self.controller().execute(1 if missing == "cookie" else 2, job_id)
                self.assertNotEqual(self.record(job_id)["status"], "deferred_uncertain")

    def test_network_error_and_timeout_keep_the_original_claim(self):
        job_id, _ = self.saved_job()
        for exc in (urllib.error.URLError("offline"), UncertainExport("Export wait deadline reached")):
            with self.subTest(error=type(exc).__name__), \
                 patch.object(Portal, "get_page", side_effect=exc):
                with self.assertRaises(type(exc)):
                    self.controller().execute(1, job_id)
                self.assertEqual(self.record(job_id)["status"], "uncertain")
                self.assertTrue(self.state.db.execute("SELECT * FROM parallel_claims").fetchall())
                self.assertIsNone(self.state.meta(session_key(1)))

    def test_known_ready_link_is_recovered_instead_of_deferred(self):
        job_id, _ = self.saved_job()
        run = self.controller()
        payload = b"'A';'B'\n'1';'original'\n"
        with patch.object(Portal, "get_page", return_value=Page(BASE, '<a href="/FisFggElbe/ausgabe/data.csv">CSV</a>')), \
             patch.object(Portal, "request", return_value=(BASE, {"Content-Type": "text/csv"}, payload)), \
             patch.object(Portal, "submit", side_effect=AssertionError("no export replay")):
            run.execute(1, job_id)
        job = self.record(job_id)
        self.assertIn(job["status"], ("complete", "complete_with_warnings"))
        self.assertEqual((job["rows"], job["attempts"]), (1, 1))
        self.assertEqual((Path(job["directory"]) / "data.csv").read_bytes(), payload)
        self.assertIsNone(self.state.meta(session_key(1)))

    def test_thirty_old_slots_are_all_deferred_with_only_two_active_workers(self):
        saved = [self.saved_job(slot)[0] for slot in range(1, 31)]
        self.run_error_recovery(self.controller(workers=2))
        self.assertTrue(all(self.record(job_id)["status"] == "deferred_uncertain" for job_id in saved))
        self.assertEqual(self.state.report(write=False)["deferred_exports"], 30)
        self.assertFalse(self.state.db.execute("SELECT * FROM parallel_claims").fetchall())

    def test_other_work_continues_using_a_fresh_session_and_completed_files_stay_unchanged(self):
        old_id, old_cookie = self.saved_job()
        complete_id = self.add_job("complete")
        complete_directory = Downloader(self.state, accept_terms=True).job_directory(self.record(complete_id))
        complete_file = complete_directory / "data.csv"
        complete_file.write_bytes(b"previously completed")
        self.state.update(complete_id, status="complete", rows=1, attempts=1, directory=str(complete_directory))
        pending_id = self.add_job("new")
        run = self.controller(workers=1)
        sessions = []
        def process(loader, job):
            self.assertEqual(job["id"], pending_id)
            sessions.append(loader.portal.cookie_file)
            self.assertNotEqual(loader.portal.cookie_file, old_cookie)
            self.assertEqual(len(loader.portal.cookie_jar), 0)
            loader.state.update(job["id"], status="complete", rows=1, attempts=1)
        with patch.object(Portal, "get_page", side_effect=lambda url: Page(url, ERROR_HTML)), \
             patch.object(Portal, "enter"), patch.object(WorkerDownloader, "process_job", new=process), \
             patch.object(run.legacy, "initialize"), patch.object(run.legacy, "discover_stations"):
            run.run()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(complete_file.read_bytes(), b"previously completed")
        self.assertEqual(self.record(old_id)["attempts"], 1)
        self.assertEqual(self.state.report(write=False)["downloaded_rows"], 2)
        self.assertFalse(self.state.report(write=False)["all_complete"])

    def test_new_export_failure_is_not_silently_deferred(self):
        job_id = self.add_job("fresh")
        claim_next(self.state, 1)
        with patch.object(Portal, "enter"), \
             patch.object(WorkerDownloader, "process_job", side_effect=UncertainExport("new export ambiguous")):
            with self.assertRaises(UncertainExport):
                self.controller().execute(1, job_id)
        self.assertNotEqual(self.record(job_id)["status"], "deferred_uncertain")
        self.assertIsNone(self.state.meta(session_key(1)))

    def test_crash_after_deferral_commit_can_resume_without_replaying_old_export(self):
        job_id, _ = self.saved_job()
        with patch.object(Portal, "get_page", side_effect=lambda url: Page(url, ERROR_HTML)):
            self.controller().execute(1, job_id)
        # Simulate a crash before drain() releases the claim.
        self.assertTrue(self.state.db.execute("SELECT * FROM parallel_claims").fetchall())
        new_cookie = self.state.meta(session_key(1))
        run = self.controller(enabled=False)
        with patch.object(run.legacy, "initialize"), patch.object(run.legacy, "discover_stations"):
            run.run()  # The global request guard proves there is no old request.
        self.assertFalse(self.state.db.execute("SELECT * FROM parallel_claims").fetchall())
        self.assertEqual(self.state.meta(session_key(1)), new_cookie)
        self.assertEqual(self.record(job_id)["attempts"], 1)
        self.assertEqual(self.record(job_id)["status"], "deferred_uncertain")

    def capture_failure(self, job_id):
        state = WorkerState(self.output, 1)
        self.addCleanup(state.db.close)
        loader = WorkerDownloader(state, self.controller())
        with self.assertRaises(UnavailableSavedExport) as raised:
            inspect_saved_page(loader, self.record(job_id), Page(BASE, ERROR_HTML))
        return state, raised.exception

    def test_evidence_tampering_cannot_change_state(self):
        job_id, _ = self.saved_job()
        state, failure = self.capture_failure(job_id)
        failure.evidence.write_text("{}")
        with self.assertRaisesRegex(PortalError, "checksum"):
            defer_saved_export(state, job_id, failure)
        self.assertEqual(self.record(job_id)["status"], "waiting")
        self.assertIsNone(self.state.meta(session_key(1)))

    def test_receipt_write_failure_does_not_defer_or_rotate(self):
        job_id, _ = self.saved_job()
        state, failure = self.capture_failure(job_id)
        with patch("parallel_recovery.private_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                defer_saved_export(state, job_id, failure)
        self.assertEqual(self.record(job_id)["status"], "waiting")
        self.assertIsNone(self.state.meta(session_key(1)))

    def test_session_update_failure_rolls_back_job_deferral_too(self):
        job_id, _ = self.saved_job()
        state, failure = self.capture_failure(job_id)
        self.state.db.executescript("""CREATE TRIGGER reject_rotation BEFORE INSERT ON metadata
            WHEN NEW.key LIKE 'parallel_cookie_filename_%'
            BEGIN SELECT RAISE(ABORT, 'simulated failure'); END;""")
        with self.assertRaises(sqlite3.IntegrityError):
            defer_saved_export(state, job_id, failure)
        self.assertEqual(self.record(job_id)["status"], "waiting")
        self.assertIsNone(self.state.meta(session_key(1)))

    def test_backup_failure_prevents_all_portal_requests_and_keeps_status(self):
        job_id, cookie = self.saved_job()
        with patch("parallel_download.backup_recovery_state", side_effect=OSError("backup unavailable")):
            with self.assertRaises(OSError):
                self.controller().run()
        self.assertEqual(self.record(job_id)["status"], "waiting")
        self.assertIsNone(self.state.meta(session_key(1)))
        self.assertTrue(cookie.is_file())

    def test_backup_manifest_covers_wal_content_and_cookie_bytes(self):
        job_id, cookie = self.saved_job()
        backup = backup_recovery_state(self.state)
        manifest = json.loads((backup / "manifest.json").read_text())
        self.assertFalse(manifest["raw_csvs_copied"])
        for file in manifest["files"]:
            self.assertEqual(digest(backup / file["name"]), file["sha256"])
        self.assertEqual((backup / cookie.name).read_bytes(), cookie.read_bytes())
        connection = sqlite3.connect(backup / "jobs.sqlite")
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()[0], 1)
        finally:
            connection.close()

    def test_unsafe_session_metadata_cannot_escape_state_directory(self):
        self.state.set_meta(session_key(1), "../outside.cookies")
        state = WorkerState(self.output, 1)
        self.addCleanup(state.db.close)
        with self.assertRaises(PortalError):
            state.cookie_path()

    def test_saved_links_or_rows_prevent_deferral_even_with_an_error_page(self):
        job_id, _ = self.saved_job()
        for extra in ({"links": json.dumps([["CSV", BASE + "/ausgabe/old.csv"]]), "rows": None},
                      {"links": None, "rows": 1}):
            with self.subTest(extra=extra):
                self.state.update(job_id, status="waiting", **extra)
                with self.assertRaises(PortalError):
                    self.run_error_recovery(self.controller())
                self.assertEqual(self.record(job_id)["status"], "uncertain")
                self.assertIsNone(self.state.meta(session_key(1)))

    def test_changed_session_file_invalidates_evidence(self):
        job_id, cookie = self.saved_job()
        state, failure = self.capture_failure(job_id)
        cookie.write_bytes(cookie.read_bytes() + b"# changed after evidence\n")
        with self.assertRaisesRegex(PortalError, "evidence"):
            defer_saved_export(state, job_id, failure)
        self.assertEqual(self.record(job_id)["status"], "waiting")
        self.assertIsNone(self.state.meta(session_key(1)))

    def test_recovery_cli_end_to_end_writes_stopped_record_and_visible_gap(self):
        job_id, _ = self.saved_job()
        with patch.object(Portal, "get_page", side_effect=lambda url: Page(url, ERROR_HTML)), \
             patch.object(Downloader, "initialize"), patch.object(Downloader, "discover_stations"):
            main(["resume", "--output", str(self.output), "--accept-terms", "--workers", "2",
                  "--defer-unavailable-exports"])
        record = json.loads((self.state.directory / "worker.json").read_text())
        self.assertTrue(record["defer_unavailable_exports"])
        self.assertIn("stopped_at", record)
        self.assertEqual(self.record(job_id)["status"], "deferred_uncertain")
        progress = json.loads((self.output / "fortschritt.json").read_text())
        self.assertFalse(progress["all_complete"])
        self.assertEqual(progress["deferred_exports"], 1)

    def test_empty_or_unrelated_checkpoint_is_not_initialized_as_a_new_download(self):
        other = self.output / "unrelated"
        directory = other / "_state"
        directory.mkdir(parents=True)
        file = directory / "jobs.sqlite"
        file.touch()
        with self.assertRaises(PortalError):
            require_recovery_state(other)
        self.assertEqual(file.stat().st_size, 0)
        connection = sqlite3.connect(file)
        connection.execute("CREATE TABLE unrelated (id INTEGER)")
        connection.close()
        before = file.read_bytes()
        with self.assertRaises(PortalError):
            require_recovery_state(other)
        self.assertEqual(file.read_bytes(), before)

    def test_cli_requires_resume_and_an_existing_state_before_creating_anything(self):
        missing = self.output / "not-created"
        for command in ("run", "relocate", "resume"):
            with self.subTest(command=command), patch("sys.stderr"), self.assertRaises(SystemExit):
                main([command, "--accept-terms", "--output", str(missing), "--defer-unavailable-exports"])
            self.assertFalse(missing.exists())

    def test_relocation_must_be_completed_before_recovery(self):
        self.saved_job()
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            main(["resume", "--accept-terms", "--output", str(self.output),
                  "--defer-unavailable-exports", "--relocate-state"])
        self.assertFalse((self.state.directory / "recovery_backups").exists())


if __name__ == "__main__":
    unittest.main()
