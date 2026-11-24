"""Select Bergwitzsee's source-preserving extension of the original V2 model.

This module never writes to a database.  The official reporting-unit mapping and
the certainty of each researched statement are distinct: an accepted WRRL code
does not make all event assignments or statements verified.  Dates remain source
strings with their original precision; publication dates are not event dates.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any
import unittest

LAKE_ID = "lake:da36285e4951d0d6a02fd80c"
WATER_BODY_ID = "DELW_DEST_EL03OW07-00"
TABLE_KEYS = {
    "dataset": "dataset_id", "raw_record": "raw_id",
    "water_body": "water_body_id", "water_body_identifier": "identifier_id",
    "monitoring_station": "station_id", "station_water_body": "link_id",
    "water_body_assessment": "assessment_id", "lake": "lake_id",
    "lake_water_body": "link_id", "match_candidate": "candidate_id",
    "source_document": "source_id", "event": "event_id",
    "event_evidence": "evidence_id", "import_issue": "issue_id",
}


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _index(rows: list[dict], key: str) -> dict:
    index = {row[key]: row for row in rows}
    if len(index) != len(rows):
        raise ValueError(f"Duplicate primary key: {key}")
    return index


def _require_references(ids: set, index: dict, name: str) -> None:
    missing = ids - index.keys()
    if missing:
        raise ValueError(f"Unresolved {name}: {sorted(missing)}")


def _distribution(values) -> dict[str, int]:
    return dict(sorted(Counter("<missing>" if v is None else str(v)
                               for v in values).items()))


def _build_extension(bundle: dict) -> dict:
    tables = bundle["tables"]
    indexes = {table: _index(tables[table], key)
               for table, key in TABLE_KEYS.items()}
    if LAKE_ID not in indexes["lake"]:
        raise ValueError(f"Required exact lake_id absent: {LAKE_ID}")
    lake = indexes["lake"][LAKE_ID]
    if lake["name"] != "Bergwitzsee":
        raise ValueError("Expected lake_id now belongs to a different lake")

    # No matching on names, fuzzy identifiers, payload text, or event prefixes.
    selected = {table: [] for table in TABLE_KEYS}
    selected["lake"] = [lake]
    selected["event"] = [r for r in tables["event"] if r["lake_id"] == LAKE_ID]
    if not selected["event"]:
        raise ValueError("Bergwitzsee has no event rows")
    event_ids = {r["event_id"] for r in selected["event"]}
    selected["event_evidence"] = [r for r in tables["event_evidence"]
                                  if r["event_id"] in event_ids]
    if event_ids - {r["event_id"] for r in selected["event_evidence"]}:
        raise ValueError("An event is missing its source evidence")
    source_ids = {r["source_id"] for r in selected["event_evidence"]}
    _require_references(source_ids, indexes["source_document"], "source_document")
    selected["source_document"] = [r for r in tables["source_document"]
                                    if r["source_id"] in source_ids]

    selected["lake_water_body"] = [r for r in tables["lake_water_body"]
                                    if r["lake_id"] == LAKE_ID]
    if not selected["lake_water_body"]:
        raise ValueError("No reviewed official water-body mapping")
    for link in selected["lake_water_body"]:
        if (link["relation_type"] != "same_water_body"
                or link["method"] != "reviewed_official_mapping"):
            raise ValueError("Bergwitzsee requires reviewed same_water_body mapping")
    water_ids = {r["water_body_id"] for r in selected["lake_water_body"]}
    _require_references(water_ids, indexes["water_body"], "water_body")
    selected["water_body"] = [r for r in tables["water_body"]
                              if r["water_body_id"] in water_ids]
    for row in selected["water_body"]:
        if row["eu_code"] != WATER_BODY_ID or row["category"] != "LW":
            raise ValueError("Reviewed mapping resolves to a different reporting unit")
    for table in ("water_body_identifier", "water_body_assessment", "station_water_body"):
        selected[table] = [r for r in tables[table] if r["water_body_id"] in water_ids]
    selected["match_candidate"] = [r for r in tables["match_candidate"]
                                    if r["lake_id"] == LAKE_ID]
    station_ids = {r["station_id"] for r in selected["station_water_body"]}
    _require_references(station_ids, indexes["monitoring_station"], "monitoring_station")
    selected["monitoring_station"] = [r for r in tables["monitoring_station"]
                                      if r["station_id"] in station_ids]
    raw_ids = {r["raw_id"] for rows in selected.values() for r in rows if r.get("raw_id")}
    _require_references(raw_ids, indexes["raw_record"], "raw_record")
    selected["raw_record"] = [r for r in tables["raw_record"] if r["raw_id"] in raw_ids]
    dataset_ids = {r["dataset_id"] for rows in selected.values() for r in rows
                   if r.get("dataset_id")}
    _require_references(dataset_ids, indexes["dataset"], "dataset")
    selected["dataset"] = [r for r in tables["dataset"] if r["dataset_id"] in dataset_ids]
    entity_ids = {r[TABLE_KEYS[table]] for table, rows in selected.items() for r in rows}
    selected["import_issue"] = [r for r in tables["import_issue"]
                                if r["entity_id"] in entity_ids]

    events = selected["event"]
    # Give the importer direct edges with source-level uncertainty.  The reviewed
    # identity mapping must never be confused with a reviewed event assertion.
    edges = []
    for row in events:
        payload = row["payload"]
        if payload.get("event_id") != row["event_id"]:
            raise ValueError("Event payload ID differs from its normalized primary key")
        assignment = payload.get("waterbody_assignment") or {}
        quality = payload.get("quality_assessment") or {}
        edges.append({
            "water_body_id": WATER_BODY_ID,
            "event_id": row["event_id"],
            "relation_type": "HAS_EVENT",
            "method": "exact_lake_id_and_reviewed_official_mapping",
            "assignment_status": assignment.get("status"),
            "assignment_reason": assignment.get("reason"),
            "review_status": quality.get("review_status"),
            "human_review": payload.get("human_review"),
        })
    audit = {
        "counts": {name: len(rows) for name, rows in selected.items()},
        "record_types": _distribution(r["record_type"] for r in events),
        "time_precisions": _distribution(r["time_precision"] for r in events),
        "review_statuses": _distribution((r["payload"].get("quality_assessment") or {})
                                         .get("review_status") for r in events),
        "human_review_values": _distribution(r["payload"].get("human_review") for r in events),
        "assignment_statuses": _distribution((r["payload"].get("waterbody_assignment") or {})
                                              .get("status") for r in events),
        "uncertain_assignment_event_ids": [edge["event_id"] for edge in edges
                                            if edge["assignment_status"] == "wahrscheinlich"],
        "event_payload_sha256": {r["event_id"]: _digest(r["payload"]) for r in events},
        "event_payload_fields": sorted(set().union(*(r["payload"] for r in events))),
        "date_policy": "Keep reported strings and precision. Do not turn years/durations into dates or use publication dates as occurrence dates.",
        "interpretation": "Event is the extraction-entry label; record_type distinguishes measurements, plans, states and events. All source uncertainty remains attached. No causal relation or human acceptance is inferred.",
    }
    return copy.deepcopy({
        "extension_version": "bergwitzsee-v2-events/1",
        "target": {"lake_id": LAKE_ID, "water_body_id": WATER_BODY_ID, "name": "Bergwitzsee"},
        "tables": selected,
        "direct_event_links": edges,
        "schema": {"water_body_label": "WaterBodyV2", "event_label": "Event",
                   "direct_event_relationship": "HAS_EVENT"},
        "audit": audit,
        "bundle_provenance": bundle.get("provenance"),
    })


def build_extension(bundle_path: str | Path) -> dict:
    """Return exact-ID-filtered original rows plus audited direct Event edges."""
    path = Path(bundle_path)
    raw = path.read_bytes()
    bundle = json.loads(raw.decode("utf-8-sig"))
    result = _build_extension(bundle)
    result["audit"]["bundle_path"] = str(path.resolve())
    result["audit"]["bundle_sha256"] = hashlib.sha256(raw).hexdigest()
    # The normalized bundle retains each original researched payload verbatim.
    # Cross-check the original input as well when available beside the bundle.
    input_path = path.parent.parent / "inputs" / "events.json"
    if input_path.is_file():
        original = json.loads(input_path.read_text(encoding="utf-8-sig"))
        original_index = _index(original, "event_id")
        for row in result["tables"]["event"]:
            if original_index.get(row["event_id"]) != row["payload"]:
                raise ValueError(f"Source payload changed: {row['event_id']}")
        result["audit"]["original_event_input"] = str(input_path.resolve())
        result["audit"]["original_payloads_verified"] = len(result["tables"]["event"])
    return result


def run_self_tests(bundle_path: str | Path) -> unittest.TestResult:
    """Integration checks against the acquired bundle plus adversarial ID checks."""
    bundle = json.loads(Path(bundle_path).read_text(encoding="utf-8-sig"))

    class ExtensionTests(unittest.TestCase):
        def test_known_source_counts_and_payloads(self):
            result = build_extension(bundle_path)
            for table, count in {"lake": 1, "event": 73, "event_evidence": 79,
                                 "source_document": 15, "water_body": 3,
                                 "water_body_assessment": 48, "monitoring_station": 8}.items():
                self.assertEqual(count, len(result["tables"][table]), table)
            for table, rows in result["tables"].items():
                source_index = _index(bundle["tables"][table], TABLE_KEYS[table])
                for row in rows:
                    self.assertEqual(source_index[row[TABLE_KEYS[table]]], row)
            self.assertEqual(73, len(result["direct_event_links"]))
            self.assertEqual({WATER_BODY_ID}, {r["water_body_id"] for r in result["direct_event_links"]})
            self.assertEqual({"BW-WASSERHALTUNG-1955", "BW-GRUNDWASSER-1959"},
                             set(result["audit"]["uncertain_assignment_event_ids"]))

        def test_exact_lake_id_even_if_names_are_identical(self):
            mutated = copy.deepcopy(bundle)
            false_match = copy.deepcopy(next(r for r in mutated["tables"]["event"]
                                             if r["lake_id"] == LAKE_ID))
            false_match["event_id"] = "OTHER-LAKE-WITH-SAME-NAME"
            false_match["payload"]["event_id"] = false_match["event_id"]
            false_match["lake_id"] = "lake:other"
            mutated["tables"]["event"].append(false_match)
            result = _build_extension(mutated)
            self.assertNotIn(false_match["event_id"], {r["event_id"] for r in result["tables"]["event"]})
            self.assertEqual(73, len(result["tables"]["event"]))

        def test_missing_source_rejected(self):
            mutated = copy.deepcopy(bundle)
            mutated["tables"]["source_document"] = [r for r in mutated["tables"]["source_document"]
                                                      if r["source_id"] != "S04"]
            with self.assertRaisesRegex(ValueError, "Unresolved source_document"):
                _build_extension(mutated)

        def test_wrong_official_identity_rejected(self):
            mutated = copy.deepcopy(bundle)
            water_id = next(r["water_body_id"] for r in mutated["tables"]["lake_water_body"]
                            if r["lake_id"] == LAKE_ID)
            next(r for r in mutated["tables"]["water_body"]
                 if r["water_body_id"] == water_id)["eu_code"] = "WRONG_CODE"
            with self.assertRaisesRegex(ValueError, "different reporting unit"):
                _build_extension(mutated)

    return unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ExtensionTests))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test and not run_self_tests(args.bundle).wasSuccessful():
        return 1
    result = build_extension(args.bundle)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["audit"], ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
