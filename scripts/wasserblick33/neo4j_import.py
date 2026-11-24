"""Project validated PostgreSQL rows into an isolated, immutable Neo4j graph.

The default is a read-only graph plan. --apply is required for Neo4j writes.
--bundle supports offline planning only: live imports always read PostgreSQL.
No entity matching is performed here and no old graphs are deleted.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent
GRAPH_MODEL_VERSION = 2
CONTRACT = json.loads((ROOT / "CONTRACT.json").read_text(encoding="utf-8"))
# Load the exact same JSON-number canonicalizer used for PostgreSQL round trips.
# Loading by file also works for standalone scripts and importlib-based tests.
_pg_spec = importlib.util.spec_from_file_location("_solve33_pg_canonical", ROOT / "pg_import.py")
_pg = importlib.util.module_from_spec(_pg_spec)
_pg_spec.loader.exec_module(_pg)
LABELS = {
    "dataset": "Dataset33", "raw_record": "RawRecord33",
    "water_body": "WaterBody33", "water_body_identifier": "WaterBodyIdentifier33",
    "monitoring_station": "MonitoringStation33", "water_body_assessment": "Assessment33",
    "lake": "Lake33", "match_candidate": "MatchCandidate33",
    "source_document": "SourceDocument33", "event": "Event33",
    "import_issue": "ImportIssue33",
}
VERSION_LABELS = {"water_body": "WaterBodyVersion33", "monitoring_station": "MonitoringStationVersion33"}
IDENTITY_LABELS = {"water_body": "ReportingUnit33", "monitoring_station": "MonitoringStationIdentity33"}
IDENTITY_NAMESPACES = {"water_body": "euSurfaceWaterBodyCode", "monitoring_station": "euMonitoringSiteCode"}
RELATION_TABLES = {"station_water_body", "lake_water_body", "event_evidence"}
ALLOWED_RELATIONS = {
    "FROM_DATASET", "FROM_RAW_RECORD", "HAS_IDENTIFIER", "MONITORS",
    "FOR_WATERBODY", "REPRESENTS", "PART_OF", "ABOUT_LAKE", "SUPPORTED_BY",
    "HAS_MATCH_CANDIDATE", "CANDIDATE_FOR", "HAS_VERSION",
}


def json_default(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        # Do not silently round precise SQL numeric values.
        return str(value)
    raise TypeError(f"Unsupported value type: {type(value).__name__}")


def canonical(value):
    return _pg.numeric_canonical(value)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def normalized_tables(tables):
    """Require the complete known table set and reject row/key ambiguity."""
    expected = set(CONTRACT["tables"])
    if set(tables) != expected:
        raise ValueError(f"Table set mismatch: missing={sorted(expected-set(tables))}; "
                         f"unknown={sorted(set(tables)-expected)}")
    normalized = {}
    for name, spec in CONTRACT["tables"].items():
        rows = tables[name]
        if not isinstance(rows, list):
            raise ValueError(f"{name}: rows must be an array")
        seen = set()
        for row in rows:
            if set(row) != set(spec["columns"]):
                raise ValueError(f"{name}: column set differs from CONTRACT.json")
            key = row[spec["pk"]]
            if not isinstance(key, str) or not key or key in seen:
                raise ValueError(f"{name}: missing or duplicate primary key")
            seen.add(key)
        normalized[name] = sorted(rows, key=lambda r: r[spec["pk"]])
    return normalized


def snapshot_id(tables):
    return digest({"graph_model_version": GRAPH_MODEL_VERSION, "tables": normalized_tables(tables)})


def node_key(run_id, table, primary_key):
    # Hash a JSON tuple rather than ambiguous concatenation of unescaped IDs.
    return run_id + ":" + digest([table, primary_key])


def row_properties(row):
    props = {"row_json": canonical(row)}
    for key, value in row.items():
        if value is None:
            continue  # Neo4j does not store null properties; row_json preserves nulls.
        if isinstance(value, (dict, list)):
            props[key + "_json"] = canonical(value)
        elif isinstance(value, bool):
            props[key] = value
        elif isinstance(value, (int, float, decimal.Decimal)):
            # A PostgreSQL JSONB number may become an expanded arbitrary-size
            # integer. Never coerce it to float or Neo4j's signed 64-bit integer.
            # The exact decimal token remains queryable as JSON text instead.
            props[key + "_json"] = canonical(value)
        elif isinstance(value, str):
            props[key] = value
        else:
            props[key] = json_default(value)
    return props


def version_properties(row, dataset):
    """Keep reporting time separate from retrieval and observation dates.

    A reporting cycle is a contextual period, not proof of a daily valid-time
    interval. Unavailable years stay absent; no day boundaries are invented.
    """
    result = {"valid_time_kind": "reporting_period_not_observation_date",
              "version_dataset_id": dataset["dataset_id"]}
    cycle = dataset.get("reporting_cycle")
    if cycle is not None:
        result["reporting_cycle"] = cycle
        match = re.fullmatch(r"\s*(\d{4})\s*[-–/]\s*(\d{4})\s*", cycle)
        if match:
            first, last = map(int, match.groups())
            if first > last:
                raise ValueError("Reporting cycle has reversed period years")
            result.update(period_start_year=first, period_end_year=last)
    metadata = dataset.get("metadata") or {}
    attrs = row.get("attributes") or {}
    year_sources = [metadata.get("report_year"), metadata.get("original_report_year"), attrs.get("cYear")]
    years = []
    for year in year_sources:
        if year is None:
            continue
        if isinstance(year, bool) or not re.fullmatch(r"\d{4}", str(year)):
            raise ValueError("Reported year must be an explicit four-digit year")
        years.append(int(year))
    if len(set(years)) > 1:
        raise ValueError("Dataset and feature report-year provenance disagree")
    if years:
        result["report_year"] = years[0]
    if dataset.get("retrieved_at") is not None:
        result["retrieved_at"] = dataset["retrieved_at"]
    return result


def build_graph(tables, run_id):
    """Return deterministic graph plan. All source rows survive as JSON properties.

    Every accepted FK is checked against its target. Candidate links are visibly
    separate from accepted lake links. Shared WRRL bodies never merge lake nodes.
    """
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
        raise ValueError("run_id must contain 1–128 ASCII letters, digits, _ or -")
    tables = normalized_tables(tables)
    nodes, edges = [], []
    lookup = {}
    datasets = {r["dataset_id"]: r for r in tables["dataset"]}
    for table, label in LABELS.items():
        pk = CONTRACT["tables"][table]["pk"]
        for row in tables[table]:
            key = node_key(run_id, table, row[pk])
            props = row_properties(row)
            props.update(key=key, run_id=run_id, source_table=table)
            node = {"key": key, "label": label, "properties": props}
            if table in VERSION_LABELS:
                if row["dataset_id"] not in datasets:
                    raise ValueError(f"Dangling dataset FK for {table} version")
                props.update(version_properties(row, datasets[row["dataset_id"]]))
                node["additional_labels"] = [VERSION_LABELS[table]]
            nodes.append(node)
            lookup[table, row[pk]] = key

    # One code identity can own multiple reporting-cycle/retrieval versions.
    # Identities retain a stable ID across runs, while graph keys stay scoped to
    # an immutable run so activating a new run never rewrites the old graph.
    identities = {}
    for table, identity_label in IDENTITY_LABELS.items():
        namespace = IDENTITY_NAMESPACES[table]
        pk = CONTRACT["tables"][table]["pk"]
        for row in tables[table]:
            code = row.get("eu_code")
            if code is None or code == "":
                continue  # No name-based station identities or fabricated codes.
            if not isinstance(code, str) or not code.strip():
                raise ValueError("Official identity code must be a nonempty string")
            identity_tuple = (namespace, code)
            if identity_tuple not in identities:
                identity_id = "identity:" + digest([namespace, code])
                key = node_key(run_id, "official_identifier_identity", identity_id)
                props = {"key": key, "run_id": run_id, "identity_id": identity_id,
                         "identifier_scheme": namespace, "eu_code": code,
                         "source_table": "derived_identifier_identity",
                         "equivalence_method": "exact_official_identifier_only",
                         "identity_scope": "identifier_identity_not_constant_spatial_extent"}
                nodes.append({"key": key, "label": identity_label, "properties": props})
                identities[identity_tuple] = key
            key = run_id + ":" + digest([table, row[pk], "has_version"])
            edges.append({"key": key, "type": "HAS_VERSION", "source": identities[identity_tuple],
                          "target": lookup[table, row[pk]], "properties": {
                              "key": key, "run_id": run_id, "source_table": table,
                              "source_primary_key": row[pk], "method": "exact_official_identifier_only"}})

    def edge(table, row, suffix, kind, a_table, a_id, b_table, b_id):
        if kind not in ALLOWED_RELATIONS:
            raise ValueError("Unknown relationship type")
        if (a_table, a_id) not in lookup or (b_table, b_id) not in lookup:
            raise ValueError(f"Dangling FK in {table}: {a_table} or {b_table} target missing")
        pk = CONTRACT["tables"][table]["pk"]
        key = run_id + ":" + digest([table, row[pk], suffix])
        props = row_properties(row) if table in RELATION_TABLES else {}
        props.update(key=key, run_id=run_id, source_table=table,
                     source_primary_key=row[pk])
        edges.append({"key": key, "type": kind, "source": lookup[a_table, a_id],
                      "target": lookup[b_table, b_id], "properties": props})

    for table in ["raw_record", "water_body", "monitoring_station"]:
        pk = CONTRACT["tables"][table]["pk"]
        for row in tables[table]:
            edge(table, row, "dataset", "FROM_DATASET", table, row[pk], "dataset", row["dataset_id"])
    for table in ["water_body", "monitoring_station", "water_body_assessment"]:
        pk = CONTRACT["tables"][table]["pk"]
        for row in tables[table]:
            if row["raw_id"] is not None:
                edge(table, row, "raw", "FROM_RAW_RECORD", table, row[pk], "raw_record", row["raw_id"])
    for row in tables["water_body_identifier"]:
        edge("water_body_identifier", row, "body", "HAS_IDENTIFIER", "water_body", row["water_body_id"],
             "water_body_identifier", row["identifier_id"])
    for row in tables["station_water_body"]:
        if row["raw_id"] is not None and ("raw_record", row["raw_id"]) not in lookup:
            raise ValueError("station_water_body: raw record reference missing")
        edge("station_water_body", row, "monitors", "MONITORS", "monitoring_station", row["station_id"],
             "water_body", row["water_body_id"])
    for row in tables["water_body_assessment"]:
        edge("water_body_assessment", row, "body", "FOR_WATERBODY", "water_body_assessment", row["assessment_id"],
             "water_body", row["water_body_id"])
    for row in tables["lake_water_body"]:
        relation = {"same_water_body": "REPRESENTS", "part_of": "PART_OF"}.get(row["relation_type"])
        if not relation:
            raise ValueError("lake_water_body: only accepted same_water_body/part_of allowed")
        edge("lake_water_body", row, "body", relation, "lake", row["lake_id"], "water_body", row["water_body_id"])
    for row in tables["match_candidate"]:
        if row["status"] not in {"auto_match", "review", "unmatched", "accepted", "rejected"}:
            raise ValueError("match_candidate: unsupported status")
        edge("match_candidate", row, "lake", "HAS_MATCH_CANDIDATE", "lake", row["lake_id"],
             "match_candidate", row["candidate_id"])
        if row["water_body_id"] is not None:
            edge("match_candidate", row, "candidate", "CANDIDATE_FOR", "match_candidate", row["candidate_id"],
                 "water_body", row["water_body_id"])
    for row in tables["event"]:
        edge("event", row, "lake", "ABOUT_LAKE", "event", row["event_id"], "lake", row["lake_id"])
    for row in tables["event_evidence"]:
        edge("event_evidence", row, "evidence", "SUPPORTED_BY", "event", row["event_id"],
             "source_document", row["source_id"])
    nodes.sort(key=lambda x: x["key"])
    edges.sort(key=lambda x: x["key"])
    if len({e["key"] for e in edges}) != len(edges):
        raise ValueError("Duplicate graph edge key")
    plan = {"format_version": GRAPH_MODEL_VERSION, "run_id": run_id, "nodes": nodes, "edges": edges,
            "table_counts": {t: len(r) for t, r in tables.items()},
            "node_counts": dict(sorted(collections.Counter(n["label"] for n in nodes).items())),
            "edge_counts": dict(sorted(collections.Counter(e["type"] for e in edges).items())),
            "additional_label_counts": dict(sorted(collections.Counter(
                label for n in nodes for label in n.get("additional_labels", [])).items())),
            "temporal_model": {"version_scope": "source dataset and reporting cycle",
                               "identity_rule": "exact official code only; no automatic equivalence for changed codes",
                               "reporting_period_is_not_observation_date": True,
                               "retrieval_time_is_separate": True}}
    plan["graph_sha256"] = digest({"nodes": nodes, "edges": edges})
    return plan


def validate_schema_name(schema):
    if not re.fullmatch(r"solve33_[a-z0-9_]{1,55}", schema):
        raise ValueError("Only dedicated solve33_* PostgreSQL schemas may be exported")
    return schema


def read_postgres(database_url, schema):
    validate_schema_name(schema)
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    tables = {}
    database_url = database_url.replace('postgresql+psycopg://', 'postgresql://', 1)
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=20) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor.execute(sql.SQL("SELECT bundle_sha256, counts, table_sha256 FROM {}._import_run WHERE singleton").format(
                sql.Identifier(schema)))
            metadata = cursor.fetchone()
            if metadata is None:
                raise ValueError("PostgreSQL schema has no validated import metadata")
            for table, spec in CONTRACT["tables"].items():
                query = sql.SQL("SELECT {} FROM {}.{} ORDER BY {}").format(
                    sql.SQL(",").join(map(sql.Identifier, spec["columns"])),
                    sql.Identifier(schema), sql.Identifier(table), sql.Identifier(spec["pk"]))
                cursor.execute(query)
                tables[table] = list(cursor.fetchall())
            if metadata["counts"] != {t: len(rows) for t, rows in tables.items()}:
                raise ValueError("PostgreSQL source row counts differ from validated import metadata")
            hashes = {t: _pg.table_digest(rows, CONTRACT["tables"][t]["pk"])
                      for t, rows in tables.items()}
            if metadata["table_sha256"] != hashes:
                raise ValueError("PostgreSQL source content differs from validated import fingerprint")
    return normalized_tables(tables)


def compare_actual_graph(plan, actual_nodes, actual_edges):
    """Verify complete properties/endpoints, not just counts or stored hashes."""
    expected_nodes = {n["key"]: n for n in plan["nodes"]}
    expected_edges = {e["key"]: e for e in plan["edges"]}
    if len(actual_nodes) != len(expected_nodes) or len(actual_edges) != len(expected_edges):
        raise ValueError("Neo4j node/edge counts differ from PostgreSQL graph plan")
    seen = set()
    for row in actual_nodes:
        key = row["key"]
        expected = expected_nodes.get(key)
        if key in seen or expected is None:
            raise ValueError("Neo4j node key mismatch")
        seen.add(key)
        labels = {"Solve33", expected["label"], *expected.get("additional_labels", [])}
        if set(row["labels"]) != labels or row["properties"] != expected["properties"]:
            raise ValueError("Neo4j node properties/labels differ from immutable source")
    seen = set()
    for row in actual_edges:
        key = row["key"]
        expected = expected_edges.get(key)
        if key in seen or expected is None:
            raise ValueError("Neo4j relationship key mismatch")
        seen.add(key)
        if any(row[k] != expected[k] for k in ["type", "source", "target", "properties"]):
            raise ValueError("Neo4j relationship differs from immutable source")


def _read_graph(tx, run_id):
    nodes = list(tx.run("MATCH (n:Solve33 {run_id:$run}) RETURN n.key AS key, labels(n) AS labels, "
                        "properties(n) AS properties", run=run_id))
    edges = list(tx.run("MATCH (a)-[r]->(b) WHERE r.run_id=$run RETURN r.key AS key, type(r) AS type, "
                        "a.key AS source, b.key AS target, properties(r) AS properties", run=run_id))
    return [dict(r) for r in nodes], [dict(r) for r in edges]


def import_transaction(tx, plan):
    """A single transaction serializes writers, validates, then activates the run."""
    run_id = plan["run_id"]
    # Writing the registry acquires a write lock before checking the snapshot.
    tx.run("MERGE (r:Solve33Registry {name:'wasserblick33'}) "
           "SET r.guard=coalesce(r.guard,0)+1 RETURN r.guard").consume()
    current = tx.run("MATCH (r:Solve33Run {run_id:$run}) RETURN r.graph_sha256 AS hash",
                     run=run_id).single()
    if current:
        if current["hash"] != plan["graph_sha256"]:
            raise ValueError("Existing Neo4j run has a different graph fingerprint")
        compare_actual_graph(plan, *_read_graph(tx, run_id))
    else:
        existing_nodes, existing_edges = _read_graph(tx, run_id)
        if existing_nodes or existing_edges:
            raise ValueError("Unregistered graph rows already use this run_id; refusing overwrite")
        for label in sorted(set(n["label"] for n in plan["nodes"])):
            assert label in {*LABELS.values(), *IDENTITY_LABELS.values()}
            additional_sets = {tuple(n.get("additional_labels", [])) for n in plan["nodes"] if n["label"] == label}
            if len(additional_sets) != 1:
                raise ValueError("Inconsistent version labels for one node class")
            additional = next(iter(additional_sets))
            if any(extra not in VERSION_LABELS.values() for extra in additional):
                raise ValueError("Unsupported version label")
            label_expression = ":".join(["Solve33", label, *additional])
            rows = [n["properties"] for n in plan["nodes"] if n["label"] == label]
            for start in range(0, len(rows), 500):
                tx.run(f"UNWIND $rows AS props CREATE (n:{label_expression}) SET n=props",
                       rows=rows[start:start+500]).consume()
        for kind in sorted(set(e["type"] for e in plan["edges"])):
            assert kind in ALLOWED_RELATIONS
            rows = [e for e in plan["edges"] if e["type"] == kind]
            for start in range(0, len(rows), 500):
                batch = rows[start:start+500]
                result = tx.run(f"UNWIND $rows AS row MATCH (a:Solve33 {{key:row.source}}) "
                                f"MATCH (b:Solve33 {{key:row.target}}) CREATE (a)-[r:{kind}]->(b) "
                                "SET r=row.properties RETURN count(r) AS n", rows=batch).single()
                if result["n"] != len(batch):
                    raise ValueError("Not every PostgreSQL relationship found both graph endpoints")
        compare_actual_graph(plan, *_read_graph(tx, run_id))
        tx.run("CREATE (r:Solve33Run {run_id:$run, graph_sha256:$hash, "
               "table_counts_json:$counts, imported_at:datetime()})",
               run=run_id, hash=plan["graph_sha256"], counts=canonical(plan["table_counts"])).consume()
    tx.run("MATCH (r:Solve33Registry {name:'wasserblick33'}) SET r.active_run_id=$run",
           run=run_id).consume()
    return {"run_id": run_id, "graph_sha256": plan["graph_sha256"],
            "nodes": len(plan["nodes"]), "relationships": len(plan["edges"]),
            "status": "already_present_verified" if current else "imported_verified"}


def apply_graph(plan, uri, user, password, database):
    from neo4j import GraphDatabase
    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            for name, label, prop in [("solve33_node_key", "Solve33", "key"),
                                      ("solve33_run_id", "Solve33Run", "run_id"),
                                      ("solve33_registry_name", "Solve33Registry", "name")]:
                session.run(f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE").consume()
            return session.execute_write(import_transaction, plan)


def load_env(path):
    if path:
        from dotenv import load_dotenv
        if not Path(path).is_file():
            raise ValueError("Requested env file does not exist")
        load_dotenv(path, override=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--schema", help="Validated PostgreSQL schema to read")
    source.add_argument("--bundle", type=Path, help="Offline graph plan only; never a live Neo4j import")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output", type=Path, help="Write complete JSON graph plan")
    parser.add_argument("--apply", action="store_true", help="Atomically create/validate/activate Neo4j snapshot")
    args = parser.parse_args(argv)
    if args.apply and args.bundle:
        parser.error("--apply requires --schema: live graphs must read PostgreSQL")
    load_env(args.env_file)
    if args.bundle:
        bundle = json.loads(args.bundle.read_text(encoding="utf-8-sig"))
        tables = bundle["tables"]
    else:
        if not os.getenv("SOLVE_DATABASE_URL"):
            raise ValueError("SOLVE_DATABASE_URL is required")
        tables = read_postgres(os.environ["SOLVE_DATABASE_URL"], args.schema)
    plan = build_graph(tables, snapshot_id(tables))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")
    result = {"run_id": plan["run_id"], "graph_sha256": plan["graph_sha256"],
              "nodes": len(plan["nodes"]), "relationships": len(plan["edges"]), "status": "plan_only"}
    if args.apply:
        required = ["NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE"]
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise ValueError("Missing configuration: " + ", ".join(missing))
        result = apply_graph(plan, *(os.environ[name] for name in required))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Driver errors can contain connection details. Keep credentials out of CLI logs.
        if isinstance(exc, (ValueError, FileNotFoundError, KeyError, ImportError)):
            print(f"Import failed: {exc}", file=sys.stderr)
        else:
            print(f"Import failed ({type(exc).__name__}); verify server availability and configuration.", file=sys.stderr)
        sys.exit(1)
