"""Shared, secret-free persistence and configuration helpers."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
PARSER_VERSION = "fine-batch-1.2.0"
PROMPT_VERSION = "1.2.0"
SCHEMA_VERSION = "1.1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_jsonl(path: Path, rows: list[dict]) -> None:
    atomic_text(path, "".join(canonical(row) + "\n" for row in rows))


def read_jsonl(path: Path, tolerate_partial_tail: bool = False) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    result = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            if tolerate_partial_tail and i == len(lines) - 1:
                break
            raise
    return result


def load_env(path: Path, environ: dict | None = None) -> None:
    """Read literal KEY=VALUE lines; no interpolation or shell evaluation.

    Existing environment variables win. Quotes around entire values are allowed.
    Error messages deliberately contain line numbers, never values.
    """
    env = os.environ if environ is None else environ
    if not path.exists():
        return
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Ungültige .env-Zeile {number}: KEY=VALUE erwartet.")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"Ungültiger Variablenname in .env-Zeile {number}.")
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"Nicht geschlossene Anführungszeichen in .env-Zeile {number}.")
            value = value[1:-1]
        env.setdefault(key, value)


class RunLock:
    """Prevent simultaneous callers sharing the same rate ledger."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("x", encoding="utf-8") as stream:
                stream.write(canonical({"pid": os.getpid(), "started_at": utc_now()}))
        except FileExistsError:
            raise ValueError(
                f"Laufsperre vorhanden: {self.path}. Läuft noch ein Parser? "
                "Nach einem Absturz die Sperrdatei erst entfernen, wenn kein Parser mehr läuft."
            ) from None
        return self

    def __exit__(self, *_):
        self.path.unlink(missing_ok=True)
