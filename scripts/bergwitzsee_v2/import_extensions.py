"""Add Bergwitzsee's researched events and historical versions to original V2.

No dummy measurements or other lakes are imported. This module preserves all
source rows, and creates direct WaterBodyV2-[:HAS_EVENT]->Event relationships.
The existing V2 tables and their measurement/assessment schema remain intact.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

WATER_BODY_ID = "DELW_DEST_EL03OW07-00"
LAKE_ID = "lake:da36285e4951d0d6a02fd80c"
NODE_TABLES = {
    "dataset": ("Dataset", "dataset_id"),
    "raw_record": ("RawRecord", "raw_id"),
    "source_document": ("SourceDocument", "source_id"),
    "event": ("Event", "event_id"),
    "event_evidence": ("EventEvidence", "evidence_id"),
    "water_body": ("WaterBodyVersion", "water_body_id"),
    "monitoring_station": ("MonitoringStationVersion", "station_id"),
    "import_issue": ("ImportIssue", "issue_id"),
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _props(row: dict) -> dict:
    props = {"row_json": _json(row)}
    for key, value in row.items():
        if isinstance(value, (dict, list)):
            props[key + "_json"] = _json(value)
        elif value is not None:
            props[key] = value
    return props


def build_import_plan(extension: dict) -> dict:
    """Build a parameterized import plan, without accessing Neo4j."""
    target = extension["target"]
    if target["water_body_id"] != WATER_BODY_ID or target["lake_id"] != LAKE_ID:
        raise ValueError("This importer only accepts the reviewed Bergwitzsee identity")
    tables = extension["tables"]
    event_ids = {r["event_id"] for r in tables["event"]}
    if len(event_ids) != len(tables["event"]) or any(r["lake_id"] != LAKE_ID for r in tables["event"]):
        raise ValueError("Events have duplicate IDs or belong to another lake")
    direct = extension["direct_event_links"]
    if ({r["event_id"] for r in direct} != event_ids or len(direct) != len(event_ids)
            or any(r["water_body_id"] != WATER_BODY_ID for r in direct)):
        raise ValueError("Direct WaterBodyV2/Event relationships are incomplete or ambiguous")
    datasets = {r["dataset_id"]: r for r in tables["dataset"]}
    nodes = {}
    for table, (label, key) in NODE_TABLES.items():
        nodes[table] = []
        for row in tables[table]:
            props = _props(row)
            props["extension_target"] = WATER_BODY_ID
            if table in ("water_body", "monitoring_station"):
                dataset = datasets[row["dataset_id"]]
                props["reporting_cycle"] = dataset["reporting_cycle"]
                props["valid_time_kind"] = "reporting_period_not_observation_date"
                props["retrieved_at"] = dataset.get("retrieved_at")
                if table == "water_body":
                    props["version_id"] = row["water_body_id"]
                    props["original_identifiers_json"] = _json([r for r in tables["water_body_identifier"]
                                                                if r["water_body_id"] == row[key]])
                    props["official_mappings_json"] = _json([r for r in tables["lake_water_body"]
                                                             if r["water_body_id"] == row[key]])
                    props["match_candidates_json"] = _json([r for r in tables["match_candidate"]
                                                            if r["water_body_id"] == row[key]])
            if table == "event":
                payload = row["payload"]
                props["review_status"] = (payload.get("quality_assessment") or {}).get("review_status")
                props["assignment_status"] = (payload.get("waterbody_assignment") or {}).get("status")
                props["human_review"] = payload.get("human_review")
                props["categories"] = payload.get("categories", [])
                props["source_url"] = payload.get("quellen_url")
                props["entry_semantics"] = "source_extraction_entry_see_record_type"
                props["payload_sha256"] = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
            if table == "event_evidence":
                props["quote"] = row["payload"].get("quote")
                props["source_url"] = row["payload"].get("url")
            nodes[table].append({"id": row[key], "props": props})
    return {"target": WATER_BODY_ID, "nodes": nodes,
            "direct_event_links": [{"event_id": r["event_id"], "props": _props(r)} for r in direct],
            "tables": tables,
            "lake_json": _json(tables["lake"][0]),
            "audit_json": _json(extension["audit"]),
            "provenance_json": _json(extension.get("bundle_provenance"))}


def import_extensions(driver, database: str, extension: dict) -> dict:
    """Import and validate atomically using an already authenticated driver.

    The WaterBodyV2 and all normalized WaterBodyAssessmentV2 rows must exist.
    Failure in an FK check or source round-trip rolls the extension back.
    """
    plan = build_import_plan(extension)
    tables = plan["tables"]
    with driver.session(database=database) as session:
        # Fixed labels/property names, never derived from untrusted source text.
        for table, (label, key) in NODE_TABLES.items():
            session.run(f"CREATE CONSTRAINT bergwitz_ext_{table}_id IF NOT EXISTS "
                        f"FOR (n:{label}) REQUIRE n.{key} IS UNIQUE").consume()

        def apply(tx):
            base_count = tx.run("MATCH (w:WaterBodyV2 {water_body_id:$id}) RETURN count(w) AS n",
                                id=WATER_BODY_ID).single()["n"]
            if base_count != 1:
                raise ValueError(f"Expected exactly one original V2 Bergwitzsee; found {base_count}")
            assessment_ids = [r["assessment_id"] for r in tables["water_body_assessment"]]
            assessments = list(tx.run(
                "MATCH (a:WaterBodyAssessmentV2) WHERE a.source_assessment_id IN $ids "
                "RETURN a.source_assessment_id AS id, a.source_version_id AS version",
                ids=assessment_ids))
            expected_assessments = {r["assessment_id"]: r["water_body_id"]
                                    for r in tables["water_body_assessment"]}
            if (len(assessments) != len(assessment_ids)
                    or {r["id"]: r["version"] for r in assessments} != expected_assessments):
                raise ValueError("Original V2 assessment rows are missing, duplicate, or reference a different version")
            for table, (label, key) in NODE_TABLES.items():
                tx.run(f"UNWIND $rows AS row MERGE (n:{label} {{{key}:row.id}}) SET n += row.props",
                       rows=plan["nodes"][table]).consume()
            tx.run("MATCH (w:WaterBodyV2 {water_body_id:$id}) "
                   "SET w.event_extension_lake_json=$lake, w.event_extension_audit_json=$audit, "
                   "w.event_extension_provenance_json=$provenance",
                   id=WATER_BODY_ID, lake=plan["lake_json"], audit=plan["audit_json"],
                   provenance=plan["provenance_json"]).consume()
            tx.run("UNWIND $rows AS row MATCH (w:WaterBodyV2 {water_body_id:$id}) "
                   "MATCH (e:Event {event_id:row.event_id}) MERGE (w)-[r:HAS_EVENT]->(e) SET r += row.props",
                   id=WATER_BODY_ID, rows=plan["direct_event_links"]).consume()
            tx.run("UNWIND $rows AS row MATCH (e:Event {event_id:row.event_id}) "
                   "MATCH (v:EventEvidence {evidence_id:row.evidence_id}) "
                   "MATCH (d:SourceDocument {source_id:row.source_id}) "
                   "MERGE (e)-[:HAS_EVIDENCE]->(v) MERGE (v)-[:FROM_DOCUMENT]->(d) "
                   "MERGE (e)-[r:SUPPORTED_BY {evidence_id:row.evidence_id}]->(d) "
                   "SET r.locator=row.locator",
                   rows=tables["event_evidence"]).consume()
            tx.run("UNWIND $rows AS row MATCH (w:WaterBodyV2 {water_body_id:$id}) "
                   "MATCH (v:WaterBodyVersion {water_body_id:row.water_body_id}) "
                   "MERGE (w)-[r:HAS_VERSION]->(v) "
                   "SET r.method='reviewed_official_mapping', r.identity_scope='reporting_unit_identity_not_constant_geometry'",
                   id=WATER_BODY_ID, rows=tables["water_body"]).consume()
            tx.run("UNWIND $rows AS row MATCH (v:WaterBodyVersion {water_body_id:row.water_body_id}) "
                   "MATCH (a:WaterBodyAssessmentV2 {source_assessment_id:row.assessment_id}) "
                   "MERGE (v)-[:HAS_ASSESSMENT]->(a) SET a.source_row_json=row.row_json",
                   rows=[{**row, "row_json": _json(row)} for row in tables["water_body_assessment"]]).consume()
            tx.run("UNWIND $rows AS row MATCH (s:MonitoringStationVersion {station_id:row.station_id}) "
                   "MATCH (v:WaterBodyVersion {water_body_id:row.water_body_id}) "
                   "MERGE (s)-[r:MONITORS {link_id:row.link_id}]->(v) "
                   "SET r.method=row.method, r.source_code=row.source_code, r.row_json=row.row_json",
                   rows=[{**row, "row_json": _json(row)} for row in tables["station_water_body"]]).consume()
            for table, (label, key) in NODE_TABLES.items():
                rows = tables[table]
                dataset_rows = [{"id": r[key], "dataset_id": r["dataset_id"]}
                                for r in rows if r.get("dataset_id")]
                raw_rows = [{"id": r[key], "raw_id": r["raw_id"]}
                            for r in rows if r.get("raw_id") and table != "raw_record"]
                if dataset_rows:
                    tx.run(f"UNWIND $rows AS row MATCH (n:{label} {{{key}:row.id}}) "
                           "MATCH (d:Dataset {dataset_id:row.dataset_id}) MERGE (n)-[:FROM_DATASET]->(d)",
                           rows=dataset_rows).consume()
                if raw_rows:
                    tx.run(f"UNWIND $rows AS row MATCH (n:{label} {{{key}:row.id}}) "
                           "MATCH (r:RawRecord {raw_id:row.raw_id}) MERGE (n)-[:FROM_RAW_RECORD]->(r)",
                           rows=raw_rows).consume()
            tx.run("UNWIND $rows AS row MATCH (a:WaterBodyAssessmentV2 {source_assessment_id:row.assessment_id}) "
                   "MATCH (r:RawRecord {raw_id:row.raw_id}) MERGE (a)-[:FROM_RAW_RECORD]->(r)",
                   rows=tables["water_body_assessment"]).consume()
            tx.run("UNWIND $rows AS row MATCH (w:WaterBodyV2 {water_body_id:$id}) "
                   "MATCH (i:ImportIssue {issue_id:row.issue_id}) MERGE (w)-[:HAS_IMPORT_ISSUE]->(i)",
                   id=WATER_BODY_ID, rows=tables["import_issue"]).consume()

            counts = {}
            for table, (label, key) in NODE_TABLES.items():
                records = list(tx.run(f"MATCH (n:{label}) WHERE n.{key} IN $ids "
                                      f"RETURN n.{key} AS id, n.row_json AS row_json",
                                      ids=[r[key] for r in tables[table]]))
                expected = {r[key]: r for r in tables[table]}
                if len(records) != len(expected) or any(json.loads(r["row_json"]) != expected[r["id"]] for r in records):
                    raise ValueError(f"Source row round-trip failed for {table}")
                counts[label] = len(records)
            direct_records = list(tx.run(
                "MATCH (:WaterBodyV2 {water_body_id:$id})-[r:HAS_EVENT]->(e:Event) "
                "WHERE e.event_id IN $event_ids RETURN e.event_id AS id, r.assignment_status AS status",
                id=WATER_BODY_ID, event_ids=list({r["event_id"] for r in tables["event"]})))
            expected_direct = {r["event_id"]: r["props"].get("assignment_status") for r in plan["direct_event_links"]}
            if len(direct_records) != len(expected_direct) or {r["id"]: r["status"] for r in direct_records} != expected_direct:
                raise ValueError("Direct WaterBodyV2/Event relationships failed validation")
            version_assessment_count = tx.run(
                "MATCH (v:WaterBodyVersion)-[:HAS_ASSESSMENT]->(a:WaterBodyAssessmentV2) "
                "WHERE v.water_body_id IN $versions AND a.source_assessment_id IN $assessments "
                "RETURN count(*) AS n", versions=[r["water_body_id"] for r in tables["water_body"]],
                assessments=assessment_ids).single()["n"]
            if version_assessment_count != len(assessment_ids):
                raise ValueError("Historical assessment/version relationships are incomplete")
            evidence_paths = tx.run(
                "MATCH (e:Event)-[:HAS_EVIDENCE]->(v:EventEvidence)-[:FROM_DOCUMENT]->(d:SourceDocument) "
                "WHERE v.evidence_id IN $ids RETURN count(*) AS n",
                ids=[r["evidence_id"] for r in tables["event_evidence"]]).single()["n"]
            if evidence_paths != len(tables["event_evidence"]):
                raise ValueError("Event evidence/document relationships are incomplete")
            version_links = tx.run(
                "MATCH (:WaterBodyV2 {water_body_id:$id})-[:HAS_VERSION]->(v:WaterBodyVersion) "
                "WHERE v.water_body_id IN $versions RETURN count(*) AS n", id=WATER_BODY_ID,
                versions=[r["water_body_id"] for r in tables["water_body"]]).single()["n"]
            if version_links != len(tables["water_body"]):
                raise ValueError("WaterBodyV2/historical version relationships are incomplete")
            station_links = tx.run(
                "MATCH (:MonitoringStationVersion)-[r:MONITORS]->(:WaterBodyVersion) "
                "WHERE r.link_id IN $ids RETURN count(*) AS n",
                ids=[r["link_id"] for r in tables["station_water_body"]]).single()["n"]
            if station_links != len(tables["station_water_body"]):
                raise ValueError("Historical station/water-body links are incomplete")
            return {"validated": True, "node_counts": counts,
                    "direct_water_body_event_links": len(direct_records),
                    "version_assessment_links": version_assessment_count,
                    "evidence_document_paths": evidence_paths,
                    "historical_version_links": version_links,
                    "historical_station_links": station_links,
                    "source_rows_roundtrip_verified": sum(counts.values()),
                    "event_payloads_preserved": len(tables["event"]),
                    "uncertain_event_assignments": sum(r["status"] == "wahrscheinlich" for r in direct_records)}

        return session.execute_write(apply)
