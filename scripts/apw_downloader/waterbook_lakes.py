"""Spatial candidate discovery and conservative, auditable lake associations.

Bounding-box hits are NOT lake assignments. No graph writes or legal inference.
All distances use metres in EPSG:25833; original PDF coordinates are retained.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from common import Snapshot, clean, digest, read_json, write_csv, write_json
import wrrl

REFERENCE_KEY = "waterbook:lake_reference"
ASSOCIATION_FIELDS = ["water_body_id", "water_body_name", "waterbook_number", "entry_id", "case_id", "site_id",
    "status", "reasons", "distance_m", "inside_lake", "name_evidence", "site_water_category",
    "easting", "northing", "crs", "source_page", "is_official_link", "snapshot", "source_url", "source_sha256"]
SUMMARY_FIELDS = ["water_body_id", "water_body_name", "search_geometry", "search_buffer_m", "query_complete",
    "search_objects", "discovered_documents", "downloaded_documents", "parsed_documents",
    "matched_documents", "review_documents", "excluded_documents"]


def spatial_modules():
    try:
        from pyproj import Transformer
        from shapely.geometry import Point, shape
        from shapely.ops import transform
    except ImportError as exc:
        raise RuntimeError("Für die Wasserbuch-Seenauswahl: python -m pip install -r requirements.txt (pyproj/shapely).") from exc
    return Transformer, Point, shape, transform


def projected_lakes(reference):
    Transformer, _, shape, transform = spatial_modules()
    project = Transformer.from_crs("EPSG:4326", "EPSG:25833", always_xy=True)
    lakes = []
    for feature in reference["features"]:
        geometry = shape(feature["geometry"])
        if geometry.geom_type not in {"Polygon", "MultiPolygon"} or geometry.is_empty or not geometry.is_valid:
            raise ValueError("Ungültige Seegeometrie; keine automatische Reparatur/Zuordnung.")
        geometry = transform(project.transform, geometry)
        if not geometry.is_valid or not all(math.isfinite(v) for v in geometry.bounds):
            raise ValueError("Seegeometrie lässt sich nicht sicher nach EPSG:25833 transformieren.")
        lakes.append({"water_body_id": feature["properties"]["EU_CD_LW"],
                      "name": feature["properties"]["S_NAME"], "geometry": geometry})
    return lakes


def read_reference(source: Path):
    source = source.resolve()
    if not (source / "manifest.json").is_file():
        raise ValueError("--lake-snapshot muss auf einen vorhandenen WRRL-Snapshot mit manifest.json zeigen.")
    original = Snapshot(source.parent, source.name)
    layer = original.state.get("wrrl", {}).get("lakes", {})
    selection = original.state.get("wrrl_scope", {})
    ids = selection.get("water_body_ids", [])
    if selection.get("kind") != "water_body_ids" or not ids or not layer.get("complete"):
        raise ValueError("Zuerst WRRL-Seen vollständig mit --water-body-id in einen eigenen Snapshot laden.")
    features, sources = [], []
    for key in layer.get("pages", []):
        cached = original.cached(key)  # Check hashes; never rewrite the source snapshot.
        if not cached:
            raise ValueError("WRRL-Referenzseite fehlt im Manifest.")
        data, meta = cached
        features.extend(wrrl.validate_selection(wrrl.feature_collection(data), "lakes", ids)["features"])
        sources.append(meta)
    actual = [f["properties"].get("EU_CD_LW") for f in features]
    if len(actual) != len(set(actual)) or set(actual) != set(ids):
        raise ValueError("Nicht jede angeforderte Gewässer-ID hat genau eine vollständige Seegeometrie.")
    if len(actual) != layer.get("downloaded") or len(actual) != layer.get("expected"):
        raise ValueError("Anzahl der Seegeometrien passt nicht zum WRRL-Manifest.")
    if any(not clean(f["properties"].get("S_NAME")) for f in features):
        raise ValueError("WRRL-See ohne Namen; manuelle Auswahl erforderlich.")
    return {"type": "FeatureCollection", "crs": {"properties": {"name": "EPSG:4326"}},
            "features": sorted(features, key=lambda f: f["properties"]["EU_CD_LW"]),
            "source_snapshot": original.state["snapshot"], "source_artifacts": sources}


def prepare(snapshot, source: Path, buffer_m=250):
    if not math.isfinite(buffer_m) or not 0 <= buffer_m <= 5000:
        raise ValueError("Suchpuffer muss zwischen 0 und 5000 Metern liegen.")
    reference = read_reference(source)
    data = (json.dumps(reference, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()
    queries = []
    for lake in projected_lakes(reference):
        # Use the actual polygon buffer's envelope, not a circle about a centroid.
        bounds = lake["geometry"].buffer(buffer_m).bounds if buffer_m else lake["geometry"].bounds
        queries.append({"water_body_id": lake["water_body_id"], "name": lake["name"],
                        "geometry_input": "gBox:" + "|".join(f"{v:.2f}" for v in bounds) + ";25833"})
    scope = {"kind": "lake_bounding_boxes", "reference_sha256": digest(data),
             "search_buffer_m": buffer_m, "crs": "EPSG:25833", "queries": queries}
    state = snapshot.state["waterbook"]
    previous = state.get("discovery_scope", state.get("index_scope"))
    if previous is not None and previous != scope:
        raise ValueError("Andere Seegeometrien, Suchpuffer oder Wasserbuch-Auswahl: neuen --snapshot verwenden.")
    cached = snapshot.cached(REFERENCE_KEY)
    if cached and cached[1]["sha256"] != scope["reference_sha256"]:
        raise ValueError("WRRL-Referenz geändert; neuen --snapshot verwenden.")
    if not cached:
        snapshot.record(REFERENCE_KEY, snapshot.root / "reference/wrrl_lakes.geojson", data, wrrl.ENDPOINT,
                        derived_from=reference["source_artifacts"])
    state["discovery_scope"] = scope
    state["lake_reference_artifact"] = REFERENCE_KEY
    snapshot.save()
    return scope


def text_mentions(name, value):
    """Deliberately narrow spelling variants, not fuzzy matching or municipality names."""
    name = clean(name).casefold()
    pattern = r"\s+".join(re.escape(part) for part in name.split())
    if name == "tiefer see":
        pattern = r"tief(?:er|en)\s+see"
    return bool(re.search(r"(?<!\w)" + pattern + r"s?(?!\w)", clean(value).casefold()))


def site_point(site):
    Transformer, Point, _, _ = spatial_modules()
    east, north, crs = site.get("easting"), site.get("northing"), site.get("crs")
    if east is None or north is None or not crs:
        return None, "coordinates_or_crs_missing"
    if not isinstance(east, (int, float)) or not isinstance(north, (int, float)) or not all(math.isfinite(v) for v in (east, north)):
        return None, "coordinates_invalid"
    # Unknown axis/unit conventions must not be guessed.
    if crs not in {"EPSG:25833", "EPSG:25832", "EPSG:4326"}:
        return None, "crs_not_supported"
    try:
        transformer = Transformer.from_crs(crs, "EPSG:25833", always_xy=True)
        x, y = transformer.transform(east, north, errcheck=True)
        if not (100000 <= x <= 650000 and 5500000 <= y <= 6100000):
            return None, "coordinates_outside_region_or_axis_swap"
        return Point(x, y), None
    except Exception:
        return None, "coordinate_transformation_failed"


def associations(parsed, lakes, discovered_for, buffer_m):
    entry = parsed["entry"]
    number = entry["waterbook_number"]
    sites = parsed.get("sites", [])
    rows = []
    case_ids = [case["case_id"] for case in parsed.get("cases", [])]
    site_ids = [site["site_id"] for site in sites]
    for site in sites or [{"site_id": None, "case_id": None}]:
        point, coordinate_error = site_point(site)
        sources = {"site.name": site.get("name")}
        uses = [u for u in parsed.get("uses", []) if site.get("case_id") and u.get("case_id") == site["case_id"]]
        sources.update({f"use.{u['use_id']}.purpose": u.get("purpose") for u in uses})
        # An entry-wide title must not be copied into every case/site.
        if len(case_ids) == 1 and len(sites) == 1:
            sources["entry.title"] = entry.get("title")
        # Deliberately exclude municipality, catchment code and full PDF text.
        mentions = {lake["water_body_id"]: [key + ": " + clean(value) for key, value in sources.items()
                    if text_mentions(lake["name"], value)] for lake in lakes}
        named_ids = {key for key, value in mentions.items() if value}
        near_ids = {lake["water_body_id"] for lake in lakes if point is not None and lake["geometry"].distance(point) <= buffer_m}
        eligible = set(discovered_for) | named_ids | near_ids
        category = clean(site.get("water_category")).casefold()
        use_text = " ".join(clean(u.get("use_type")) for u in uses).casefold()
        for lake in lakes:
            identifier = lake["water_body_id"]
            if identifier not in eligible:
                continue
            distance = lake["geometry"].distance(point) if point is not None else None
            named = bool(mentions[identifier])
            status, reasons = "review", []
            if "grundwasser" in category or (not category and "grundwasser" in use_text):
                status, reasons = "excluded", ["groundwater_use_not_direct_lake_use"]
            elif coordinate_error:
                reasons = [coordinate_error]
            elif distance > buffer_m:
                status = "review" if named else "excluded"
                reasons = ["named_lake_but_distant_coordinates" if named else "outside_lake_buffer"]
            elif not site.get("case_id") or case_ids.count(site["case_id"]) != 1 or site_ids.count(site.get("site_id")) != 1:
                reasons = ["case_or_site_assignment_ambiguous"]
            elif category != "oberflächengewässer":
                reasons = ["surface_water_category_not_confirmed"]
            elif len(named_ids) > 1:
                reasons = ["multiple_named_lakes"]
            elif named:
                status, reasons = "matched", ["documented_name_and_compatible_location"]
            else:
                reasons = ["spatial_candidate_without_documented_lake_name"]
            rows.append({"water_body_id": identifier, "water_body_name": lake["name"], "waterbook_number": number,
                "entry_id": entry["entry_id"], "case_id": site.get("case_id"), "site_id": site.get("site_id"),
                "status": status, "reasons": reasons, "distance_m": round(distance, 2) if distance is not None else None,
                "inside_lake": lake["geometry"].covers(point) if point is not None else None,
                "name_evidence": mentions[identifier], "site_water_category": site.get("water_category"),
                "easting": site.get("easting"), "northing": site.get("northing"), "crs": site.get("crs"),
                "source_page": site.get("source_page"), "is_official_link": False})
    return rows


def export(snapshot):
    state = snapshot.state["waterbook"]
    if "lake_reference_artifact" not in state:
        return
    data, reference_meta = snapshot.cached(state["lake_reference_artifact"])
    scope = state["discovery_scope"]
    if reference_meta["sha256"] != scope["reference_sha256"]:
        raise ValueError("Referenzgeometrien passen nicht zur gespeicherten Wasserbuch-Auswahl.")
    lakes = projected_lakes(json.loads(data))
    discovered = {}
    summaries = []
    for query in scope["queries"]:
        identifier = query["water_body_id"]
        progress = state.get("queries", {}).get(identifier, {})
        numbers = set()
        if progress.get("artifact"):
            import waterbook
            raw, _ = snapshot.cached(progress["artifact"])
            payload = json.loads(raw)
            if progress.get("complete"):
                waterbook.validate_result(payload)
            numbers = {waterbook.pdf_number(record["url"]) for record in payload["records"]}
        discovered[identifier] = numbers
        documents = state.get("documents", {})
        summaries.append({"water_body_id": identifier, "water_body_name": query["name"],
            "search_geometry": query["geometry_input"], "search_buffer_m": scope["search_buffer_m"],
            "query_complete": bool(progress.get("complete")), "search_objects": progress.get("objects"),
            "discovered_documents": len(numbers), "downloaded_documents": len(numbers & documents.keys()),
            "parsed_documents": sum(n in documents and documents[n].get("parse_status") != "error" for n in numbers)})
    rows = []
    for number, document in state.get("documents", {}).items():
        _, meta = snapshot.cached(document["artifact"])
        found_for = {identifier for identifier, numbers in discovered.items() if number in numbers}
        if document.get("parsed_path"):
            path = (snapshot.root / document["parsed_path"]).resolve()
            if not path.is_relative_to(snapshot.root):
                raise ValueError("Ungültiger Pfad zur PDF-Auswertung.")
            parsed = read_json(path)
            associated = associations(parsed, lakes, found_for, scope["search_buffer_m"])
        else:
            associated = [{"water_body_id": lake["water_body_id"], "water_body_name": lake["name"],
                "waterbook_number": number, "entry_id": "apw:ewabu:" + number, "status": "review",
                "reasons": ["pdf_parse_failed"], "is_official_link": False}
                for lake in lakes if lake["water_body_id"] in found_for]
        for row in associated:
            rows.append({**row, "snapshot": snapshot.state["snapshot"], "source_url": meta["source_url"], "source_sha256": meta["sha256"]})
    for summary in summaries:
        for status in ("matched", "review", "excluded"):
            summary[status + "_documents"] = len({row["waterbook_number"] for row in rows
                if row["water_body_id"] == summary["water_body_id"] and row["status"] == status})
    write_json(snapshot.root / "parsed/waterbook/lake_associations.json", rows)
    write_csv(snapshot.root / "tables/waterbook/lake_associations.csv", rows, ASSOCIATION_FIELDS)
    write_csv(snapshot.root / "tables/waterbook/lake_review.csv", [r for r in rows if r["status"] == "review"], ASSOCIATION_FIELDS)
    write_csv(snapshot.root / "tables/waterbook/lake_summary.csv", summaries, SUMMARY_FIELDS)
    write_json(snapshot.root / "tables/waterbook/lake_coverage.json", {
        "selection": scope, "lakes": summaries, "all_water_rights_complete": False,
        "notes": "Only publicly displayed objects inside the recorded search boxes. Nearby objects are candidates, not direct lake uses. No upstream/catchment-wide search, no inference of legal validity. matched is a documented inference, not an official WRRL foreign key. Zero hits do not prove absence of water rights."})
    state["lake_summary"] = summaries
    snapshot.save()
