"""Build the versioned Neo4j graph from PostgreSQL ``water_kg_2``.

The importer never touches the legacy, unversioned graph.  New data is first
loaded with ``*V2Build`` labels, validated against PostgreSQL and only then
promoted to the public ``*V2`` labels.  Rebuilds remove nodes carrying the
dedicated ``WaterKgV2`` marker only.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from neo4j import GraphDatabase
from sqlalchemy import create_engine, inspect, text


SOURCE_SCHEMA = "water_kg_2"
FINAL_MARKER = "WaterKgV2"
BUILD_MARKER = "WaterKgV2Build"
GRAPH_VERSION = "2"

DATABASE_URL = os.getenv(
    "SOLVE_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/SOLVE",
)
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "postgres")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")


def quote_identifier(value: str) -> str:
    """Quote a trusted PostgreSQL identifier."""
    return '"' + value.replace('"', '""') + '"'


def safe_name(value: str) -> str:
    """Return a safe fragment for generated Neo4j schema object names."""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def composite_key(*parts: Any) -> str:
    """Build an unambiguous, deterministic key even when values contain separators."""
    encoded = []
    for part in parts:
        value = "" if part is None else str(part)
        encoded.append(f"{len(value)}:{value}")
    return "|".join(encoded)


def source_key(row: Mapping[str, Any]) -> str:
    return composite_key(row.get("source_file"), row.get("source_row_number"))


def station_key(row: Mapping[str, Any]) -> str:
    return composite_key(row.get("water_body_id"), row.get("identity_station_code"))


def parameter_key(row: Mapping[str, Any]) -> str:
    return composite_key(row.get("name"), row.get("quality_component"))


def identifier_key(row: Mapping[str, Any]) -> str:
    return composite_key(row.get("identifier_type"), row.get("identifier"))


def protected_area_key(row: Mapping[str, Any]) -> str:
    return composite_key(row.get("area_type"), row.get("protected_area_id"))


def issue_key(row: Mapping[str, Any]) -> str:
    return composite_key(
        row.get("entity_type"), row.get("source_file"), row.get("source_row_number"),
        row.get("source_column"), row.get("issue_code"),
    )


def clean_value(value: Any) -> Any:
    """Convert SQL values into values supported by the Neo4j driver."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (str, int, float, bool, date, datetime)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if hasattr(value, "item"):
        return clean_value(value.item())
    return str(value)


def clean_properties(row: Mapping[str, Any], excluded: Iterable[str] = ()) -> dict[str, Any]:
    excluded_set = set(excluded)
    return {
        key: cleaned
        for key, value in row.items()
        if key not in excluded_set and (cleaned := clean_value(value)) is not None
    }


@dataclass(frozen=True)
class NodeSpec:
    table: str
    label: str
    key_property: str
    key_builder: Callable[[Mapping[str, Any]], Any]
    extra_unique: tuple[str, ...] = ()
    point_from: tuple[str, str] | None = None

    @property
    def build_label(self) -> str:
        return f"{self.label}Build"


@dataclass(frozen=True)
class RelationshipSpec:
    name: str
    relationship_type: str
    start_label: str
    start_property: str
    end_label: str
    end_property: str
    query: str
    property_columns: tuple[str, ...] = field(default_factory=tuple)

    @property
    def start_build_label(self) -> str:
        return f"{self.start_label}Build"

    @property
    def end_build_label(self) -> str:
        return f"{self.end_label}Build"


