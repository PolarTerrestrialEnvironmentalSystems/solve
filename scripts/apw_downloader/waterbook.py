"""Public browser discovery + downloads of actually published PDF links only."""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from common import Http, Snapshot, clean, digest, now, read_json, write_csv, write_json

LOG = logging.getLogger("apw")
APW_URL = "https://apw.brandenburg.de/?feature=showNodesInTree%7Cwabuwre%2Ctrue&th=wabuwre"
THEME = "Wasserrechtliche Erlaubnisse und Befugnisse"
PDF_PATH = re.compile(r"/project/cardoMap/Documents/ewabu/wasserbuchblatt_([0-9]+)\.pdf")

# These expressions inspect visible/DOM-backed result tables, never application
# state, cookies, private APIs, guessed document IDs or browser network logs.
RESULTS_JS = r"""() => {
  const records = Array.from(document.querySelectorAll('a[href*="/Documents/ewabu/"]')).map(a => {
    const table = a.closest('table');
    const fields = {};
    if (table) for (const row of table.rows) {
      if (row.cells.length === 2) fields[row.cells[0].innerText.trim()] = row.cells[1].innerText.trim();
    }
    return {url: a.href, fields};
  });
  return {text: document.body.innerText, records};
}"""


def pdf_number(url: str) -> str:
    parsed = urlparse(url)
    match = PDF_PATH.fullmatch(parsed.path)
    if parsed.scheme != "https" or parsed.netloc != "apw.brandenburg.de" or parsed.query or parsed.fragment or not match:
        raise ValueError(f"Kein öffentlicher Wasserbuchblatt-Link: {url}")
    return match.group(1)


def validate_result(payload):
    text = payload["text"]
    records = payload["records"]
    for record in records:
        number = pdf_number(record["url"])
        listed = record.get("fields", {}).get("Wasserbuchblattnummer:")
        if listed and clean(listed) != number:
            raise ValueError("Blattnummer und verlinktes Dokument widersprechen sich.")
    if "Die Anfrage ergab keine Treffer." in text and not records:
        return 0
    counts = re.findall(r"(?m)^\s*([\d.]+)\s+Objekte?\s*$", text)
    if len(counts) != 1:
        raise ValueError("Ergebnisanzahl nicht eindeutig erkennbar; keine Vollständigkeit behauptet.")
    expected = int(counts[0].replace(".", ""))
    if len(records) != expected:
        raise ValueError(f"Wasserbuch-Ergebnis unvollständig: {len(records)} verlinkte Objekte / {expected} angezeigt.")
    if re.search(r"(?i)mehr als \d|ersten \d+ (?:Treffer|Ergebnisse)|Ergebnis(?:se|menge).{0,30}(?:begrenzt|gekürzt)", text):
        raise ValueError("Die Oberfläche meldet eine begrenzte Ergebnismenge.")
    return expected


def open_research(page):
    page.goto(APW_URL, wait_until="domcontentloaded", timeout=60_000)
    theme_button = page.get_by_role("button", name=THEME, exact=True)
    if not theme_button.is_visible():
        tree_label = page.get_by_text("Elektronisches Wasserbuch", exact=True).last
        if not tree_label.is_visible():
            page.get_by_role("button", name="Themen", exact=True).click()
        page.get_by_text("Elektronisches Wasserbuch", exact=True).last.click()
    theme_button.click()
    page.get_by_role("link", name="Expertenrecherche im Thema", exact=True).click()
    page.get_by_role("combobox").first.click()
    page.get_by_role("option", name="Wasserbehörde", exact=True).click()


def public_authorities(page):
    open_research(page)
    page.locator('a[data-qtip="Datenvorschau"]').click()
    page.get_by_text(re.compile(r"\d+ Werte gefunden")).wait_for(timeout=30_000)
    result = page.evaluate(r"""() => ({
      text: document.body.innerText,
      names: Array.from(document.querySelectorAll('tr')).filter(r => r.cells.length === 1 && r.getBoundingClientRect().width > 0).map(r => r.innerText.trim())
    })""")
    count = re.search(r"([\d.]+) Werte gefunden", result["text"])
    names = result["names"]
    if not count or int(count[1].replace(".", "")) != len(names) or len(names) != len(set(names)) or not all(names):
        raise RuntimeError("Behördenliste nicht vollständig lesbar. Gezielt --authority verwenden.")
    return names


