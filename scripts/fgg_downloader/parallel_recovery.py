"""Opt-in deferral of unavailable saved exports; never replay an export POST.

Called only while the parallel runner owns its output lock. A portal error does
not establish that the old export was cancelled: deferred jobs remain gaps.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from pathlib import Path

from downloader import canonical_path, now
from portal import Page, PortalError, UncertainExport, clean_label, public_url

MIN_EXPORT_AGE_SECONDS = 3600
SAVED_STATES = {"submitting", "waiting", "uncertain"}


def require_recovery_state(output):
    """Inspect read-only before State() can initialize an accidentally empty DB."""
    file = output / "_state" / "jobs.sqlite"
    if not file.is_file() or file.stat().st_size == 0:
        raise PortalError("Recovery requires an existing _state/jobs.sqlite with saved jobs")
    connection = sqlite3.connect(file.as_uri() + "?mode=ro", uri=True)
    try:
        if not connection.execute("SELECT 1 FROM jobs LIMIT 1").fetchone():
            raise PortalError("Recovery requires existing saved jobs; no new download was created")
    except sqlite3.DatabaseError as exc:
        raise PortalError("Recovery state cannot be read; no new download was created") from exc
    finally:
        connection.close()


def private_json(path, value):
    """Exclusive, durable evidence file. No overwrite of an earlier receipt."""
    with path.open("x", encoding="utf-8") as stream:
        if os.name != "nt":
            os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def session_key(slot):
    return f"parallel_cookie_filename_{slot:02d}"


def session_path(state, slot):
    if not 1 <= slot <= 30:
        raise PortalError("Invalid parallel session slot")
    name = state.meta(session_key(slot)) or f"parallel_session_{slot:02d}.cookies"
    if not isinstance(name, str) or not re.fullmatch(
            rf"parallel_session_{slot:02d}(?:_[a-f0-9]{{32}})?\.cookies", name):
        raise PortalError("Invalid parallel session filename")
    path = state.directory / name
    if path.is_symlink() or canonical_path(path).parent != canonical_path(state.directory):
        raise PortalError("Unsafe parallel session path")
    return path


def backup_recovery_state(state):
    """Consistent SQLite backup (including WAL) and all owned cookie files."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = state.directory / "recovery_backups" / (stamp + "_" + uuid.uuid4().hex)
    if not canonical_path(directory).is_relative_to(canonical_path(state.directory)):
        raise PortalError("Unsafe recovery backup path")
    directory.mkdir(parents=True, mode=0o700)
    target = sqlite3.connect(directory / "jobs.sqlite")
    try:
        state.db.backup(target)
    finally:
        target.close()
    files = [directory / "jobs.sqlite"]
    for source in sorted(state.directory.glob("*.cookies")):
        if source.is_symlink() or canonical_path(source).parent != canonical_path(state.directory):
            raise PortalError("Unsafe cookie file; recovery not started")
        destination = directory / source.name
        shutil.copy2(source, destination)
        files.append(destination)
    for file in files:
        if os.name != "nt":
            file.chmod(0o600)
    private_json(directory / "manifest.json", {
        "created_at": now(), "kind": "parallel_recovery_state_backup",
        "raw_csvs_copied": False,
        "files": [{"name": file.name, "sha256": digest(file)} for file in files],
    })
    return directory


def reset_selection_details(page, job):
    """Recognize an explicitly rejected, reset selection, not any missing CSV.

    A changed count alone may be a source update. Require the known form,
    multiple validation messages and a requested filter explicitly reset to
    'keine Auswahl'/empty. Never infer a reset from absent or renamed fields.
    """
    if not 1 <= job["expected"] <= 10000:
        return None
    text = page.text
    counts = re.findall(r"Die aktuelle Abfrage umfasst\s+([\d.]+)\s+Messwerte?\b", text)
    if len(counts) != 1:
        return None
    try:
        actual_count = int(counts[0].replace(".", ""))
    except ValueError:
        return None
    if actual_count <= 10000 or not re.search(
            r"Es können maximal 10[.\s]?000 Messwerte auf einmal ausgegeben werden", text):
        return None
    prompts = [message for message in (
        "Bitte eine Messstelle oder einen Parameter auswählen!",
        "Bitte eine Messwertart auswählen!", "Bitte ein Medium auswählen!",
        "Bitte einen Messvorgang auswählen!", "Bitte ein Messjahr auswählen!",
    ) if message in text]
    if len(prompts) < 2:
        return None
    try:
        form = page.form()
        fields = page.fields(form)
    except PortalError:
        return None
    buttons = [node for node in form.find_all("input")
               if str(node.attrs.get("name", "")).endswith("_export_tabelle")]
    if len(buttons) != 1 or not any(option.label == "Standardtabelle"
                                   for option in page.selects().get("gewaehlterTabellentyp", [])):
        return None
    filters = json.loads(job["filters"])
    empty = {"", "keine auswahl"}
    lost = {key: {"expected": expected, "actual": fields[key]}
            for key, expected in filters.items()
            if key.startswith("gewaehlt") and key in fields and isinstance(expected, str)
            and clean_label(expected).casefold() not in empty
            and clean_label(fields[key]).casefold() in empty}
    if not lost:
        return None
    return {"expected_count": job["expected"], "actual_count": actual_count,
            "lost_filters": lost, "validation_messages": prompts}