NODE_SPECS: tuple[NodeSpec, ...] = (
    NodeSpec("planning_unit", "PlanningUnitV2", "planning_unit_code", lambda r: r["planning_unit_code"]),
    NodeSpec("water_body", "WaterBodyV2", "water_body_id", lambda r: r["water_body_id"]),
    NodeSpec("water_body_identifier", "WaterBodyIdentifierV2", "identifier_key", identifier_key),
    NodeSpec("groundwater_body", "GroundwaterBodyV2", "groundwater_body_id", lambda r: r["groundwater_body_id"]),
    NodeSpec("catchment", "CatchmentV2", "catchment_id", lambda r: r["catchment_id"]),
    NodeSpec("monitoring_station", "MonitoringStationV2", "station_key", station_key, ("station_id",),
             ("longitude_wgs84", "latitude_wgs84")),
    NodeSpec("station_alias", "StationAliasV2", "alias_id", lambda r: r["alias_id"]),
    NodeSpec("core_location", "CoreLocationV2", "core_code", lambda r: r["core_code"], (),
             ("longitude_wgs84", "latitude_wgs84")),
    NodeSpec("parameter", "ParameterV2", "parameter_key", parameter_key, ("parameter_id",)),
    NodeSpec("unit", "UnitV2", "unit_id", lambda r: r["unit_id"], ("symbol",)),
    NodeSpec("pollutant", "PollutantV2", "pollutant_id", lambda r: r["pollutant_id"], ("pollutant_code",)),
    NodeSpec("paleo_element", "PaleoElementV2", "element_id", lambda r: r["element_id"], ("symbol",)),
    NodeSpec("protected_area", "ProtectedAreaV2", "protected_area_key", protected_area_key,
             ("protected_area_id",)),
    NodeSpec("sampling_section", "SamplingSectionV2", "source_key", source_key, ("sampling_section_id",)),
    NodeSpec("measurement", "MeasurementV2", "source_key", source_key, ("measurement_id",)),
    NodeSpec("water_body_assessment", "WaterBodyAssessmentV2", "source_key", source_key, ("assessment_id",)),
    NodeSpec("station_assessment", "StationAssessmentV2", "source_key", source_key, ("assessment_id",)),
    NodeSpec("chemical_status", "ChemicalStatusV2", "source_key", source_key, ("chemical_status_id",)),
    NodeSpec("paleo_measurement", "PaleoMeasurementV2", "source_key", source_key, ("paleo_measurement_id",)),
    NodeSpec("protected_area_assessment", "ProtectedAreaAssessmentV2", "source_key", source_key, ("assessment_id",)),
    NodeSpec("import_issue", "ImportIssueV2", "issue_key", issue_key, ("issue_id",)),
)


def relation(
    name: str,
    relationship_type: str,
    start_label: str,
    start_property: str,
    end_label: str,
    end_property: str,
    query: str,
    *properties: str,
) -> RelationshipSpec:
    return RelationshipSpec(
        name, relationship_type, start_label, start_property, end_label,
        end_property, query, tuple(properties),
    )


