"""Public Elbe portal HTTP client. Only Python's standard library is required.

This client owns its guest session; it never reads a browser profile or cookies.
Form actions and download links are taken from the received public HTML.
"""
from __future__ import annotations

import csv
import hashlib
import http.cookiejar
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

ORIGIN = "https://www.elbe-datenportal.de"
BASE = ORIGIN + "/FisFggElbe/"
START = BASE + "content/start/ZurStartseite.action"
TERMS = BASE + "content/statisch/nutzungsbedingungen.jsp"
COUNT_SUFFIX = re.compile(r"\s*---\s*\(([\d.]+)\s+[^)]*\)\s*$")
VOID = set("area base br col embed hr img input link meta param source track wbr".split())


class PortalError(RuntimeError):
    pass


class UncertainExport(PortalError):
    """The export may still be running: do not automatically submit another."""


def clean_label(value: str) -> str:
    return COUNT_SUFFIX.sub("", value).strip()


def option_count(value: str) -> int | None:
    found = COUNT_SUFFIX.search(value)
    return int(found[1].replace(".", "")) if found else None


def safe_url(url: str, base: str = BASE) -> str:
    result = urllib.parse.urljoin(base, url)
    parsed = urllib.parse.urlsplit(result)
    if parsed.scheme != "https" or parsed.netloc != "www.elbe-datenportal.de":
        raise PortalError("Refusing a request outside the HTTPS portal: " + result)
    return result


def public_url(url: str) -> str:
    return re.sub(r";jsessionid=[A-Za-z0-9]+", "", url)


