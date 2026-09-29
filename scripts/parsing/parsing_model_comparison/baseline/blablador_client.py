"""Blablador REST client. Every wire attempt is logged; no implicit retries."""
from __future__ import annotations

import csv
import io
import json
import math
import os
import random
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import wraps
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from common import atomic_text, canonical, read_jsonl, write_json


_LOG_LOCK = threading.RLock()


def synchronized(lock):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with lock:
                return function(*args, **kwargs)
        return wrapped
    return decorate


@dataclass
class Settings:
    api_key: str = field(default="", repr=False)
    base_url: str = "https://api.blablador.fz-juelich.de/v1/"
    model: str = "alias-large"
    rpm: int = 5
    timeout: float = 180
    max_retries: int = 3
    max_output_tokens: int = 8000
    context_tokens: int = 65536
    temperature: float = 0
    json_mode: str = "auto"
    thinking_mode: str = "auto"

    @classmethod
    def from_env(cls) -> "Settings":
        def get(name, default, cast=str):
            try:
                return cast(os.environ.get("BLABLADOR_" + name, default))
            except (ValueError, TypeError):
                raise ValueError(f"Ungültiger Wert für BLABLADOR_{name}.") from None
        return cls(api_key=get("API_KEY", ""), base_url=get("BASE_URL", cls.base_url),
                   model=get("MODEL", cls.model), rpm=get("MAX_REQUESTS_PER_MINUTE", 5, int),
                   timeout=get("TIMEOUT_SECONDS", 180, float), max_retries=get("MAX_RETRIES", 3, int),
                   max_output_tokens=get("MAX_OUTPUT_TOKENS", 8000, int),
                   context_tokens=get("CONTEXT_TOKENS", 65536, int),
                   temperature=get("TEMPERATURE", 0, float), json_mode=get("JSON_MODE", "auto"),
                   thinking_mode=get("THINKING_MODE", "auto"))

    def validate(self, require_key: bool = True) -> None:
        url = urlsplit(self.base_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("BLABLADOR_BASE_URL muss eine HTTPS-URL ohne Zugangsdaten oder Query sein.")
        if self.rpm < 1 or self.timeout <= 0 or self.max_retries < 0 or self.max_retries > 10:
            raise ValueError("RPM/Timeout müssen positiv sein; MAX_RETRIES muss zwischen 0 und 10 liegen.")
        if not math.isfinite(self.timeout) or not math.isfinite(self.temperature):
            raise ValueError("Timeout und Temperatur müssen endliche Zahlen sein.")
        if self.max_output_tokens < 256 or self.context_tokens <= self.max_output_tokens:
            raise ValueError("Kontextbudget muss größer als das Antwortbudget (mindestens 256 Tokens) sein.")
        if self.json_mode not in {"auto", "json_schema", "json_object", "none"}:
            raise ValueError("JSON_MODE: auto, json_schema, json_object oder none erwartet.")
        if self.thinking_mode not in {"auto", "on", "off"}:
            raise ValueError("THINKING_MODE: auto, on oder off erwartet.")
        if not self.model.strip():
            raise ValueError("BLABLADOR_MODEL ist leer.")
        if require_key and (not self.api_key or "HIER_" in self.api_key or "DEINEN_" in self.api_key):
            raise ValueError("API-Key fehlt. In parsing_tests/.env BLABLADOR_API_KEY eintragen.")

    def public(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "api_key"}


class Clock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0, seconds))


