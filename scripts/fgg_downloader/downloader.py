"""Resumable, sequential Elbe CSV downloader, independent of SOLVE/Neo4j.

Run ``python downloader.py --help``. Files beneath the selected output directory
are the only application data written. Raw exports are never normalized in place.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import signal
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import uuid
from pathlib import Path

from portal import (BASE, ORIGIN, TERMS, Node, Option, Page, Portal, PortalError,
                    UncertainExport, clean_label, inspect_csv, public_url, safe_url, write_json, parse_portal_rows)

VERSION = "1.0"
DEFAULT_OUTPUT = Path(r"C:\Users\jowals001\awi\solve\dummy_data\fgg_data")
TOPIC_NAMES = {
    "Phytoplankton": "phytoplankton", "BenPhyto": "makrophyten_phytobenthos",
    "BenZoo": "makrozoobenthos", "Fischbestand": "fischfauna",
    "ChemWas": "schadstoffe_wasserphase", "ChemSch": "schadstoffe_schwebstoffe",
    "ChemSed": "schadstoffe_sediment", "ChemBio": "schadstoffe_biota",
    "Physchem": "allgemeine_gewaesserguete", "Hydro": "hydrologie",
    "Meteo": "meteorologie", "Bakt": "bakterien",
}
REQUIRED_DIMENSIONS = ["gewaehltMedium", "gewaehltBerechnung", "gewaehltMessvorgangart"]
SPLIT_DIMENSIONS = ["gewaehltMessstelle", "gewaehltParameter", "gewaehltGewaesser",
                    "gewaehltWasserkoerper", "gewaehltMessvorgang"]
FILTER_ORDER = REQUIRED_DIMENSIONS + ["gewaehltMessjahrVon", "gewaehltMessjahrBis"] + SPLIT_DIMENSIONS
TERMINAL = {"complete", "complete_with_warnings", "empty"}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def canonical_path(path):
    """Resolve links, then unify Windows DOS/extended-length spellings.

    Windows can return the extended prefix when an intermediate directory is
    created concurrently. Compare equivalent paths without dropping resolution
    or the descendant check. Do not reinterpret device namespaces.
    """
    resolved = Path(path).resolve()
    text = str(resolved)
    if os.name == "nt":
        if text.startswith("\\\\?\\UNC\\"):
            return Path("\\\\" + text[8:])
        if text.startswith("\\\\?\\") and re.match(r"[A-Za-z]:\\", text[4:]):
            return Path(text[4:])
    return resolved


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()[:24]


def check_result_selection(filters, actual, actual_count, expected_count):
    """Accept only an equal-count narrowing of the requested calendar years.

    CSV dates are checked separately before this can become a completed job.
    Station/parameter changes, larger periods, missing values and count changes
    remain errors. Matching row counts alone never excuse different entities.
    """
    differences = {k: {"expected": v, "actual": actual.get(k)} for k, v in filters.items()
                   if clean_label(actual.get(k, "")) != v}
    years = {"gewaehltMessjahrVon", "gewaehltMessjahrBis"}
    if actual_count == expected_count and not differences:
        return None
    if actual_count == expected_count and differences and set(differences) <= years:
        values = [filters.get("gewaehltMessjahrVon", ""), filters.get("gewaehltMessjahrBis", ""),
                  clean_label(actual.get("gewaehltMessjahrVon", "")), clean_label(actual.get("gewaehltMessjahrBis", ""))]
        if all(re.fullmatch(r"\d{4}", v) for v in values):
            requested_lo, requested_hi, actual_lo, actual_hi = map(int, values)
            if requested_lo <= actual_lo <= actual_hi <= requested_hi:
                return {"kind": "equal_count_narrower_year_range", "expected_count": expected_count,
                        "requested_years": [requested_lo, requested_hi], "returned_years": [actual_lo, actual_hi],
                        "differences": differences}
    raise UncertainExport("Export result differs: " + json.dumps(
        {"expected_count": expected_count, "actual_count": actual_count, "filters": differences}, ensure_ascii=False))


def check_csv_period(raw, encoding, normalization):
    """Validate each original date; never pad, remove or rewrite a CSV record."""
    parsed, _ = parse_portal_rows(raw.decode(encoding))
    parsed = [r for r in parsed if any(c.strip() for c in r)]
    if not parsed or "Datum" not in parsed[0]:
        raise PortalError("Narrowed year range requires an original Datum column; no dates inferred")
    date_column = parsed[0].index("Datum")
    lo, hi = normalization["returned_years"]
    for row in parsed[1:]:
        try:
            text = row[date_column].strip()
            if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):
                raise ValueError("unknown date format")
            date = dt.datetime.strptime(text, "%d.%m.%Y").date()
        except (IndexError, ValueError) as exc:
            raise PortalError("Narrowed year range has missing or invalid original dates; raw file retained") from exc
        if not lo <= date.year <= hi:
            raise PortalError("CSV date is outside the returned narrowed year range; raw file retained")
    return {"column": "Datum", "checked_rows": len(parsed) - 1, "returned_years": [lo, hi]}


def count_page(page: Page) -> int:
    match = re.search(r"Die aktuelle Abfrage umfasst\s+([\d.]+)\s+Messwert", page.text)
    if not match:
        raise PortalError("No unambiguous measurement count in the current page")
    return int(match[1].replace(".", ""))


def real_options(page: Page, field: str) -> list[Option]:
    result = [o for o in page.selects().get(field, []) if o.value != "keine Auswahl"]
    labels = [o.label for o in result]
    if len(set(labels)) != len(labels):
        raise PortalError("Ambiguous duplicate labels in " + field)
    return result


def check_partition(options: list[Option], parent_count: int):
    if not options or any(o.count is None or o.count < 0 for o in options):
        raise PortalError("Missing partition counts; coverage cannot be established")
    if sum(o.count for o in options) != parent_count:
        raise PortalError(f"Partition counts do not reconcile: {sum(o.count for o in options)} != {parent_count}")


def year_groups(options: list[Option], target: int):
    """Disjoint inclusive year ranges, including every available year exactly once."""
    if any(not re.fullmatch(r"\d{4}", o.label) or o.count is None for o in options):
        raise PortalError("Unrecognized or undated year option; not silently discarded")
    groups, group, count = [], [], 0
    for option in sorted(options, key=lambda o: int(o.label), reverse=True):
        if group and count + option.count > target:
            groups.append((min(group), max(group), count))
            group, count = [], 0
        group.append(int(option.label))
        count += option.count
    if group:
        groups.append((min(group), max(group), count))
    return groups


def parse_catalog_station(page: Page, label: str, expected: int):
    values = {}
    # Observed page layout: adjacent sibling divs with classes label and inhalt.
    # Dropdown filter boxes are excluded; only station detail values are kept.
    for parent in [page.root, *page.root.find_all()]:
        children = [c for c in parent.children if isinstance(c, Node)]
        for left, right in zip(children, children[1:]):
            if "label" not in str(left.attrs.get("class", "")).split():
                continue
            if "inhalt" not in str(right.attrs.get("class", "")).split():
                continue
            if list(right.find_all("select")):
                continue
            key, value = left.text(), right.text()
            if key:
                if key in values and values[key] != value:
                    raise PortalError("Conflicting catalog field: " + key)
                values[key] = value
    if values.get("Bezeichnung") != label:
        raise PortalError("Station detail does not match its requested designation")
    count = re.search(r"Zu dieser Messstelle gibt es\s+([\d.]+)\s+Messwerte", page.text)
    if not count or int(count[1].replace(".", "")) != expected:
        raise PortalError("Station catalog measurement count changed")
    return {"source_station_key": "fgg-catalog-" + identity(label),
            "portal_station_id": page.fields().get("idDerGewaehltenMessstelle", ""),
            "selection_label": label, "measurement_count": expected,
            "retrieved_at": now(), "source_url": public_url(page.url),
            "coordinates_crs": "UTM zone 32N; datum not yet independently confirmed",
            "fields": values}


class State:
    def __init__(self, output: Path):
        self.output = canonical_path(output)
        self.directory = self.output / "_state"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "jobs.sqlite", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS topics(
                code TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL,
                expected INTEGER NOT NULL, discovered_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, topic TEXT NOT NULL, parent TEXT, filters TEXT NOT NULL,
                expected INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                depth INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, error TEXT, rows INTEGER,
                links TEXT, directory TEXT, details TEXT);
            CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(topic,status,depth);
            CREATE INDEX IF NOT EXISTS jobs_parent ON jobs(parent);
            CREATE TABLE IF NOT EXISTS stations(
                id TEXT PRIMARY KEY, label TEXT NOT NULL UNIQUE, expected INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', details TEXT, error TEXT);
        """)
        if self.meta("version") not in {None, VERSION}:
            raise PortalError("Incompatible state version")
        self.set_meta("version", VERSION)
        if not self.meta("run_id"):
            self.set_meta("run_id", dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))

    def meta(self, key):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_meta(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def add_job(self, topic, filters, expected, parent=None, depth=0):
        job_id = identity({"topic": topic, "filters": filters})
        timestamp = now()
        self.db.execute("""INSERT OR IGNORE INTO jobs
            (id,topic,parent,filters,expected,depth,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?)""",
                        (job_id, topic, parent, json.dumps(filters, ensure_ascii=False),
                         expected, depth, timestamp, timestamp))
        return job_id

    def update(self, job_id, **fields):
        allowed = {"expected", "status", "attempts", "error", "rows", "links", "directory", "details"}
        if set(fields) - allowed:
            raise ValueError("Unknown state field")
        fields["updated_at"] = now()
        with self.db:
            self.db.execute("UPDATE jobs SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?",
                            [*fields.values(), job_id])

    def split(self, job, children, field):
        if sum(count for _, count in children) != job["expected"]:
            raise PortalError("Child jobs do not cover their parent")
        with self.db:
            ids = [self.add_job(job["topic"], filters, count, job["id"], job["depth"] + 1)
                   for filters, count in children]
            if len(ids) != len(set(ids)) or job["id"] in ids:
                raise PortalError("Duplicate or self-referencing child job")
            self.db.execute("UPDATE jobs SET status='split',details=?,updated_at=? WHERE id=?",
                            (json.dumps({"split_by": field, "children": ids}), now(), job["id"]))

    def cookie_path(self):
        name = self.meta("active_cookie_filename") or "guest_session.cookies"
        if not re.fullmatch(r"guest_session(?:_[a-f0-9]{32})?\.cookies", name):
            raise PortalError("Invalid downloader guest-session filename")
        return self.directory / name

    def defer_uncertain(self, job_id, evidence_path, reason):
        """Explicitly leave an unrecoverable export open, without resubmitting it.

        This is never called by normal resume or monitoring. Keep the full job,
        its former session and a receipt; isolate subsequent work in a new guest
        session. The deferred job still blocks any claim of complete coverage.
        """
        job = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job or job["status"] != "uncertain" or not reason.strip():
            raise PortalError("Expected an explicitly selected uncertain job and reason")
        if self.db.execute("SELECT 1 FROM jobs WHERE status IN ('waiting','submitting','ready','uncertain') AND id<>?",
                           (job_id,)).fetchone():
            raise PortalError("Another in-flight export must be resolved first")
        context = json.loads(job["details"] or "{}")
        if context.get("refresh_url") or job["links"]:
            raise PortalError("Saved polling/result address exists; inspect it before deferring")
        started = dt.datetime.fromisoformat(context["started_at"])
        if started.tzinfo is None or (dt.datetime.now(dt.timezone.utc) - started).total_seconds() < 3600:
            raise PortalError("Do not defer a recently submitted export")
        directory = Path(job["directory"]).resolve()
        if not directory.is_relative_to(self.output) or evidence_path.resolve().parent != directory:
            raise PortalError("Recovery evidence must belong to this job's output directory")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        if (evidence.get("job_id") != job_id or evidence.get("expected_count") != job["expected"]
                or evidence.get("probe_kind") != "saved_selection_page" or evidence.get("links")
                or evidence.get("refresh") or evidence.get("actual_count") is not None
                or not any(marker in evidence.get("text", "") for marker in ["Sitzung verloren", "java.lang.NullPointerException"])):
            raise PortalError("Evidence does not establish an unavailable saved selection page")
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        receipt_name = "deferred_receipt_" + stamp + ".json"
        new_cookie_name = "guest_session_" + uuid.uuid4().hex + ".cookies"
        receipt = {"at": now(), "reason": reason, "original_job": dict(job),
                   "evidence": evidence_path.name, "previous_cookie_filename": self.cookie_path().name,
                   "next_cookie_filename": new_cookie_name, "resubmitted": False}
        write_json(directory / receipt_name, receipt)
        with self.db:
            self.db.execute("UPDATE jobs SET status='deferred_uncertain',details=?,updated_at=? WHERE id=?",
                            (json.dumps({**context, "deferral_receipt": receipt_name}, ensure_ascii=False), now(), job_id))
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)",
                            ("active_cookie_filename", json.dumps(new_cookie_name)))
        return receipt

    def report(self, *, write=True):
        by_topic = []
        for topic in self.db.execute("SELECT * FROM topics ORDER BY code").fetchall():
            summary = {"topic": topic["code"], "name": topic["name"], "expected": topic["expected"]}
            counts = self.db.execute("""SELECT status,count(*) AS jobs,coalesce(sum(rows),0) AS rows,
                coalesce(sum(expected),0) AS expected FROM jobs WHERE topic=? AND status<>'split' GROUP BY status""",
                                     (topic["code"],)).fetchall()
            summary["states"] = {r["status"]: {k: r[k] for k in ["jobs", "rows", "expected"]} for r in counts}
            summary["downloaded_rows"] = sum(r["rows"] for r in counts if r["status"] in TERMINAL)
            summary["coverage_complete"] = (bool(counts) and all(r["status"] in TERMINAL for r in counts)
                                              and summary["downloaded_rows"] == topic["expected"])
            by_topic.append(summary)
        station_counts = dict(self.db.execute("SELECT status,count(*) FROM stations GROUP BY status"))
        problems = [dict(r) for r in self.db.execute("SELECT id,topic,filters,status,error FROM jobs WHERE status IN ('error','uncertain','deferred_uncertain','needs_review')")]
        if self.meta("catalog_count_issue"):
            problems.append({"id": "station_catalog_count", "topic": "Stationskatalog", "status": "needs_review",
                             "error": self.meta("catalog_count_issue")})
        problems.extend({"id": r["id"], "topic": "Stationskatalog", "status": "error", "error": r["error"]}
                        for r in self.db.execute("SELECT id,error FROM stations WHERE status='error'"))
        result = {"updated_at": now(), "run_id": self.meta("run_id"), "topics": by_topic,
                  "downloaded_rows": sum(t["downloaded_rows"] for t in by_topic),
                  "completed_exports": sum(t["states"].get(s, {}).get("jobs", 0)
                                           for t in by_topic for s in ("complete", "complete_with_warnings")),
                  "stations": station_counts, "problems": problems,
                  "all_complete": bool(by_topic) and len(by_topic) == 12 and all(t["coverage_complete"] for t in by_topic)
                                  and station_counts.get("complete", 0) == self.meta("station_count")
                                  and not station_counts.get("error", 0) and not station_counts.get("pending", 0)}
        result["expected_rows"] = sum(t["expected"] for t in by_topic)
        result["percent"] = (100 * result["downloaded_rows"] / result["expected_rows"]) if result["expected_rows"] else 0.0
        result["active_jobs"] = [dict(r) for r in self.db.execute(
            "SELECT id,topic,expected,status,updated_at,details FROM jobs WHERE status IN ('planning','submitting','waiting','ready')")]
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='parallel_claims'").fetchone():
            slots = dict(self.db.execute("SELECT job_id,slot FROM parallel_claims"))
            for active in result["active_jobs"]:
                active["worker_slot"] = slots.get(active["id"])
        for active in result["active_jobs"]:
            details = json.loads(active.pop("details") or "{}")
            active["wait_seconds"] = details.get("wait_seconds")
            active["last_poll_at"] = details.get("last_poll_at")
            active["started_at"] = details.get("started_at")
            active["response_timeout_seconds"] = details.get("response_timeout_seconds")
        worker_file = self.directory / "worker.json"
        result["worker"] = json.loads(worker_file.read_text(encoding="utf-8")) if worker_file.exists() else None
        result["last_activity"] = self.meta("last_activity")
        result["live_resume_test"] = self.meta("live_resume_test")
        result["report_write_errors"] = self.meta("report_write_errors") or {}
        if write:
            progress = self.output / "fortschritt.txt"
            def write_progress():
                temporary = progress.with_suffix(".txt.tmp")
                temporary.write_text(format_progress(result) + "\n", encoding="utf-8-sig")
                temporary.replace(progress)
            self._write_report_file(progress, write_progress)
            self._write_report_file(self.output / "dateiuebersicht.csv", self.write_index)
            status = self.output / "fortschritt.json"
            # A successfully published JSON snapshot proves its own write recovered.
            # Include any current TXT/index warnings, not a stale JSON-write warning.
            result["report_write_errors"] = {
                k: v for k, v in (self.meta("report_write_errors") or {}).items() if k != status.name}
            self._write_report_file(status, lambda: write_json(status, result))
            result["report_write_errors"] = self.meta("report_write_errors") or {}
        return result

    def _write_report_file(self, path, writer):
        """Best-effort access to derived reports only, never raw data/checkpoints.

        A Windows reader can briefly deny replacement of an otherwise writable
        file. Retry five times total (1.5 s backoff), then keep the old snapshot.
        Other I/O errors, such as a full disk, still propagate. Persistent access
        failures are recorded in SQLite and remain visible via status/progress.
        """
        for attempt in range(5):
            try:
                writer()
            except OSError as exc:
                if not isinstance(exc, PermissionError) and getattr(exc, "winerror", None) not in {5, 32, 33}:
                    raise
                if attempt < 4:
                    time.sleep(0.1 * 2 ** attempt)
                    continue
                issues = self.meta("report_write_errors") or {}
                previous = issues.get(path.name)
                issues[path.name] = {"error": str(exc), "first_seen": previous["first_seen"] if previous else now(),
                                     "last_seen": now()}
                self.set_meta("report_write_errors", issues)
                if previous is None:
                    self._report_notice(f"Statusdatei {path.name} nicht aktualisierbar: {exc}; Download läuft weiter.")
                return False
            else:
                issues = self.meta("report_write_errors") or {}
                if path.name in issues:
                    del issues[path.name]
                    self.set_meta("report_write_errors", issues)
                    self._report_notice(f"Statusdatei {path.name} wird wieder aktualisiert.")
                return True

    @staticmethod
    def _report_notice(message):
        # A blocked redirected stderr must not turn a reporting warning into a stop.
        try:
            print(now() + " " + message, file=sys.stderr, flush=True)
        except OSError:
            pass

    def write_index(self):
        # A machine-readable download index, not a rewritten measurement table.
        path = self.output / "dateiuebersicht.csv"
        temporary = path.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, delimiter=";")
            writer.writerow(["job_id", "thema", "status", "erwartete_messwerte", "gelesene_zeilen", "ordner", "filter_json", "hinweis"])
            for row in self.db.execute("SELECT * FROM jobs WHERE status<>'split' ORDER BY topic,created_at,id"):
                writer.writerow([row["id"], row["topic"], row["status"], row["expected"], row["rows"],
                                 row["directory"], row["filters"], row["error"]])
        temporary.replace(path)

    def write_catalog(self):
        rows = [json.loads(r[0]) for r in self.db.execute("SELECT details FROM stations WHERE status='complete' ORDER BY label")]
        columns = sorted({k for r in rows for k in r["fields"]})
        fixed = ["source_station_key", "portal_station_id", "selection_label", "measurement_count",
                 "retrieved_at", "source_url", "coordinates_crs"]
        path = self.output / "messstellen.csv"
        temporary = path.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fixed + columns, delimiter=";")
            writer.writeheader()
            for row in rows:
                writer.writerow({**{k: row[k] for k in fixed}, **row["fields"]})
        temporary.replace(path)


