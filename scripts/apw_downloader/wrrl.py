"""Read-only WFS acquisition. Raw service codes are NOT interpreted as measurements."""
from __future__ import annotations

import json
import logging
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urlencode

from common import Http, Snapshot, clean, write_csv, write_json

LOG = logging.getLogger("apw")
ENDPOINT = "https://maps.brandenburg.de/services/wfs/wrrl3bwz_wfs"
PREFIX = "wrrl_3_bwz_wfs:"
LAYERS = {
    "surface_stations": ("Messstellen_Oberflaechenwasserkoerper", "EU_CD_SM", "EU_CD_WB", "NAME_STN"),
    "groundwater_stations": ("Messstellen_Grundwasserkoerper", "EU_CD_GM", "EU_CD_GB", "NAME"),
    "rivers": ("Fliessgewaesserwasserkoerper", "EU_CD_RW", "EU_CD_RW", "S_NAME"),
    "lakes": ("Seewasserkoerper", "EU_CD_LW", "EU_CD_LW", "S_NAME"),
    "groundwater": ("Grundwasserkoerper", "EU_CD_GB_1", "EU_CD_GB_1", None),
}
STATUS_FIELDS = {"ECO_STAT": "ecological_status", "ECO_POT": "ecological_potential", "CHEM_STAT": "chemical_status"}


def url(request, **parameters):
    return ENDPOINT + "?" + urlencode({"service": "WFS", "version": "2.0.0", "request": request, **parameters})


def xml(data: bytes):
    root = ET.fromstring(data)
    if root.tag.rsplit("}", 1)[-1] in {"ExceptionReport", "ServiceExceptionReport"}:
        raise ValueError("WFS-Fehler: " + " ".join(root.itertext()))
    return root


def selection_scope(water_body_ids=None):
    identifiers = list(water_body_ids or [])
    if any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in identifiers):
        raise ValueError("Gewässer-IDs dürfen nicht leer sein oder äußere Leerzeichen enthalten.")
    identifiers = sorted(set(identifiers))
    return {"kind": "water_body_ids", "water_body_ids": identifiers} if identifiers else {"kind": "all"}


def filter_parameters(layer, water_body_ids=None):
    """Exact FES 2.0 equality; station layers use their water-body foreign key."""
    identifiers = selection_scope(water_body_ids).get("water_body_ids", [])
    if not identifiers:
        return {}
    ns = "{http://www.opengis.net/fes/2.0}"
    root = ET.Element(ns + "Filter")
    parent = ET.SubElement(root, ns + "Or") if len(identifiers) > 1 else root
    for identifier in identifiers:
        equality = ET.SubElement(parent, ns + "PropertyIsEqualTo")
        ET.SubElement(equality, ns + "ValueReference").text = LAYERS[layer][2]
        ET.SubElement(equality, ns + "Literal").text = identifier
    return {"filter": ET.tostring(root, encoding="unicode")}


def validate_selection(collection, layer, water_body_ids=None):
    if water_body_ids:
        field = LAYERS[layer][2]
        for feature in collection["features"]:
            if clean(feature["properties"].get(field)) not in water_body_ids:
                raise RuntimeError(f"{layer}: Antwort enthält ein Gewässer außerhalb des Filters ({field}).")
    return collection


def hits(http, layer, water_body_ids=None):
    result = xml(http.get(url("GetFeature", typeNames=PREFIX + LAYERS[layer][0], resultType="hits",
                              **filter_parameters(layer, water_body_ids))))
    value = result.get("numberMatched", "")
    if not value.isdigit():
        raise ValueError(f"Keine belastbare Gesamtanzahl für {layer}: {value!r}")
    return int(value)


def reference_date(capabilities):
    abstract = " ".join(n.text or "" for n in xml(capabilities).iter() if n.tag.endswith("}Abstract"))
    match = re.search(r"Stand(?: der Daten)?\s*:?\s*(\d{2}\.\d{2}\.\d{4})", abstract)
    return datetime.strptime(match[1], "%d.%m.%Y").date().isoformat() if match else None


def feature_id(feature):
    props = feature.get("properties") or {}
    value = feature.get("id")
    if value is None:
        value = props.get("GmlID")
    if value is None:
        value = props.get("OBJECTID_1", props.get("OBJECTID"))
    if value is None:
        raise ValueError("WFS-Feature ohne eindeutige Quellkennung.")
    return str(value)


def positions(coordinates):
    if not isinstance(coordinates, list):
        raise ValueError("Ungültige Geometriekoordinaten.")
    if coordinates and isinstance(coordinates[0], (int, float)):
        yield coordinates
    else:
        for part in coordinates:
            yield from positions(part)


