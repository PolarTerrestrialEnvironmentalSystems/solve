"""Offline tests: never contact the Elbe portal or a deployment server."""
import concurrent.futures as futures
import datetime as dt
import email.message
import email.utils
import http.cookiejar
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from downloader import Downloader, State, canonical_path, format_progress, worker_lock
from parallel_download import (ParallelRun, RequestGate, WorkerDownloader, WorkerState,
                               LINUX_OUTPUT, check_paths, claim_next, main, prepare_queue)
from portal import Page, Portal, PortalError, UncertainExport, write_json


class ParallelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name).resolve()
        self.state = State(self.output)
        self.addCleanup(self.state.db.close)
        prepare_queue(self.state)
        with self.state.db:
            self.state.db.execute("INSERT INTO topics VALUES ('Hydro','Hydrologie','url',30,'date')")

    def add_jobs(self, count):
        with self.state.db:
            return [self.state.add_job("Hydro", {"gewaehltMessstelle": str(i)}, 1) for i in range(count)]

    def test_thirty_claims_are_disjoint_and_never_reclaimed_by_age(self):
        self.add_jobs(31)
        jobs = [claim_next(self.state, slot) for slot in range(1, 31)]
        self.assertEqual(len({j["id"] for j in jobs}), 30)
        self.assertTrue(all(j["status"] == "planning" for j in jobs))
        with self.state.db:
            self.state.db.execute("UPDATE parallel_claims SET claimed_at='1970-01-01'")
        with self.assertRaises(PortalError):
            claim_next(self.state, 1)
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM jobs WHERE status='pending'").fetchone()[0], 1)

    def test_concurrent_sqlite_claims_cannot_duplicate(self):
        self.add_jobs(30)
        def claim(slot):
            state = WorkerState(self.output, slot)
            try:
                return claim_next(state, slot)["id"]
            finally:
                state.db.close()
        with futures.ThreadPoolExecutor(max_workers=30) as pool:
            ids = list(pool.map(claim, range(1, 31)))
        self.assertEqual(len(set(ids)), 30)

    def test_completed_claim_is_cleaned_but_original_owner_is_preserved(self):
        self.add_jobs(1)
        job = claim_next(self.state, 1)
        self.state.update(job["id"], status="complete", rows=1)
        prepare_queue(self.state)
        self.assertFalse(self.state.db.execute("SELECT * FROM parallel_claims").fetchall())
        self.assertEqual(self.state.db.execute("SELECT slot FROM parallel_owners").fetchone()[0], 1)
        self.assertEqual(self.state.report(write=False)["downloaded_rows"], 1)

    def test_cookie_paths_are_stable_distinct_and_not_the_legacy_cookie(self):
        paths = []
        for slot in range(1, 31):
            state = WorkerState(self.output, slot)
            try:
                paths.append(state.cookie_path())
                self.assertIsNone(state.report())
            finally:
                state.db.close()
        self.assertEqual(len(set(paths)), 30)
        self.assertNotIn(self.state.cookie_path(), paths)
        reopened = WorkerState(self.output, 30)
        try:
            self.assertEqual(reopened.cookie_path(), paths[-1])
        finally:
            reopened.db.close()

    def test_worker_does_not_publish_report_files(self):
        state = WorkerState(self.output, 1)
        try:
            with patch.object(Path, "replace", side_effect=AssertionError("worker must not publish")):
                state.report()
            self.assertFalse((self.output / "fortschritt.json").exists())
        finally:
            state.db.close()

    def test_only_the_assigned_worker_can_execute(self):
        self.add_jobs(1)
        job = claim_next(self.state, 1)
        run = ParallelRun(self.state, workers=2)
        with patch.object(Portal, "request", side_effect=AssertionError("no HTTP")):
            with self.assertRaisesRegex(PortalError, "own"):
                run.execute(2, job["id"])

    def test_resume_uses_only_saved_owner_and_never_enters_or_resubmits(self):
        self.add_jobs(1)
        job = claim_next(self.state, 30)
        self.state.update(job["id"], status="waiting", attempts=1, details=json.dumps({"refresh_url": "url"}))
        run = ParallelRun(self.state, workers=1, accept_terms=True)
        observed = []
        def recover(loader, jobs):
            observed.append((loader.state.slot, jobs[0]["id"]))
            loader.state.update(jobs[0]["id"], status="complete", rows=1)
        with patch.object(WorkerDownloader, "recover_inflight", new=recover), \
             patch.object(Portal, "enter", side_effect=AssertionError("no reset before recovery")), \
             patch.object(WorkerDownloader, "export", side_effect=AssertionError("no duplicate POST")):
            with futures.ThreadPoolExecutor(max_workers=1) as pool:
                run.drain(pool, {}, recovery=[(30, job["id"])])
        self.assertEqual(observed, [(30, job["id"])])
        self.assertEqual(self.state.db.execute("SELECT attempts FROM jobs").fetchone()[0], 1)

    def test_thirty_real_worker_tasks_preserve_each_result_once(self):
        self.add_jobs(30)
        run = ParallelRun(self.state, workers=30, accept_terms=True)
        barrier = threading.Barrier(30, timeout=15)
        seen = []
        lock = threading.Lock()
        def process(loader, job):
            barrier.wait()
            with lock:
                seen.append((loader.state.slot, job["id"]))
            loader.state.update(job["id"], status="complete", rows=1, attempts=1)
        with patch.object(Portal, "enter"), patch.object(WorkerDownloader, "process_job", new=process):
            with futures.ThreadPoolExecutor(max_workers=30) as pool:
                run.drain(pool, {}, dispatch=True)
        self.assertEqual(len({slot for slot, _ in seen}), 30)
        self.assertEqual(len({job for _, job in seen}), 30)
        report = self.state.report()
        self.assertEqual((report["completed_exports"], report["downloaded_rows"]), (30, 30))
        self.assertFalse(report["all_complete"])
        self.assertFalse(self.state.db.execute("SELECT * FROM parallel_claims").fetchall())

    def test_actual_recovery_pipeline_keeps_two_sessions_and_original_csvs_separate(self):
        self.add_jobs(2)
        claimed = [claim_next(self.state, slot) for slot in (1, 2)]
        for slot, job in enumerate(claimed, 1):
            http.cookiejar.LWPCookieJar().save(str(self.state.directory / f"parallel_session_{slot:02d}.cookies"))
            self.state.update(job["id"], status="waiting", attempts=1,
                              details=json.dumps({"refresh_url": f"https://www.elbe-datenportal.de/poll/{slot}"}))
        def poll(portal, url, *args, **kwargs):
            slot = int(portal.cookie_file.stem[-2:])
            self.assertEqual(url, f"https://www.elbe-datenportal.de/poll/{slot}")
            return Page(url, f'<a href="/FisFggElbe/ausgabe/result_{slot}.csv">CSV</a>')
        def download(portal, url, *args, **kwargs):
            slot = int(portal.cookie_file.stem[-2:])
            self.assertTrue(url.endswith(f"result_{slot}.csv"))
            return url, {"Content-Type": "text/csv"}, f"'A';'B'\n'{slot}';'original'\n".encode()
        run = ParallelRun(self.state, workers=2, accept_terms=True)
        with patch.object(Portal, "get_page", new=poll), patch.object(Portal, "request", new=download), \
             patch.object(Portal, "enter", side_effect=AssertionError("no session reset")), \
             patch.object(Portal, "submit", side_effect=AssertionError("no export replay")):
            with futures.ThreadPoolExecutor(max_workers=2) as pool:
                run.drain(pool, {}, recovery=[(slot, job["id"]) for slot, job in enumerate(claimed, 1)])
        for slot, job in enumerate(claimed, 1):
            record = self.state.db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
            self.assertEqual((record["attempts"], record["rows"]), (1, 1))
            saved = Path(record["directory"]) / f"result_{slot}.csv"
            self.assertEqual(saved.read_bytes(), f"'A';'B'\n'{slot}';'original'\n".encode())

    def test_failure_drains_other_worker_checkpoint_before_returning(self):
        self.add_jobs(3)
        run = ParallelRun(self.state, workers=2)
        barrier = threading.Barrier(2, timeout=10)
        checkpointed = threading.Event()
        def process(loader, job):
            barrier.wait()
            if loader.state.slot == 1:
                loader.state.update(job["id"], status="uncertain", attempts=1)
                raise UncertainExport("lost response")
            self.assertTrue(run.stopped.wait(10))
            loader.state.update(job["id"], status="ready", attempts=1,
                                links=json.dumps([["CSV", "https://www.elbe-datenportal.de/ausgabe/saved.csv"]]))
            checkpointed.set()
            raise KeyboardInterrupt
        with patch.object(Portal, "enter"), patch.object(WorkerDownloader, "process_job", new=process):
            with futures.ThreadPoolExecutor(max_workers=2) as pool:
                with self.assertRaises(UncertainExport):
                    run.drain(pool, {}, dispatch=True)
        self.assertTrue(checkpointed.is_set())
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM parallel_claims").fetchone()[0], 2)
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM jobs WHERE status='pending'").fetchone()[0], 1)

    def test_terms_initialization_precedes_restarting_unsubmitted_planning(self):
        self.add_jobs(1)
        claim_next(self.state, 30)
        run = ParallelRun(self.state, workers=1, accept_terms=True)
        sequence = []
        def process(loader, job):
            sequence.append("planning")
            loader.state.update(job["id"], status="complete", rows=1)
        with patch.object(run.legacy, "initialize", side_effect=lambda: sequence.append("terms")), \
             patch.object(run.legacy, "discover_stations"), patch.object(run.legacy, "log"), \
             patch.object(Portal, "enter"), patch.object(WorkerDownloader, "process_job", new=process):
            run.run()
        self.assertEqual(sequence, ["terms", "planning"])

    def test_known_problem_jobs_are_not_claimed_or_counted_as_complete(self):
        ids = self.add_jobs(5)
        for job_id, status in zip(ids, ["error", "needs_review", "uncertain", "deferred_uncertain", "complete"]):
            self.state.update(job_id, status=status)
        self.assertIsNone(claim_next(self.state, 1))
        self.assertEqual(len(self.state.report(write=False)["problems"]), 4)

    def test_split_children_still_get_processed(self):
        self.add_jobs(1)
        run = ParallelRun(self.state, workers=4)
        seen = []
        def process(loader, job):
            if job["depth"] == 0:
                loader.state.split(job, [({"gewaehltMessstelle": "child"}, 1)], "station")
            else:
                seen.append(job["id"])
                loader.state.update(job["id"], status="complete", rows=1)
        with patch.object(Portal, "enter"), patch.object(WorkerDownloader, "process_job", new=process):
            with futures.ThreadPoolExecutor(max_workers=4) as pool:
                run.drain(pool, {}, dispatch=True)
        self.assertEqual(len(seen), 1)

    def test_uncertain_export_stops_new_dispatch_and_keeps_claim(self):
        self.add_jobs(3)
        run = ParallelRun(self.state, workers=1)
        def process(loader, job):
            loader.state.update(job["id"], status="uncertain", attempts=1)
            raise UncertainExport("no response")
        with patch.object(Portal, "enter"), patch.object(WorkerDownloader, "process_job", new=process):
            with futures.ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaises(UncertainExport):
                    run.drain(pool, {}, dispatch=True)
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM parallel_claims").fetchone()[0], 1)
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM jobs WHERE status='pending'").fetchone()[0], 2)

    def test_stop_does_not_claim_more_work(self):
        self.add_jobs(2)
        run = ParallelRun(self.state, workers=30)
        (self.output / "STOP").touch()
        with futures.ThreadPoolExecutor(max_workers=30) as pool:
            with self.assertRaises(KeyboardInterrupt):
                run.drain(pool, {}, dispatch=True)
        self.assertFalse(self.state.db.execute("SELECT * FROM parallel_claims").fetchall())

    def test_stop_file_is_honored_between_initialization_requests_too(self):
        run = ParallelRun(self.state, workers=30)
        (self.output / "STOP").touch()
        with self.assertRaises(KeyboardInterrupt):
            run.gate.wait()
        self.assertTrue(run.stopped.is_set())

    def test_three_network_failures_stop_the_whole_pool(self):
        self.add_jobs(5)
        run = ParallelRun(self.state, workers=1)
        def process(loader, job):
            loader.state.update(job["id"], status="error", error="network")
        with patch.object(Portal, "enter"), patch.object(WorkerDownloader, "process_job", new=process):
            with futures.ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaisesRegex(PortalError, "Three"):
                    run.drain(pool, {}, dispatch=True)
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM jobs WHERE status='error'").fetchone()[0], 3)

    def test_progress_exposes_planning_slot(self):
        self.add_jobs(1)
        claim_next(self.state, 4)
        report = self.state.report()
        self.assertEqual(report["active_jobs"][0]["worker_slot"], 4)
        self.assertIn("Worker 04", format_progress(report))

    def test_second_runner_is_refused_without_network(self):
        with worker_lock(self.output / "_state" / "worker.lock"), \
             patch.object(Portal, "request", side_effect=AssertionError("no HTTP")):
            with self.assertRaisesRegex(PortalError, "Another"):
                main(["run", "--accept-terms", "--output", str(self.output), "--workers", "30"])

    def test_existing_legacy_uncertainty_prevents_new_parallel_exports(self):
        job_id = self.add_jobs(1)[0]
        self.state.update(job_id, status="uncertain", attempts=1)
        run = ParallelRun(self.state, workers=30, accept_terms=True)
        with patch.object(Portal, "request", side_effect=AssertionError("no HTTP")):
            with self.assertRaises(UncertainExport):
                run.run()
        self.assertFalse(self.state.db.execute("SELECT * FROM parallel_claims").fetchall())

    def test_relocation_is_explicit_audited_and_does_not_modify_raw(self):
        job_id = self.add_jobs(1)[0]
        destination = self.output / "hydrologie" / "unknown" / job_id
        destination.mkdir(parents=True)
        raw = destination / "data.csv"
        raw.write_bytes(b"original")
        write_json(destination / "metadata.json", {"job_id": job_id, "files": [{"filename": "data.csv"}]})
        self.state.update(job_id, status="complete", directory="C:/old/data/" + job_id)
        with self.assertRaisesRegex(PortalError, "relocate-state"):
            check_paths(self.state)
        self.assertEqual(check_paths(self.state, relocate=True), 1)
        self.assertEqual(raw.read_bytes(), b"original")
        self.assertEqual(self.state.db.execute("SELECT directory FROM jobs").fetchone()[0], str(destination))
        self.assertEqual(len(list(self.state.directory.glob("path_relocation_*.json"))), 1)
        self.assertEqual(check_paths(self.state), 0)

    def test_missing_copy_is_not_silently_rebased(self):
        job_id = self.add_jobs(1)[0]
        self.state.update(job_id, status="complete", directory="/old/" + job_id)
        with self.assertRaisesRegex(PortalError, "missing"):
            check_paths(self.state, relocate=True)

    @unittest.skipUnless(os.name == "nt", "Windows path spellings")
    def test_extended_windows_path_normalization_does_not_allow_escape(self):
        inside = self.output / "child"
        outside = self.output.parent / "another-directory"
        with patch.object(Path, "resolve", return_value=Path("\\\\?\\" + str(inside))):
            self.assertEqual(canonical_path(inside), inside)
        job_id = self.add_jobs(1)[0]
        job = self.state.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        loader = Downloader(self.state, accept_terms=False)
        with patch.object(Path, "resolve", return_value=Path("\\\\?\\" + str(outside))):
            with self.assertRaisesRegex(PortalError, "outside"):
                loader.job_directory(job)

    def test_relocation_rejects_another_jobs_metadata_without_any_database_changes(self):
        job_id = self.add_jobs(1)[0]
        destination = self.output / "hydrologie" / "unknown" / job_id
        destination.mkdir(parents=True)
        write_json(destination / "metadata.json", {"job_id": "not-this-job"})
        self.state.update(job_id, directory="/old/" + job_id)
        with self.assertRaisesRegex(PortalError, "another job"):
            check_paths(self.state, relocate=True)
        self.assertEqual(self.state.db.execute("SELECT directory FROM jobs").fetchone()[0], "/old/" + job_id)