def format_progress(report):
    percent = report["percent"]
    filled = min(30, int(percent * 30 / 100))
    lines = ["Elbe-Datenportal: Downloadfortschritt", "Stand (UTC): " + report["updated_at"],
             "[" + "#" * filled + "-" * (30 - filled) + f"] {percent:.3f} %",
             f"Messwerte: {report['downloaded_rows']:,} / {report['expected_rows']:,}",
             f"Geprüfte CSV-Exporte: {report['completed_exports']}",
             f"Messstellen: {report['stations'].get('complete', 0)}",
             f"Offene Probleme: {len(report['problems'])}",
             "Vollständig: " + ("ja" if report["all_complete"] else "nein"), ""]
    worker = report.get("worker") or {}
    if worker.get("mode") == "parallel_threads":
        lines += [f"Konfigurierte parallele I/O-Worker: {worker['workers']}", ""]
    if worker.get("stopped_at"):
        lines += ["Prozess beendet (UTC): " + worker["stopped_at"],
                  "Grund: " + worker.get("reason", "siehe Protokoll"), ""]
    activity = report.get("last_activity")
    if activity:
        lines += ["Letzte Aktivität (UTC): " + activity["at"], activity["message"], ""]
    if report.get("report_write_errors"):
        lines += ["Warnung: Statusdateien konnten nicht aktualisiert werden (Anzeige möglicherweise veraltet):"]
        lines += [f"{name}: {issue['error']}" for name, issue in report["report_write_errors"].items()]
        lines += [""]
    for topic in report["topics"]:
        lines.append(f"{topic['topic']}: {topic['downloaded_rows']:,} / {topic['expected']:,}")
    if report["active_jobs"]:
        lines += ["", "Aktuelle Aufträge:"]
        for job in report["active_jobs"]:
            waited = job.get("wait_seconds")
            suffix = f" | wartet seit {int(waited)} Sekunden" if waited is not None and job["status"] == "waiting" else ""
            if job["status"] == "submitting":
                suffix = (" | wartet auf erste Portalantwort seit " + str(job.get("started_at") or "unbekannt")
                          + f" | HTTP-Zeitgrenze: {job.get('response_timeout_seconds') or 'unbekannt'} Sekunden")
            slot = f"Worker {job['worker_slot']:02d} | " if job.get("worker_slot") else ""
            lines.append(f"  {slot}{job['topic']}: {job['status']} ({job['expected']:,} Werte)" + suffix)
    return "\n".join(lines)