def feature_collection(data: bytes):
    try:
        result = json.loads(data)
    except ValueError as exc:
        raise ValueError("Keine GeoJSON-Antwort: " + data[:500].decode("utf-8", errors="replace")) from exc
    if not isinstance(result, dict) or result.get("type") != "FeatureCollection" or not isinstance(result.get("features"), list):
        raise ValueError("Ungültige WFS-FeatureCollection.")
    crs = result.get("crs", {}).get("properties", {}).get("name")
    if crs not in {None, "EPSG:4326", "urn:ogc:def:crs:OGC:1.3:CRS84"}:
        raise ValueError(f"Unerwartetes GeoJSON-Koordinatensystem {crs!r}.")
    identifiers = set()
    for feature in result["features"]:
        if feature.get("type") != "Feature" or not isinstance(feature.get("properties"), dict):
            raise ValueError("Ungültiges WFS-Feature.")
        fid = feature_id(feature)
        if fid in identifiers:
            raise ValueError(f"Doppelte Quellkennung innerhalb einer Seite: {fid}")
        identifiers.add(fid)
        geometry = feature.get("geometry")
        if geometry:
            if geometry.get("type") not in {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}:
                raise ValueError("Nicht unterstützter Geometrietyp.")
            for point in positions(geometry.get("coordinates")):
                # Generous Brandenburg+neighbourhood envelope catches axis swaps.
                # The service changes axis order when srsName=...4326 is supplied!
                if len(point) < 2 or not all(math.isfinite(v) for v in point) or not (9 <= point[0] <= 17 and 49 <= point[1] <= 56):
                    raise ValueError(f"Koordinaten außerhalb des Prüfbereichs; CRS/Achsen prüfen: {point[:2]}")
    return result


def download(http: Http, snapshot: Snapshot, layers, page_size=500, max_features=None, water_body_ids=None):
    scope = selection_scope(water_body_ids)
    previous = snapshot.state.get("wrrl_scope")
    if previous is None and snapshot.state["wrrl"]:
        previous = {"kind": "all"}  # Manifests created before filters existed.
    if previous is not None and previous != scope:
        raise RuntimeError("WRRL-Gewässerfilter wurde geändert. Einen neuen --snapshot verwenden; alte Daten bleiben erhalten.")
    snapshot.state["wrrl_scope"] = scope
    snapshot.save()
    water_body_ids = scope.get("water_body_ids", [])
    LOG.info("WRRL-Auswahl: %s", ", ".join(water_body_ids) if water_body_ids else "alle Gewässer (kein räumlicher Filter)")
    cap_url = url("GetCapabilities")
    capabilities, _ = http.cached(snapshot, "wrrl:capabilities", "raw/wrrl/capabilities.xml", cap_url, xml)
    supported = {n.text for n in xml(capabilities).iter() if n.tag.endswith("}Name")}
    snapshot.state["wrrl_metadata"] = {"cycle": "2022-2027", "reference_date": reference_date(capabilities)}
    http.cached(snapshot, "wrrl:schema", "raw/wrrl/schema.xsd", url("DescribeFeatureType"), xml)
    for layer in layers:
        if PREFIX + LAYERS[layer][0] not in supported:
            raise ValueError(f"Datenebene fehlt in GetCapabilities: {layer}")
        state = snapshot.state["wrrl"].setdefault(layer, {"pages": [], "complete": False, "downloaded": 0})
        observed = hits(http, layer, water_body_ids)
        if "expected" in state and observed != state["expected"]:
            raise RuntimeError(f"{layer}: Bestand hat sich geändert ({state['expected']} -> {observed}). Neuen --snapshot verwenden.")
        state["expected"] = observed
        seen = set()
        start = 0
        for key in state["pages"]:
            cached = snapshot.cached(key)
            if not cached:
                raise RuntimeError("Manifest verweist auf eine unbekannte Seite.")
            collection = validate_selection(feature_collection(cached[0]), layer, water_body_ids)
            for feature in collection["features"]:
                fid = feature_id(feature)
                if fid in seen:
                    raise RuntimeError("Überlappende WFS-Seiten im Snapshot.")
                seen.add(fid)
            start += len(collection["features"])
        limit = min(observed, max_features) if max_features is not None else observed
        if start > observed:
            raise RuntimeError("Gespeicherte Seiten überschreiten die Gesamtanzahl.")
        while start < limit:
            count = min(page_size, limit - start)
            # Live verified: sortBy is rejected; supplying EPSG:4326 reverses axes.
            request_url = url("GetFeature", typeNames=PREFIX + LAYERS[layer][0],
                              count=count, startIndex=start, outputFormat="GEOJSON",
                              **filter_parameters(layer, water_body_ids))
            key = f"wrrl:{layer}:{start}:{count}"
            def validate_page(data):
                return validate_selection(feature_collection(data), layer, water_body_ids)
            data, _ = http.cached(snapshot, key, f"raw/wrrl/{layer}/{start:08d}_{count}.geojson", request_url, validate_page)
            collection = feature_collection(data)
            features = collection["features"]
            if not features or len(features) > count:
                raise RuntimeError(f"Unerwartete Seitengröße bei {layer}, Start {start}.")
            page_ids = {feature_id(f) for f in features}
            if seen & page_ids:
                raise RuntimeError(f"Überlappende WFS-Seiten bei {layer}; Abruf nicht als vollständig markiert.")
            seen.update(page_ids)
            state["pages"].append(key)
            start += len(features)
            state["downloaded"] = start
            state["complete"] = False
            snapshot.save()
            LOG.info("WRRL %-22s %s / %s (%.1f%%)", layer, start, observed, 100 * start / max(1, observed))
        if start == observed:
            if hits(http, layer, water_body_ids) != observed:
                raise RuntimeError(f"{layer}: Gesamtanzahl während des Abrufs geändert.")
            state["complete"] = True
        state["downloaded"] = start
        snapshot.save()
    export(snapshot)


