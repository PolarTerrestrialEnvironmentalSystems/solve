"""Prepare and resume source-only Elbe campaigns from reviewed seed manifests.

No search, download or model request is performed by catalog/prepare/status.
Prepare requires a NEW absolute workspace; resume always uses run on that same
workspace, without preparing again. Budgets apply per run and persistently
stored pending work can continue on the next run.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import copy
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import uuid
from urllib.parse import parse_qsl, urlsplit

import run_web_enrichment as core

PROJECT = Path(__file__).resolve().parent.parent
INPUT = PROJECT / "inputs" / "elbe_20261110"
DEFAULT_REGISTRY = INPUT / "registry" / "waterbodies.json"
DEFAULT_CONFIG = PROJECT / "inputs" / "sites_blablador_33_serper_v1_6_2.json"
STATES = {"BB": "Brandenburg", "BE": "Berlin", "MV": "Mecklenburg-Vorpommern",
          "SN": "Sachsen", "ST": "Sachsen-Anhalt", "TH": "Thüringen"}
SEED_FIELDS = ("title", "provider", "identity_scope", "identity_method", "validation_basis",
               "topic_evidence", "checked_at", "http_status", "content_type", "sha256", "hash",
               "sample_sha256", "hash_scope", "acceptance_reason", "locator", "report_type",
               "catalogue_verified_waterbody_id", "catalogue_page", "page")
RIVER_TOPICS = {
    "Entstehung und Morphometrie": ["Flussmorphologie", "Gewässerstruktur", "Querprofil"],
    "Wasserhaushalt": ["Abfluss", "Pegel", "Niedrigwasser"],
    "Biologische Entwicklung": ["Makrozoobenthos", "Fischdurchgängigkeit"],
    "Wasserbauliche Eingriffe": ["Querbauwerk", "Uferverbau", "Flussbegradigung"],
    "Sanierung und Restaurierung": ["Renaturierung", "Auenanbindung"],
    "Maßnahmen im Einzugsgebiet": ["Gewässerrandstreifen", "Hochwasserrückhalt"],
    "Messreihen und Monitoring": ["Abflussmessreihe", "Gewässergütemessstelle"],
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    # Versioned registry/manifest checksums must agree on Windows and Linux.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    temporary.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    os.replace(temporary, path)


def load_registry(path):
    data = read_json(path)
    rows = data["waterbodies"] if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("Registry muss eine Liste von Wasserkörpern enthalten")
    seen = set()
    for row in rows:
        identifier = row.get("waterbody_id", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", identifier) or identifier in seen:
            raise ValueError(f"Ungültige oder doppelte Registry-ID: {identifier}")
        if row.get("river_basin_code") != "DE5000" or row.get("country_code") != "DE":
            raise ValueError(f"Registry außerhalb des deutschen Elbegebiets: {identifier}")
        if row.get("waterbody_type") not in {"lake", "river"}:
            raise ValueError(f"Unbekannter Gewässertyp: {identifier}")
        if not set(row.get("spatial_states_target", [])) & set(STATES):
            raise ValueError(f"Keine räumliche Zuordnung zu den sechs Ziel-Ländern: {identifier}")
        seen.add(identifier)
    return rows


def select_rows(rows, kind="all", states=None):
    selected_states = set(states or STATES)
    return [row for row in rows
            if (kind == "all" or row["waterbody_type"] == {"lakes": "lake", "rivers": "river"}[kind])
            and selected_states.intersection(row["spatial_states_target"])]


def name_aliases(row):
    """Search variants only; the official name/ID and reach context stay intact."""
    name = " ".join(row["name_original"].split())
    variants = [name]
    umlaut = name.upper().replace("AE", "Ä").replace("OE", "Ö").replace("UE", "Ü")
    plain = name.upper().replace("AE", "A").replace("OE", "O").replace("UE", "U")
    variants.extend([umlaut, plain])
    if row["waterbody_type"] == "river":
        # ELBE1 / ELBE 1 -> ELBE is an alias, never a replacement identifier.
        for value in list(variants):
            without_number = re.sub(r"(?<=[A-Za-zÄÖÜäöüß])\s*\d+[A-Za-z]?\s*$", "", value).strip()
            if len(without_number) >= 3:
                variants.append(without_number)
    return [value for value in dict.fromkeys(variants) if value != name]


def public_seed_url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        if {key.casefold() for key, _ in parse_qsl(parsed.query)} & {
                "api_key", "apikey", "access_token", "authorization", "password", "token"}:
            return None
        if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
            return None
        try:
            if not ipaddress.ip_address(parsed.hostname).is_global:
                return None
        except ValueError:
            pass
        return core.canonical(value)
    except ValueError:
        return None


def seed_records(value):
    if isinstance(value, list):
        for item in value:
            yield from seed_records(item)
    elif isinstance(value, dict):
        if isinstance(value.get("waterbody_id"), str) and isinstance(value.get("accepted"), list):
            yield value
        elif isinstance(value.get("waterbodies"), list):
            yield from seed_records(value["waterbodies"])


def sanitized_review_metadata(seed):
    """Retain review outcome/provenance, never model reasoning or input excerpts."""
    raw = seed.get("llm_content_review", seed.get("review_metadata"))
    if not isinstance(raw, dict):
        return None
    result = {}
    for key, pattern in (("status", r"[a-z_]{1,80}"),
                         ("policy", r"[A-Za-z0-9_.:-]{1,120}"),
                         ("checked_at", r"[0-9TZ:.+\-]{10,40}"),
                         ("input_sha256", r"[0-9a-fA-F]{64}")):
        value = raw.get(key)
        if isinstance(value, str) and re.fullmatch(pattern, value):
            result[key] = value
    if not result.get("status"):
        return None
    completed = result["status"] == "completed"
    result["llm_review_completed"] = completed
    if completed:
        decision = raw.get("decision") if isinstance(raw.get("decision"), dict) else raw
        allowed = {"action": {"accept", "reject", "uncertain"},
                   "identity": {"match", "mismatch", "uncertain"},
                   "category": {"authority", "research", "monitoring", "management", "original_reporting",
                                "tourism", "unrelated", "unknown"}}
        for key, choices in allowed.items():
            if isinstance(decision.get(key), str) and decision[key] in choices:
                result[key] = decision[key]
        model = re.fullmatch(r"llm-content-veto-\d+:(alias-[A-Za-z0-9_.-]+)", result.get("policy", ""))
        if model:
            result["model_alias"] = model.group(1)
    return result


def sanitized_seed(seed):
    """Avoid copying full snippets, HTML, prompts, arbitrary evidence or local paths."""
    url = public_seed_url(seed.get("final_url") or seed.get("url")) if isinstance(seed, dict) else None
    if not url:
        return None
    result = {"url": url, "final_url": url}
    review = sanitized_review_metadata(seed)
    if review:
        result["review_metadata"] = review
    for key in ("source_url", "catalogue_url"):
        public_url = public_seed_url(seed.get(key))
        if public_url:
            result[key] = public_url
    for key in ("catalogue_pages", "context_pages"):
        pages = seed.get(key)
        if isinstance(pages, list):
            result[key] = sorted({page for page in pages
                if isinstance(page, int) and not isinstance(page, bool) and 1 <= page <= 100000})[:20]
    for key in SEED_FIELDS:
        value = seed.get(key)
        if isinstance(value, (str, int, float, bool)):
            # A short topical label is metadata; full passages remain outside Git.
            if key == "topic_evidence" and not isinstance(value, bool):
                if not isinstance(value, str) or not re.fullmatch(r"[A-Za-zÄÖÜäöüß0-9 /(),&+_-]{1,80}", value):
                    continue
            if isinstance(value, str):
                if re.search(r"(?:[A-Za-z]:[\\/]|/home/|/Users/)", value):
                    continue
                value = value[:500 if key == "title" else 240]
            result[key] = value
    http = seed.get("http") or {}
    for key in ("http_status", "content_type", "sha256"):
        if key not in result and isinstance(http.get(key), (str, int)):
            result[key] = http[key]
    # Some clients use numeric status; collector status='fetched' is NOT HTTP 200.
    if "http_status" not in result and isinstance(http.get("status"), int):
        result["http_status"] = http["status"]
    if "checked_at" not in result:
        checked = http.get("checked_at") or seed.get("verified_at")
        if isinstance(checked, str):
            result["checked_at"] = checked[:40]
    if isinstance(http.get("sample_sha256"), str) and re.fullmatch(r"[0-9a-fA-F]{64}", http["sample_sha256"]):
        result["sample_sha256"] = http["sample_sha256"].lower()
        result["hash_scope"] = "http_response_sample"
    if isinstance(seed.get("reason"), str):
        # A bounded reviewer classification, never the original evidence field.
        reason = seed["reason"].replace("\n", " ")[:240]
        if not re.search(r"(?:[A-Za-z]:[\\/]|/home/|/Users/)", reason):
            result["acceptance_reason"] = reason
    if "content_type" not in result and isinstance(seed.get("mime"), str):
        result["content_type"] = seed["mime"][:120]
    result.setdefault("title", urlsplit(url).hostname)
    return result


def load_seeds(paths):
    by_body = defaultdict(dict)
    issues, manifests, file_seen = [], [], set()
    for path in paths:
        path = Path(path)
        files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
        for file in files:
            if file.resolve() in file_seen:
                continue
            file_seen.add(file.resolve())
            try:
                records = list(seed_records(read_json(file)))
            except (OSError, ValueError) as exc:
                issues.append({"file": file.name, "reason": type(exc).__name__})
                continue
            if not records:
                continue
            manifests.append({"file": file.name, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
            for record in records:
                for raw in record["accepted"]:
                    seed = sanitized_seed(raw)
                    if seed is None:
                        issues.append({"file": file.name, "waterbody_id": record["waterbody_id"],
                                       "reason": "invalid_or_nonpublic_accepted_url"})
                        continue
                    by_body[record["waterbody_id"]].setdefault(seed["url"], seed)
    return {key: list(value.values()) for key, value in by_body.items()}, manifests, issues


def coverage_counts(records):
    return {"waterbodies": len(records), "accepted_links": sum(row["accepted_count"] for row in records),
            "with_accepted_sources": sum(row["accepted_count"] > 0 for row in records),
            "ready_at_default_minimum": sum(row["status"] == "ready" for row in records),
            "shortfall_with_some_sources": sum(row["status"] == "shortfall" for row in records),
            "missing_all_sources": sum(row["status"] == "missing" for row in records)}


def merge_seeds(args):
    """Publish a reusable, sanitized input snapshot; no network or crawling."""
    rows = load_registry(args.registry)
    seeds, manifests, issues = load_seeds(args.seeds)
    known_ids = {row["waterbody_id"] for row in rows}
    records = []
    for row in rows:
        accepted = seeds.get(row["waterbody_id"], [])[:args.maximum_seeds]
        minimum = args.minimum_lake_seeds if row["waterbody_type"] == "lake" else args.minimum_river_seeds
        count = len(accepted)
        records.append({"waterbody_id": row["waterbody_id"], "name": row["name_original"],
            "waterbody_type": row["waterbody_type"], "reporting_state": row["reporting_state"],
            "spatial_states_target": row["spatial_states_target"], "accepted_count": count,
            "minimum_required": minimum, "shortfall": max(0, minimum - count),
            "status": "ready" if count >= minimum else "shortfall" if count else "missing",
            "accepted": accepted})
    coverage = {"schema_version": 1, "created_at": now(), "totals": coverage_counts(records),
        "minimum_lake_seeds": args.minimum_lake_seeds, "minimum_river_seeds": args.minimum_river_seeds,
        "maximum_seeds": args.maximum_seeds,
        "by_type": {kind: coverage_counts([row for row in records if row["waterbody_type"] == kind]) for kind in ("lake", "river")},
        "by_spatial_state_nonadditive": {state: coverage_counts([row for row in records if state in row["spatial_states_target"]]) for state in STATES},
        "by_reporting_state_additive": {state: coverage_counts([row for row in records if row["reporting_state"] == state])
                                        for state in sorted({row["reporting_state"] for row in records})},
        "unknown_input_waterbody_ids": sorted(set(seeds) - known_ids), "input_manifest_issues": issues,
        "note": "Coverage snapshot, not a live claim. Spatial-state counts are not additive; whole-lake/subbasin identities remain separate.",
        "waterbodies": [{key: value for key, value in row.items() if key != "accepted"} for row in records]}
    output = {"schema_version": 1, "created_at": coverage["created_at"],
        "selection_scope": "Accepted start-source metadata only. Ordinary crawler preflight and relevance review still apply.",
        "registry_sha256": hashlib.sha256(Path(args.registry).read_bytes()).hexdigest(),
        "input_manifests": manifests, "coverage": {key: value for key, value in coverage.items() if key not in {"waterbodies", "input_manifest_issues"}},
        "waterbodies": records}
    write_json(args.output, output)
    coverage_path = args.coverage or args.output.with_name("seed_coverage.json")
    write_json(coverage_path, coverage)
    render_seed_coverage(records, coverage, args.output.with_name("SEED_COVERAGE.md"), args.output.name)
    render_lake_startlinks(records, args.output.parent / "startlinks")
    print(json.dumps(coverage["totals"], ensure_ascii=False))
    print(f"Seeds: {args.output}\nCoverage: {coverage_path}")
    return 0


def render_seed_coverage(records, coverage, path, manifest_name):
    unique_urls = {seed["url"] for row in records for seed in row["accepted"]}
    totals = coverage["totals"]
    lines = ["# Abdeckung der vorbereiteten Startquellen", "",
        f"Snapshot: {coverage['created_at']}", "",
        f"**{totals['waterbodies']} amtliche Wasserkörper**, {totals['accepted_links']} Gewässer-URL-Zuordnungen, "
        f"{len(unique_urls)} verschiedene finale URLs. Ein Bericht kann mehreren Gewässern zugeordnet sein.", "",
        "| Typ | Im Register | Mit Quellen | Mindestzahl erreicht | Mit Quellen unter Mindestzahl | Ohne Quelle |",
        "|---|---:|---:|---:|---:|---:|"]
    for kind, label in (("lake", "Seen-Wasserkörper"), ("river", "Flussabschnitte")):
        item = coverage["by_type"][kind]
        lines.append(f"| {label} | {item['waterbodies']} | {item['with_accepted_sources']} | "
            f"{item['ready_at_default_minimum']} | {item['shortfall_with_some_sources']} | {item['missing_all_sources']} |")
    lines += ["", f"Mindestzahlen: {coverage['minimum_lake_seeds']} URLs pro See, {coverage['minimum_river_seeds']} pro Flussabschnitt; "
        f"höchstens {coverage['maximum_seeds']} URLs pro Profil im gelieferten Manifest.", "",
        "Die Zahl beschreibt Einstiegsmöglichkeiten, keine Zahl unabhängiger Studien oder bereits extrahierter Ereignisse. "
        "Mehrere Seiten desselben PDFs zählen als eine URL. HTTP-Zugriff, fachlicher Kontext und Zuordnung wurden vorgeprüft; "
        "die normale Blablador-Vorprüfung und Inhaltsbewertung im Crawler bleiben erforderlich.", "",
        "Die begrenzte Recherche ist kein Vollständigkeitsnachweis des Internets. Weniger als zehn angenommene URLs bedeutet nicht, "
        "dass keine weiteren Quellen existieren. Unklare, blockierte oder touristische Treffer werden nicht zur Erfüllung der Mindestzahl gezählt.", "",
        f"Das wiederverwendbare [Quellenmanifest]({manifest_name}) enthält die URLs und Provenienz. "
        "[Maschinenlesbare Abdeckung](seed_coverage.json) · [Vollständige Landeslisten](catalog/README.md)", "",
        "## Nach Bundesland", "", "Grenzgewässer erscheinen mehrfach; diese Landeszahlen sind **nicht additiv**.", "",
        "| Land | Seen-WK | Davon Mindestzahl erreicht | Fluss-WK | Davon mit Quelle |",
        "|---|---:|---:|---:|---:|"]
    for state, label in STATES.items():
        local = [row for row in records if state in row["spatial_states_target"]]
        lakes = [row for row in local if row["waterbody_type"] == "lake"]
        rivers = [row for row in local if row["waterbody_type"] == "river"]
        lines.append(f"| {label} | {len(lakes)} | {sum(row['status'] == 'ready' for row in lakes)} | "
                     f"{len(rivers)} | {sum(row['accepted_count'] > 0 for row in rivers)} |")
    lines += ["", "## Seen einzeln", "",
        "Der Standardlauf nimmt Seen erst ab der Mindestzahl auf. `prepare --allow-shortfall` "
        "nimmt ausdrücklich auch Seen unterhalb der Mindestzahl auf, sofern mindestens eine Quelle vorliegt. "
        "Ohne Quellen startet kein Profil.", "",
        "| Amtlicher Name | Kennung | Räumliche Länder | URLs | Fehlend bis Mindestzahl |",
        "|---|---|---|---:|---:|"]
    for row in sorted((row for row in records if row["waterbody_type"] == "lake"), key=lambda row: row["name"]):
        lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in
            (row["name"], row["waterbody_id"], ", ".join(row["spatial_states_target"]), row["accepted_count"], row["shortfall"])) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def render_lake_startlinks(records, directory):
    """Human-readable source links, alongside the machine-readable manifest."""
    directory.mkdir(parents=True, exist_ok=True)
    index = ["# Startquellen für die Seen", "",
        "Geprüfte Einstiegsmöglichkeiten, keine abschließende fachliche Freigabe. "
        "Grenzgewässer erscheinen in mehreren Landeslisten; ihre amtliche ID bleibt dieselbe.", "",
        "[Abdeckung und Defizite](../SEED_COVERAGE.md)", ""]
    for state, label in STATES.items():
        lakes = sorted((row for row in records if row["waterbody_type"] == "lake" and
                        state in row["spatial_states_target"]), key=lambda row: (row["name"], row["waterbody_id"]))
        index.append(f"- [{label}: {len(lakes)} Seen-Wasserkörper]({state}.md)")
        lines = [f"# {label}: Startquellen für Seen", "",
                 "Jede URL zählt einmal pro Gewässer. Fehlende Quellenplätze werden nicht mit touristischen Treffern gefüllt.", ""]
        for row in lakes:
            lines += ["## " + row["name"].replace("\n", " "), "",
                f"Kennung: `{row['waterbody_id']}` · {row['accepted_count']} Quellen · "
                f"{row['shortfall']} fehlen bis zur Mindestzahl.", ""]
            for seed in row["accepted"]:
                title = " ".join((seed.get("title") or urlsplit(seed["url"]).hostname).split())
                title = title.replace("[", "\\[").replace("]", "\\]")
                lines.append(f"- [{title}](<{seed['url']}>)")
            if not row["accepted"]:
                lines.append("Noch keine angenommene Startquelle.")
            lines.append("")
        (directory / (state + ".md")).write_text("\n".join(lines), encoding="utf-8", newline="\n")
    (directory / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8", newline="\n")


def assess_seed_coverage(rows, seeds, minimum_lakes=10, minimum_rivers=1, allow_shortfall=False, maximum=20):
    eligible, decisions = [], []
    for row in rows:
        accepted = seeds.get(row["waterbody_id"], [])[:maximum]
        required = minimum_lakes if row["waterbody_type"] == "lake" else minimum_rivers
        enough = len(accepted) >= required
        included = enough or bool(accepted) and allow_shortfall
        status = "ready" if enough else "included_shortfall" if included else "excluded_shortfall" if accepted else "excluded_unseeded"
        decisions.append({"waterbody_id": row["waterbody_id"], "name": row["name_original"],
                          "kind": row["waterbody_type"], "accepted_count": len(accepted),
                          "required_count": required, "shortfall": max(0, required - len(accepted)),
                          "status": status})
        if included:
            eligible.append((row, accepted))
    return eligible, decisions


def make_waterbody(row, accepted):
    states = [STATES[key] for key in row["spatial_states_target"]]
    river = row["waterbody_type"] == "river"
    context = (f"Amtlicher WRRL-Wasserkörper {row['waterbody_id']}; Originalname: {row['name_original']}. "
        f"Flussgebiet DE5000/Elbe; Berichtsstand {row['reporting_year']}. Räumlicher Bezug: {', '.join(states)}. "
        f"Meldendes Land: {row['reporting_state']}; dies ersetzt keine räumliche Zuordnung. "
        "Transliterationsvarianten sind Suchhilfen, keine zusätzlichen Identitäten. ")
    if river:
        context += ("Dieser Datensatz bezeichnet einen abgegrenzten Flussabschnitt, nicht den ganzen Fluss. "
                    "Ein Alias ohne Abschnittsnummer dient nur zum Auffinden von Dokumenten. "
                    "Quellenbezug zum Abschnitt anhand Kennung, Orts-/Mündungsangaben oder Geografie prüfen; "
                    "benachbarte Abschnitte nicht gleichsetzen. ")
    if row.get("low_overlap_states_review"):
        context += "Kleine Grenzüberschneidungen in der generalisierten Geometrie sind zur Prüfung markiert. "
    return {
        "id": row["waterbody_id"], "name": row["name_original"], "query_name": row["name_original"],
        "waterbody_kind": "river_segment" if river else "lake", "aliases": name_aliases(row),
        "identifiers": [row["waterbody_id"], row["waterbody_id"].split("_", 1)[-1]],
        "region": ", ".join(states), "geography_terms": states, "keywords": states +
            (["Fließgewässer", "Flussabschnitt", "Abfluss", "Gewässerstruktur"] if river else
             ["See", "Gewässergüte", "Phosphor", "Morphometrie"]),
        "exclude_keywords": [], "seed_urls": [seed["url"] for seed in accepted],
        "seed_titles": {seed["url"]: seed.get("title", "Geprüfte Startquelle") for seed in accepted},
        "seed_evidence": {seed["url"]: {key: value for key, value in seed.items() if key not in {"url", "final_url", "title"}}
                          for seed in accepted},
        "preferred_domains": list(dict.fromkeys(urlsplit(seed["url"]).hostname for seed in accepted)),
        "identity_requires_confirmation": False, "context": context,
        "input_provenance": "EEA WRRL2022 DE5000, räumlich gefiltert mit BKG VG250; geprüfte Seed-Manifeste",
        "research_questions": ["Historische Entwicklung und Nutzungen, besonders vor 2000",
                               "Messreihen, Gewässergüte, Phosphor und Sedimente",
                               "Maßnahmen, Renaturierung, morphologische Veränderungen und ihre Wirkung"],
        "english_terms": ["river restoration", "discharge", "sediment"] if river else ["limnology", "sediment", "lake restoration"],
        "spatial_states": row["spatial_states"], "reporting_state": row["reporting_state"],
        "boundary_review_states": row.get("low_overlap_states_review", []),
    }


def scale_budgets(config, count):
    config["limits"].update(max_tasks_per_run=500 * count, max_fetches_per_run=100 * count,
        max_searches_per_run=40 * count, max_seconds_per_run=7 * 24 * 3600,
        max_urls_per_host_per_run=max(300, 100 * count))
    config["preflight"]["max_calls_per_run"] = 500 * count
    config["review"]["max_calls_per_run"] = 100 * count


def campaign_config(eligible, base_path):
    config = copy.deepcopy(read_json(base_path))
    config.pop("credentials_env_file", None)
    config["waterbodies"] = [make_waterbody(row, accepted) for row, accepted in eligible]
    if len(config.get("topics", [])) != 19:
        raise ValueError("Basiskonfiguration muss die 19 Recherchethemen enthalten")
    if any(row["waterbody_type"] == "river" for row, _ in eligible):
        for topic in config["topics"]:
            topic["keywords"] = list(dict.fromkeys(topic.get("keywords", []) + RIVER_TOPICS.get(topic["name"], [])))
    config["scientific_sources"] = []
    config.setdefault("scientific_discovery", {}).update(enabled=False, datacite_enabled=False)
    config.setdefault("retrieval", {}).update(enabled=True, crossref_enabled=False)
    config.setdefault("search", {}).update(provider="serper", api_key_env="SERPER_KEY", strategy="focused")
    config.setdefault("preflight", {}).update(enabled=True, api_key_env="BLABLADOR_KEY")
    config.setdefault("review", {}).update(mode="api", required=True, api_key_env="BLABLADOR_KEY")
    config.setdefault("start_links", {}).update(prepared=True, skip_selection=True, enabled=True)
    config.setdefault("storage", {}).update(persist_extracted_text=False, shared_cache_dir="download_cache")
    config["limits"]["parse_timeout_seconds"] = max(600, config["limits"].get("parse_timeout_seconds", 90))
    config["context"] = config.get("context", "") + (
        " Research official Elbe waterbodies in the configured spatial scope. Preserve river-segment and lake-subbasin identities. "
        "Use the supplied accepted start sources first; continue via relevant outgoing links and focused Serper searches. "
        "Only original sources and provenance are retained; reading/parsing is transient. No database import or event extraction.")
    scale_budgets(config, len(eligible))
    return core.load_config(config)


def absolute_workspace(value):
    root = Path(value).expanduser()
    if not root.is_absolute():
        raise ValueError("--workspace muss ein absoluter Pfad sein")
    return root.resolve()


def prepare(args):
    root = absolute_workspace(args.workspace)
    if root.exists():
        raise ValueError("Workspace existiert bereits. Zum Fortsetzen run verwenden; prepare benötigt einen neuen Pfad.")
    rows = select_rows(load_registry(args.registry), args.kind, args.state)
    seeds, manifests, issues = load_seeds(args.seeds)
    eligible, decisions = assess_seed_coverage(rows, seeds, args.minimum_lake_seeds,
        args.minimum_river_seeds, args.allow_shortfall, args.maximum_seeds)
    report = {"prepared_at": now(), "kind": args.kind, "states": args.state or list(STATES),
        "registry_sha256": hashlib.sha256(Path(args.registry).read_bytes()).hexdigest(),
        "seed_manifests": manifests, "seed_manifest_issues": issues,
        "requested_count": len(rows), "included_count": len(eligible),
        "excluded_count": len(rows) - len(eligible), "allow_shortfall": args.allow_shortfall,
        "minimum_lake_seeds": args.minimum_lake_seeds, "minimum_river_seeds": args.minimum_river_seeds,
        "maximum_seeds": args.maximum_seeds, "status_counts": dict(Counter(item["status"] for item in decisions)),
        "waterbodies": decisions,
        "note": "Unseeded waterbodies are always excluded. Accepted seeds remain subject to ordinary LLM preflight/content review."}
    if not eligible:
        root.mkdir(parents=True)
        write_json(root / "preparation_report.json", report)
        raise ValueError(f"Kein Gewässer erfüllt die Startquellen-Regeln. Bericht: {root / 'preparation_report.json'}; kein lauffähiges Manifest erstellt.")
    config = campaign_config(eligible, args.base_config)
    groups = min(args.groups, len(eligible))
    parts = []
    for index in range(groups):
        part = copy.deepcopy(config)
        part["waterbodies"] = config["waterbodies"][index::groups]
        scale_budgets(part, len(part["waterbodies"]))
        # Match the established parallel coordinator's aggregate search pacing.
        part["search"]["delay_seconds"] = config["search"].get("delay_seconds", 2) * groups
        parts.append(core.load_config(part))
    root.mkdir(parents=True)
    with core.WorkspaceLock(root):
        entries = []
        for index, part in enumerate(parts, 1):
            name = f"group-{index:02d}"
            directory = root / name
            directory.mkdir()
            write_json(directory / "config.json", part)
            entries.append({"directory": name, "waterbodies": [body["id"] for body in part["waterbodies"]]})
        report.update(actual_groups=groups, campaign="elbe_wfd2022_six_states")
        write_json(root / "preparation_report.json", report)
        write_json(root / "accepted_start_sources.json", [{"waterbody_id": row["waterbody_id"], "accepted": accepted}
                                                          for row, accepted in eligible])
        # Write last so a partial preparation cannot be started accidentally.
        write_json(root / "parallel.json", {"version": 1, "campaign": "elbe_wfd2022_six_states",
            "prepared_at": report["prepared_at"], "groups": entries, "imported_checkpoint": False})
    print(f"Vorbereitet: {len(eligible)} Gewässer in {groups} Gruppen; {len(rows) - len(eligible)} ausgeschlossen.")
    print(f"Workspace: {root}\nDetails: {root / 'preparation_report.json'}")
    print("Start/Fortsetzen: bash start-elbe-linux.sh run --workspace \"" + str(root) + "\" --env-file /absoluter/pfad/.env")
    return 0


def group_config(root, entry):
    directory = (root / entry["directory"]).resolve()
    if directory.parent != root:
        raise ValueError("Gruppenpfad liegt außerhalb des Workspace")
    config = core.load_config(directory / "config.json")
    if set(entry["waterbodies"]) != {body["id"] for body in config["waterbodies"]}:
        raise ValueError(f"Manifest/Konfiguration stimmen nicht überein: {entry['directory']}")
    return directory, config


def current_group_status(directory, config):
    profiles = {core.stable(dict(waterbody=body, topics=config["topics"], context=config.get("context", "")))
                for body in config["waterbodies"]}
    result = {"directory": directory.name, "waterbody_count": len(config["waterbodies"]),
              "current_pending_tasks": None, "current_pending_searches": None, "count_source": "not_initialized"}
    last_file = directory / "last_run.json"
    if last_file.exists():
        last = read_json(last_file)
        result["last_run"] = {key: last.get(key) for key in
            ("stop_reason", "tasks", "fetch_attempts", "searches", "pending_tasks", "pending_searches", "api_calls")}
        result["last_run_note"] = "Historical run summary only; never added to the current pending totals."
    database = directory / "research.sqlite"
    if not database.exists():
        return result
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as connection:
            placeholders = ",".join("?" for _ in profiles)
            for table in ("tasks", "searches"):
                counts = dict(connection.execute(f"SELECT status,COUNT(*) FROM {table} WHERE profile IN ({placeholders}) GROUP BY status", tuple(profiles)))
                result[table + "_by_status"] = counts
                pending = ("pending", "retry", "in_progress") if table == "tasks" else ("pending", "retry")
                result["current_pending_" + table] = sum(counts.get(key, 0) for key in pending)
            result["count_source"] = "sqlite_current_config_profiles_only"
    except sqlite3.Error as error:
        result["count_source"] = "database_unavailable"
        result["warning"] = str(error)
    return result


def campaign_status(root):
    root = absolute_workspace(root)
    manifest = read_json(root / "parallel.json")
    groups, seen = [], set()
    for entry in manifest["groups"]:
        directory, config = group_config(root, entry)
        ids = {body["id"] for body in config["waterbodies"]}
        if ids & seen:
            raise ValueError("Gewässer mehrfach im Manifest; keine belastbare Gesamtsumme möglich")
        seen.update(ids)
        groups.append(current_group_status(directory, config))
    counted = [group for group in groups if group["count_source"] == "sqlite_current_config_profiles_only"]
    return {"workspace": str(root), "waterbody_count": len(seen), "group_count": len(groups),
            "counted_groups": len(counted), "uncounted_groups": len(groups) - len(counted),
            "current_pending_tasks": sum(group["current_pending_tasks"] for group in counted),
            "current_pending_searches": sum(group["current_pending_searches"] for group in counted),
            "totals_complete": len(counted) == len(groups), "groups": groups}


def render_catalog(rows, directory):
    directory = Path(directory)
    index = ["# Elbe-Register: Seen und Flussabschnitte", "",
             f"{len(rows)} eindeutige WRRL-Wasserkörper. Zuordnung nach räumlichem Schnitt mit BKG-Landesgrenzen.",
             "Ein Grenzgewässer erscheint in mehreren Landeslisten; deren Zahlen sind **nicht additiv**.",
             "Die Anzahl bezeichnet amtliche Wasserkörper, nicht vollständige physische Seen/Flüsse. Unversorgte Gewässer bleiben im Register.", "",
             "| Land | Seen-WK | Fluss-WK |", "|---|---:|---:|"]
    for state, label in STATES.items():
        state_rows = [row for row in rows if state in row["spatial_states_target"]]
        counts = Counter(row["waterbody_type"] for row in state_rows)
        index.append(f"| {label} | [{counts['lake']}]({state}/lakes.md) | [{counts['river']}]({state}/rivers.md) |")
        for kind, filename, title in (("lake", "lakes.md", "Seen-Wasserkörper"), ("river", "rivers.md", "Flussabschnitte")):
            selected = sorted((row for row in state_rows if row["waterbody_type"] == kind), key=lambda row: (row["name_original"], row["waterbody_id"]))
            lines = [f"# {label}: {title}", "", f"{len(selected)} Einträge. Amtliche IDs bleiben getrennt, auch bei gleichem Namen.",
                     "Mehrländer-Zuordnung ist nicht additiv. `Grenzprüfung` kennzeichnet Überlappungen unter 1 % der Quellgeometrie.", "",
                     "| Originalname | Amtliche ID | Meldeland | Räumliche Länder | Grenzprüfung |", "|---|---|---|---|---|"]
            for row in selected:
                lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in (
                    row["name_original"], row["waterbody_id"], row["reporting_state"],
                    ", ".join(row["spatial_states"]), ", ".join(row.get("low_overlap_states_review", [])) or "–")) + " |")
            target = directory / state / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (directory / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8", newline="\n")
    return directory / "README.md"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    catalog = commands.add_parser("catalog", help="Vollständige Listen nach Land und Gewässertyp schreiben")
    catalog.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    catalog.add_argument("--output", type=Path, default=INPUT / "catalog")
    merge = commands.add_parser("merge-seeds", help="Angenommene Seed-Manifeste sanitisiert zusammenführen und vollständige Coverage ausweisen")
    merge.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    merge.add_argument("--seeds", type=Path, nargs="+", required=True)
    merge.add_argument("--output", type=Path, default=INPUT / "seeds.json")
    merge.add_argument("--coverage", type=Path)
    merge.add_argument("--minimum-lake-seeds", type=int, default=10)
    merge.add_argument("--minimum-river-seeds", type=int, default=1)
    merge.add_argument("--maximum-seeds", type=int, default=20)
    prep = commands.add_parser("prepare", help="Neue Kampagne nur mit angenommenen Startquellen vorbereiten")
    prep.add_argument("--workspace", required=True)
    prep.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    prep.add_argument("--base-config", type=Path, default=DEFAULT_CONFIG)
    prep.add_argument("--seeds", type=Path, nargs="+", required=True, help="JSON-Manifeste oder Verzeichnisse; nur accepted[] wird übernommen")
    prep.add_argument("--kind", choices=["lakes", "rivers", "all"], default="all")
    prep.add_argument("--state", choices=list(STATES), action="append")
    prep.add_argument("--groups", type=int, default=30)
    prep.add_argument("--minimum-lake-seeds", type=int, default=10)
    prep.add_argument("--minimum-river-seeds", type=int, default=1)
    prep.add_argument("--maximum-seeds", type=int, default=20)
    prep.add_argument("--allow-shortfall", action="store_true", help="Unter Mindestzahl zulassen, aber nie ohne eine angenommene Quelle")
    run = commands.add_parser("run", help="Vorbereiteten Workspace starten oder fortsetzen; kein neues prepare")
    run.add_argument("--workspace", required=True)
    run.add_argument("--workers", type=int, default=30)
    run.add_argument("--llm-workers", type=int, default=2)
    run.add_argument("--llm-interval", type=float, default=6)
    run.add_argument("--llm-cooldown", type=float, default=60)
    run.add_argument("--env-file", default=os.getenv("SOLVE_ENV_FILE"))
    check = commands.add_parser("check-api", help="Blablador-Zugang und Modellliste prüfen; keine Such-/Generierungsanfragen")
    check.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    check.add_argument("--env-file", default=os.getenv("SOLVE_ENV_FILE"))
    status = commands.add_parser("status", help="Aktuelle ausstehende Arbeit nur für die konfigurierten Gruppenprofile zählen")
    status.add_argument("--workspace", required=True)
    status.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "catalog":
        print(render_catalog(load_registry(args.registry), args.output))
        return 0
    if args.command == "merge-seeds":
        if min(args.minimum_lake_seeds, args.minimum_river_seeds, args.maximum_seeds) < 1:
            parser.error("Startquellen-Grenzen müssen mindestens 1 sein")
        if max(args.minimum_lake_seeds, args.minimum_river_seeds) > args.maximum_seeds:
            parser.error("Mindestzahl darf --maximum-seeds nicht überschreiten")
        return merge_seeds(args)
    if args.command == "prepare":
        if min(args.groups, args.minimum_lake_seeds, args.minimum_river_seeds, args.maximum_seeds) < 1:
            parser.error("Gruppen und Startquellen-Grenzen müssen mindestens 1 sein")
        if max(args.minimum_lake_seeds, args.minimum_river_seeds) > args.maximum_seeds:
            parser.error("Mindestzahl darf --maximum-seeds nicht überschreiten")
        return prepare(args)
    if args.command == "check-api":
        import check_blablador
        report = check_blablador.check_config(core.load_config(args.config), args.env_file)
        check_blablador.print_report(report)
        return 0 if report["ok"] else 1
    if args.command == "status":
        result = campaign_status(args.workspace)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"{result['waterbody_count']} Gewässer; {result['group_count']} Gruppen, {result['counted_groups']} mit aktuellem DB-Stand.")
            print(f"Ausstehend in diesen DBs: {result['current_pending_tasks']} Abruf-/Prüfaufgaben, {result['current_pending_searches']} Suchaufträge.")
            if not result["totals_complete"]:
                print("Gesamtsumme unvollständig: nicht gestartete oder unlesbare Gruppen werden nicht als null ausgegeben.")
            for group in result["groups"]:
                print(f"{group['directory']}: pending={group['current_pending_tasks']}, searches={group['current_pending_searches']}; "
                      f"letzter Stopp={group.get('last_run', {}).get('stop_reason', 'noch kein Abschluss')}")
        return 0
    root = absolute_workspace(args.workspace)
    manifest = read_json(root / "parallel.json")
    configs = []
    for entry in manifest["groups"]:
        _, config = group_config(root, entry)
        if not config["start_links"].get("prepared") or any(not wb["seed_urls"] for wb in config["waterbodies"]):
            raise ValueError("run benötigt vorbereitete Startquellen für jedes konfigurierte Gewässer")
        configs.append(config)
    # Campaign groups share provider settings. Refuse a mismatched hand-edited
    # workspace rather than checking one provider and running another silently.
    def api_settings(cfg):
        return [{field: cfg[section].get(field) for field in
                 ("enabled", "mode", "required", "api_key_env", "base_url", "base_url_env", "model", "model_env")}
                for section in ("preflight", "review")]
    if not configs or any(api_settings(cfg) != api_settings(configs[0]) for cfg in configs):
        raise ValueError("Gruppen müssen dieselbe Blablador-Konfiguration verwenden")
    import check_blablador
    report = check_blablador.check_config(configs[0], args.env_file)
    check_blablador.print_report(report)
    if not report["ok"]:
        return 1
    import parallel_crawler
    return parallel_crawler.run(root, args.workers, args.env_file, args.llm_workers, args.llm_interval, args.llm_cooldown)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