def discover(snapshot: Snapshot, authorities, headed=False, delay=1.5):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Für die öffentliche Suche: pip install -r requirements.txt und python -m playwright install chromium. Alternativ --index / --pdf-url verwenden.") from exc
    state = snapshot.state["waterbook"]
    scope = {"kind": "authorities", "requested": sorted(authorities)} if authorities else {"kind": "public_authority_list"}
    old = state.get("discovery_scope")
    if old is not None and old != scope:
        raise ValueError("Anderer Suchumfang im selben Snapshot. Bitte neuen --snapshot verwenden.")
    state["discovery_scope"] = scope
    queries = state.setdefault("queries", {})
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=not headed)
        try:
            context = browser.new_context(locale="de-DE", viewport={"width": 1440, "height": 1100})
            page = context.new_page()
            page.set_default_timeout(30_000)
            if not authorities:
                if "authorities" not in state:
                    state["authorities"] = public_authorities(page)
                    snapshot.save()
                authorities = state["authorities"]
            for authority in authorities:
                if queries.get(authority, {}).get("complete"):
                    snapshot.cached(queries[authority]["artifact"])
                    continue
                time.sleep(delay)
                open_research(page)
                page.get_by_role("group", name="Verknüpfen mit UND", exact=True).get_by_role("textbox").fill(authority)
                page.get_by_text("Recherche ausführen", exact=True).click()
                page.wait_for_function(r"""() => {
                  const t = document.body.innerText;
                  return t.includes('Die Anfrage ergab keine Treffer.') ||
                    (document.querySelector('a[href*="/Documents/ewabu/"]') && !t.includes('Lade Ergebnisse')) ||
                    t.includes('Unbekannter Fehler');
                }""", timeout=90_000)
                payload = page.evaluate(RESULTS_JS)
                # Save the evidence even when completeness checks fail.
                data = json.dumps(payload, ensure_ascii=False).encode()
                key = "waterbook:query:" + digest(authority.encode()) + ":" + digest(data)
                filename = digest(authority.encode())[:12] + "_" + digest(data)[:20] + ".json"
                snapshot.record(key, snapshot.root / "raw/waterbook/search" / filename, data, APW_URL, authority=authority)
                queries[authority] = {"artifact": key, "complete": False}
                snapshot.save()
                count = validate_result(payload)
                queries[authority].update(complete=True, objects=count)
                snapshot.save()
                LOG.info("Wasserbuch: %s — %s öffentliche Objekte", authority, count)
        finally:
            browser.close()
    records = []
    for query in queries.values():
        data, _ = snapshot.cached(query["artifact"])
        records.extend(json.loads(data)["records"])
    index = {"schema_version": 1, "source_url": APW_URL, "retrieved_at": now(),
             "scope": scope, "complete_for_scope": all(q["complete"] for q in queries.values()), "records": records}
    write_json(snapshot.root / "waterbook_index.json", index)
    return index


def load_index(path: Path):
    index = read_json(path)
    if index.get("schema_version") != 1 or not isinstance(index.get("records"), list):
        raise ValueError("Index benötigt schema_version=1 und records=[{url, fields}].")
    for record in index["records"]:
        pdf_number(record["url"])
    return index