@contextlib.contextmanager
def worker_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise PortalError("Another downloader already owns this output directory")
    try:
        yield
    finally:
        stream.close()


class Downloader:
    def __init__(self, state: State, *, accept_terms, target=8000, delay=1.5, export_timeout=3600):
        if not 1 <= target <= 10000:
            raise ValueError("target must be within 1..10000")
        self.state = state
        self.target = target
        self.portal = Portal(accept_terms=accept_terms, delay=delay,
                             cookie_file=state.cookie_path(), export_request_timeout=export_timeout)
        self.export_timeout = export_timeout
        self.active_topic = None
        self.active_filters = {}
        self.active_page = None
        self.stopping = False
        self.completed_this_process = 0
        self.consecutive_network_errors = 0
        self.checkpoint_after_submit = False

    def log(self, message):
        line = now() + " " + message
        print(line, flush=True)
        with (self.state.directory / "download.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        self.state.set_meta("last_activity", {"at": now(), "message": message})

    def check_stop(self):
        if self.stopping or (self.state.output / "STOP").exists():
            raise KeyboardInterrupt

    def initialize(self):
        if not self.portal.accept_terms:
            raise PortalError("Use --accept-terms only with the data user's consent")
        # Terms receipt is stored separately; no browser session information is copied.
        _, headers, raw = self.portal.request(TERMS)
        terms_page = Page(TERMS, raw.decode(headers.get_content_charset() or "utf-8"))
        if "Eine Weiterleitung an Dritte ist nicht gestattet." not in terms_page.text:
            raise PortalError("Terms page is not the previously inspected terms document")
        # HTML includes random jsessionid values in links. Compare readable terms,
        # not those transient session markers. Keep the earlier raw hash as audit.
        terms_content = [n for n in terms_page.root.find_all("div") if n.attrs.get("id") == "kontakt"]
        if len(terms_content) != 1:
            raise PortalError("Cannot isolate the terms content from session-dependent navigation")
        terms_hash = hashlib.sha256(terms_content[0].text().encode("utf-8")).hexdigest()
        approved_hash = self.state.meta("terms_content_sha256")
        if approved_hash and approved_hash != terms_hash:
            raise PortalError("Portal terms changed since this run was approved; obtain renewed consent")
        self.state.set_meta("terms_content_sha256", terms_hash)
        self.state.set_meta("terms_url", TERMS)
        self.state.set_meta("terms_accepted_at", self.state.meta("terms_accepted_at") or now())
        self.state.set_meta("target", self.target)
        # Respect published robots rules. A 404 is recorded, not treated as a license.
        try:
            import urllib.robotparser
            _, _, robots = self.portal.request(ORIGIN + "/robots.txt")
            robot = urllib.robotparser.RobotFileParser()
            robot.parse(robots.decode("utf-8").splitlines())
            if not robot.can_fetch("FGG-Local-Research-Downloader", BASE + "content/auswertung/"):
                raise PortalError("Published robots.txt disallows the intended automated path")
            crawl_delay = robot.crawl_delay("FGG-Local-Research-Downloader") or robot.crawl_delay("*")
            if crawl_delay:
                self.portal.delay = max(self.portal.delay, crawl_delay)
            self.state.set_meta("robots", {"status": 200, "checked_at": now()})
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            self.state.set_meta("robots", {"status": 404, "checked_at": now()})
        self.discover_topics()

    def discover_topics(self):
        root = self.portal.enter()
        found = {}
        for _, url in root.links():
            match = re.search(r"Untersuchungsbereich(\w+)_start_x\.action", url)
            if match:
                found[match[1]] = url
        if set(found) != set(TOPIC_NAMES):
            raise PortalError("Portal topic catalog changed: " + repr(sorted(found)))
        for code, url in found.items():
            if self.state.db.execute("SELECT 1 FROM topics WHERE code=?", (code,)).fetchone():
                continue
            page = self.portal.get_page(url)
            page = self.reset_if_filtered(page)
            expected = count_page(page)
            heading = next(page.root.find_all("h1")).text()
            write_json(self.state.directory / "catalog" / (code + ".json"), page.summary())
            with self.state.db:
                self.state.db.execute("INSERT INTO topics VALUES (?,?,?,?,?)", (code, heading, public_url(url), expected, now()))
                self.state.add_job(code, {}, expected)
            self.log(f"Thema {code}: {expected:,} Messwerte")
        self.active_page = None
        self.active_topic = None
        self.state.report()

    def reset_if_filtered(self, page):
        fields = page.fields()
        if not any(name.startswith("aktuell") and value not in {"", "keine Auswahl"} for name, value in fields.items()):
            return page
        buttons = [n for n in page.form().find_all("input")
                   if str(n.attrs.get("name", "")).endswith("_notFilter_alle")]
        if len(buttons) != 1:
            raise PortalError("Cannot reset a previous selection")
        name = buttons[0].attrs["name"]
        page = self.portal.submit(page, {name + ".x": "1", name + ".y": "1"})
        if any(name.startswith("aktuell") and value not in {"", "keine Auswahl"} for name, value in page.fields().items()):
            raise PortalError("Portal did not clear its filters")
        return page

    def load_filters(self, topic, filters):
        can_extend = (self.active_page is not None and self.active_topic == topic
                      and all(filters.get(k) == v for k, v in self.active_filters.items()))
        if can_extend:
            page = self.active_page
        else:
            url = self.state.db.execute("SELECT url FROM topics WHERE code=?", (topic,)).fetchone()[0]
            page = self.portal.get_page(url)
            page = self.reset_if_filtered(page)
            self.active_filters = {}
        for name in FILTER_ORDER:
            if name in filters and self.active_filters.get(name) != filters[name]:
                self.check_stop()
                page = self.portal.choose(page, name, filters[name])
        actual = page.fields()
        for name, value in filters.items():
            if clean_label(actual.get(name, "")) != value:
                raise PortalError("Selection verification failed: " + name)
        self.active_page, self.active_topic, self.active_filters = page, topic, dict(filters)
        return page

    def refine_review_job(self, job):
        """Replace only a completed, count-mismatched export by verified children.

        The original raw CSV stays in the parent directory as audit evidence;
        only the new disjoint child exports contribute to completed coverage.
        """
        if job["status"] != "needs_review" or not str(job["error"]).startswith("CSV row count "):
            raise PortalError("Only a completed count-mismatched export can be refined")
        filters = json.loads(job["filters"])
        page = self.load_filters(job["topic"], filters)
        if count_page(page) != job["expected"]:
            raise PortalError("Live count changed; review the source before repartitioning")
        for field in ["gewaehltMessstelle", "gewaehltParameter", "gewaehltGewaesser"]:
            if field in filters:
                continue
            options = real_options(page, field)
            try:
                check_partition(options, job["expected"])
            except PortalError:
                continue
            if len(options) < 2:
                continue
            write_json(self.job_directory(job) / "refinement.json",
                       {"at": now(), "reason": job["error"], "original_job": dict(job),
                        "split_by": field, "children": [{"label": o.label, "expected": o.count} for o in options]})
            self.state.split(job, [({**filters, field: o.label}, o.count) for o in options], field)
            self.log(f"Abweichenden Export eingegrenzt: {job['id']} | {len(options)} geprüfte Teilmengen nach {field}; Original bleibt erhalten")
            self.state.report()
            return
        raise PortalError("No finer verified partition available; raw export remains under review")

    def plan(self, job, page):
        count = count_page(page)
        if count != job["expected"]:
            raise PortalError(f"Portal data changed or filters differ: {count} != expected {job['expected']}")
        if count == 0:
            self.state.update(job["id"], status="empty", rows=0)
            return True
        filters = json.loads(job["filters"])
        for name in REQUIRED_DIMENSIONS:
            if name in filters or name not in page.selects():
                continue
            options = real_options(page, name)
            check_partition(options, count)
            children = [({**filters, name: o.label}, o.count) for o in options]
            self.state.split(job, children, name)
            return True
        # The live Standardtabelle validation requires a station OR a parameter
        # (observed on Phytoplankton). Selecting one is also a valid partition in
        # the other topics. Do it before time splitting to avoid many tiny jobs.
        if not any(name in filters for name in ("gewaehltMessstelle", "gewaehltParameter")):
            anchors = []
            for name in ("gewaehltMessstelle", "gewaehltParameter"):
                options = real_options(page, name)
                try:
                    check_partition(options, count)
                except PortalError:
                    continue
                score = sum(max(1, math.ceil(o.count / self.target)) for o in options) + 0.05 * len(options)
                anchors.append((score, len(options), name, options))
            if not anchors:
                raise PortalError("Neither station nor parameter can be partitioned without losing records")
            _, _, name, options = min(anchors, key=lambda c: c[:3])
            self.state.split(job, [({**filters, name: o.label}, o.count) for o in options], name)
            return True
        if "gewaehltMessjahrVon" not in filters:
            options = real_options(page, "gewaehltMessjahrVon")
            check_partition(options, count)
            children = [({**filters, "gewaehltMessjahrVon": str(lo), "gewaehltMessjahrBis": str(hi)}, n)
                        for lo, hi, n in year_groups(options, self.target)]
            self.state.split(job, children, "year_ranges")
            return True
        if count <= self.target:
            return False
        candidates = []
        for name in SPLIT_DIMENSIONS:
            if name in filters:
                continue
            options = real_options(page, name)
            if len(options) < 2:
                continue
            try:
                check_partition(options, count)
            except PortalError:
                continue
            # Prefer fewer sufficiently large files. Group labels are never used.
            score = sum(max(1, math.ceil(o.count / self.target)) for o in options) + 0.05 * len(options)
            candidates.append((score, len(options), name, options))
        if candidates:
            _, _, name, options = min(candidates, key=lambda c: c[:3])
            self.state.split(job, [({**filters, name: o.label}, o.count) for o in options], name)
            return True
        if count <= 10000:
            return False  # target is a preference; the actual hard limit is 10,000.
        raise PortalError("Smallest verified partition still exceeds 10,000; no invented pagination")

    def job_directory(self, job):
        filters = json.loads(job["filters"])
        lo, hi = filters.get("gewaehltMessjahrVon", "unknown"), filters.get("gewaehltMessjahrBis", "unknown")
        period = lo if lo == hi else lo + "-" + hi
        directory = self.state.output / TOPIC_NAMES[job["topic"]] / period / job["id"]
        resolved = canonical_path(directory)
        if not resolved.is_relative_to(self.state.output):
            raise PortalError(f"Unsafe output path: {resolved!s} outside {self.state.output!s}")
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def download_links(self, job, links):
        directory = self.job_directory(job)
        existing = directory / "metadata.json"
        previous = json.loads(existing.read_text(encoding="utf-8")) if existing.exists() else {}
        saved = {f["source_url"]: f for f in previous.get("files", [])}
        context = self.state.db.execute("SELECT details FROM jobs WHERE id=?", (job["id"],)).fetchone()
        normalization = json.loads(context[0] or "{}").get("result_normalization")
        files = []
        for title, link in links:
            self.check_stop()
            link = safe_url(link)
            name = Path(urllib.parse.urlsplit(link).path).name
            if not re.fullmatch(r"[A-Za-z0-9_.-]+\.csv", name, re.I):
                raise PortalError("Unexpected download filename")
            path = directory / name
            if link in saved and path.exists():
                result = saved[link]
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != result["sha256"]:
                    raise PortalError("Existing raw CSV checksum differs; file preserved")
            else:
                _, headers, raw = self.portal.request(link)
                if path.exists() and path.read_bytes() != raw:
                    raise PortalError("Refusing to overwrite a different raw export")
                if not path.exists():
                    part = path.with_suffix(".csv.part")
                    with part.open("wb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    part.replace(path)
                result = inspect_csv(raw, headers.get("Content-Type", ""))
                result.update({"filename": name, "source_url": link, "title": title,
                               "content_type": headers.get("Content-Type", ""), "retrieved_at": now()})
            if normalization:
                result["period_validation"] = check_csv_period(raw, result["encoding"], normalization)
                warning = "portal_narrowed_year_range_with_equal_count; every_original_date_checked; raw_bytes_unchanged"
                if warning not in result["warnings"]:
                    result["warnings"].append(warning)
            files.append(result)
            write_json(existing, {"version": VERSION, "run_id": self.state.meta("run_id"),
                                  "job_id": job["id"], "topic": job["topic"],
                                  "filters": json.loads(job["filters"]), "expected": job["expected"], "files": files,
                                  "result_normalization": normalization})
        rows = sum(f["rows"] for f in files)
        if rows != job["expected"]:
            raise PortalError(f"CSV row count {rows} differs from expected {job['expected']}; raw files preserved")
        warnings = [w for f in files for w in f["warnings"]]
        status = "complete_with_warnings" if warnings else "complete"
        self.state.update(job["id"], status=status, rows=rows, directory=str(directory),
                          details=json.dumps({"files": len(files), "warnings": warnings,
                                              "result_normalization": normalization}), error=None)
        self.completed_this_process += 1
        self.consecutive_network_errors = 0
        self.log(f"CSV gespeichert: {job['topic']} | {rows:,} Zeilen | {job['id']}" + (" | Schemahinweis" if warnings else ""))

    def export(self, job, page):
        directory = self.job_directory(job)
        request_record = {"retrieved_at": now(), "url": public_url(page.url),
                          "fields": page.fields(), "expected": job["expected"], "filters": json.loads(job["filters"])}
        if not (directory / "abfrage.json").exists():
            write_json(directory / "abfrage.json", request_record)
        write_json(directory / f"abfrage_attempt_{job['attempts'] + 1}.json", request_record)
        buttons = [n for n in page.form().find_all("input") if str(n.attrs.get("name", "")).endswith("_export_tabelle")]
        if len(buttons) != 1:
            raise PortalError("No unique observed table export button")
        table = [o for o in page.selects().get("gewaehlterTabellentyp", []) if o.label == "Standardtabelle"]
        if len(table) != 1:
            raise PortalError("Standard table export not available")
        previous_links = {public_url(u) for _, u in page.links() if "/ausgabe/" in u}
        self.state.update(job["id"], status="submitting", attempts=job["attempts"] + 1, directory=str(directory),
                          details=json.dumps({"previous_links": list(previous_links), "started_at": now(),
                                              "response_timeout_seconds": self.portal.export_request_timeout}))
        self.log(f"Export angefordert: {job['topic']} | {job['expected']:,} Werte | {job['filters']}")
        self.state.report()
        name = buttons[0].attrs["name"]
        page = self.portal.submit(page, {name + ".x": "1", name + ".y": "1",
                                        "gewaehltesTabellenformat": "csv", "gewaehlterTabellentyp": table[0].value}, export=True)
        self.await_export(job, page, previous_links=previous_links)

    def await_export(self, job, page, *, previous_links=()):
        directory = self.job_directory(job)
        started = time.monotonic()
        stored = self.state.db.execute("SELECT details FROM jobs WHERE id=?", (job["id"],)).fetchone()
        context = json.loads(stored[0] or "{}")
        started_at = context.get("started_at", now())
        last_log = -60.0
        polls = 0
        while True:
            if "Sitzung verloren" in page.text:
                raise UncertainExport("Saved guest session expired; no duplicate export was submitted")
            links = list(dict.fromkeys((t, public_url(u)) for t, u in page.links()
                                      if "/ausgabe/" in u and urllib.parse.urlsplit(u).path.lower().endswith(".csv")))
            if links:
                actual = page.fields() if len(list(page.root.find_all("form"))) == 1 else {}
                try:
                    actual_count = count_page(page)
                except PortalError:
                    actual_count = None
                differences = {k: {"expected": v, "actual": actual.get(k)}
                               for k, v in json.loads(job["filters"]).items()
                               if clean_label(actual.get(k, "")) != v}
                # Save candidate links and exact differences BEFORE rejecting a
                # response. The polling endpoint can consume a finished result.
                stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                response_path = directory / ("export_response_" + stamp + ".json")
                write_json(response_path, {"captured_at": now(), "job_id": job["id"], "url": public_url(page.url),
                           "expected_count": job["expected"], "actual_count": actual_count, "fields": actual,
                           "differences": differences, "links": links, "previous_links": list(previous_links),
                           "refresh": page.refresh(), "text": page.text})
                if any(u in previous_links for _, u in links):
                    raise UncertainExport("Only an earlier export link was returned; not treated as the new result")
                normalization = None
                if list(page.root.find_all("form")):
                    try:
                        normalization = check_result_selection(json.loads(job["filters"]), actual, actual_count, job["expected"])
                    except UncertainExport as exc:
                        raise UncertainExport(str(exc) + " | evidence=" + response_path.name) from exc
                latest_context = json.loads(self.state.db.execute("SELECT details FROM jobs WHERE id=?", (job["id"],)).fetchone()[0] or "{}")
                self.state.update(job["id"], status="ready", links=json.dumps(links), error=None,
                                  details=json.dumps({**latest_context, "result_normalization": normalization,
                                                      "export_response": response_path.name}))
                self.state.report()
                self.active_page = page if normalization is None else None
                if normalization:
                    self.active_topic, self.active_filters = None, {}
                    self.log("Portal hat den Jahresbereich bei gleicher Trefferzahl eingegrenzt; prüfe alle CSV-Datumswerte.")
                self.download_links(job, links)
                return
            refresh = page.refresh()
            if not refresh:
                write_json(directory / "antwort.json", {"url": public_url(page.url), "text": page.text})
                if "Ihre Anfrage wird bearbeitet" in page.text:
                    raise UncertainExport("Processing page has no verified refresh action")
                raise PortalError("Export rejected or no CSV link: " + page.text[-2600:])
            elapsed = time.monotonic() - started
            wait_seconds = max(0, (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(started_at)).total_seconds())
            self.state.update(job["id"], status="waiting", error=None, details=json.dumps(
                {"refresh_url": public_url(refresh[1]), "started_at": started_at,
                 "previous_links": list(previous_links), "last_poll_at": now(),
                 "polls_this_process": polls, "wait_seconds": round(wait_seconds)}))
            if elapsed - last_log >= 60:
                self.log(f"Portal erstellt CSV: {job['topic']} | {job['expected']:,} Werte | Wartezeit {wait_seconds / 60:.1f} min | Verbindung antwortet")
                last_log = elapsed
            if polls % 3 == 0:
                self.state.report()
            if self.checkpoint_after_submit:
                self.checkpoint_after_submit = False
                attempts = self.state.db.execute("SELECT attempts FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
                self.state.set_meta("live_resume_test", {"job_id": job["id"], "attempts": attempts,
                                                       "status": "checkpoint_saved", "created_at": now()})
                self.state.report()
                self.log("Live-Wiederanlauftest: nach gespeichertem Export-Wartezustand beendet.")
                raise KeyboardInterrupt
            if elapsed > self.export_timeout:
                raise UncertainExport("Export wait deadline reached; not submitted twice")
            # Persist the polling context before honoring a stop. Resume never
            # repeats the export POST, even when the original is still running.
            self.check_stop()
            remaining = max(refresh[0], min(5 + polls * 2, 30))
            while remaining > 0:
                step = min(remaining, 30)
                time.sleep(step)
                remaining -= step
                self.check_stop()
            try:
                page = self.portal.get_page(refresh[1])
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise UncertainExport("Export polling response missing; not resubmitted") from exc
            polls += 1

    def recover_inflight(self, jobs=None):
        if jobs is None:
            jobs = self.state.db.execute("SELECT * FROM jobs WHERE status IN ('submitting','waiting','uncertain')").fetchall()
        if not jobs:
            return
        if len(jobs) != 1:
            raise UncertainExport("More than one unresolved server export; manual inspection required")
        job = jobs[0]
        context = json.loads(job["details"] or "{}")
        if not context.get("refresh_url") or not self.portal.cookie_file.exists():
            raise UncertainExport("No saved polling context for the interrupted export; not blindly resubmitted")
        if not self.portal.accept_terms:
            raise PortalError("--accept-terms is required to resume the approved guest export")
        self.log("Unterbrochenen Serverexport wieder aufnehmen: " + job["id"])
        try:
            # Resume the server-issued polling URL in our own persisted session.
            # Do not initialize/reset the portal or replay the export POST first.
            page = self.portal.get_page(context["refresh_url"])
            self.await_export(job, page, previous_links=context.get("previous_links", []))
            test = self.state.meta("live_resume_test")
            if test and test.get("job_id") == job["id"]:
                record = self.state.db.execute("SELECT status,attempts,rows FROM jobs WHERE id=?", (job["id"],)).fetchone()
                if record["status"] not in TERMINAL or record["attempts"] != test["attempts"]:
                    raise PortalError("Live resume test did not preserve the original submission count")
                self.state.set_meta("live_resume_test", {**test, "status": "passed", "resumed_at": now(), "rows": record["rows"]})
                self.log("Live-Wiederanlauftest bestanden: vorhandener Export ohne erneute Anforderung übernommen.")
        except UncertainExport as exc:
            self.state.update(job["id"], status="uncertain", error=str(exc))
            self.state.report()
            raise
        except (PortalError, ValueError, csv.Error) as exc:
            current = self.state.db.execute("SELECT status FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
            # A finished export with saved links is not an uncertain submission.
            self.state.update(job["id"], status="needs_review" if current == "ready" else "uncertain", error=str(exc))
            self.state.report()
            raise
        except (urllib.error.URLError, OSError) as exc:
            self.state.update(job["id"], status="uncertain", error=str(exc))
            self.state.report()
            raise

    def process_job(self, job):
        try:
            if job["status"] == "ready":
                self.download_links(job, json.loads(job["links"]))
            else:
                filters = json.loads(job["filters"])
                page = self.load_filters(job["topic"], filters)
                if not self.plan(job, page):
                    self.export(job, page)
        except UncertainExport as exc:
            self.state.update(job["id"], status="uncertain", error=str(exc))
            self.state.report()
            # Never initiate a second server export while the first is uncertain.
            raise
        except (PortalError, ValueError, csv.Error) as exc:
            self.state.update(job["id"], status="needs_review", error=str(exc))
            self.active_page = None
            self.active_topic = None
            self.log("Teilmenge zur Prüfung zurückgestellt: " + str(exc))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.state.update(job["id"], status="error", error=str(exc))
            self.active_page = None
            self.active_topic = None
            self.log("Netzwerkfehler; Teilauftrag gespeichert: " + str(exc))
            self.consecutive_network_errors += 1
            if self.consecutive_network_errors >= 3:
                self.state.report()
                raise PortalError("Three consecutive network failures; stopping to avoid repeatedly loading a failing service") from exc
        self.state.report()

    def next_job(self, topic=None):
        sql = "SELECT * FROM jobs WHERE status IN ('pending','ready')"
        args = []
        if topic:
            sql += " AND topic=?"
            args.append(topic)
        sql += " ORDER BY CASE status WHEN 'ready' THEN 0 ELSE 1 END, depth DESC, rowid LIMIT 1"
        return self.state.db.execute(sql, args).fetchone()

    def samples(self, max_exports=None):
        # These are ordinary full-run partitions, not duplicate sample exports.
        for topic in TOPIC_NAMES:
            if max_exports is not None and self.completed_this_process >= max_exports:
                return
            if self.state.db.execute("SELECT 1 FROM jobs WHERE topic=? AND status IN ('complete','complete_with_warnings')", (topic,)).fetchone():
                continue
            while job := self.next_job(topic):
                self.check_stop()
                before = self.completed_this_process
                self.process_job(job)
                if self.completed_this_process > before:
                    break

    def discover_stations(self):
        if self.state.meta("stations_discovered"):
            return
        page = self.portal.enter("Messstellen")
        options = real_options(page, "gewaehltMessstelle")
        match = re.search(r"Die aktuelle Abfrage umfasst\s+([\d.]+)\s+Messstellen", page.text)
        if not match or not options or any(o.count is None for o in options):
            raise PortalError("Station catalog has no verifiable count or named entries")
        advertised = int(match[1].replace(".", ""))
        issue = (f"Portal advertises {advertised} stations, but its dropdown exposes {len(options)} distinct selectable stations"
                 if advertised != len(options) else None)
        self.state.set_meta("catalog_count_issue", issue)
        if issue:
            self.log("Stationskatalog mit Quellabweichung: " + issue + "; verfügbare Stationen werden dennoch gespeichert")
        with self.state.db:
            for o in options:
                self.state.db.execute("INSERT OR IGNORE INTO stations(id,label,expected) VALUES (?,?,?)",
                                      (identity(o.label), o.label, o.count))
        # Keep the portal target, not a reduced target chosen to claim completion.
        self.state.set_meta("station_count", advertised)
        self.state.set_meta("station_selectable_count", len(options))
        self.state.set_meta("stations_discovered", True)
        self.state.set_meta("stations_url", public_url(page.url))
        self.active_page, self.active_topic = None, None
        write_json(self.state.directory / "catalog" / "stations.json", page.summary())
        self.log(f"Stationskatalog: {len(options)} Einträge")

    def station_batch(self, limit=10):
        self.discover_stations()
        rows = self.state.db.execute("SELECT * FROM stations WHERE status='pending' ORDER BY label LIMIT ?", (limit,)).fetchall()
        if not rows:
            return
        page = self.portal.enter("Messstellen")
        for row in rows:
            self.check_stop()
            try:
                page = self.reset_if_filtered(page)
                page = self.portal.choose(page, "gewaehltMessstelle", row["label"])
                record = parse_catalog_station(page, row["label"], row["expected"])
                write_json(self.state.directory / "stations" / (row["id"] + ".json"), record)
                with self.state.db:
                    self.state.db.execute("UPDATE stations SET status='complete',details=?,error=NULL WHERE id=?",
                                          (json.dumps(record, ensure_ascii=False), row["id"]))
            except (PortalError, urllib.error.URLError, ValueError, OSError) as exc:
                with self.state.db:
                    self.state.db.execute("UPDATE stations SET status='error',error=? WHERE id=?", (str(exc), row["id"]))
                self.log("Stationsfehler: " + row["label"] + " | " + str(exc))
                page = self.portal.enter("Messstellen")
        self.state.write_catalog()
        self.active_page, self.active_topic = None, None
        self.log("Messstellen gespeichert: " + str(self.state.db.execute("SELECT count(*) FROM stations WHERE status='complete'").fetchone()[0]))
        self.state.report()

    def run(self, *, samples_only=False, max_exports=None):
        self.recover_inflight()
        self.initialize()
        self.station_batch(5)
        self.samples(max_exports)
        if samples_only:
            return
        jobs_since_catalog = 0
        while True:
            self.check_stop()
            if max_exports is not None and self.completed_this_process >= max_exports:
                self.log("Configured export batch limit reached; remaining jobs are resumable")
                break
            job = self.next_job()
            if job:
                before = self.completed_this_process
                self.process_job(job)
                jobs_since_catalog += self.completed_this_process - before
            catalog_pending = self.state.db.execute("SELECT count(*) FROM stations WHERE status='pending'").fetchone()[0]
            if catalog_pending and (jobs_since_catalog >= 5 or not job):
                self.station_batch(20)
                jobs_since_catalog = 0
            if not job and not catalog_pending:
                break
        report = self.state.report()
        self.log("Abruf vollständig." if report["all_complete"] else "Lauf beendet; offene Teilmengen stehen in fortschritt.json.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "resume", "samples", "status", "progress", "verify", "retry-errors", "stop", "refine", "defer-uncertain"], nargs="?", default="status")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--accept-terms", action="store_true", help="Explicit consent to the linked portal terms for this data retrieval")
    parser.add_argument("--target", type=int, default=8000)
    parser.add_argument("--delay", type=float, default=1.5, help="Minimum delay between portal requests, in seconds")
    parser.add_argument("--max-exports", type=int)
    parser.add_argument("--export-timeout", type=int, default=3600, help="Timeout for the initial export response and, separately, subsequent polling per process (seconds; default: 3600)")
    parser.add_argument("--job-id", help="Exact count-mismatched job to refine into verified smaller partitions")
    parser.add_argument("--evidence", type=Path, help="Saved failed-selection inspection for explicit defer-uncertain")
    parser.add_argument("--reason", help="Reason for explicitly deferring an unrecoverable export")
    parser.add_argument("--checkpoint-after-submit", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.delay < 1:
        parser.error("--delay must be at least one second")
    if args.export_timeout < 60:
        parser.error("--export-timeout must be at least 60 seconds")
    if args.command == "refine" and not args.job_id:
        parser.error("refine requires --job-id")
    if args.command == "defer-uncertain" and not (args.job_id and args.evidence and args.reason):
        parser.error("defer-uncertain requires --job-id, --evidence and --reason")
    state = State(args.output)
    if args.command in {"status", "progress"}:
        report = state.report(write=False)
        print(format_progress(report) if args.command == "progress" else json.dumps(report, ensure_ascii=False, indent=2))
        return
    if args.command == "stop":
        (state.output / "STOP").touch(exist_ok=True)
        print("Stop requested; a waiting export may require manual resolution before restart.")
        return
    with worker_lock(state.directory / "worker.lock"):
        parallel_table = state.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='parallel_claims'").fetchone()
        if parallel_table and args.command in {"run", "resume", "samples", "refine", "retry-errors", "defer-uncertain"}:
            owners = state.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='parallel_owners'").fetchone()
            if state.db.execute("SELECT 1 FROM parallel_claims LIMIT 1").fetchone() or (
                    owners and state.db.execute("SELECT 1 FROM parallel_owners LIMIT 1").fetchone()):
                raise PortalError("This directory uses parallel sessions; use parallel_download.py. Sequential retry/refine/defer need session-aware manual review")
        if args.command == "defer-uncertain":
            receipt = state.defer_uncertain(args.job_id, args.evidence, args.reason)
            state.report()
            print("Export bleibt ungeklärt und wird nicht erneut angefordert; neue Sitzung für übrige Aufträge: "
                  + receipt["next_cookie_filename"])
            return
        if args.command == "resume":
            # Only the explicit resume command clears this application's stop flag.
            (state.output / "STOP").unlink(missing_ok=True)
        if args.command == "retry-errors":
            with state.db:
                state.db.execute("UPDATE jobs SET status=CASE WHEN links IS NOT NULL THEN 'ready' ELSE 'pending' END WHERE status='error'")
                state.db.execute("UPDATE stations SET status='pending' WHERE status='error'")
            print("Network failures queued again. Review/uncertain jobs were not automatically retried.")
            return
        if args.command == "verify":
            verified = 0
            for row in state.db.execute("SELECT * FROM jobs WHERE status IN ('complete','complete_with_warnings')"):
                directory = Path(row["directory"])
                meta = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
                count = 0
                for entry in meta["files"]:
                    raw = (directory / entry["filename"]).read_bytes()
                    result = inspect_csv(raw, entry["content_type"])
                    if result["sha256"] != entry["sha256"] or result["rows"] != entry["rows"]:
                        raise PortalError("Saved export changed: " + str(directory))
                    if meta.get("result_normalization"):
                        check_csv_period(raw, result["encoding"], meta["result_normalization"])
                    count += result["rows"]
                    verified += 1
                if count != row["expected"]:
                    raise PortalError("Saved export count mismatch")
            print(f"Verified {verified} original CSV files.")
            return
        downloader = Downloader(state, accept_terms=args.accept_terms, target=args.target, delay=args.delay,
                                export_timeout=args.export_timeout)
        downloader.checkpoint_after_submit = args.checkpoint_after_submit
        def stop_signal(signum, frame):
            downloader.stopping = True
        signal.signal(signal.SIGINT, stop_signal)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, stop_signal)
        write_json(state.directory / "worker.json", {"pid": os.getpid(), "started_at": now(), "command": args.command,
                                                    "python": sys.executable, "script": str(Path(__file__).resolve())})
        reason = "Lauf regulär beendet; siehe Vollständigkeitsprüfung"
        try:
            downloader.log("Download gestartet; gespeicherte Aufträge werden fortgesetzt.")
            if args.command == "refine":
                if state.db.execute("SELECT 1 FROM jobs WHERE status IN ('submitting','waiting','uncertain')").fetchone():
                    raise PortalError("Resolve the in-flight export before changing filters")
                downloader.initialize()
                job = state.db.execute("SELECT * FROM jobs WHERE id=?", (args.job_id,)).fetchone()
                if not job:
                    raise PortalError("Unknown job ID")
                downloader.refine_review_job(job)
            else:
                downloader.run(samples_only=args.command == "samples", max_exports=args.max_exports)
        except KeyboardInterrupt:
            reason = "Unterbrochen; Fortschritt gespeichert"
            downloader.log("Unterbrochen; vorhandene Dateien bleiben erhalten.")
        except Exception as exc:
            reason = str(exc)
            downloader.log("Prozess gestoppt: " + reason)
            raise
        finally:
            write_json(state.directory / "worker.json", {"pid": os.getpid(), "stopped_at": now(),
                                                        "command": args.command, "reason": reason})
            state.report()


if __name__ == "__main__":
    main()