RELATIONSHIP_SPECS: tuple[RelationshipSpec, ...] = (
    relation("water_planning", "IN_PLANNING_UNIT", "WaterBodyV2", "water_body_id", "PlanningUnitV2",
             "planning_unit_code", "SELECT water_body_id AS start_key, planning_unit_code AS end_key FROM water_kg_2.water_body WHERE planning_unit_code IS NOT NULL"),
    relation("groundwater_planning", "IN_PLANNING_UNIT", "GroundwaterBodyV2", "groundwater_body_id", "PlanningUnitV2",
             "planning_unit_code", "SELECT groundwater_body_id AS start_key, planning_unit_code AS end_key FROM water_kg_2.groundwater_body WHERE planning_unit_code IS NOT NULL"),
    relation("catchment_planning", "IN_PLANNING_UNIT", "CatchmentV2", "catchment_id", "PlanningUnitV2",
             "planning_unit_code", "SELECT catchment_id AS start_key, planning_unit_code AS end_key FROM water_kg_2.catchment WHERE planning_unit_code IS NOT NULL"),
    relation("water_identifier", "HAS_IDENTIFIER", "WaterBodyV2", "water_body_id", "WaterBodyIdentifierV2",
             "identifier_key", "SELECT water_body_id AS start_key, identifier_type, identifier FROM water_kg_2.water_body_identifier", "identifier_type", "identifier"),
    relation("station_water", "MONITORS", "MonitoringStationV2", "station_id", "WaterBodyV2", "water_body_id",
             "SELECT station_id AS start_key, water_body_id AS end_key FROM water_kg_2.monitoring_station WHERE water_body_id IS NOT NULL"),
    relation("station_alias", "HAS_ALIAS", "MonitoringStationV2", "station_id", "StationAliasV2", "alias_id",
             "SELECT station_id AS start_key, alias_id AS end_key FROM water_kg_2.station_alias"),
    relation("core_water", "IN_WATERBODY", "CoreLocationV2", "core_code", "WaterBodyV2", "water_body_id",
             "SELECT core_code AS start_key, water_body_id AS end_key FROM water_kg_2.core_location WHERE water_body_id IS NOT NULL"),
    relation("water_section", "HAS_SAMPLING_SECTION", "WaterBodyV2", "water_body_id", "SamplingSectionV2", "sampling_section_id",
             "SELECT water_body_id AS start_key, sampling_section_id AS end_key FROM water_kg_2.sampling_section WHERE water_body_id IS NOT NULL"),
    relation("station_section", "HAS_SAMPLING_SECTION", "MonitoringStationV2", "station_id", "SamplingSectionV2", "sampling_section_id",
             "SELECT station_id AS start_key, sampling_section_id AS end_key FROM water_kg_2.sampling_section WHERE station_id IS NOT NULL"),
    relation("station_measurement", "RECORDED", "MonitoringStationV2", "station_id", "MeasurementV2", "measurement_id",
             "SELECT station_id AS start_key, measurement_id AS end_key FROM water_kg_2.measurement WHERE station_id IS NOT NULL"),
    relation("measurement_water", "FOR_WATERBODY", "MeasurementV2", "measurement_id", "WaterBodyV2", "water_body_id",
             "SELECT measurement_id AS start_key, water_body_id AS end_key FROM water_kg_2.measurement WHERE water_body_id IS NOT NULL"),
    relation("measurement_section", "SAMPLED_IN", "MeasurementV2", "measurement_id", "SamplingSectionV2", "sampling_section_id",
             "SELECT measurement_id AS start_key, sampling_section_id AS end_key FROM water_kg_2.measurement WHERE sampling_section_id IS NOT NULL"),
    relation("measurement_parameter", "MEASURES_PARAMETER", "MeasurementV2", "measurement_id", "ParameterV2", "parameter_id",
             "SELECT measurement_id AS start_key, parameter_id AS end_key FROM water_kg_2.measurement"),
    relation("measurement_unit", "HAS_UNIT", "MeasurementV2", "measurement_id", "UnitV2", "unit_id",
             "SELECT measurement_id AS start_key, unit_id AS end_key FROM water_kg_2.measurement WHERE unit_id IS NOT NULL"),
    relation("water_assessment", "HAS_ASSESSMENT", "WaterBodyV2", "water_body_id", "WaterBodyAssessmentV2", "assessment_id",
             "SELECT water_body_id AS start_key, assessment_id AS end_key FROM water_kg_2.water_body_assessment WHERE water_body_id IS NOT NULL"),
    relation("water_assessment_parameter", "FOR_PARAMETER", "WaterBodyAssessmentV2", "assessment_id", "ParameterV2", "parameter_id",
             "SELECT assessment_id AS start_key, parameter_id AS end_key FROM water_kg_2.water_body_assessment"),
    relation("station_assessment", "HAS_ASSESSMENT", "MonitoringStationV2", "station_id", "StationAssessmentV2", "assessment_id",
             "SELECT station_id AS start_key, assessment_id AS end_key FROM water_kg_2.station_assessment WHERE station_id IS NOT NULL"),
    relation("station_assessment_water", "FOR_WATERBODY", "StationAssessmentV2", "assessment_id", "WaterBodyV2", "water_body_id",
             "SELECT assessment_id AS start_key, water_body_id AS end_key FROM water_kg_2.station_assessment WHERE water_body_id IS NOT NULL"),
    relation("station_assessment_parameter", "FOR_PARAMETER", "StationAssessmentV2", "assessment_id", "ParameterV2", "parameter_id",
             "SELECT assessment_id AS start_key, parameter_id AS end_key FROM water_kg_2.station_assessment"),
    relation("station_assessment_unit", "HAS_UNIT", "StationAssessmentV2", "assessment_id", "UnitV2", "unit_id",
             "SELECT assessment_id AS start_key, unit_id AS end_key FROM water_kg_2.station_assessment WHERE unit_id IS NOT NULL"),
    relation("surface_chemical", "HAS_CHEMICAL_STATUS", "WaterBodyV2", "water_body_id", "ChemicalStatusV2", "chemical_status_id",
             "SELECT surface_water_body_id AS start_key, chemical_status_id AS end_key FROM water_kg_2.chemical_status WHERE surface_water_body_id IS NOT NULL"),
    relation("ground_chemical", "HAS_CHEMICAL_STATUS", "GroundwaterBodyV2", "groundwater_body_id", "ChemicalStatusV2", "chemical_status_id",
             "SELECT groundwater_body_id AS start_key, chemical_status_id AS end_key FROM water_kg_2.chemical_status WHERE groundwater_body_id IS NOT NULL"),
    relation("chemical_pollutant", "FOR_POLLUTANT", "ChemicalStatusV2", "chemical_status_id", "PollutantV2", "pollutant_id",
             "SELECT chemical_status_id AS start_key, pollutant_id AS end_key FROM water_kg_2.chemical_status"),
    relation("core_paleo", "HAS_PALEO_MEASUREMENT", "CoreLocationV2", "core_code", "PaleoMeasurementV2", "paleo_measurement_id",
             "SELECT core_code AS start_key, paleo_measurement_id AS end_key FROM water_kg_2.paleo_measurement"),
    relation("paleo_element", "OF_ELEMENT", "PaleoMeasurementV2", "paleo_measurement_id", "PaleoElementV2", "element_id",
             "SELECT paleo_measurement_id AS start_key, element_id AS end_key FROM water_kg_2.paleo_measurement"),
    relation("protected_assessment", "HAS_ASSESSMENT", "ProtectedAreaV2", "protected_area_id", "ProtectedAreaAssessmentV2", "assessment_id",
             "SELECT protected_area_id AS start_key, assessment_id AS end_key FROM water_kg_2.protected_area_assessment WHERE protected_area_id IS NOT NULL"),
    relation("protected_assessment_surface", "FOR_WATERBODY", "ProtectedAreaAssessmentV2", "assessment_id", "WaterBodyV2", "water_body_id",
             "SELECT assessment_id AS start_key, surface_water_body_id AS end_key FROM water_kg_2.protected_area_assessment WHERE surface_water_body_id IS NOT NULL"),
    relation("protected_assessment_ground", "FOR_GROUNDWATER_BODY", "ProtectedAreaAssessmentV2", "assessment_id", "GroundwaterBodyV2", "groundwater_body_id",
             "SELECT assessment_id AS start_key, groundwater_body_id AS end_key FROM water_kg_2.protected_area_assessment WHERE groundwater_body_id IS NOT NULL"),
    relation("water_ground_overlap", "OVERLAPS", "WaterBodyV2", "water_body_id", "GroundwaterBodyV2", "groundwater_body_id",
             "SELECT water_body_id AS start_key, groundwater_body_id AS end_key, overlap_area_km2, water_body_overlap_ratio, determination_method FROM water_kg_2.water_body_groundwater_body",
             "overlap_area_km2", "water_body_overlap_ratio", "determination_method"),
    relation("catchment_ground_overlap", "OVERLAPS", "CatchmentV2", "catchment_id", "GroundwaterBodyV2", "groundwater_body_id",
             "SELECT catchment_id AS start_key, groundwater_body_id AS end_key, overlap_area_km2, catchment_overlap_ratio, determination_method FROM water_kg_2.catchment_groundwater_body",
             "overlap_area_km2", "catchment_overlap_ratio", "determination_method"),
    relation("catchment_water_overlap", "OVERLAPS", "CatchmentV2", "catchment_id", "WaterBodyV2", "water_body_id",
             "SELECT catchment_id AS start_key, water_body_id AS end_key, overlap_area_km2, catchment_overlap_ratio, water_body_overlap_ratio, determination_method FROM water_kg_2.catchment_water_body",
             "overlap_area_km2", "catchment_overlap_ratio", "water_body_overlap_ratio", "determination_method"),
    relation("station_ground", "LOCATED_IN", "MonitoringStationV2", "station_id", "GroundwaterBodyV2", "groundwater_body_id",
             "SELECT station_id AS start_key, groundwater_body_id AS end_key, determination_method FROM water_kg_2.station_groundwater_body", "determination_method"),
    relation("station_catchment", "LOCATED_IN", "MonitoringStationV2", "station_id", "CatchmentV2", "catchment_id",
             "SELECT station_id AS start_key, catchment_id AS end_key, determination_method FROM water_kg_2.station_catchment", "determination_method"),
    relation("water_protected", "OVERLAPS_PROTECTED_AREA", "WaterBodyV2", "water_body_id", "ProtectedAreaV2", "protected_area_id",
             "SELECT water_body_id AS start_key, protected_area_id AS end_key, relation_method, overlap_area_km2, water_body_overlap_ratio FROM water_kg_2.water_body_protected_area",
             "relation_method", "overlap_area_km2", "water_body_overlap_ratio"),
)


