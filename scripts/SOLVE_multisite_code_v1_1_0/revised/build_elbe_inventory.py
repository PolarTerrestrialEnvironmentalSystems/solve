"""Reproduce the official WFD Elbe inventory and its spatial state selection.

Install optional dependencies from requirements-inventory.txt. Examples:
  python revised/build_elbe_inventory.py --output ./elbe_inventory
  python revised/build_elbe_inventory.py --replay-from ./saved_inventory --output ./replayed

The reporting state encoded in a waterbody ID is NOT a spatial state filter.
All German DE5000 river/lake bodies are acquired, then intersected with BKG
VG250 state polygons. A positive intersection is retained, including small
boundary overlaps marked for review. No waterbody names are deduplicated.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

EEA = "https://water.discomap.eea.europa.eu/arcgis/rest/services/WISE_WFD/WFD2022_SurfaceWaterBody_WM/MapServer"
BKG = "https://sgx.geodatenzentrum.de/wfs_vg250"
WHERE = "countryCode='DE' AND euRBDCode='DE5000'"
TARGET_STATES = ("BB", "BE", "MV", "SN", "ST", "TH")
LAYERS = {2: "RW", 3: "RW", 4: "LW"}
FIELDS = (
    "OBJECTID,cYear,countryCode,euRBDCode,euSubUnitCode,euSurfaceWaterBodyCode,"
    "surfaceWaterBodyName,surfaceWaterBodyCategory,naturalAWBHMWB,"
    "surfaceWaterBodyTransboundary,cArea,cLength,reservoir"
)
SOURCE_URLS = {
    "basin": "https://www.fgg-elbe.de/fgg-elbe.html",
    "basin_context": "https://undine.bafg.de/elbe/elbegebiet.html",
    "wfd_report": "https://www.fgg-elbe.de/berichte/aktualisierung-nach-art-13-2021.html",
    "transboundary_definition": "https://dd.eionet.europa.eu/schema/WFD2022/SWB_2022.xsd/view",
    "state_metadata": "https://gdz.bkg.bund.de/index.php/default/wfs-verwaltungsgebiete-1-250-000-stand-01-01-wfs-vg250.html",
}
# Curated research aids, grounded in the cited official descriptions. These are
# deliberately proposals: neither observations nor waterbody identities merge.
GROUP_PROPOSALS = (
    ("candidate_schaalsee", "Schaalsee", "whole_lake_candidate",
     [f"DELW_DEMV_230010{i}" for i in range(1, 8)] + ["DELW_DESH_0288", "DELW_DESH_0488"],
     "https://www.schleswig-holstein.de/mm/downloads/Fachinhalte/Wasserrahmenrichtlinie/BP_ElbeSH_3BWZ.pdf",
     "Nine waterbody units across MV/SH; a unit outside the target states remains context only. "
     "WRRL basin assignment is retained despite managed outlet diversions."),
    ("candidate_schweriner_see_mv", "Schweriner See (Mecklenburg-Vorpommern)", "whole_lake_candidate",
     ["DELW_DEMV_1700101", "DELW_DEMV_1700103"],
     "https://mecklenburg-vorpommern.de/badestelle?gaia.badestelle.id=121",
     "Inner and outer lake; the similarly named lake in Brandenburg is excluded."),
    ("candidate_mueritz", "Müritz", "whole_lake_candidate",
     [f"DELW_DEMV_250010{i}" for i in range(1, 5)],
     "https://fis-wasser-mv.de/charts/steckbriefe/lw/lw_wk.php?schema=reporting_bp3&sg=2500104",
     "Explicitly named subbasins; Müritzarm is not automatically included."),
    ("candidate_berliner_unterhavel", "Berliner Unterhavel (Recherchegruppe)", "research_group_not_asserted_single_lake",
     ["DELW_DEBE_8000958359_1", "DELW_DEBE_8000958359_2"],
     "https://www.parlament-berlin.de/ados/19/UK/vorgang/UK19-0112-01-v.pdf",
     "Two distinct LW records; do not merge with the separate RW Unterhavel record."),
)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def read_json(path: Path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(value, dict) and value.get("error"):
        raise ValueError(f"Upstream error in {path.name}: {value['error']}")
    return value


def query_url(layer: int, **kwargs) -> str:
    return f"{EEA}/{layer}/query?" + urllib.parse.urlencode({"f": "json", "where": WHERE, **kwargs})


def download(url: str, destination: Path, log: list[dict], timeout: float = 60) -> bytes:
    """Read-only HTTP, bounded retries, no credentials or service bypasses."""
    request = urllib.request.Request(url, headers={"User-Agent": "SOLVE-WFD-inventory/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read(100_000_001)
                if len(data) > 100_000_000:
                    raise ValueError("Response exceeds the 100 MB per-request limit")
                entry = {"url": url, "final_url": response.url, "http_status": response.status,
                         "content_type": response.headers.get("Content-Type")}
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            entry.update(file=destination.name, bytes=len(data),
                         sha256=hashlib.sha256(data).hexdigest(),
                         retrieved_at=datetime.now(timezone.utc).isoformat())
            log.append(entry)
            print(f"Downloaded {destination.name}: {len(data):,} bytes", flush=True)
            return data
        except urllib.error.HTTPError as error:
            if error.code not in (429, 502, 503, 504) or attempt == 2:
                raise
            time.sleep(min(30, 2 ** (attempt + 1)))
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 ** (attempt + 1))
    raise AssertionError("unreachable")


def acquire(directory: Path, timeout: float) -> list[dict]:
    if any(directory.glob("layer*_ids.json")) or any(directory.glob("eea_elbe_layer*_offset*.json")):
        raise ValueError("Output already contains a snapshot; choose a new directory or use --replay-from")
    log: list[dict] = []
    download(EEA + "?f=json", directory / "eea_wfd2022_service.json", log, timeout)
    service = read_json(directory / "eea_wfd2022_service.json")
    listed = {layer["id"]: layer for layer in service["layers"]}
    for layer, category in LAYERS.items():
        expected = "lakeWaterBody" if category == "LW" else "riverWaterBody"
        if expected not in listed[layer]["name"]:
            raise ValueError(f"Layer {layer} semantics changed; inspect the service before continuing")
        download(f"{EEA}/{layer}?f=json", directory / f"layer{layer}_meta.json", log, timeout)
        metadata = read_json(directory / f"layer{layer}_meta.json")
        missing = set(FIELDS.split(",")) - {field["name"] for field in metadata["fields"]}
        if missing:
            raise ValueError(f"Layer {layer} missing fields: {sorted(missing)}")
        download(query_url(layer, returnCountOnly="true"), directory / f"layer{layer}_count.json", log, timeout)
        download(query_url(layer, returnIdsOnly="true"), directory / f"layer{layer}_ids.json", log, timeout)
        count = read_json(directory / f"layer{layer}_count.json")["count"]
        ids = read_json(directory / f"layer{layer}_ids.json").get("objectIds") or []
        if len(ids) != count or len(set(ids)) != count:
            raise ValueError(f"Layer {layer}: count and IDs disagree")
        step = min(2000, int(metadata["maxRecordCount"]))
        for offset in range(0, count, step):
            url = query_url(layer, outFields=FIELDS, returnGeometry="false", orderByFields="OBJECTID",
                            resultOffset=offset, resultRecordCount=step)
            download(url, directory / f"eea_elbe_layer{layer}_offset{offset}.json", log, timeout)
        for offset in range(0, count, 500):
            url = query_url(layer, f="geojson", outFields="OBJECTID,euSurfaceWaterBodyCode",
                            returnGeometry="true", outSR=25833, orderByFields="OBJECTID",
                            resultOffset=offset, resultRecordCount=500)
            download(url, directory / f"layer{layer}_geometry_offset{offset}.geojson", log, timeout)
    capabilities = download(BKG + "?service=WFS&request=GetCapabilities",
                            directory / "bkg_capabilities.xml", log, timeout)
    root = ET.fromstring(capabilities)
    types = {element.text for element in root.iter() if element.tag.endswith("}Name")}
    if "vg250:vg250_lan" not in types:
        raise ValueError("Expected BKG state feature type is no longer advertised")
    url = BKG + "?" + urllib.parse.urlencode({"service": "WFS", "version": "2.0.0",
        "request": "GetFeature", "typeNames": "vg250:vg250_lan", "outputFormat": "application/json",
        "srsName": "EPSG:25833"})
    download(url, directory / "bkg_states_25833.geojson", log, timeout)
    write_json(directory / "http_download_manifest.json", log)
    return log


def validate_completeness(expected_ids: list[int], attributes: list[dict], geometries: list[dict]) -> dict:
    ids = set(expected_ids)
    attr_ids = [row["OBJECTID"] for row in attributes]
    geom_ids = [row["properties"]["OBJECTID"] for row in geometries]
    if len(ids) != len(expected_ids) or len(attr_ids) != len(set(attr_ids)) or len(geom_ids) != len(set(geom_ids)):
        raise ValueError("Duplicate OBJECTID in IDs, attribute pages or geometry pages")
    if set(attr_ids) != ids or set(geom_ids) != ids:
        raise ValueError("Incomplete snapshot: IDs, attribute pages and geometry pages do not match")
    if {r["OBJECTID"]: r["euSurfaceWaterBodyCode"] for r in attributes} != {
        r["properties"]["OBJECTID"]: r["properties"]["euSurfaceWaterBodyCode"] for r in geometries
    }:
        raise ValueError("Waterbody codes differ between attribute and geometry pages")
    return {"count": len(ids), "ids_equal_attributes_and_geometry": True}


def require_crs_25833(collection: dict, filename: str) -> None:
    crs_name = collection.get("crs", {}).get("properties", {}).get("name", "")
    if not re.search(r"(?:EPSG(?:::|:|/0/))25833$", crs_name):
        raise ValueError(f"Missing or unexpected CRS in {filename}: {crs_name!r}")


def reporting_state(code: str) -> str:
    match = re.match(r"DE(?:RW|LW)_DE([A-Z]{2})", code)
    if not match:
        raise ValueError(f"Unrecognized state namespace in waterbody ID {code!r}")
    return match.group(1)


def intersect_states(geometry, states: dict, category: str, review_fraction: float = .01) -> tuple:
    unit = "m" if category == "RW" else "m2"
    total = geometry.length if category == "RW" else geometry.area
    measures, touch_only = {}, []
    for key, state in states.items():
        if not geometry.intersects(state):
            continue
        intersection = geometry.intersection(state)
        value = intersection.length if category == "RW" else intersection.area
        if value > 0:
            measures[key] = {"intersection_measure": round(value, 3), "unit": unit,
                             "fraction_of_source_geometry": round(value / total, 8) if total else None}
        else:
            touch_only.append(key)
    low_overlap = sorted(key for key, measure in measures.items()
                         if measure["fraction_of_source_geometry"] is not None
                         and measure["fraction_of_source_geometry"] < review_fraction)
    return measures, sorted(touch_only), low_overlap


def group_proposals(records: list[dict]) -> list[dict]:
    index = {record["waterbody_id"]: record for record in records}
    proposals = []
    for code, name, kind, members, url, note in GROUP_PROPOSALS:
        proposals.append({"group_id": code, "proposed_display_name": name, "group_kind": kind,
            "status": "proposal_do_not_merge_waterbody_ids", "source_url": url, "notes": note,
            "missing_expected_ids_review": sorted(set(members) - set(index)),
            "members": [{key: index[member][key] for key in ("waterbody_id", "name_original",
                "selected_spatial_6states", "reporting_state")} for member in members if member in index]})
    return proposals


def build(directory: Path, output: Path, targets: set[str], review_fraction: float) -> dict:
    try:
        from shapely.geometry import shape
        from shapely.ops import unary_union
        from shapely import make_valid
        from pyproj import Transformer
    except ImportError as exc:
        raise RuntimeError("Install optional requirements-inventory.txt before building the inventory") from exc
    rows, geometries, checks = [], {}, {}
    for layer, category in LAYERS.items():
        attributes, features = [], []
        ids = read_json(directory / f"layer{layer}_ids.json").get("objectIds") or []
        for file in sorted(directory.glob(f"eea_elbe_layer{layer}_offset*.json")):
            attributes.extend(item["attributes"] for item in read_json(file)["features"])
        for file in sorted(directory.glob(f"layer{layer}_geometry_offset*.geojson")):
            data = read_json(file)
            require_crs_25833(data, file.name)
            features.extend(data["features"])
        checks[str(layer)] = validate_completeness(ids, attributes, features)
        count_file = directory / f"layer{layer}_count.json"
        if count_file.exists() and read_json(count_file)["count"] != len(ids):
            raise ValueError(f"Layer {layer} count differs from its ID set")
        for row in attributes:
            if row["countryCode"] != "DE" or row["euRBDCode"] != "DE5000" or row["surfaceWaterBodyCategory"] != category:
                raise ValueError(f"Unexpected scope/category in layer {layer}")
            rows.append(dict(row, eea_layer=layer))
        for feature in features:
            code = feature["properties"]["euSurfaceWaterBodyCode"]
            if code in geometries:
                raise ValueError(f"Duplicate waterbody code across geometries: {code}; inspect representations")
            geometries[code] = shape(feature["geometry"])
    if len({row["euSurfaceWaterBodyCode"] for row in rows}) != len(rows):
        raise ValueError("Duplicate waterbody code: no automatic merge is permitted")
    data = read_json(directory / "bkg_states_25833.geojson")
    require_crs_25833(data, "BKG states")
    state_parts, names = defaultdict(list), {}
    repaired_states = []
    for feature in data["features"]:
        properties, geometry = feature["properties"], shape(feature["geometry"])
        if not geometry.is_valid:
            repaired_states.append(properties.get("objid"))
            geometry = make_valid(geometry)
        code = properties["lkz"]
        names[code] = properties["gen"]
        state_parts[code].append(geometry)
    if targets - set(state_parts):
        raise ValueError(f"Missing target state polygons: {sorted(targets - set(state_parts))}")
    states = {key: unary_union(parts) for key, parts in state_parts.items()}
    to_lonlat = Transformer.from_crs(25833, 4326, always_xy=True).transform
    normalized, repaired_waterbodies = [], []
    for row in rows:
        code, category = row["euSurfaceWaterBodyCode"], row["surfaceWaterBodyCategory"]
        geometry = geometries[code]
        if geometry.is_empty:
            raise ValueError(f"Empty geometry for {code}")
        if not geometry.is_valid:
            repaired_waterbodies.append(code)
            geometry = make_valid(geometry)
        reported_state = reporting_state(code)
        if reported_state not in states:
            raise ValueError(f"Reporting state {reported_state} missing from BKG")
        measures, touch, low = intersect_states(geometry, states, category, review_fraction)
        spatial = sorted(measures)
        point = geometry.representative_point()
        longitude, latitude = to_lonlat(point.x, point.y)
        normalized.append({
            "waterbody_id": code, "name_original": row["surfaceWaterBodyName"],
            "waterbody_type": "river" if category == "RW" else "lake", "wfd_category": category,
            "reporting_year": row["cYear"], "country_code": row["countryCode"],
            "river_basin_code": row["euRBDCode"], "subunit_code": row["euSubUnitCode"],
            "reporting_state": reported_state, "reporting_state_name": names[reported_state],
            "spatial_states": spatial, "spatial_states_target": sorted(targets.intersection(spatial)),
            "state_intersection_measures": measures, "touch_only_states": touch,
            "low_overlap_states_review": low, "multistate_spatial_candidate": len(spatial) > 1,
            "cross_national_source_flag": row["surfaceWaterBodyTransboundary"],
            "natural_artificial_modified": row["naturalAWBHMWB"], "reservoir_source_flag": row["reservoir"],
            "reported_area_km2": row["cArea"], "reported_length_km": row["cLength"],
            "geometry_reference_crs": "EPSG:25833", "representative_longitude": longitude,
            "representative_latitude": latitude, "eea_layer": row["eea_layer"],
            "source_objectid": row["OBJECTID"],
            "selected_spatial_6states": bool(targets.intersection(spatial)),
            "selected_reporting_6states": reported_state in targets,
            "inclusion_note": "Positive line-length or polygon-area intersection; small overlaps require review"
                if targets.intersection(spatial) else None,
        })
    normalized.sort(key=lambda row: (row["reporting_state"], row["waterbody_type"], row["waterbody_id"]))
    selected = [row for row in normalized if row["selected_spatial_6states"]]
    additions = [row for row in selected if not row["selected_reporting_6states"]]
    missing = [row for row in normalized if row["selected_reporting_6states"] and not row["selected_spatial_6states"]]
    for filename, value in [("all_german_elbe_waterbodies.json", normalized),
                            ("selected_six_states_waterbodies.json", selected),
                            ("boundary_additions.json", additions),
                            ("reporting_selected_but_no_spatial_overlap.json", missing)]:
        write_json(output / filename, value)
    write_json(output / "whole_lake_group_proposals.json", group_proposals(normalized))
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "scope_filter": WHERE,
        "target_states": sorted(targets), "geometry_completeness": checks,
        "unique_ids": len(normalized), "selected_reporting_count": sum(r["selected_reporting_6states"] for r in normalized),
        "selected_spatial_count": len(selected), "selected_spatial_by_type": dict(Counter(r["waterbody_type"] for r in selected)),
        "spatial_additions_count": len(additions), "reporting_not_spatial_count": len(missing),
        "per_reporting_state": {key: dict(Counter(r["waterbody_type"] for r in selected if r["reporting_state"] == key)) for key in sorted(states)},
        "per_spatial_state_memberships_nonadditive": {key: dict(Counter(r["waterbody_type"] for r in selected if key in r["spatial_states"])) for key in sorted(targets)},
        "invalid_geometries_repaired": repaired_waterbodies, "invalid_state_geometries_repaired": repaired_states,
        "multistate_candidates": sum(r["multistate_spatial_candidate"] for r in selected),
        "transnational_yes": sum(r["cross_national_source_flag"] == "Yes" for r in selected),
        "review_fraction_threshold": review_fraction,
        "sources": SOURCE_URLS,
        "warnings": ["Counts refer to WRRL waterbody units, not whole physical rivers or lakes.",
                     "VG250 and WFD geometries have different generalization; boundary intersections are review candidates.",
                     "Reporting-state prefixes do not identify every spatial state. Transboundary flag refers to international borders.",
                     "WRRL inventory is not exhaustive for small ponds or brooks; source reporting thresholds apply."],
    }
    write_json(output / "inventory_summary.json", summary)
    hashes = [{"file": file.name, "bytes": file.stat().st_size,
               "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
              for file in sorted(directory.iterdir()) if file.is_file() and file.name != "replay_provenance.json"]
    write_json(output / "replay_provenance.json", {"source_snapshot": str(directory.resolve()),
        "created_at": datetime.now(timezone.utc).isoformat(), "source_hashes": hashes,
        "selection_method": "BKG VG250 state union by lkz; positive area/length intersection in EPSG25833",
        "bkg_attribution": "© BKG, dl-de/by-2-0; https://sgx.geodatenzentrum.de/web_public/gdz/datenquellen/datenquellen_vg_nuts.pdf"})
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay-from", type=Path, help="Use saved raw responses; performs no network requests")
    parser.add_argument("--states", nargs="+", default=TARGET_STATES)
    parser.add_argument("--review-fraction", type=float, default=.01)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if not 0 <= args.review_fraction <= 1:
        parser.error("--review-fraction must be between 0 and 1")
    targets = {state.upper() for state in args.states}
    args.output.mkdir(parents=True, exist_ok=True)
    directory = args.replay_from or args.output
    if args.replay_from is None:
        acquire(directory, args.timeout)
    summary = build(directory, args.output, targets, args.review_fraction)
    print(json.dumps({key: summary[key] for key in ("unique_ids", "selected_spatial_count", "selected_spatial_by_type", "spatial_additions_count")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