def unavailable_marker(page, job=None):
    # Keep any possible result or continuing computation on the normal path.
    if page.refresh() or any("/ausgabe/" in url for _, url in page.links()):
        return None
    for marker in ("Sitzung verloren", "java.lang.NullPointerException"):
        if marker in page.text:
            return marker
    if job is not None and reset_selection_details(page, job) is not None:
        return "saved_selection_reset"
    return None


def eligible_saved_job(job):
    if (job["status"] not in SAVED_STATES or job["attempts"] < 1
            or job["links"] or job["rows"] not in (None, 0)):
        return False
    context = json.loads(job["details"] or "{}")
    if not context.get("refresh_url"):
        return False
    try:
        started = dt.datetime.fromisoformat(context["started_at"])
        return (started.tzinfo is not None and
                (dt.datetime.now(dt.timezone.utc) - started).total_seconds() >= MIN_EXPORT_AGE_SECONDS)
    except (KeyError, TypeError, ValueError):
        return False


class UnavailableSavedExport(UncertainExport):
    def __init__(self, evidence):
        self.evidence = evidence
        self.sha256 = digest(evidence)
        super().__init__("Saved export returned an unavailable page; evidence=" + evidence.name)


def inspect_saved_page(loader, job, page):
    """Called only during opted-in recovery, with a freshly received page."""
    current = loader.state.db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
    marker = unavailable_marker(page, current)
    if not marker or not eligible_saved_job(current):
        return
    cookie = loader.state.cookie_path()
    if not cookie.is_file():
        raise PortalError("Saved session file missing; cannot safely defer")
    directory = loader.job_directory(current)
    evidence = directory / ("recovery_response_" + uuid.uuid4().hex + ".json")
    private_json(evidence, {
        "captured_at": now(), "kind": "saved_parallel_export_unavailable",
        "job_id": current["id"], "slot": loader.state.slot, "marker": marker,
        "selection_reset": reset_selection_details(page, current) if marker == "saved_selection_reset" else None,
        "original_job": dict(current), "cookie_filename": cookie.name,
        "cookie_sha256": digest(cookie), "url": public_url(page.url),
        "html": page.html, "text": page.text,
        "export_cancelled": "unknown", "resubmitted": False,
    })
    raise UnavailableSavedExport(evidence)


def defer_saved_export(state, job_id, failure):
    """Commit job deferral and future session selection in one transaction.

    The claim remains for the coordinator to release. A crash after commit is
    handled by prepare_queue, which removes claims for deferred jobs as well.
    """
    job = state.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    claim = state.db.execute("SELECT slot FROM parallel_claims WHERE job_id=?", (job_id,)).fetchone()
    if not job or not claim or claim[0] != state.slot or not eligible_saved_job(job):
        raise PortalError("Saved job is not eligible for session-aware deferral")
    evidence_path = canonical_path(failure.evidence)
    if (not job["directory"] or evidence_path.parent != canonical_path(job["directory"])
            or not evidence_path.is_relative_to(state.output) or digest(evidence_path) != failure.sha256):
        raise PortalError("Recovery evidence path or checksum differs")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    page = Page(evidence["url"], evidence["html"])
    previous = evidence["original_job"]
    cookie = state.cookie_path()
    marker = unavailable_marker(page, previous)
    reset_details = reset_selection_details(page, previous) if marker == "saved_selection_reset" else None
    if (evidence.get("kind") != "saved_parallel_export_unavailable"
            or evidence.get("job_id") != job_id or evidence.get("slot") != state.slot
            or not marker or evidence.get("marker") != marker or not eligible_saved_job(previous)
            or evidence.get("selection_reset") != reset_details
            or any(previous[k] != job[k] for k in
                   ("id", "topic", "filters", "expected", "attempts", "links", "rows", "details", "directory"))
            or evidence.get("cookie_filename") != cookie.name
            or evidence.get("cookie_sha256") != digest(cookie)):
        raise PortalError("Recovery evidence does not match the current job and session")
    new_name = f"parallel_session_{state.slot:02d}_{uuid.uuid4().hex}.cookies"
    if (state.directory / new_name).exists():
        raise PortalError("Fresh parallel session filename already exists")
    receipt_path = evidence_path.parent / ("deferred_receipt_" + uuid.uuid4().hex + ".json")
    private_json(receipt_path, {
        "created_at": now(), "reason": "Explicit --defer-unavailable-exports on resume",
        "original_job": dict(job), "slot": state.slot,
        "marker": marker, "selection_reset": reset_details,
        "evidence": evidence_path.name, "evidence_sha256": failure.sha256,
        "previous_cookie_filename": cookie.name, "previous_cookie_sha256": digest(cookie),
        "next_cookie_filename": new_name,
        "export_cancelled": "unknown", "resubmitted": False,
    })
    context = json.loads(job["details"] or "{}")
    with state.db:
        state.db.execute("""UPDATE jobs SET status='deferred_uncertain',details=?,error=?,updated_at=?
                            WHERE id=?""",
                         (json.dumps({**context, "deferral_receipt": receipt_path.name}),
                          f"Saved export unavailable ({marker}); explicitly deferred, NOT downloaded or resubmitted",
                          now(), job_id))
        state.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)",
                         (session_key(state.slot), json.dumps(new_name)))
    return receipt_path