class RateTests(unittest.TestCase):
    def test_linux_default_output_without_creating_any_server_directory(self):
        with patch("parallel_download.os", SimpleNamespace(name="posix")), \
             patch("parallel_download.canonical_path", side_effect=RuntimeError("stop before filesystem")) as resolve:
            with self.assertRaisesRegex(RuntimeError, "stop before filesystem"):
                main(["resume", "--accept-terms", "--workers", "30"])
        resolve.assert_called_once_with(LINUX_OUTPUT)
        self.assertEqual(LINUX_OUTPUT.as_posix(), "/bioing/data/WaterPlace/data/fgg_elbe")

    def test_explicit_output_still_overrides_linux_default(self):
        with patch("parallel_download.os", SimpleNamespace(name="posix")), \
             patch("parallel_download.canonical_path", side_effect=RuntimeError("stop before filesystem")) as resolve:
            with self.assertRaisesRegex(RuntimeError, "stop before filesystem"):
                main(["resume", "--accept-terms", "--output", "/custom/fgg"])
        resolve.assert_called_once_with(Path("/custom/fgg"))

    def test_windows_still_requires_an_explicit_output(self):
        with patch("parallel_download.os", SimpleNamespace(name="nt")), patch("sys.stderr"), \
             patch("parallel_download.canonical_path") as resolve:
            with self.assertRaises(SystemExit):
                main(["resume", "--accept-terms"])
        resolve.assert_not_called()

    def test_global_spacing_under_concurrency(self):
        gate = RequestGate(0.015, threading.Event())
        moments = []
        lock = threading.Lock()
        def request(_):
            gate.wait()
            with lock:
                moments.append(time.monotonic())
        with futures.ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(request, range(10)))
        moments.sort()
        self.assertGreaterEqual(moments[-1] - moments[0], 0.015 * 9 - 0.01)

    def test_retry_after_seconds_applies_globally_even_to_export_errors(self):
        gate = RequestGate(1.5, threading.Event(), clock=lambda: 10)
        headers = email.message.Message()
        headers["Retry-After"] = "123"
        error = urllib.error.HTTPError("https://www.elbe-datenportal.de/", 429, "busy", headers, None)
        gate.on_error(error)
        self.assertEqual(gate.cooldown_until, 133)
        portal = Portal(delay=0, error_hook=gate.on_error)
        with patch.object(portal.opener, "open", side_effect=error) as opened:
            with self.assertRaises(UncertainExport):
                portal.request("https://www.elbe-datenportal.de/test", {}, export=True)
        self.assertEqual(opened.call_count, 1)

    def test_http_date_retry_after_and_minimum_cooldown(self):
        gate = RequestGate(1.5, threading.Event(), clock=lambda: 0)
        headers = email.message.Message()
        date = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=120)
        headers["Retry-After"] = email.utils.format_datetime(date, usegmt=True)
        gate.on_error(urllib.error.HTTPError("url", 503, "busy", headers, None))
        self.assertGreater(gate.cooldown_until, 115)
        gate = RequestGate(1.5, threading.Event(), clock=lambda: 0)
        gate.on_error(urllib.error.HTTPError("url", 429, "busy", {}, None))
        self.assertEqual(gate.cooldown_until, 60)

    def test_stop_interrupts_gate_without_request(self):
        stopped = threading.Event()
        stopped.set()
        gate = RequestGate(100, stopped)
        with self.assertRaises(KeyboardInterrupt):
            gate.wait()

    def test_cli_rejects_invalid_workers_and_nan_delay(self):
        for extra in (["--workers", "31"], ["--workers", "0"], ["--delay", "nan"]):
            with self.subTest(extra=extra), patch("sys.stderr"), self.assertRaises(SystemExit):
                main(["--output", "unused", "--accept-terms", *extra])


if __name__ == "__main__":
    unittest.main()