@synchronized(_LOG_LOCK)
def append_log(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Drop only an incomplete last write left by process termination.
    if path.exists():
        with path.open("r+b") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            if size:
                stream.seek(size - 1)
                if stream.read(1) != b"\n":
                    stream.seek(0)
                    content = stream.read()
                    stream.truncate(content.rfind(b"\n") + 1)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(canonical(row) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


class RateLimiter:
    def __init__(self, rpm: int, ledger: Path, clock: Clock):
        self.rpm, self.ledger, self.clock = rpm, ledger, clock
        self.lock = threading.Lock()
        self.cooldown_until = 0.0
        self.sent = deque()
        for row in read_jsonl(ledger, tolerate_partial_tail=True):
            age = (clock.now() - datetime.fromisoformat(row["started_at_utc"])).total_seconds()
            if age < 60:
                self.sent.append(clock.monotonic() - max(0, age))
        self.sent = deque(sorted(self.sent))

    def defer(self, seconds: float) -> None:
        with self.lock:
            self.cooldown_until = max(self.cooldown_until, self.clock.monotonic() + seconds)

    def acquire(self) -> int:
        while True:
            with self.lock:
                now = self.clock.monotonic()
                while self.sent and now - self.sent[0] >= 60:
                    self.sent.popleft()
                delay = max(0.0, self.cooldown_until - now)
                if self.sent:
                    delay = max(delay, 60 / self.rpm - (now - self.sent[-1]))
                if len(self.sent) >= self.rpm:
                    delay = max(delay, 60 - (now - self.sent[0]) + .001)
                if delay <= 0:
                    self.sent.append(self.clock.monotonic())
                    append_log(self.ledger, {"started_at_utc": self.clock.now().isoformat()})
                    return len(self.sent)
            # Other workers can extend a server cooldown while this one waits.
            self.clock.sleep(delay)


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_http(method: str, url: str, headers: dict, body: dict | None, timeout: float) -> Response:
    request = Request(url, data=canonical(body).encode("utf-8") if body is not None else None,
                      headers=headers, method=method)
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as reply:
            return Response(reply.status, dict(reply.headers.items()), reply.read())
    except HTTPError as exc:
        return Response(exc.code, dict(exc.headers.items()), exc.read())


class APIError(RuntimeError):
    def __init__(self, status: int | None, message: str, unsupported_format: bool = False,
                 context_exceeded: bool = False):
        super().__init__(message)
        self.status, self.unsupported_format = status, unsupported_format
        self.context_exceeded = context_exceeded


def retry_delay(headers: dict, body: dict, now: datetime, attempt: int, status: int | None) -> float:
    retry = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    values = []
    if retry is not None:
        try:
            values.append(float(retry))
        except (ValueError, TypeError):
            try:
                date = parsedate_to_datetime(str(retry))
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                values.append((date - now).total_seconds())
            except (ValueError, TypeError):
                pass
    for obj in (body, body.get("error", {})):
        if isinstance(obj, dict):
            for key in ("retry_after", "retry_after_seconds", "remaining_time"):
                value = obj.get(key)
                if isinstance(value, (int, float)) and math.isfinite(value):
                    values.append(float(value))
    base = (60 if status == 429 else 2) * 2 ** (attempt - 1)
    return max([0, base] + values) + random.uniform(0, .25)


@synchronized(_LOG_LOCK)
def minute_report(log_path: Path, output: Path, now: datetime) -> dict:
    rows = read_jsonl(log_path, tolerate_partial_tail=True)
    starts = {r["request_id"]: r for r in rows if r["event"] == "request_started"}
    ends = {r["request_id"]: r for r in rows if r["event"] == "request_finished"}
    session_ends = {r["session_id"]: datetime.fromisoformat(r["at"]) for r in rows if r["event"] == "session_finished"}
    minutes = set()
    for row in rows:
        if row["event"] != "session_started":
            continue
        start = datetime.fromisoformat(row["at"]).replace(second=0, microsecond=0)
        # An interrupted historical session ends at its last recorded activity, not days later.
        activity = [datetime.fromisoformat(r.get("at", r.get("started_at_utc", row["at"])))
                    for r in rows if r.get("session_id") == row["session_id"]]
        end = session_ends.get(row["session_id"], max(activity)).replace(second=0, microsecond=0)
        while start <= end:
            minutes.add(start.isoformat())
            start += timedelta(minutes=1)
    groups = {("total", "", "", "", "")}
    buckets = {}
    for request_id, row in starts.items():
        minute = datetime.fromisoformat(row["started_at_utc"]).replace(second=0, microsecond=0).isoformat()
        minutes.add(minute)
        group = ("detail", row.get("run_id", ""), row.get("ontology", ""), row.get("model", ""), row["endpoint"])
        groups.add(group)
        for key in [(minute, ("total", "", "", "", "")), (minute, group)]:
            items = buckets.setdefault(key, [])
            items.append((row, ends.get(request_id)))
    stream = io.StringIO(newline="")
    names = ["minute_utc", "scope", "run_id", "ontology", "model", "endpoint", "requests", "successful",
             "failed", "unfinished", "retries", "http_429", "max_requests_last_60s", "prompt_tokens", "completion_tokens"]
    writer = csv.DictWriter(stream, fieldnames=names)
    writer.writeheader()
    for minute in sorted(minutes):
        for group in sorted(groups):
            items = buckets.get((minute, group), [])
            success = sum(e is not None and e.get("error_type") is None and 200 <= (e.get("http_status") or 0) < 300 for _, e in items)
            failed = sum(e is not None for _, e in items) - success
            def tokens(name):
                available = [e.get("usage", {}).get(name) for _, e in items if e]
                available = [n for n in available if isinstance(n, (int, float))]
                return sum(available) if available else ""
            writer.writerow(dict(zip(names, [minute, *group, len(items), success, failed,
                              len(items)-success-failed, sum(s["attempt"] > 1 for s, _ in items),
                              sum(e is not None and e.get("http_status") == 429 for _, e in items),
                              max([s["requests_last_60s"] for s, _ in items], default=0),
                              tokens("prompt_tokens"), tokens("completion_tokens")])))
    atomic_text(output, stream.getvalue())
    return {"requests": len(starts), "unfinished": len(set(starts)-set(ends)),
            "max_requests_last_60s": max((r["requests_last_60s"] for r in starts.values()), default=0)}


class BlabladorClient:
    def __init__(self, settings: Settings, logs: Path, ledger: Path, run_id: str,
                 transport=send_http, clock: Clock | None = None):
        settings.validate()
        self.settings, self.transport, self.clock = settings, transport, clock or Clock()
        self.state_lock = threading.RLock()
        self.admission_lock = threading.Lock()
        self.cancelled = threading.Event()
        self.local = threading.local()
        self.log_path, self.minute_path = logs / "api_requests.jsonl", logs / "api_requests_per_minute.csv"
        self.limiter = RateLimiter(settings.rpm, ledger, self.clock)
        self.run_id, self.session_id = run_id, uuid.uuid4().hex
        self._format_mode = "json_schema" if settings.json_mode == "auto" else settings.json_mode
        self.observed_model = None
        self.request_model = settings.model
        self.expected_model_root = None
        self.model_roots = {}
        self.accepted_model_labels = set()
        self._log({"event": "session_started", "at": self.clock.now().isoformat()})

    @property
    def format_mode(self):
        return getattr(self.local, "response_format", self._format_mode)

    @format_mode.setter
    def format_mode(self, value):
        self._format_mode = value

    def cancel(self):
        self.cancelled.set()

    def _admit(self, body, endpoint, attempt, context):
        # Serialize admission only; HTTP response waits run concurrently.
        with self.admission_lock:
            if self.cancelled.is_set():
                raise RuntimeError("Weitere API-Aufrufe nach Abbruch gestoppt; Checkpoints bleiben erhalten.")
            rolling = self.limiter.acquire()
            if self.cancelled.is_set():
                raise RuntimeError("Weitere API-Aufrufe nach Abbruch gestoppt; Checkpoints bleiben erhalten.")
            request_id, started = uuid.uuid4().hex, self.clock.monotonic()
            self._log({"event": "request_started", "request_id": request_id,
                       "started_at_utc": self.clock.now().isoformat(), "model": self.settings.model,
                       "requested_model": (body or {}).get("model"),
                       "endpoint": endpoint, "attempt": attempt, "requests_last_60s": rolling, **context})
            return request_id, started

    def _log(self, value: dict):
        append_log(self.log_path, {"session_id": self.session_id, "run_id": self.run_id, **value})

    def close(self):
        self._log({"event": "session_finished", "at": self.clock.now().isoformat()})
        minute_report(self.log_path, self.minute_path, self.clock.now())

    def request(self, method: str, endpoint: str, body: dict | None = None, **context) -> dict:
        for attempt in range(1, self.settings.max_retries + 2):
            request_id, started = self._admit(body, endpoint, attempt, context)
            status, headers, data, error = None, {}, {}, None
            format_unsupported = context_exceeded = False
            provider_error = None
            try:
                response = self.transport(method, self.settings.base_url.rstrip("/") + "/" + endpoint.lstrip("/"),
                                          {"Authorization": "Bearer " + self.settings.api_key,
                                           "Content-Type": "application/json"}, body, self.settings.timeout)
                status, headers = response.status, response.headers
                try:
                    data = json.loads(response.body)
                    if not isinstance(data, dict):
                        raise ValueError("object expected")
                except (ValueError, UnicodeDecodeError):
                    data, error = {}, "invalid_json_response"
                if status >= 300:
                    error = "http_error"
                    detail = canonical(data).lower()
                    format_unsupported = status in {400, 422} and any(t in detail for t in ["response_format", "json_schema", "json_object"]) and any(t in detail for t in ["support", "invalid", "unknown", "not allowed", "not permitted"])
                    context_exceeded = status in {400, 413, 422} and any(t in detail for t in ["context_length_exceeded", "maximum context length", "max_model_len", "too many tokens", "context window"])
                    # Keep a bounded diagnostic, never headers or whole server bodies.
                    problem = data.get("error", {})
                    message = problem.get("message", problem.get("code")) if isinstance(problem, dict) else problem
                    if isinstance(message, str):
                        provider_error = message.replace(self.settings.api_key, "[REDACTED]")[:500]
            except (URLError, TimeoutError, OSError) as exc:
                error = type(exc).__name__
            retryable = status in {408, 429, 500, 502, 503, 504} or status is None
            will_retry = retryable and attempt <= self.settings.max_retries
            delay = retry_delay(headers, data, self.clock.now(), attempt, status) if will_retry else 0
            if status == 429:
                self.limiter.defer(delay or retry_delay(headers, data, self.clock.now(), attempt, status))
            if status in {401, 403}:
                self.cancel()
            self._log({"event": "request_finished", "request_id": request_id, "at": self.clock.now().isoformat(),
                       "duration_ms": round((self.clock.monotonic()-started)*1000), "http_status": status,
                       "error_type": error, "retry_wait_s": delay,
                       "provider_error": provider_error,
                       "response_model": data.get("model") if isinstance(data.get("model"), str) else None,
                       "usage": data.get("usage") if isinstance(data.get("usage"), dict) else {}})
            minute_report(self.log_path, self.minute_path, self.clock.now())
            if not error:
                return data
            if will_retry:
                print(f"API {status or error}: Wiederholung nach {delay:.1f} s.", flush=True)
                self.clock.sleep(delay)
                continue
            # Do not expose server bodies, tokens, headers or URLs in exceptions.
            raise APIError(status, f"Blablador-Anfrage fehlgeschlagen ({status or error}); Details im Anfragelog.", format_unsupported, context_exceeded)
        raise AssertionError("unreachable")

    def models(self, purpose: str = "model_list") -> dict:
        return self.request("GET", "models", purpose=purpose, ontology="setup", chunk_id="")

    def _index_models(self, snapshot: dict) -> list[dict]:
        models = snapshot.get("data")
        if not isinstance(models, list):
            raise ValueError("Die Modellabfrage lieferte keine data-Liste.")
        labels = {}
        entries = [m for m in models if isinstance(m, dict) and isinstance(m.get("id"), str)]
        for entry in entries:
            root = entry.get("root") or entry["id"]
            if not isinstance(root, str):
                continue
            for name in (entry["id"], root):
                labels.setdefault(name, set()).add(root)
        # Never guess an equivalence from a name substring or model family.
        self.model_roots = {name: next(iter(roots)) for name, roots in labels.items() if len(roots) == 1}
        return entries

    def configure_models(self, snapshot: dict) -> None:
        entries = self._index_models(snapshot)
        root = self.model_roots.get(self.settings.model)
        if root is None:
            raise ValueError("Konfiguriertes Modell im Katalog nicht eindeutig zuordenbar.")
        self.expected_model_root = root
        # Use a catalogued concrete endpoint with the same root instead of a moving alias.
        concrete = [m["id"] for m in entries if m.get("root") == root and not m["id"].startswith("alias-")]
        if self.settings.model.startswith("alias-") and concrete:
            self.request_model = sorted(concrete)[0]
        self._log({"event": "model_binding", "at": self.clock.now().isoformat(),
                   "configured_model": self.settings.model, "requested_model": self.request_model,
                   "expected_model_root": root})

    def _verify_response_model(self, response: dict) -> None:
        with self.state_lock:
            self._verify_response_model_locked(response)

    def _verify_response_model_locked(self, response: dict) -> None:
        label = response.get("model")
        if not isinstance(label, str) or not label:
            # Some OpenAI-compatible servers omit this optional diagnostic.
            self._log({"event": "model_label_missing", "at": self.clock.now().isoformat(),
                       "requested_model": self.request_model})
            return
        previous = self.observed_model
        root = self.model_roots.get(label)
        refresh_error = None
        if self.expected_model_root is not None and root is None:
            try:
                snapshot = self.models(purpose="model_verification")
                write_json(self.log_path.parent / "model_catalogs" / (uuid.uuid4().hex + ".json"), snapshot)
                self._index_models(snapshot)
                root = self.model_roots.get(label)
            except (APIError, ValueError) as exc:
                refresh_error = type(exc).__name__
        matched = root == self.expected_model_root if self.expected_model_root is not None else previous in {None, label}
        if not matched:
            self.cancel()
            artifact = self.log_path.parent / "model_mismatches" / (uuid.uuid4().hex + ".json")
            write_json(artifact, response)
            self._log({"event": "model_verification_failed", "at": self.clock.now().isoformat(),
                       "previous_label": previous, "response_label": label,
                       "expected_model_root": self.expected_model_root, "response_model_root": root,
                       "catalog_refresh_error": refresh_error, "response_file": str(artifact)})
            reason = "andere Modellbasis" if root is not None else "nicht eindeutig im Modellkatalog zuordenbar"
            raise RuntimeError(
                f"Modellprüfung: Antwortkennung {label!r} ({reason}). "
                f"Erwartete Modellbasis: {self.expected_model_root or previous!r}. "
                f"Antwort und Diagnose gespeichert: {artifact}."
            )
        self.accepted_model_labels.add(label)
        if previous != label:
            self._log({"event": "model_label_verified", "at": self.clock.now().isoformat(),
                       "previous_label": previous, "response_label": label,
                       "model_root": root, "verification": "catalog_root" if root else "single_label"})
        self.observed_model = label

    def complete(self, messages: list[dict], schema: dict, **context) -> dict:
        while True:
            with self.state_lock:
                format_mode = self._format_mode
            body = {"model": self.request_model, "messages": messages, "stream": False,
                    "temperature": self.settings.temperature, "max_tokens": self.settings.max_output_tokens}
            if self.settings.thinking_mode != "auto":
                body["chat_template_kwargs"] = {"enable_thinking": self.settings.thinking_mode == "on"}
            if format_mode == "json_schema":
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": "extraction", "strict": False, "schema": schema}}
            elif format_mode == "json_object":
                body["response_format"] = {"type": "json_object"}
            try:
                response = self.request("POST", "chat/completions", body, response_format=format_mode,
                                        thinking_mode=self.settings.thinking_mode, **context)
                self._verify_response_model(response)
                self.local.response_format = format_mode
                return response
            except APIError as exc:
                if self.settings.json_mode == "auto" and exc.unsupported_format and format_mode != "none":
                    with self.state_lock:
                        if self._format_mode == format_mode:
                            self._format_mode = "json_object" if format_mode == "json_schema" else "none"
                    print(f"JSON-Ausgabeformat angepasst: {self._format_mode}; lokale Validierung bleibt aktiv.", flush=True)
                    continue
                raise
