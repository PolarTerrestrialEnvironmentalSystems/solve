"""Single-host, resumable download concurrency. No server access or deployment.

One process owns worker.lock and up to 30 I/O threads. Only the coordinator
assigns work and publishes reports; each request session belongs to one slot.
SQLite must be on local disk, not shared between hosts or mounted over NFS.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import datetime as dt
import email.utils
import json
import math
import os
import signal
import sqlite3
import sys
import threading
import time
import urllib.error
from pathlib import Path

from downloader import Downloader, State, TOPIC_NAMES, canonical_path, now, worker_lock
from portal import Portal, PortalError, write_json

LINUX_OUTPUT = Path("/bioing/data/WaterPlace/data/fgg_elbe")
INFLIGHT = {"submitting", "waiting", "uncertain"}
RECOVERABLE = INFLIGHT | {"pending", "planning", "ready"}


class RequestGate:
    """Shared start spacing and Retry-After backoff, including HTTP redirects.

    This limits request starts, not the number of outstanding server exports.
    Existing HTTP calls are never forcefully killed on stop.
    """
    def __init__(self, delay, stopped, clock=time.monotonic, stop_file=None):
        self.delay = delay
        self.stopped = stopped
        self.clock = clock
        self.stop_file = stop_file
        self.lock = threading.Lock()
        self.next_start = 0.0
        self.cooldown_until = 0.0

    def wait(self, minimum=0):
        while True:
            if self.stop_file is not None and self.stop_file.exists():
                self.stopped.set()
            if self.stopped.is_set():
                raise KeyboardInterrupt
            with self.lock:
                current = self.clock()
                remaining = max(self.next_start, self.cooldown_until) - current
                if remaining <= 0:
                    self.next_start = current + max(self.delay, minimum)
                    return
            self.stopped.wait(min(remaining, 0.25))

    def on_error(self, error):
        if not isinstance(error, urllib.error.HTTPError) or error.code not in {429, 503}:
            return
        retry_after = error.headers.get("Retry-After", "") if error.headers else ""
        try:
            seconds = float(retry_after)
        except ValueError:
            try:
                date = email.utils.parsedate_to_datetime(retry_after)
                seconds = (date - dt.datetime.now(dt.timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                seconds = 60
        if not math.isfinite(seconds):
            seconds = 60
        with self.lock:
            self.cooldown_until = max(self.cooldown_until, self.clock() + max(60, seconds))


def prepare_queue(state):
    # Additive schema: original jobs, counts, attempts and problem states survive.
    state.db.executescript("""
        CREATE TABLE IF NOT EXISTS parallel_claims(
            job_id TEXT PRIMARY KEY REFERENCES jobs(id),
            slot INTEGER NOT NULL UNIQUE CHECK(slot BETWEEN 1 AND 30),
            claimed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS parallel_owners(
            job_id TEXT PRIMARY KEY REFERENCES jobs(id), slot INTEGER NOT NULL);
    """)
    with state.db:
        # A crash after committing a result but before releasing its slot is safe.
        state.db.execute("""DELETE FROM parallel_claims WHERE job_id IN
            (SELECT id FROM jobs WHERE status IN
             ('complete','complete_with_warnings','empty','split','needs_review','error'))""")
    for row in state.db.execute("""SELECT c.*,j.status FROM parallel_claims c
                                  LEFT JOIN jobs j ON j.id=c.job_id"""):
        if row["status"] not in RECOVERABLE:
            raise PortalError("Unrecognized saved parallel claim: " + row["job_id"])


def claim_next(state, slot):
    """Assignment and pre-export planning state are committed together.

    Never reclaim on a timer: a slow server response is not a dead worker.
    This is called only while the process owns the output-directory lock.
    """
    with state.db:
        state.db.execute("BEGIN IMMEDIATE")
        if state.db.execute("SELECT 1 FROM parallel_claims WHERE slot=?", (slot,)).fetchone():
            raise PortalError("Slot still owns an unfinished job")
        job = state.db.execute("""SELECT j.* FROM jobs j WHERE j.status='pending'
            AND NOT EXISTS (SELECT 1 FROM parallel_claims c WHERE c.job_id=j.id)
            ORDER BY j.depth DESC,j.rowid LIMIT 1""").fetchone()
        if job is None:
            return None
        state.db.execute("INSERT INTO parallel_claims VALUES (?,?,?)", (job["id"], slot, now()))
        state.db.execute("INSERT OR REPLACE INTO parallel_owners VALUES (?,?)", (job["id"], slot))
        state.db.execute("UPDATE jobs SET status='planning',updated_at=? WHERE id=?", (now(), job["id"]))
    return state.db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()


class WorkerState(State):
    def __init__(self, output, slot):
        # Each task creates and closes its own connection in the executing thread.
        # No shared SQLite connection, schema initialization or report temp files.
        self.output = canonical_path(output)
        self.directory = self.output / "_state"
        self.slot = slot
        self.db = sqlite3.connect(self.directory / "jobs.sqlite", timeout=30)
        self.db.row_factory = sqlite3.Row

    def cookie_path(self):
        if not 1 <= self.slot <= 30:
            raise ValueError("Invalid worker slot")
        return self.directory / f"parallel_session_{self.slot:02d}.cookies"

    def report(self, *, write=True):
        # Only the coordinator publishes the combined progress/index snapshot.
        return None


class WorkerDownloader(Downloader):
    def __init__(self, state, controller):
        super().__init__(state, accept_terms=controller.accept_terms,
                         target=controller.target, delay=controller.gate.delay,
                         export_timeout=controller.export_timeout)
        self.controller = controller
        self.portal = Portal(accept_terms=controller.accept_terms, delay=controller.gate.delay,
                             cookie_file=state.cookie_path(), export_request_timeout=controller.export_timeout,
                             request_gate=controller.gate.wait, error_hook=controller.gate.on_error)

    def check_stop(self):
        if self.controller.stopped.is_set():
            raise KeyboardInterrupt
        super().check_stop()

    def log(self, message):
        # One whole log entry and activity update at a time, no interleaved lines.
        with self.controller.log_lock:
            super().log(f"[Worker {self.state.slot:02d}] " + message)


class ParallelRun:
    def __init__(self, state, *, workers=1, accept_terms=False, target=8000,
                 delay=1.5, export_timeout=3600):
        if not 1 <= workers <= 30:
            raise ValueError("workers must be within 1..30")
        self.state, self.workers = state, workers
        self.accept_terms, self.target = accept_terms, target
        self.export_timeout = export_timeout
        self.stopped = threading.Event()
        self.gate = RequestGate(delay, self.stopped, stop_file=state.output / "STOP")
        self.log_lock = threading.Lock()
        self.legacy = Downloader(state, accept_terms=accept_terms, target=target,
                                 delay=delay, export_timeout=export_timeout)
        self.legacy.portal = Portal(accept_terms=accept_terms, delay=delay,
                                   cookie_file=state.cookie_path(), export_request_timeout=export_timeout,
                                   request_gate=self.gate.wait, error_hook=self.gate.on_error)
        self.failures = 0

    def request_stop(self):
        self.stopped.set()
        self.legacy.stopping = True

    def check_stop(self):
        if (self.state.output / "STOP").exists():
            self.request_stop()
        if self.stopped.is_set():
            raise KeyboardInterrupt

    def execute(self, slot, job_id):
        state = WorkerState(self.state.output, slot)
        try:
            loader = WorkerDownloader(state, self)
            job = state.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            owner = state.db.execute("SELECT slot FROM parallel_claims WHERE job_id=?", (job_id,)).fetchone()
            if not owner or owner[0] != slot:
                raise PortalError("Worker does not own this job")
            loader.check_stop()
            if job["status"] in INFLIGHT:
                # The original guest context is used before any portal reset.
                loader.recover_inflight([job])
            elif job["status"] in {"pending", "planning", "ready"}:
                if job["status"] != "ready":
                    loader.portal.enter()
                loader.process_job(job)
            else:
                raise PortalError("Job is not eligible for execution")
        finally:
            state.db.close()

    def drain(self, pool, running, *, dispatch=False, recovery=()):
        """Keep reports live; on failure drain safe checkpoints before unlocking."""
        backlog = list(recovery)
        first_error = None
        last_report = 0.0
        while True:
            try:
                self.check_stop()
                if first_error is None:
                    if dispatch:
                        busy = set(running.values())
                        for slot in range(1, self.workers + 1):
                            if slot not in busy:
                                job = claim_next(self.state, slot)
                                if job is not None:
                                    running[pool.submit(self.execute, slot, job["id"])] = slot
                    else:
                        while backlog and len(running) < self.workers:
                            slot, job_id = backlog.pop(0)
                            running[pool.submit(self.execute, slot, job_id)] = slot
                if time.monotonic() - last_report >= 5:
                    self.state.report()
                    last_report = time.monotonic()
            except BaseException as exc:
                first_error = first_error or exc
                self.request_stop()
            if not running:
                if first_error is not None:
                    raise first_error
                return
            done, _ = futures.wait(running, timeout=0.5, return_when=futures.FIRST_COMPLETED)
            for future in done:
                slot = running.pop(future)
                try:
                    future.result()
                    current = self.state.db.execute("""SELECT j.status FROM jobs j
                        JOIN parallel_claims c ON c.job_id=j.id WHERE c.slot=?""", (slot,)).fetchone()
                    if not current or current[0] in RECOVERABLE:
                        raise PortalError("Worker exited without a terminal checkpoint")
                    with self.state.db:
                        self.state.db.execute("DELETE FROM parallel_claims WHERE slot=?", (slot,))
                    self.failures = self.failures + 1 if current[0] == "error" else 0
                    if self.failures >= 3:
                        raise PortalError("Three consecutive worker failures; stopping the whole pool")
                except BaseException as exc:
                    first_error = first_error or exc
                    self.request_stop()

    def run(self):
        prepare_queue(self.state)
        self.check_stop()
        # Resolve the old sequential session before creating any fresh requests.
        legacy_jobs = self.state.db.execute("""SELECT j.* FROM jobs j WHERE
            status IN ('submitting','waiting','uncertain','ready') AND NOT EXISTS
            (SELECT 1 FROM parallel_owners c WHERE c.job_id=j.id)""").fetchall()
        orphan = self.state.db.execute("""SELECT j.id FROM jobs j JOIN parallel_owners o ON o.job_id=j.id
            WHERE j.status IN ('submitting','waiting','uncertain','ready') AND NOT EXISTS
            (SELECT 1 FROM parallel_claims c WHERE c.job_id=j.id) LIMIT 1""").fetchone()
        if orphan:
            raise PortalError("Saved parallel session has no active claim; inspect before continuing: " + orphan[0])
        self.legacy.recover_inflight([j for j in legacy_jobs if j["status"] in INFLIGHT])
        for job in legacy_jobs:
            if job["status"] == "ready":
                self.legacy.process_job(job)
        saved = self.state.db.execute("""SELECT c.slot,c.job_id,j.status FROM parallel_claims c
                                        JOIN jobs j ON j.id=c.job_id ORDER BY c.slot""").fetchall()
        with futures.ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="fgg") as pool:
            # Recover ALL saved slots even when resuming with fewer threads.
            # No fresh work until every old in-flight export is resolved.
            self.drain(pool, {}, recovery=[(r["slot"], r["job_id"]) for r in saved
                                          if r["status"] in INFLIGHT | {"ready"}])
            self.check_stop()
            self.legacy.initialize()
            self.gate.delay = max(self.gate.delay, self.legacy.portal.delay)
            self.drain(pool, {}, recovery=[(r["slot"], r["job_id"]) for r in saved
                                          if r["status"] in {"pending", "planning"}])
            self.state.set_meta("parallel_workers", self.workers)
            self.legacy.log(f"Parallelabruf gestartet: bis zu {self.workers} I/O-Worker, globaler Abstand {self.gate.delay}s")
            self.drain(pool, {}, dispatch=True)
        # Only the coordinator touches the catalog, in its independent session.
        self.check_stop()
        self.legacy.discover_stations()
        while self.state.db.execute("SELECT 1 FROM stations WHERE status='pending'").fetchone():
            self.check_stop()
            self.legacy.station_batch(20)
        report = self.state.report()
        self.legacy.log("Abruf vollständig." if report["all_complete"] else "Lauf beendet; offene Teilmengen bleiben in fortschritt.json.")


def check_paths(state, *, relocate=False):
    """Rebase only copied job-directory labels, not originals or cookies."""
    changes = []
    for job in state.db.execute("SELECT * FROM jobs WHERE directory IS NOT NULL"):
        filters = json.loads(job["filters"])
        lo = filters.get("gewaehltMessjahrVon", "unknown")
        hi = filters.get("gewaehltMessjahrBis", "unknown")
        period = lo if lo == hi else lo + "-" + hi
        destination = canonical_path(state.output / TOPIC_NAMES[job["topic"]] / period / job["id"])
        if not destination.is_relative_to(state.output):
            raise PortalError("Unsafe copied job path")
        if Path(job["directory"]) == destination:
            continue
        if not relocate:
            raise PortalError("Saved paths differ from --output. Copy the FULL stopped data folder, then use --relocate-state")
        if not destination.is_dir():
            raise PortalError("Copied job directory is missing: " + str(destination))
        metadata = destination / "metadata.json"
        if metadata.exists():
            meta = json.loads(metadata.read_text(encoding="utf-8"))
            if meta.get("job_id") != job["id"]:
                raise PortalError("Copied metadata belongs to another job")
            for entry in meta.get("files", []):
                file = canonical_path(destination / entry["filename"])
                if not file.is_relative_to(destination) or not file.is_file():
                    raise PortalError("Copied raw file missing or unsafe")
        elif job["status"] in {"complete", "complete_with_warnings"}:
            raise PortalError("Completed copied job has no metadata")
        changes.append({"job_id": job["id"], "old": job["directory"], "new": str(destination)})
    if changes:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        write_json(state.directory / f"path_relocation_{stamp}.json", {"at": now(), "changes": changes})
        with state.db:
            state.db.executemany("UPDATE jobs SET directory=? WHERE id=?", [(c["new"], c["job_id"]) for c in changes])
    return len(changes)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "resume", "relocate"], nargs="?", default="run")
    parser.add_argument("--output", type=Path,
                        default=LINUX_OUTPUT if os.name != "nt" else None, required=os.name == "nt",
                        help=f"Data directory (Linux default: {LINUX_OUTPUT.as_posix()}; explicit path required on Windows)")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent I/O workers, 1..30; not CPU kernels")
    parser.add_argument("--accept-terms", action="store_true")
    parser.add_argument("--delay", type=float, default=1.5, help="Global minimum request start spacing, seconds")
    parser.add_argument("--target", type=int, default=8000)
    parser.add_argument("--export-timeout", type=int, default=3600)
    parser.add_argument("--relocate-state", action="store_true", help="Audit/rebase paths after copying the full stopped data folder")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 30:
        parser.error("--workers must be between 1 and 30")
    if not math.isfinite(args.delay) or args.delay < 1:
        parser.error("--delay must be finite and at least one second (globally)")
    if not 1 <= args.target <= 10000 or args.export_timeout < 60:
        parser.error("--target must be 1..10000 and --export-timeout at least 60 seconds")
    if args.command != "relocate" and not args.accept_terms:
        parser.error("Explicit --accept-terms consent is required")
    output = canonical_path(args.output)
    with worker_lock(output / "_state" / "worker.lock"):
        state = State(output)
        try:
            check_paths(state, relocate=args.relocate_state or args.command == "relocate")
            if args.command == "relocate":
                state.report()
                print("Local paths checked/rebased. No portal requests. Run downloader.py verify next.")
                return
            if args.command == "resume":
                (output / "STOP").unlink(missing_ok=True)
            controller = ParallelRun(state, workers=args.workers, accept_terms=args.accept_terms,
                                     target=args.target, delay=args.delay, export_timeout=args.export_timeout)
            old_signals = {}
            for name in ("SIGINT", "SIGTERM"):
                if hasattr(signal, name):
                    sig = getattr(signal, name)
                    old_signals[sig] = signal.signal(sig, lambda *_: controller.request_stop())
            record = {"pid": os.getpid(), "started_at": now(), "command": args.command,
                      "python": sys.executable, "script": str(Path(__file__).resolve()),
                      "workers": args.workers, "mode": "parallel_threads"}
            write_json(state.directory / "worker.json", record)
            reason = "Lauf regulär beendet; siehe Vollständigkeitsprüfung"
            try:
                controller.run()
            except KeyboardInterrupt:
                reason = "Unterbrochen; alle Worker haben sichere Haltepunkte erreicht"
                controller.legacy.log(reason)
            except Exception as exc:
                reason = str(exc)
                controller.legacy.log("Parallelabruf gestoppt: " + reason)
                raise
            finally:
                # ThreadPoolExecutor drains before run() exits; keep the lock until then.
                controller.request_stop()
                for sig, previous in old_signals.items():
                    signal.signal(sig, previous)
                write_json(state.directory / "worker.json", {**record, "stopped_at": now(), "reason": reason})
                state.report()
        finally:
            state.db.close()


if __name__ == "__main__":
    main()