@dataclass
class Node:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list = field(default_factory=list)

    def find_all(self, tag: str | None = None):
        for child in self.children:
            if isinstance(child, Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.find_all(tag)

    def text(self) -> str:
        if self.tag in {"script", "style"}:
            return ""
        return " ".join(" ".join(c.text() if isinstance(c, Node) else c
                                 for c in self.children).split())


class TreeParser(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        # HTML option end tags may be omitted.
        if tag == "option" and self.stack[-1].tag == "option":
            self.stack.pop()
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


@dataclass(frozen=True)
class Option:
    label: str
    value: str
    count: int | None
    selected: bool = False


@dataclass
class Page:
    url: str
    html: str

    def __post_init__(self):
        self.root = TreeParser(self.html).root

    @property
    def text(self):
        return self.root.text()

    def form(self, name: str | None = None) -> Node:
        forms = list(self.root.find_all("form"))
        if name:
            forms = [f for f in forms if f.attrs.get("name") == name or f.attrs.get("id") == name]
        if len(forms) != 1:
            raise PortalError(f"Expected one form ({name}); found {len(forms)} at {public_url(self.url)}")
        return forms[0]

    def selects(self) -> dict[str, list[Option]]:
        result = {}
        for select in self.root.find_all("select"):
            name = select.attrs.get("name")
            if not name or "disabled" in select.attrs:
                continue
            result[name] = [Option(clean_label(o.text()), str(o.attrs.get("value", o.text())),
                                   option_count(o.text()), "selected" in o.attrs)
                            for o in select.find_all("option") if "disabled" not in o.attrs]
        return result

    def fields(self, form: Node | None = None) -> dict[str, str]:
        result = {}
        form = form or self.form()
        for element in form.find_all():
            name = element.attrs.get("name")
            if not name or "disabled" in element.attrs:
                continue
            if element.tag == "input":
                kind = str(element.attrs.get("type", "text")).lower()
                if kind in {"submit", "image", "button", "reset", "file"}:
                    continue
                if kind in {"checkbox", "radio"} and "checked" not in element.attrs:
                    continue
                result[name] = str(element.attrs.get("value", ""))
            elif element.tag == "select":
                options = list(element.find_all("option"))
                selected = next((o for o in options if "selected" in o.attrs), options[0] if options else None)
                if selected:
                    result[name] = str(selected.attrs.get("value", selected.text()))
            elif element.tag == "textarea":
                result[name] = element.text()
        return result

    def links(self):
        return [(a.text(), safe_url(a.attrs["href"], self.url))
                for a in self.root.find_all("a")
                if a.attrs.get("href") and not str(a.attrs["href"]).startswith(("#", "javascript:", "mailto:", "http://"))
                and urllib.parse.urlsplit(urllib.parse.urljoin(self.url, a.attrs["href"])).netloc == "www.elbe-datenportal.de"]

    def refresh(self):
        for meta in self.root.find_all("meta"):
            if str(meta.attrs.get("http-equiv", "")).lower() == "refresh":
                match = re.fullmatch(r"\s*([\d.]+)\s*;\s*url\s*=\s*['\"]?(.+?)['\"]?\s*",
                                     str(meta.attrs.get("content", "")), re.I)
                if match:
                    return float(match[1]), safe_url(match[2], self.url)
        return None

    def summary(self):
        return {"url": public_url(self.url), "text": self.text,
                "forms": [{"attributes": f.attrs, "inputs": [n.attrs for n in f.find_all("input")]} for f in self.root.find_all("form")],
                "selects": {k: [vars(o) for o in opts] for k, opts in self.selects().items()},
                "links": self.links(),
                "onchange": [n.attrs for n in self.root.find_all() if n.attrs.get("onchange")],
                "onclick": [n.attrs for n in self.root.find_all() if n.attrs.get("onclick")],
                "refresh": self.refresh()}


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    request_gate = None

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl, req.full_url)
        if self.request_gate:
            self.request_gate()
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Portal:
    def __init__(self, *, accept_terms=False, delay=1.5, timeout=120, cookie_file=None,
                 export_request_timeout=3600, request_gate=None, error_hook=None):
        self.accept_terms = accept_terms
        self.delay = delay
        self.timeout = timeout
        self.export_request_timeout = export_request_timeout
        self.request_gate = request_gate
        self.error_hook = error_hook
        self.last_request = 0.0
        # This is this downloader's own public guest session, not browser storage.
        self.cookie_file = Path(cookie_file) if cookie_file else None
        self.cookie_jar = http.cookiejar.LWPCookieJar()
        if self.cookie_file and self.cookie_file.exists():
            self.cookie_jar.load(str(self.cookie_file), ignore_discard=True, ignore_expires=False)
        redirect = SameOriginRedirect()
        if request_gate:
            redirect.request_gate = lambda: request_gate(self.delay)
        self.opener = urllib.request.build_opener(
            redirect, urllib.request.HTTPCookieProcessor(self.cookie_jar))
        mode = "bounded parallel CSV export" if request_gate else "sequential CSV export"
        self.opener.addheaders = [("User-Agent", f"FGG-Local-Research-Downloader/1.1 ({mode})"),
                                  ("Accept-Language", "de,en;q=0.5")]
        self.page = None

    def request(self, url, fields=None, *, export=False):
        url = safe_url(url)
        data = urllib.parse.urlencode(fields).encode("utf-8") if fields is not None else None
        for attempt in range(3):
            time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
            if self.request_gate:
                self.request_gate(self.delay)
            self.last_request = time.monotonic()
            try:
                req = urllib.request.Request(url, data=data)
                if data is not None:
                    req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
                if self.page:
                    req.add_header("Referer", public_url(self.page.url))
                request_timeout = self.export_request_timeout if export else self.timeout
                with self.opener.open(req, timeout=request_timeout) as response:
                    if self.cookie_file:
                        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
                        temporary = self.cookie_file.with_suffix(".tmp")
                        self.cookie_jar.save(str(temporary), ignore_discard=True, ignore_expires=False)
                        if os.name != "nt":
                            temporary.chmod(0o600)
                        temporary.replace(self.cookie_file)
                    payload = response.read()
                    return response.url, response.headers, payload
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if self.error_hook:
                    self.error_hook(exc)
                if export:
                    raise UncertainExport("Export response missing; submission may still be running") from exc
                if isinstance(exc, urllib.error.HTTPError) and exc.code not in {408, 429, 500, 502, 503, 504}:
                    raise
                if attempt == 2:
                    raise
                retry = 3 * 2 ** attempt
                if isinstance(exc, urllib.error.HTTPError):
                    try:
                        retry = max(retry, float(exc.headers.get("Retry-After", "0")))
                    except ValueError:
                        pass
                # Respect the full Retry-After, in small interruptible intervals.
                remaining = retry
                while remaining > 0:
                    step = min(remaining, 30)
                    time.sleep(step)
                    remaining -= step
        raise AssertionError("unreachable")

    def get_page(self, url, fields=None, *, export=False):
        url, headers, payload = self.request(url, fields, export=export)
        encoding = headers.get_content_charset() or "utf-8"
        html = payload.decode(encoding, errors="strict")
        self.page = Page(url, html)
        return self.page

    def initialize(self):
        page = self.get_page(START)
        if "Sitzung verloren" in page.text:
            links = [(t, u) for t, u in page.links() if t == "zur Startseite"]
            if len(links) != 1:
                raise PortalError("Cannot locate the guest-session entry")
            page = self.get_page(links[0][1])
        page.form("Start")
        return page

    def enter(self, text="Datenabruf"):
        if not self.accept_terms:
            raise PortalError("Explicit --accept-terms consent is required before entering the portal")
        page = self.initialize()
        candidates = [n for n in page.root.find_all("a") if n.text() == text and n.attrs.get("onclick")]
        if len(candidates) != 1:
            raise PortalError("Cannot locate start action: " + text)
        match = re.search(r"SubmitOhneButton\(['\"]([^'\"]+)", candidates[0].attrs["onclick"])
        if not match:
            raise PortalError("Unknown start action")
        fields = page.fields(page.form("Start"))
        if "__checkbox_nutzungsbedingungenAkzeptiert" not in fields:
            raise PortalError("Terms checkbox missing; refusing unverified entry")
        fields["nutzungsbedingungenAkzeptiert"] = "true"
        result = self.get_page(safe_url(match[1], page.url), fields)
        if any(n.attrs.get("name") == "nutzungsbedingungenAkzeptiert" for n in result.root.find_all("input")):
            raise PortalError("Portal did not accept the terms submission")
        return result

    def submit(self, page: Page, updates=None, *, action=None, export=False):
        form = page.form()
        fields = page.fields(form)
        fields.update(updates or {})
        url = safe_url(action or form.attrs["action"], page.url)
        result = self.get_page(url, fields, export=export)
        if "Sitzung verloren" in result.text:
            raise PortalError("Guest session lost")
        return result

    def choose(self, page: Page, name: str, label: str):
        options = [o for o in page.selects().get(name, []) if o.label == label]
        if len(options) != 1:
            # Selected dimensions often become hidden inputs, not dropdowns.
            current = page.fields().get(name)
            if current is not None and clean_label(current) == label:
                return page
            raise PortalError(f"Selection no longer unambiguous: {name}={label!r}")
        select = next(n for n in page.root.find_all("select") if n.attrs.get("name") == name)
        onchange = str(select.attrs.get("onchange", ""))
        action_match = re.search(r"SubmitOhneButton\(['\"]([^'\"]+)", onchange)
        action = action_match[1] if action_match else None
        result = self.submit(page, {name: options[0].value}, action=action)
        # Check that the server actually applied the selected semantic value.
        current = result.fields().get(name)
        if current is None or clean_label(current) != label:
            raise PortalError(f"Server did not retain {name}={label!r}; actual={current!r}")
        return result


def parse_portal_rows(text: str):
    """Read original portal fields, retaining literal apostrophes in PCB names.

    The live Biota export contains 'PCB-153 (2,2',4,4',5,5'-...)' without
    escaping its interior quotes. A quote closes a quoted field only directly
    before a delimiter, newline or EOF. Any deviation is reported, never saved
    over the original CSV. Ambiguous field widths are rejected by inspect_csv.
    """
    text = io.StringIO(text, newline=None).read()
    try:
        return list(csv.reader(io.StringIO(text), delimiter=";", quotechar="'", strict=True)), []
    except csv.Error:
        pass
    rows, row, value = [], [], []
    quoted = False
    at_start = True
    interior_quotes = 0
    index = 0
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if quoted:
            if char == "'" and following == "'":
                value.append("'")
                index += 2
                continue
            if char == "'" and following in {"", ";", "\n"}:
                quoted = False
            else:
                if char == "'":
                    interior_quotes += 1
                value.append(char)
        elif at_start and char == "'":
            quoted = True
            at_start = False
        elif char in {";", "\n"}:
            row.append("".join(value))
            value, at_start = [], True
            if char == "\n":
                rows.append(row)
                row = []
        else:
            value.append(char)
            at_start = False
        index += 1
    if quoted:
        raise PortalError("Unterminated quoted CSV field; raw file retained")
    if value or row:
        rows.append(row + ["".join(value)])
    if not interior_quotes:
        raise PortalError("Unrecognized invalid CSV quoting; raw file retained")
    return rows, ["literal_apostrophes_inside_quoted_fields; portal dialect parsed without changing raw bytes"]


def inspect_csv(payload: bytes, content_type: str = "") -> dict:
    """Read-only validation: original CSV bytes and measurement text stay intact."""
    if payload.lstrip().lower().startswith((b"<!doctype html", b"<html")) or "text/html" in content_type.lower():
        raise PortalError("The download is an HTML page, not a CSV file")
    charset = re.search(r"charset\s*=\s*([^;\s]+)", content_type, re.I)
    encodings = ["utf-8-sig"] if payload.startswith(b"\xef\xbb\xbf") else []
    if charset:
        encodings.append(charset[1].strip('"'))
    encodings += ["utf-8", "iso-8859-1"]
    for encoding in dict.fromkeys(encodings):
        try:
            text = payload.decode(encoding, errors="strict")
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        raise PortalError("Cannot decode CSV")
    parsed, warnings = parse_portal_rows(text)
    parsed = [row for row in parsed if any(cell.strip() for cell in row)]
    if len(parsed) < 1 or len(parsed[0]) < 2:
        raise PortalError("No recognizable semicolon-separated CSV header")
    header, rows = parsed[0], parsed[1:]
    widths = {}
    for row in rows:
        widths[str(len(row))] = widths.get(str(len(row)), 0) + 1
    allowed_widths = {len(header)}
    if header[-1].strip().lower() == "zusätzliche informationen":
        allowed_widths.add(len(header) - 1)
    if any(len(row) not in allowed_widths for row in rows):
        raise PortalError(f"Unexpected CSV row widths {widths} for {len(header)} headers; no silent repair")
    if any(len(row) != len(header) for row in rows):
        warnings.append("row_width_differs_from_header; raw bytes unchanged; no automatic column padding")
    return {"encoding": encoding, "header": header, "rows": len(rows), "row_widths": widths,
            "warnings": warnings, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