def download(http: Http, snapshot: Snapshot, index, max_documents=None):
    from waterbook_pdf import parse_pdf
    unique = {}
    for record in index["records"]:
        number = pdf_number(record["url"])
        if number not in unique:
            unique[number] = {"url": record["url"], "summaries": []}
        unique[number]["summaries"].append(record.get("fields", {}))
    state = snapshot.state["waterbook"]
    canonical_records = sorted(index["records"], key=lambda r: json.dumps(r, sort_keys=True, ensure_ascii=False))
    index_fingerprint = digest(json.dumps(canonical_records, sort_keys=True, ensure_ascii=False).encode())
    if state.get("index_fingerprint") not in (None, index_fingerprint):
        raise ValueError("Index verändert; bitte neuen Snapshot für einen neuen Quellenstand verwenden.")
    if "index_scope" in state and state["index_scope"] != index.get("scope"):
        raise ValueError("Index-Suchumfang verändert; bitte einen neuen Snapshot verwenden.")
    state.update(index_fingerprint=index_fingerprint, index_scope=index.get("scope"),
                 index_complete_for_scope=bool(index.get("complete_for_scope", False)), expected_documents=len(unique))
    documents = state.setdefault("documents", {})
    state["download_complete"] = False
    state["parse_complete"] = False
    snapshot.save()
    chosen = sorted(unique)[:max_documents] if max_documents is not None else sorted(unique)
    for i, number in enumerate(chosen, 1):
        record = unique[number]
        key = "waterbook:pdf:" + number
        def validate_pdf(data):
            if not data.lstrip().startswith(b"%PDF-"):
                raise ValueError(f"{number}: Antwort ist kein PDF.")
        data, meta = http.cached(snapshot, key, f"raw/waterbook/pdf/{number}.pdf", record["url"], validate_pdf)
        # Reparse cached files so parser improvements do not require redownloads.
        try:
            parsed = parse_pdf(snapshot.root / meta["path"], number)
        except Exception as exc:
            documents[number] = {"artifact": key, "parsed_path": None,
                                 "parse_status": "error", "warnings": [str(exc)]}
            state["downloaded_documents"] = len(documents)
            state["parsed_documents"] = sum(d["parse_status"] != "error" for d in documents.values())
            snapshot.save()
            LOG.error("PDF %s gespeichert, aber nicht auswertbar: %s", number, exc)
            continue
        parsed["source"] = {**meta, "snapshot": snapshot.state["snapshot"], "public_summaries": record["summaries"]}
        write_json(snapshot.root / f"parsed/waterbook/{number}.json", parsed)
        documents[number] = {"artifact": key, "parsed_path": f"parsed/waterbook/{number}.json",
                             "parse_status": parsed["parse_status"], "warnings": parsed["warnings"]}
        state["downloaded_documents"] = len(documents)
        state["parsed_documents"] = sum(d["parse_status"] != "error" for d in documents.values())
        snapshot.save()
        LOG.info("Wasserbuch PDF %s / %s: %s (%s)", i, len(unique), number, parsed["parse_status"])
    state["downloaded_documents"] = len(documents)
    state["download_complete"] = len(documents) == len(unique)
    state["parsed_documents"] = sum(d["parse_status"] != "error" for d in documents.values())
    state["parse_complete"] = state["download_complete"] and state["parsed_documents"] == len(unique)
    snapshot.save()
    export(snapshot)
    if any(d["parse_status"] == "error" for d in documents.values()):
        raise RuntimeError("Mindestens ein PDF wurde gespeichert, konnte aber nicht ausgewertet werden. Siehe review.csv und manifest.json.")


def export(snapshot):
    collections = {k: [] for k in ("entries", "cases", "uses", "sites", "limits", "review")}
    for number, document in snapshot.state["waterbook"].get("documents", {}).items():
        if not document["parsed_path"]:
            _, meta = snapshot.cached(document["artifact"])
            for warning in document["warnings"]:
                collections["review"].append({"waterbook_number": number, "warning": warning,
                    "snapshot": snapshot.state["snapshot"], "source_url": meta["source_url"],
                    "source_sha256": meta["sha256"], "retrieved_at": meta["retrieved_at"], "parse_status": "error"})
            continue
        parsed = read_json(snapshot.root / document["parsed_path"])
        source = parsed["source"]
        shared = {"snapshot": source["snapshot"], "source_url": source["source_url"],
                  "source_sha256": source["sha256"], "retrieved_at": source["retrieved_at"], "parse_status": parsed["parse_status"]}
        collections["entries"].append({**parsed["entry"], **shared})
        for kind in ("cases", "uses", "sites", "limits"):
            collections[kind].extend({**row, **shared} for row in parsed[kind])
        for warning in parsed["warnings"]:
            collections["review"].append({"waterbook_number": number, "warning": warning, **shared})
    from waterbook_pdf import FIELDS
    provenance = ["snapshot", "source_url", "source_sha256", "retrieved_at", "parse_status"]
    for kind, rows in collections.items():
        write_csv(snapshot.root / f"tables/waterbook/{kind}.csv", rows, FIELDS[kind] + provenance)
    write_json(snapshot.root / "tables/waterbook/coverage.json", {
        key: snapshot.state["waterbook"].get(key) for key in
        ("index_scope", "index_complete_for_scope", "expected_documents", "downloaded_documents", "download_complete", "parsed_documents", "parse_complete")
    })
