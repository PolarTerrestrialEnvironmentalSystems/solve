"""Small, shared building blocks. No database connections or credentials."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

LOG = logging.getLogger("apw")
PROVIDER = "Landesamt für Umwelt Brandenburg"
LICENSE = "https://www.govdata.de/dl-de/by-2-0"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def atomic_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def write_json(path: Path, value):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    """UTF-8 BOM + semicolon. Protect text cells against spreadsheet formulas."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, delimiter=";", extrasaction="raise")
    writer.writeheader()
    for row in rows:
        normalized = {}
        for key in fields:
            value = row.get(key)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            normalized[key] = value
        writer.writerow(normalized)
    atomic_bytes(path, buffer.getvalue().encode("utf-8-sig"))


class OutputLock:
    """OS lock: automatically released on a crash; no PID/stale-lock guessing."""
    def __init__(self, root: Path):
        self.path = root / ".download.lock"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        self.stream.seek(0, 2)
        if not self.stream.tell():
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise RuntimeError("Dieser Ausgabeordner wird bereits von einem Downloader benutzt.") from exc
        return self

    def __exit__(self, *args):
        self.stream.close()


class Snapshot:
    def __init__(self, output: Path, name: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name):
            raise ValueError("Snapshotname: nur Buchstaben, Zahlen, _ und - (max. 80).")
        self.root = output.resolve() / name
        self.path = self.root / "manifest.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.state = read_json(self.path) if self.path.exists() else {
            "schema_version": 1, "snapshot": name, "created_at": now(),
            "provider": PROVIDER, "license_url": LICENSE, "artifacts": {},
            "wrrl": {}, "waterbook": {},
        }
        if self.state.get("schema_version") != 1:
            raise ValueError("Unbekannte Manifestversion; bitte einen neuen Snapshot verwenden.")

    def save(self):
        self.state["updated_at"] = now()
        write_json(self.path, self.state)

    def cached(self, key: str):
        meta = self.state["artifacts"].get(key)
        if not meta:
            return None
        path = (self.root / meta["path"]).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Ungültiger Pfad im Manifest.")
        if not path.is_file() or digest(path.read_bytes()) != meta["sha256"]:
            raise RuntimeError(f"Cache fehlt/ist verändert: {path}. Nicht stillschweigend überschrieben.")
        return path.read_bytes(), meta

    def record(self, key: str, path: Path, data: bytes, url: str, **extra):
        atomic_bytes(path, data)
        meta = {"path": str(path.relative_to(self.root)).replace("\\", "/"), "source_url": url,
                "retrieved_at": now(), "sha256": digest(data), "bytes": len(data), **extra}
        self.state["artifacts"][key] = meta
        self.save()
        return data, meta


def retry_delay(header: str | None, attempt: int) -> float:
    minimum = min(60, 2 ** attempt)
    if not header:
        return minimum
    try:
        return max(minimum, float(header))
    except ValueError:
        try:
            return max(minimum, (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError):
            return minimum


class PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def validate_url(url: str):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"apw.brandenburg.de", "maps.brandenburg.de"}:
        raise ValueError("Nur die öffentlichen HTTPS-Adressen von APW/maps.brandenburg.de sind erlaubt.")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Keine Zugangsdaten oder fremden Ports in Download-URLs erlaubt.")


class Http:
    def __init__(self, delay=1.5, timeout=60, retries=4):
        self.delay, self.timeout, self.retries = delay, timeout, retries
        self.last = 0.0
        self.opener = urllib.request.build_opener(PublicRedirects())

    def get(self, url: str, max_bytes=100_000_000) -> bytes:
        validate_url(url)
        for attempt in range(self.retries + 1):
            time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            request = urllib.request.Request(url, headers={"User-Agent": "SOLVE-APW-Downloader/1.0 (public research data; sequential)"})
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    data = response.read(max_bytes + 1)
                    if len(data) > max_bytes:
                        raise ValueError("Antwort überschreitet das Größenlimit. Kleinere Seiten verwenden.")
                    return data
            except urllib.error.HTTPError as exc:
                if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == self.retries:
                    details = exc.read(1500).decode("utf-8", errors="replace")
                    raise RuntimeError(f"HTTP {exc.code}: {url}\n{details}") from exc
                wait = retry_delay(exc.headers.get("Retry-After"), attempt + 1)
                if wait > 300:
                    raise RuntimeError(f"Server fordert {wait:.0f}s Pause. Später denselben Befehl erneut starten.") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt == self.retries:
                    raise RuntimeError(f"Abruf fehlgeschlagen: {url}: {exc}") from exc
                wait = retry_delay(None, attempt + 1)
            LOG.warning("Abruf wird nach %.0fs wiederholt (%s/%s).", wait, attempt + 1, self.retries)
            time.sleep(wait)

    def cached(self, snapshot: Snapshot, key: str, relative: str, url: str, validator=None):
        cached = snapshot.cached(key)
        if cached:
            return cached
        data = self.get(url)
        if validator:
            validator(data)
        return snapshot.record(key, snapshot.root / relative, data, url)