def batches(rows: Iterable[Mapping[str, Any]], size: int) -> Iterator[list[Mapping[str, Any]]]:
    batch: list[Mapping[str, Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


class WaterKgV2Importer:
    def __init__(self, engine, driver, database: str, batch_size: int = 2000):
        self.engine = engine
        self.driver = driver
        self.database = database
        self.batch_size = batch_size
        self.run_id = str(uuid.uuid4())
        self.source_counts: dict[str, int] = {}
        self.relationship_counts: dict[str, int] = {}

    def neo4j_scalar(self, query: str, **parameters: Any) -> Any:
        with self.driver.session(database=self.database) as session:
            return session.run(query, **parameters).single().value()

    def neo4j_write(self, query: str, **parameters: Any) -> None:
        with self.driver.session(database=self.database) as session:
            session.execute_write(lambda tx: tx.run(query, **parameters).consume())

    def delete_marker(self, marker: str) -> None:
        while True:
            deleted = self.neo4j_scalar(
                f"MATCH (n:{marker}) WITH n LIMIT $limit DETACH DELETE n RETURN count(n)",
                limit=self.batch_size,
            )
            if not deleted:
                break

    def source_columns(self, spec: NodeSpec) -> list[str]:
        columns = inspect(self.engine).get_columns(spec.table, schema=SOURCE_SCHEMA)
        return [column["name"] for column in columns if column["name"] not in {"geometry", "location"}]

    def node_query(self, spec: NodeSpec) -> str:
        columns = self.source_columns(spec)
        if spec.table == "monitoring_station":
            selections = [f"m.{quote_identifier(column)}" for column in columns]
            selections.extend([
                "identity.alias_code AS identity_station_code",
                "ST_X(ST_Transform(m.location, 4326)) AS longitude_wgs84",
                "ST_Y(ST_Transform(m.location, 4326)) AS latitude_wgs84",
            ])
            return (
                f"SELECT {', '.join(selections)} "
                f"FROM {quote_identifier(SOURCE_SCHEMA)}.{quote_identifier(spec.table)} m "
                "LEFT JOIN LATERAL ("
                "SELECT a.alias_code FROM water_kg_2.station_alias a "
                "WHERE a.station_id = m.station_id "
                "AND a.alias_type IN ('local_code', 'observed_code') "
                "ORDER BY CASE a.alias_type WHEN 'local_code' THEN 0 ELSE 1 END "
                "LIMIT 1) identity ON TRUE"
            )
        selections = [quote_identifier(column) for column in columns]
        if spec.table == "core_location":
            selections.extend([
                "longitude AS longitude_wgs84",
                "latitude AS latitude_wgs84",
            ])
        return (
            f"SELECT {', '.join(selections)} FROM {quote_identifier(SOURCE_SCHEMA)}."
            f"{quote_identifier(spec.table)}"
        )

    def sql_rows(self, query: str) -> Iterator[Mapping[str, Any]]:
        with self.engine.connect().execution_options(stream_results=True) as connection:
            result = connection.execute(text(query))
            while rows := result.mappings().fetchmany(self.batch_size):
                yield from rows

    def create_constraints(self, build: bool) -> None:
        for spec in NODE_SPECS:
            label = spec.build_label if build else spec.label
            properties = (spec.key_property,) + spec.extra_unique
            for prop in properties:
                name = safe_name(f"wkg2_{'build' if build else 'final'}_{label}_{prop}")
                self.neo4j_write(
                    f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) "
                    f"REQUIRE n.{prop} IS UNIQUE"
                )

    def drop_build_constraints(self) -> None:
        for spec in NODE_SPECS:
            for prop in (spec.key_property,) + spec.extra_unique:
                name = safe_name(f"wkg2_build_{spec.build_label}_{prop}")
                self.neo4j_write(f"DROP CONSTRAINT {name} IF EXISTS")

    def import_nodes(self, spec: NodeSpec) -> int:
        point_fields = set(spec.point_from or ())
        total = 0
        cypher = (
            f"UNWIND $rows AS row "
            f"MERGE (n:{spec.build_label} {{{spec.key_property}: row.key}}) "
            f"SET n:{BUILD_MARKER}, n += row.properties, "
            "n.graph_version = $graph_version, n.import_run_id = $run_id "
        )
        if spec.point_from:
            cypher += (
                "FOREACH (_ IN CASE WHEN row.longitude IS NULL OR row.latitude IS NULL THEN [] ELSE [1] END | "
                "SET n.location = point({longitude: row.longitude, latitude: row.latitude})) "
            )
        cypher += "RETURN count(n)"

        prepared: list[dict[str, Any]] = []
        for row in self.sql_rows(self.node_query(spec)):
            key = clean_value(spec.key_builder(row))
            if key is None or key == "":
                raise ValueError(f"{spec.table}: node without {spec.key_property}: {dict(row)}")
            item = {
                "key": key,
                "properties": clean_properties(row, point_fields),
                "longitude": clean_value(row.get(spec.point_from[0])) if spec.point_from else None,
                "latitude": clean_value(row.get(spec.point_from[1])) if spec.point_from else None,
            }
            item["properties"][spec.key_property] = key
            prepared.append(item)
            if len(prepared) >= self.batch_size:
                self.neo4j_write(cypher, rows=prepared, graph_version=GRAPH_VERSION, run_id=self.run_id)
                total += len(prepared)
                prepared = []
        if prepared:
            self.neo4j_write(cypher, rows=prepared, graph_version=GRAPH_VERSION, run_id=self.run_id)
            total += len(prepared)
        self.source_counts[spec.label] = total
        print(f"nodes {spec.label}: {total}")
        return total

    def prepare_relationship_row(self, spec: RelationshipSpec, row: Mapping[str, Any]) -> dict[str, Any]:
        start = clean_value(row["start_key"])
        if spec.name == "water_identifier":
            end = composite_key(row.get("identifier_type"), row.get("identifier"))
        else:
            end = clean_value(row["end_key"])
        prepared = {
            "start": start,
            "end": end,
            "properties": clean_properties(
                {column: row.get(column) for column in spec.property_columns}
            ),
        }
        if spec.name == "water_protected":
            prepared["relationship_key"] = composite_key(
                start, end, row.get("relation_method")
            )
        return prepared

    def import_relationships(self, spec: RelationshipSpec) -> int:
        merge_relationship = (
            f"MERGE (a)-[r:{spec.relationship_type} "
            "{relationship_key: row.relationship_key}]->(b) "
            if spec.name == "water_protected"
            else f"MERGE (a)-[r:{spec.relationship_type}]->(b) "
        )
        cypher = (
            f"UNWIND $rows AS row "
            f"MATCH (a:{spec.start_build_label} {{{spec.start_property}: row.start}}) "
            f"MATCH (b:{spec.end_build_label} {{{spec.end_property}: row.end}}) "
            + merge_relationship +
            "SET r += row.properties, r.graph_version = $graph_version, r.import_run_id = $run_id "
            "RETURN count(r)"
        )
        expected = 0
        matched = 0
        for sql_batch in batches(self.sql_rows(spec.query), self.batch_size):
            rows = [self.prepare_relationship_row(spec, row) for row in sql_batch]
            if any(row["start"] is None or row["end"] is None for row in rows):
                raise ValueError(f"{spec.name}: relationship query returned a null key")
            with self.driver.session(database=self.database) as session:
                result = session.execute_write(
                    lambda tx: tx.run(
                        cypher, rows=rows, graph_version=GRAPH_VERSION, run_id=self.run_id
                    ).single().value()
                )
            expected += len(rows)
            matched += result
        if matched != expected:
            raise ValueError(
                f"{spec.name}: PostgreSQL has {expected} rows but only {matched} relationships matched"
            )
        self.relationship_counts[spec.name] = matched
        print(f"relationships {spec.name}: {matched}")
        return matched

    def validate_build(self) -> None:
        errors: list[str] = []
        for spec in NODE_SPECS:
            actual = self.neo4j_scalar(f"MATCH (n:{spec.build_label}) RETURN count(n)")
            expected = self.source_counts[spec.label]
            if actual != expected:
                errors.append(f"{spec.label}: PostgreSQL={expected}, Neo4j={actual}")
        for spec in RELATIONSHIP_SPECS:
            actual = self.neo4j_scalar(
                f"MATCH (a:{spec.start_build_label})-[r:{spec.relationship_type}]->"
                f"(b:{spec.end_build_label}) RETURN count(r)"
            )
            expected = self.relationship_counts[spec.name]
            if actual != expected:
                errors.append(f"{spec.name}: PostgreSQL={expected}, Neo4j={actual}")
        wrong_run = self.neo4j_scalar(
            f"MATCH (n:{BUILD_MARKER}) WHERE n.import_run_id <> $run_id RETURN count(n)",
            run_id=self.run_id,
        )
        if wrong_run:
            errors.append(f"{wrong_run} build nodes belong to another import run")
        orphan_relationships = self.neo4j_scalar(
            f"MATCH (a:{BUILD_MARKER})-[r]->(b) "
            f"WHERE NOT b:{BUILD_MARKER} OR r.import_run_id <> $run_id RETURN count(r)",
            run_id=self.run_id,
        )
        if orphan_relationships:
            errors.append(f"{orphan_relationships} build relationships leave the V2 build graph")
        if errors:
            raise ValueError("Build validation failed:\n- " + "\n- ".join(errors))

    def promote(self, rebuild: bool) -> None:
        existing = self.neo4j_scalar(f"MATCH (n:{FINAL_MARKER}) RETURN count(n)")
        if existing and not rebuild:
            raise RuntimeError(
                f"The V2 graph already contains {existing} nodes. Use --rebuild to replace V2 only."
            )
        if existing:
            self.delete_marker(FINAL_MARKER)
        for spec in NODE_SPECS:
            self.neo4j_write(
                f"MATCH (n:{spec.build_label}) SET n:{spec.label} REMOVE n:{spec.build_label}"
            )
        self.neo4j_write(f"MATCH (n:{BUILD_MARKER}) SET n:{FINAL_MARKER} REMOVE n:{BUILD_MARKER}")
        self.create_constraints(build=False)
        self.drop_build_constraints()

    def validate_final(self) -> None:
        errors = []
        total_expected = sum(self.source_counts.values())
        total_actual = self.neo4j_scalar(f"MATCH (n:{FINAL_MARKER}) RETURN count(n)")
        if total_actual != total_expected:
            errors.append(f"all V2 nodes: expected={total_expected}, actual={total_actual}")
        for spec in NODE_SPECS:
            actual = self.neo4j_scalar(f"MATCH (n:{spec.label}) RETURN count(n)")
            expected = self.source_counts[spec.label]
            if actual != expected:
                errors.append(f"{spec.label}: expected={expected}, actual={actual}")
        for spec in RELATIONSHIP_SPECS:
            actual = self.neo4j_scalar(
                f"MATCH (a:{spec.start_label})-[r:{spec.relationship_type}]->"
                f"(b:{spec.end_label}) RETURN count(r)"
            )
            expected = self.relationship_counts[spec.name]
            if actual != expected:
                errors.append(f"{spec.name}: expected={expected}, actual={actual}")
        if errors:
            raise ValueError("Final validation failed:\n- " + "\n- ".join(errors))

    def run(self, rebuild: bool) -> None:
        tables = set(inspect(self.engine).get_table_names(schema=SOURCE_SCHEMA))
        missing = sorted({spec.table for spec in NODE_SPECS} - tables)
        if missing:
            raise RuntimeError(f"Missing PostgreSQL source tables: {', '.join(missing)}")
        existing = self.neo4j_scalar(f"MATCH (n:{FINAL_MARKER}) RETURN count(n)")
        if existing and not rebuild:
            raise RuntimeError(
                f"The V2 graph already contains {existing} nodes. Use --rebuild to replace V2 only."
            )

        self.delete_marker(BUILD_MARKER)
        self.create_constraints(build=True)
        try:
            for spec in NODE_SPECS:
                self.import_nodes(spec)
            for spec in RELATIONSHIP_SPECS:
                self.import_relationships(spec)
            self.validate_build()
            self.promote(rebuild=rebuild)
            self.validate_final()
        except Exception:
            self.delete_marker(BUILD_MARKER)
            raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import water_kg_2 into versioned Neo4j V2 labels")
    parser.add_argument("--rebuild", action="store_true", help="Replace an existing V2 graph; legacy labels remain untouched")
    parser.add_argument("--batch-size", type=int, default=2000, help="Rows per Neo4j transaction (default: 2000)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    if args.batch_size < 1:
        raise ValueError("--batch-size must be at least 1")
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with engine.connect():
            pass
        driver.verify_connectivity()
        importer = WaterKgV2Importer(engine, driver, NEO4J_DATABASE, args.batch_size)
        importer.run(rebuild=args.rebuild)
        print("Neo4j V2 import and validation completed successfully.")
        print(f"run_id: {importer.run_id}")
        print(f"nodes: {sum(importer.source_counts.values())}")
        print(f"relationships: {sum(importer.relationship_counts.values())}")
    finally:
        driver.close()
        engine.dispose()


if __name__ == "__main__":
    main()