def export(snapshot):
    bodies, stations, assessments = [], [], []
    scope = snapshot.state.get("wrrl_scope", {"kind": "all"})
    water_body_ids = scope.get("water_body_ids", [])
    coverage = {identifier: {layer: 0 for layer in snapshot.state["wrrl"]} for identifier in water_body_ids}
    for layer, state in snapshot.state["wrrl"].items():
        service_name, id_field, body_field, name_field = LAYERS[layer]
        for key in state["pages"]:
            data, meta = snapshot.cached(key)
            for feature in validate_selection(feature_collection(data), layer, water_body_ids)["features"]:
                props = feature["properties"]
                record_id = f"apw:wrrl3:{layer}:{feature_id(feature)}"
                row = {
                    "source_record_id": record_id, "snapshot": snapshot.state["snapshot"],
                    "layer": layer, "identifier": clean(props.get(id_field)) or None,
                    "water_body_id": clean(props.get(body_field)) or None,
                    "name": clean(props.get(name_field)) or None if name_field else None,
                    "geometry": feature.get("geometry"), "geometry_crs": "EPSG:4326",
                    "source_properties": props, "source_url": meta["source_url"],
                    "retrieved_at": meta["retrieved_at"], "source_sha256": meta["sha256"],
                    "layer_complete": state["complete"],
                }
                (stations if layer.endswith("stations") else bodies).append(row)
                if water_body_ids:
                    coverage[row["water_body_id"]][layer] += 1
                for field, kind in STATUS_FIELDS.items():
                    code = clean(props.get(field))
                    if code:
                        assessments.append({
                            "assessment_id": record_id + ":" + field, "water_body_id": row["water_body_id"],
                            "source_record_id": record_id, "snapshot": snapshot.state["snapshot"],
                            "assessment_type": kind, "source_field": field, "value_raw": code,
                            "is_valid": False if code == "999" else None, "label": None,
                            "review_reason": "sentinel_999" if code == "999" else "official_code_list_not_applied",
                            "cycle": "2022-2027", "dataset_reference_date": snapshot.state.get("wrrl_metadata", {}).get("reference_date"),
                            "source_url": meta["source_url"], "source_sha256": meta["sha256"],
                        })
    entity_fields = ["source_record_id", "snapshot", "layer", "identifier", "water_body_id", "name", "geometry", "geometry_crs", "source_properties", "source_url", "retrieved_at", "source_sha256", "layer_complete"]
    for name, rows in [("water_bodies", bodies), ("monitoring_stations", stations)]:
        write_csv(snapshot.root / "tables" / "wrrl" / f"{name}.csv", rows, entity_fields)
    write_csv(snapshot.root / "tables/wrrl/assessments.csv", assessments,
              ["assessment_id", "water_body_id", "source_record_id", "snapshot", "assessment_type", "source_field", "value_raw", "is_valid", "label", "review_reason", "cycle", "dataset_reference_date", "source_url", "source_sha256"])
    write_json(snapshot.root / "tables/wrrl/coverage.json", {
        "scope": "Selected published WRRL3 WFS feature types; not every APW theme, protection layer or time series.",
        "selection": scope, "water_body_coverage": coverage,
        "completeness_basis": "Counts and layer_complete refer to the selected IDs when filtered, not all Brandenburg records.",
        "layers": snapshot.state["wrrl"], "transactional_snapshot": False,
        "metadata": snapshot.state.get("wrrl_metadata"),
        "notes": "No label interpretation, no groundwater status invented, all original properties retained. Cached pages remain unchanged on resume.",
    })
