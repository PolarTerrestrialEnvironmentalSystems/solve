"""Curated, read-only Cypher queries and German question routing.

The result shape of every query is intentionally tabular.  It can therefore be
shown in the UI and exported unchanged as a CSV datamart.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class QueryDefinition:
    key: str
    title: str
    cypher: str
    description: str


QUERIES = {
    "largest_lake": QueryDefinition(
        key="largest_lake",
        title="Größter See",
        description="Seen nach ihrer in den Stammdaten hinterlegten Fläche.",
        cypher="""
            MATCH (w:WaterBody)
            WHERE w.area_km2 IS NOT NULL
            RETURN w.eu_cd_lw AS lake_id, w.name AS lake,
                   w.area_km2 / 100.0 AS area_km2, w.volume_m3 AS volume_m3,
                   w.max_depth_m AS max_depth_m
            ORDER BY area_km2 DESC
        """,
    ),
    "deepest_lake": QueryDefinition(
        key="deepest_lake",
        title="Tiefster See",
        description="Seen nach maximaler Tiefe.",
        cypher="""
            MATCH (w:WaterBody)
            WHERE w.max_depth_m IS NOT NULL
            RETURN w.eu_cd_lw AS lake_id, w.name AS lake,
                   w.max_depth_m AS max_depth_m, w.area_km2 / 100.0 AS area_km2,
                   w.volume_m3 AS volume_m3
            ORDER BY max_depth_m DESC
        """,
    ),
    "chemical_assessment_relation": QueryDefinition(
        key="chemical_assessment_relation",
        title="Chemischer Zustand und Bewertung",
        description=(
            "Analysegrain: ein See und eine Wasserkörperbewertung. Chemische Statusangaben "
            "werden direkt oder über überlappende Grundwasserkörper aus dem Graphen abgeleitet "
            "und je See aggregiert."
        ),
        cypher="""
            MATCH (w:WaterBody)
            OPTIONAL MATCH (w)-[:HAS_CHEMICAL_STATUS]->(direct:ChemicalStatus)
            WITH w, collect(DISTINCT direct) AS direct_statuses
            OPTIONAL MATCH (w)-[:OVERLAPS]->(groundwater:GroundWaterBody)
                           -[:HAS_CHEMICAL_STATUS]->(derived:ChemicalStatus)
            WITH w, direct_statuses,
                 collect(DISTINCT derived) AS derived_statuses,
                 [name IN collect(DISTINCT groundwater.name) WHERE name IS NOT NULL]
                    AS overlapping_groundwater_bodies
            WITH w, direct_statuses, derived_statuses, overlapping_groundwater_bodies,
                 direct_statuses + derived_statuses AS statuses
            WITH w, overlapping_groundwater_bodies,
                 size(direct_statuses) AS direct_chemical_status_count,
                 size(derived_statuses) AS derived_chemical_status_count,
                 size(statuses) AS chemical_status_count,
                 size([status IN statuses WHERE status.failed_standard = true])
                    AS failed_standard_count,
                 reduce(codes = [], status IN statuses |
                    codes + [(status)-[:FOR_POLLUTANT]->(p:Pollutant) | p.pollutant_code])
                    AS pollutant_codes,
                 reduce(failed_codes = [], status IN statuses |
                    failed_codes + [(status)-[:FOR_POLLUTANT]->(p:Pollutant)
                                    WHERE status.failed_standard = true | p.pollutant_code])
                    AS failed_pollutants
            OPTIONAL MATCH (w)-[:HAS_ASSESSMENT]->(assessment:WaterBodyAssessment)
            OPTIONAL MATCH (assessment)-[:FOR_PARAMETER]->(parameter:Parameter)
            RETURN w.eu_cd_lw AS lake_id, w.name AS lake,
                   w.area_km2 / 100.0 AS area_km2,
                   chemical_status_count,
                   direct_chemical_status_count,
                   derived_chemical_status_count,
                   overlapping_groundwater_bodies,
                   pollutant_codes,
                   failed_standard_count,
                   failed_pollutants,
                   assessment.assessment_year AS assessment_year,
                   assessment.value AS assessment_value,
                   coalesce(parameter.name, assessment.parameter_name)
                     AS assessment_parameter,
                   coalesce(parameter.quality_component, assessment.quality_component)
                     AS quality_component,
                   assessment.method AS assessment_method
            ORDER BY lake, assessment_year, assessment_parameter
        """,
    ),
    "chemical_status": QueryDefinition(
        key="chemical_status",
        title="Chemische Überschreitungen",
        description=(
            "Chemische Statusangaben mit zugehörigen Schadstoffen. Neben direkten Angaben "
            "werden Statuswerte über räumlich überlappende Grundwasserkörper abgeleitet."
        ),
        cypher="""
            MATCH (w:WaterBody)-[:HAS_CHEMICAL_STATUS]->(cs:ChemicalStatus)
            OPTIONAL MATCH (cs)-[:FOR_POLLUTANT]->(p:Pollutant)
            RETURN w.eu_cd_lw AS lake_id, w.name AS lake,
                   'direkt' AS derivation,
                   null AS groundwater_body,
                   cs.failed_standard AS failed_standard,
                   p.pollutant_code AS pollutant_code,
                   p.name AS pollutant_name,
                   cs.fail_reason_code AS fail_reason_code,
                   cs.exemption_type AS exemption_type,
                   cs.inserted_at AS inserted_at
            UNION ALL
            MATCH (w:WaterBody)-[:OVERLAPS]->(g:GroundWaterBody)
                  -[:HAS_CHEMICAL_STATUS]->(cs:ChemicalStatus)
            OPTIONAL MATCH (cs)-[:FOR_POLLUTANT]->(p:Pollutant)
            RETURN w.eu_cd_lw AS lake_id, w.name AS lake,
                   'über Grundwasser-Überlappung' AS derivation,
                   g.name AS groundwater_body,
                   cs.failed_standard AS failed_standard,
                   p.pollutant_code AS pollutant_code,
                   p.name AS pollutant_name,
                   cs.fail_reason_code AS fail_reason_code,
                   cs.exemption_type AS exemption_type,
                   cs.inserted_at AS inserted_at
            ORDER BY failed_standard DESC, lake, pollutant_code
        """,
    ),
    "assessments": QueryDefinition(
        key="assessments",
        title="Bewertungen der Seen",
        description="Wasserkörperbewertungen mit Parameter, Jahr, Wert und Methode.",
        cypher="""
            MATCH (w:WaterBody)-[:HAS_ASSESSMENT]->(a:WaterBodyAssessment)
            OPTIONAL MATCH (a)-[:FOR_PARAMETER]->(p:Parameter)
            RETURN w.eu_cd_lw AS lake_id, w.name AS lake,
                   a.assessment_year AS assessment_year,
                   a.value AS assessment_value,
                   coalesce(p.name, a.parameter_name) AS assessment_parameter,
                   coalesce(p.quality_component, a.quality_component) AS quality_component,
                   a.method AS assessment_method
            ORDER BY lake, assessment_year, assessment_parameter
        """,
    ),
    "measurement_summary": QueryDefinition(
        key="measurement_summary",
        title="Messdatenübersicht",
        description="Aggregierter Datamart je See und Parameter; Rohmessungen bleiben im Graphen.",
        cypher="""
            MATCH (w:WaterBody)<-[:FOR_WATERBODY]-(m:Measurement)
                  -[:MEASURES_PARAMETER]->(p:Parameter)
            RETURN w.eu_cd_lw AS lake_id, w.name AS lake,
                   p.parameter_id AS parameter_id, p.name AS parameter,
                   p.quality_component AS quality_component, p.unit AS unit,
                   count(m) AS measurement_count,
                   min(m.value) AS minimum, avg(m.value) AS average,
                   max(m.value) AS maximum,
                   min(m.measured_at) AS first_measurement,
                   max(m.measured_at) AS last_measurement
            ORDER BY measurement_count DESC, lake, parameter
            LIMIT 1000
        """,
    ),
    "protected_areas": QueryDefinition(
        key="protected_areas",
        title="Seen und Schutzgebiete",
        description="Direkte beziehungsweise räumlich abgeleitete Schutzgebietsbeziehungen.",
        cypher="""
            MATCH (w:WaterBody)-[r:HAS_PROTECTED_AREA]->(p:ProtectedArea)
            RETURN w.eu_cd_lw AS lake_id, w.name AS lake,
                   p.protected_area_id AS protected_area_id,
                   p.name AS protected_area, p.area_type AS area_type,
                   r.relation_method AS relation_method,
                   r.overlap_area_km2 AS overlap_area_km2,
                   r.overlap_ratio AS overlap_ratio
            ORDER BY lake, protected_area
        """,
    ),
    "graph_overview": QueryDefinition(
        key="graph_overview",
        title="Inhalt des Knowledge Graphen",
        description="Anzahl der Knoten je Entitätstyp.",
        cypher="""
            MATCH (n)
            RETURN labels(n)[0] AS entity_type, count(*) AS entity_count
            ORDER BY entity_count DESC
        """,
    ),
}


def normalize_question(question: str) -> str:
    value = unicodedata.normalize("NFKD", question.casefold())
    return "".join(char for char in value if not unicodedata.combining(char))


def classify_question(question: str) -> str:
    """Map common German analytical questions to a transparent query template."""
    text = normalize_question(question)

    if re.search(r"\b(grosst|grosste|groesst|groeste|flachengrosst)\w*\b", text) and re.search(
        r"\bsee\w*\b", text
    ):
        return "largest_lake"
    if re.search(r"\b(tiefst|tiefste|maximale tiefe)\w*\b", text):
        return "deepest_lake"
    if "chem" in text and re.search(r"bewert|zusammenhang|beziehung|korrelation", text):
        return "chemical_assessment_relation"
    if re.search(r"chem|schadstoff|uberschreit|pollut", text):
        return "chemical_status"
    if re.search(r"bewert|zustandsklasse|potentialklasse", text):
        return "assessments"
    if re.search(r"mess|parameter|datamart|analyse", text):
        return "measurement_summary"
    if re.search(r"schutzgebiet|ffh|protected", text):
        return "protected_areas"
    return "graph_overview"
