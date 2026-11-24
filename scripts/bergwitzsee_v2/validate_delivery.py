"""Read-only, independent source-to-PostgreSQL-to-Neo4j delivery audit.

Expected observations are reconstructed directly from the acquired CSV exports,
not from the build adapter. No source data, database, or graph is modified.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import json
import math
import re
import sys
import io
import zipfile
from collections import Counter
from pathlib import Path

from dotenv import dotenv_values
from neo4j import GraphDatabase
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/bergwitzsee_neo4j_v2_events_20261101"
SOURCES = ROOT / "outputs/bergwitzsee_lhw_20261101/prepared"
WATER_ID = "DELW_DEST_EL03OW07-00"


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def number(value):
    return None if value in (None, "") else float(str(value).replace(",", "."))


def key(row):
    return (row["source_archive"], row["source_member"],
            int(row["source_csv_record"]), str(row["source_column"]))


def build_expected(source):
    expected = {}
    counts = Counter()
    definitions = [
        ("einzeldaten_wasser.csv", None),
        ("einzeldaten_sediment.csv", None),
        ("phytoplankton_Messreihen.csv", ("Zellzahl", "Biovolumen")),
        ("makrophyten_diatomeen_Messreihen.csv", ("MP_HK", "Dia_abs", "Dia_%")),
        ("makrozoobenthos_Messreihen.csv", ("IZ",)),
    ]
    for filename, metrics in definitions:
        for row in read_csv(source / filename):
            for metric in metrics or (None,):
                if metric is not None and not row[metric]:
                    continue
                k = (row["source_archive"], row["source_member"],
                     int(row["source_csv_record"]), metric or row["source_column"])
                if k in expected:
                    raise ValueError(f"Duplicate input cell: {k}")
                expected[k] = {
                    "payload": row,
                    "value": number(row[metric]) if metric else number(row["WERT_exakt"]),
                    "limit": None if metric else number(row["Grenzwert"]),
                    "raw": row[metric] if metric else row["WERT_original"],
                    "comparison": "=" if metric else row["Vergleichszeichen"],
                    "unit": ("%" if metric == "Dia_%" else None) if metric else row["EINHEIT"],
                    "parameter": row["Taxon"] if metric else row["Parameterbezeichnung"],
                    "abundance_type": metric,
                    "date_original": row["Datum"] if metric else row["DATUM_original"],
                    "date": dt.datetime.strptime(row["Datum"] if metric else row["DATUM_original"],
                                                 "%d.%m.%Y %H:%M:%S").date().isoformat(),
                }
                counts[filename] += 1
    return expected, dict(counts)


def audit_raw_exports(audit, source, expected):
    """Cross-check normalized locators and cells against the untouched ZIPs."""
    members = {}
    mismatches = []
    raw_cells = set()
    for k, exp in expected.items():
        archive, member, row_number, column = k
        if (archive, member) not in members:
            with zipfile.ZipFile(source.parent / "raw" / archive) as z:
                raw = z.read(member)
            try:
                content = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                content = raw.decode("cp1252")
            members[archive, member] = [r for r in csv.reader(io.StringIO(content), delimiter=";") if r]
        rows = members[archive, member]
        original_row = rows[row_number - 1]
        payload = exp["payload"]
        if exp["abundance_type"] is None:
            col = int(column) - 1
            header = rows[2][col]
            m = re.fullmatch(r"(.*) \[(.*)\]", header)
            parameter, unit = m.groups() if m else (header, "")
            okay = (original_row[col] == exp["raw"] and original_row[0] == exp["date_original"]
                    and parameter == exp["parameter"] and unit == exp["unit"]
                    and rows[0][col] == payload["Kompartiment"] and rows[1][col] == payload["Stoffgruppe"])
        else:
            original = dict(zip(rows[0], original_row))
            okay = all(payload.get(field) == value for field, value in original.items())
        raw_cells.add(k)
        if not okay:
            mismatches.append(k)
    audit.check("Original ZIP cells agree with all prepared source locators", not mismatches,
                {"checked_cells": len(raw_cells), "zip_members": len(members), "mismatches": mismatches[:12]})


class Audit:
    def __init__(self):
        self.checks = []

    def check(self, name, passed, detail=None):
        item = {"check": name, "passed": bool(passed)}
        if detail is not None:
            item["detail"] = detail
        self.checks.append(item)

    def count(self, name, actual, expected):
        self.check(name, actual == expected, {"actual": actual, "expected": expected})

    def compare_observations(self, name, rows, expected):
        errors = Counter()
        examples = []
        seen = set()
        for row in rows:
            k = key(row)
            if k in seen:
                errors["duplicate_source_cell"] += 1
            seen.add(k)
            exp = expected.get(k)
            if exp is None:
                errors["unexpected_source_cell"] += 1
                continue
            try:
                payload = json.loads(row["source_payload_json"])
            except (TypeError, ValueError, KeyError):
                payload = None
            checks = {
                "original_payload": payload == exp["payload"],
                "raw_value": row.get("value_original") == exp["raw"],
                "comparison": row.get("comparison_operator") == exp["comparison"],
                "exact_value": numeric_equal(row.get("value"), exp["value"]),
                "limit_value": numeric_equal(row.get("detection_limit"), exp["limit"]),
                "source_unit": row.get("unit_symbol") == exp["unit"],
                "parameter": row.get("parameter_name") == exp["parameter"],
                "metric": row.get("abundance_type") == exp["abundance_type"],
                "original_date": row.get("measured_at_original") == exp["date_original"],
                "calendar_date": str(row.get("measured_at")) == exp["date"],
                "water_body": row.get("water_body_id") == WATER_ID,
            }
            for field, passed in checks.items():
                if not passed:
                    errors[field] += 1
                    if len(examples) < 12:
                        examples.append({"source_cell": k, "field": field})
        errors["missing_source_cell"] += len(set(expected) - seen)
        errors = {k: v for k, v in errors.items() if v}
        self.check(name, not errors,
                   {"source_cells": len(expected), "observed_cells": len(seen),
                    "errors": errors, "examples": examples})


def numeric_equal(a, b):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=1e-15)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def audit_postgres(audit, env_path, expected):
    env = dotenv_values(env_path)
    url = env.get("SOLVE_DATABASE_URL") or env.get("DATABASE_URL")
    if not url:
        raise ValueError("PostgreSQL env lacks SOLVE_DATABASE_URL or DATABASE_URL")
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        def scalar(sql):
            return conn.execute(text(sql)).scalar_one()

        def records(sql):
            return [dict(r) for r in conn.execute(text(sql)).mappings()]

        audit.count("PostgreSQL: one WaterBody", scalar("SELECT count(*) FROM water_kg_2.water_body"), 1)
        audit.check("PostgreSQL: exact official Bergwitzsee identity",
                    scalar("SELECT water_body_id FROM water_kg_2.water_body") == WATER_ID)
        audit.count("PostgreSQL: observations", scalar("SELECT count(*) FROM water_kg_2.measurement"), 23789)
        audit.count("PostgreSQL: stations", scalar("SELECT count(*) FROM water_kg_2.monitoring_station"), 25)
        audit.count("PostgreSQL: unresolved station coordinates stay NULL",
                    scalar("SELECT count(*) FROM water_kg_2.monitoring_station WHERE location IS NOT NULL"), 0)
        audit.count("PostgreSQL: local codes not invented EU station codes",
                    scalar("SELECT count(*) FROM water_kg_2.monitoring_station WHERE eu_station_code IS NOT NULL"), 0)
        audit.count("PostgreSQL: no station belongs to another lake",
                    scalar(f"SELECT count(*) FROM water_kg_2.monitoring_station WHERE water_body_id<>'{WATER_ID}'"), 0)
        audit.count("PostgreSQL: all measurements have station and parameter",
                    scalar("SELECT count(*) FROM water_kg_2.measurement WHERE station_id IS NULL OR parameter_id IS NULL"), 0)
        audit.count("PostgreSQL: no duplicate source keys",
                    scalar("SELECT count(*) FROM (SELECT source_file,source_row_number FROM water_kg_2.measurement GROUP BY 1,2 HAVING count(*)>1) q"), 0)
        audit.count("PostgreSQL: qualified observations",
                    scalar("SELECT count(*) FROM water_kg_2.measurement WHERE comparison_operator='<'"), 2627)
        audit.count("PostgreSQL: censored bounds never exact values",
                    scalar("SELECT count(*) FROM water_kg_2.measurement WHERE comparison_operator='<' AND (value IS NOT NULL OR detection_limit IS NULL)"), 0)
        audit.count("PostgreSQL: historical assessments", scalar("SELECT count(*) FROM water_kg_2.water_body_assessment"), 48)
        audit.count("PostgreSQL: biological sampling sections", scalar("SELECT count(*) FROM water_kg_2.sampling_section"), 324)
        depth_errors = []
        for section in records("SELECT * FROM water_kg_2.sampling_section"):
            raw = section["depth_original"]
            match = re.fullmatch(r"(\d+)-(\d+) m", raw or "")
            if match:
                okay = (numeric_equal(section["depth_from"], float(match[1]))
                        and numeric_equal(section["depth_to"], float(match[2]))
                        and section["depth_lower_open"] is False)
            elif raw == ">4 m":
                okay = (numeric_equal(section["depth_from"], 4)
                        and section["depth_to"] is None and section["depth_lower_open"] is True)
            else:
                okay = section["depth_from"] is None and section["depth_to"] is None
            if not okay:
                depth_errors.append(section["sampling_section_id"])
        audit.check("PostgreSQL: source depth intervals preserved including open bound", not depth_errors, depth_errors)
        for table, expected_count in [("event", 73), ("event_evidence", 79), ("source_document", 15), ("water_body_version", 3)]:
            audit.count(f"PostgreSQL: {table}", scalar(f"SELECT count(*) FROM water_events.{table}"), expected_count)
        rows = records("""SELECT m.*,p.name AS parameter_name,u.symbol AS unit_symbol
                          FROM water_kg_2.measurement m
                          JOIN water_kg_2.parameter p USING(parameter_id)
                          LEFT JOIN water_kg_2.unit u USING(unit_id)""")
        audit.compare_observations("PostgreSQL: every original cell/payload preserved", rows, expected)

        original = load_module("original_v2_schema_audit", ROOT / "scripts/water_kg_2_import.py")
        ddl = original.schema_ddl("water_kg_2")
        tables, columns, constraints = set(), set(), set()
        for statement in ddl:
            match = re.search(r'CREATE TABLE "water_kg_2"\.(\w+)\s*\(', statement)
            if not match:
                continue
            table = match.group(1)
            tables.add(table)
            for line in statement.splitlines()[1:]:
                col = re.match(r"\s*(\w+)\s+(TEXT|BIGINT|INTEGER|DOUBLE|BOOLEAN|DATE|geometry)\b", line)
                if col:
                    columns.add((table, col.group(1)))
            constraints.update(re.findall(r"CONSTRAINT\s+(\w+)", statement))
        actual_columns = {(r["table_name"], r["column_name"]) for r in records(
            "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='water_kg_2'")}
        actual_tables = {t for t, _ in actual_columns}
        actual_constraints = {r["conname"] for r in records(
            "SELECT c.conname FROM pg_constraint c JOIN pg_namespace n ON c.connamespace=n.oid WHERE n.nspname='water_kg_2'")}
        audit.check("PostgreSQL: original V2 tables retained", tables <= actual_tables, sorted(tables - actual_tables))
        audit.check("PostgreSQL: original V2 columns retained", columns <= actual_columns, sorted(columns - actual_columns))
        audit.check("PostgreSQL: original named constraints retained", constraints <= actual_constraints, sorted(constraints - actual_constraints))
        # Absence of invented data is substantive: these structures have no acquired records.
        for table in ["groundwater_body", "catchment", "core_location", "paleo_measurement"]:
            audit.count(f"PostgreSQL: no fabricated {table}", scalar(f"SELECT count(*) FROM water_kg_2.{table}"), 0)
    engine.dispose()


def audit_neo4j(audit, env_path, expected, bundle_path):
    env = dotenv_values(env_path)
    with GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USER"], env["NEO4J_PASSWORD"])) as driver:
        with driver.session(database=env.get("NEO4J_DATABASE", "neo4j"), default_access_mode="READ") as session:
            def scalar(query):
                return session.run(query).single()[0]

            labels = {r["label"]: r["n"] for r in session.run(
                "MATCH(n) UNWIND labels(n) AS label RETURN label,count(*) AS n")}
            for label, count in {"WaterBodyV2": 1, "MonitoringStationV2": 25,
                                  "MeasurementV2": 23789, "WaterBodyAssessmentV2": 48,
                                  "Event": 73, "EventEvidence": 79, "SourceDocument": 15,
                                  "WaterBodyVersion": 3}.items():
                audit.count(f"Neo4j: {label}", labels.get(label, 0), count)
            audit.count("Neo4j: direct WaterBody to Event links", scalar(
                f"MATCH(w:WaterBodyV2 {{water_body_id:'{WATER_ID}'}})-[:HAS_EVENT]->(e:Event) RETURN count(*)"), 73)
            audit.count("Neo4j: every Event directly belongs to this WaterBody", scalar(
                f"MATCH(e:Event) WHERE NOT EXISTS {{ MATCH(:WaterBodyV2 {{water_body_id:'{WATER_ID}'}})-[:HAS_EVENT]->(e) }} RETURN count(e)"), 0)
            audit.count("Neo4j: no dummy/legacy lake nodes", sum(labels.get(x, 0) for x in ("Lake33", "WaterBody", "Lake")), 0)
            for relation, other, direction in [("FOR_WATERBODY", "WaterBodyV2", "out"),
                                                ("MEASURES_PARAMETER", "ParameterV2", "out"),
                                                ("RECORDED", "MonitoringStationV2", "in")]:
                pattern = f"(m)-[:{relation}]->(t:{other})" if direction == "out" else f"(m)<-[:{relation}]-(t:{other})"
                audit.count(f"Neo4j: exactly one {relation} per observation", scalar(
                    f"MATCH(m:MeasurementV2) OPTIONAL MATCH {pattern} WITH m,count(t) AS links WHERE links<>1 RETURN count(m)"), 0)
            audit.count("Neo4j: historical versions directly connected", scalar(
                f"MATCH(:WaterBodyV2 {{water_body_id:'{WATER_ID}'}})-[:HAS_VERSION]->(:WaterBodyVersion) RETURN count(*)"), 3)
            audit.count("Neo4j: each assessment attached to its historical version", scalar(
                "MATCH(v:WaterBodyVersion)-[:HAS_ASSESSMENT]->(a:WaterBodyAssessmentV2) WHERE a.source_version_id=v.version_id RETURN count(*)"), 48)
            audit.count("Neo4j: complete Event-evidence-document paths", scalar(
                "MATCH(:Event)-[:HAS_EVIDENCE]->(:EventEvidence)-[:FROM_DOCUMENT]->(:SourceDocument) RETURN count(*)"), 79)
            audit.count("Neo4j: all biological measurements linked to sampling sections", scalar(
                "MATCH(m:MeasurementV2)-[:SAMPLED_IN]->(:SamplingSectionV2) WHERE m.abundance_type IS NOT NULL RETURN count(*)"), 13244)
            rows = []
            for rec in session.run("""MATCH(m:MeasurementV2)-[:MEASURES_PARAMETER]->(p:ParameterV2)
                                     OPTIONAL MATCH(m)-[:HAS_UNIT]->(u:UnitV2)
                                     RETURN properties(m) AS row,p.name AS parameter_name,u.symbol AS unit_symbol"""):
                row = dict(rec["row"])
                row.update(parameter_name=rec["parameter_name"], unit_symbol=rec["unit_symbol"])
                rows.append(row)
            audit.compare_observations("Neo4j: every original cell/payload preserved", rows, expected)
            # Exact researched Event payloads must survive; no claim of human verification is inferred.
            bundle = json.loads(bundle_path.read_text(encoding="utf-8-sig"))
            lake = next(r for r in bundle["tables"]["lake"] if r["name"] == "Bergwitzsee")
            source_events = {r["event_id"]: r["payload"] for r in bundle["tables"]["event"] if r["lake_id"] == lake["lake_id"]}
            graph_events = {r["e"]["event_id"]: dict(r["e"]) for r in session.run("MATCH(e:Event) RETURN properties(e) AS e")}
            audit.check("Neo4j: exact source Event IDs", set(source_events) == set(graph_events))
            changed = []
            for event_id, payload in source_events.items():
                node = graph_events.get(event_id, {})
                raw = node.get("payload_json", node.get("payload"))
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except ValueError:
                        raw = None
                if raw != payload:
                    changed.append(event_id)
            audit.check("Neo4j: original Event payloads unchanged", not changed, changed)
            source_evidence = {r["evidence_id"]: r for r in bundle["tables"]["event_evidence"]
                               if r["event_id"] in source_events}
            source_ids = {r["source_id"] for r in source_evidence.values()}
            source_documents = {r["source_id"]: r for r in bundle["tables"]["source_document"]
                                if r["source_id"] in source_ids}
            versions = {r["water_body_id"]: r for r in bundle["tables"]["water_body"]
                        if r["eu_code"] == WATER_ID}
            for label, id_field, originals in [("EventEvidence", "evidence_id", source_evidence),
                                               ("SourceDocument", "source_id", source_documents),
                                               ("WaterBodyVersion", "water_body_id", versions)]:
                actual = {r["id"]: json.loads(r["row_json"]) for r in session.run(
                    f"MATCH(n:{label}) RETURN n.{id_field} AS id,n.row_json AS row_json")}
                audit.check(f"Neo4j: original {label} rows unchanged", actual == originals,
                            {"checked_rows": len(actual), "expected_rows": len(originals)})
            expected_links = {(r["event_id"], r["evidence_id"], r["source_id"]) for r in source_evidence.values()}
            actual_links = {(r["event"], r["evidence"], r["source"]) for r in session.run(
                "MATCH(e:Event)-[:HAS_EVIDENCE]->(v:EventEvidence)-[:FROM_DOCUMENT]->(s:SourceDocument) "
                "RETURN e.event_id AS event,v.evidence_id AS evidence,s.source_id AS source")}
            audit.check("Neo4j: evidence linked to exact original event/document pairs", expected_links == actual_links)
            expected_uncertainty = {event_id: (p.get("waterbody_assignment") or {}).get("status")
                                    for event_id, p in source_events.items()}
            actual_uncertainty = {r["id"]: r["status"] for r in session.run(
                "MATCH(:WaterBodyV2)-[r:HAS_EVENT]->(e:Event) RETURN e.event_id AS id,r.assignment_status AS status")}
            audit.check("Neo4j: direct event links retain original assignment uncertainty", actual_uncertainty == expected_uncertainty)
            assessments = {r["assessment_id"]: r for r in bundle["tables"]["water_body_assessment"]
                           if r["water_body_id"] in versions}
            actual_assessments = {r["id"]: json.loads(r["payload"]) for r in session.run(
                "MATCH(a:WaterBodyAssessmentV2) RETURN a.source_assessment_id AS id,a.source_payload_json AS payload")}
            audit.check("Neo4j: original historical assessment payloads unchanged", actual_assessments == assessments)
            return labels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCES)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--bundle", type=Path, default=ROOT / "outputs/wasserblick33_20261031/data/prepared/bundle.json")
    parser.add_argument("--source-only", action="store_true")
    args = parser.parse_args()
    audit = Audit()
    expected, counts = build_expected(args.source)
    audit.count("Acquired source numeric cells", len(expected), 23789)
    audit_raw_exports(audit, args.source, expected)
    graph_labels = {}
    if not args.source_only:
        for name, action in [
            ("PostgreSQL", lambda: audit_postgres(audit, args.output / "runtime/postgres.env", expected)),
            ("Neo4j", lambda: audit_neo4j(audit, args.output / "runtime/neo4j_preview/preview.env", expected, args.bundle)),
        ]:
            try:
                result = action()
                if name == "Neo4j":
                    graph_labels = result
            except Exception as exc:
                # Do not serialize a connection exception, which may contain credentials.
                audit.check(f"{name}: audit completed", False, {"exception_type": type(exc).__name__})
    report = {
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "read_only": True,
        "source_only": args.source_only,
        "passed": all(c["passed"] for c in audit.checks),
        "source_counts": counts,
        "graph_labels": graph_labels,
        "checks": audit.checks,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / ("source_audit.json" if args.source_only else "independent_delivery_audit.json")
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": len(audit.checks), "report": str(destination)}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
