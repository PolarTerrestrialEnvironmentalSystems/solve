"""German alias lexicon for the versioned SOLVE WaterKgV2 schema.

The lexicon deliberately retains ambiguity.  An alias such as ``name`` maps
to several label/property candidates; callers resolve it using entity types,
the intended answer type, graph reachability, and question context.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Iterable


LABEL_ALIASES: dict[str, tuple[str, ...]] = {
    "CatchmentV2": ("einzugsgebiet", "einzugsgebiete", "catchment"),
    "ChemicalStatusV2": ("chemischer status", "chemischer zustand", "chemiebewertung"),
    "CoreLocationV2": ("bohrkernstandort", "kernstandort", "bohrstelle", "core location"),
    "GroundwaterBodyV2": ("grundwasserkörper", "grundwasserkoerper", "grundwasser"),
    "ImportIssueV2": ("importproblem", "importfehler", "datenproblem", "validierungsproblem"),
    "MeasurementV2": ("messung", "messungen", "messwert", "messwerte"),
    "MonitoringStationV2": ("messstation", "messstelle", "monitoringstation", "station"),
    "PaleoElementV2": ("paläoelement", "palaeoelement", "element", "chemisches element"),
    "PaleoMeasurementV2": ("paläomessung", "palaeomessung", "bohrkernmessung"),
    "ParameterV2": ("parameter", "messparameter", "messgröße", "messgroesse"),
    "PlanningUnitV2": ("planungseinheit", "bewirtschaftungseinheit", "arbeitsgebiet"),
    "PollutantV2": ("schadstoff", "schadstoffe", "problemstoff"),
    "ProtectedAreaAssessmentV2": (
        "schutzgebietsbewertung", "bewertung des schutzgebiets", "schutzgebiet bewertung"
    ),
    "ProtectedAreaV2": ("schutzgebiet", "schutzgebiete", "wasserschutzgebiet"),
    "SamplingSectionV2": ("probenabschnitt", "beprobungsabschnitt", "tiefenabschnitt"),
    "StationAliasV2": ("stationsalias", "stationscode", "alternativer stationscode"),
    "StationAssessmentV2": ("stationsbewertung", "messstellenbewertung", "bewertung der station"),
    "UnitV2": ("einheit", "maßeinheit", "masseinheit", "messwert-einheit"),
    "WaterBodyAssessmentV2": ("wasserkörperbewertung", "gewaesserbewertung", "seebewertung"),
    "WaterBodyIdentifierV2": ("wasserkörperkennung", "gewässerkennung", "see-kennung"),
    "WaterBodyV2": ("wasserkörper", "gewässer", "gewaesser", "see", "seen", "oberflächengewässer"),
    "WaterKgV2": ("water kg v2", "wasser knowledge graph", "solve knowledge graph", "v2 graph"),
}


# The sets reflect the properties produced by neo4j_v2_import.py from the
# water_kg_2 source tables.  WaterKgV2 is a marker label, not a separate node
# type; its entries describe metadata common to all V2 nodes.
LABEL_PROPERTIES: dict[str, set[str]] = {
    "CatchmentV2": {
        "catchment_id", "name", "area_km2", "area_number", "source_water_body_code",
        "source_planning_unit_code", "planning_unit_code", "river_basin_code",
        "water_authority", "description_from", "description_to", "comment",
        "metadata_reference",
    },
    "ChemicalStatusV2": {
        "chemical_status_id", "source_file", "source_row_number", "source_key",
        "surface_water_body_id", "groundwater_body_id", "pollutant_id",
        "failed_standard", "risk_code", "upward_trend_code", "trend_code",
        "trend_reversal_code", "exemption_type", "exemption_reason", "exception_code",
        "level_set", "fail_reason_code", "fail_reason_other", "inserted_at",
        "inserted_by", "river_basin_code", "metadata_reference",
    },
    "CoreLocationV2": {"core_code", "water_body_id", "longitude", "latitude", "location"},
    "GroundwaterBodyV2": {
        "groundwater_body_id", "name", "horizon", "aquifer_type", "geological_formation",
        "layered", "river_basin_code", "source_planning_unit_code", "planning_unit_code",
        "quantitative_status", "chemical_status", "metadata_reference",
    },
    "ImportIssueV2": {
        "issue_id", "issue_key", "entity_type", "source_file", "source_row_number",
        "source_column", "source_value", "issue_code", "message",
    },
    "MeasurementV2": {
        "measurement_id", "source_file", "source_row_number", "source_index", "source_key",
        "water_body_id", "station_id", "sampling_section_id", "source_station_code",
        "source_section_code", "parameter_id", "unit_id", "measured_at", "value",
        "comment", "method", "monitoring_program", "abundance_type",
    },
    "MonitoringStationV2": {
        "station_id", "station_key", "water_body_id", "eu_station_code", "name",
        "station_origin", "identity_station_code", "location",
    },
    "PaleoElementV2": {"element_id", "symbol"},
    "PaleoMeasurementV2": {
        "paleo_measurement_id", "source_file", "source_row_number", "source_key",
        "core_code", "element_id", "age_years_bp", "value_dimensionless",
    },
    "ParameterV2": {"parameter_id", "parameter_key", "name", "quality_component"},
    "PlanningUnitV2": {
        "planning_unit_code", "name", "work_area_code", "river_basin_code",
        "country_state_code", "delivery_date", "metadata_reference", "url", "wb_username",
    },
    "PollutantV2": {"pollutant_id", "pollutant_code", "pollutant_other"},
    "ProtectedAreaAssessmentV2": {
        "assessment_id", "source_file", "source_row_number", "source_key",
        "source_protected_area_code", "source_water_body_code", "protected_area_id",
        "surface_water_body_id", "groundwater_body_id", "template", "name",
        "legislation_code", "legislation_link", "area_type", "designation_begin",
        "comment", "exemption_code", "habitat_status", "metadata_reference",
    },
    "ProtectedAreaV2": {
        "protected_area_id", "protected_area_key", "name", "area_type",
        "legislation_code", "member_state_code", "water_protection_zone", "area_status",
        "river_basin_code", "metadata_reference", "url", "source_file",
    },
    "SamplingSectionV2": {
        "sampling_section_id", "source_file", "source_row_number", "source_key",
        "water_body_id", "station_id", "source_station_code", "section_code",
        "sampling_year", "length_from", "length_to", "depth_from", "depth_to",
    },
    "StationAliasV2": {
        "alias_id", "station_id", "water_body_id", "alias_code", "alias_type", "source_file",
    },
    "StationAssessmentV2": {
        "assessment_id", "source_file", "source_row_number", "source_key", "water_body_id",
        "station_id", "source_station_code", "parameter_id", "unit_id", "assessment_year",
        "value_raw", "value_normalized", "is_valid", "validation_reason", "description",
        "method", "expert_judgement", "comment", "reported_x", "reported_y",
    },
    "UnitV2": {"unit_id", "symbol"},
    "WaterBodyAssessmentV2": {
        "assessment_id", "source_file", "source_row_number", "source_key", "water_body_id",
        "parameter_id", "assessment_year", "value_raw", "value_normalized", "is_valid",
        "validation_reason", "method",
    },
    "WaterBodyIdentifierV2": {"identifier", "identifier_key", "identifier_type", "water_body_id"},
    "WaterBodyV2": {
        "water_body_id", "segment_code", "name", "lawa_id", "area_km2", "volume_m3",
        "volume_source", "max_depth_m", "stratification", "residence_time", "lawa_type",
        "water_type", "artificial", "modified", "river_basin_code",
        "source_planning_unit_code", "planning_unit_code", "ecological_status",
        "chemical_status",
    },
    "WaterKgV2": {"graph_version", "import_run_id"},
}


# Curated domain-language aliases.  The technical property name and a version
# with spaces are always added automatically, so this table can focus on words
# users are likely to put in German questions.
CURATED_PROPERTY_ALIASES: dict[str, tuple[str, ...]] = {
    "abundance_type": ("abundanztyp", "häufigkeitstyp", "zählweise"),
    "age_years_bp": ("alter", "alter vor heute", "jahre vor heute", "bp alter"),
    "alias_code": ("aliascode", "alternativer stationscode"),
    "alias_id": ("alias-id", "aliaskennung"),
    "alias_type": ("aliastyp", "art des stationscodes"),
    "aquifer_type": ("grundwasserleitertyp", "aquifertyp"),
    "area_km2": ("fläche", "fläche in km2", "größe", "flaeche"),
    "area_number": ("gebietsnummer", "flächennummer"),
    "area_status": ("gebietsstatus", "schutzgebietsstatus"),
    "area_type": ("gebietstyp", "schutzgebietstyp", "gebietsart"),
    "artificial": ("künstlich", "künstliches gewässer"),
    "assessment_id": ("bewertungs-id", "bewertungskennung"),
    "assessment_year": ("bewertungsjahr", "jahr der bewertung"),
    "catchment_id": ("einzugsgebiets-id", "einzugsgebietskennung"),
    "chemical_status": ("chemischer status", "chemischer zustand"),
    "chemical_status_id": ("chemischer-status-id", "chemiebewertungs-id"),
    "comment": ("kommentar", "bemerkung", "anmerkung"),
    "core_code": ("bohrkerncode", "kerncode"),
    "country_state_code": ("bundeslandcode", "ländercode"),
    "delivery_date": ("lieferdatum", "abgabedatum"),
    "depth_from": ("tiefe von", "obere tiefe", "starttiefe"),
    "depth_to": ("tiefe bis", "untere tiefe", "endtiefe"),
    "description": ("beschreibung", "beschreibungstext"),
    "description_from": ("beschreibung von", "herkunftsbeschreibung"),
    "description_to": ("beschreibung bis", "zielbeschreibung"),
    "designation_begin": ("ausweisungsbeginn", "beginn der ausweisung"),
    "ecological_status": ("ökologischer status", "ökologischer zustand"),
    "element_id": ("element-id", "elementkennung"),
    "entity_type": ("entitätstyp", "betroffener knotentyp"),
    "eu_station_code": ("eu-stationscode", "eu-messstellencode"),
    "exception_code": ("ausnahmecode", "exception code"),
    "exemption_code": ("ausnahmegenehmigungscode", "befreiungscode"),
    "exemption_reason": ("ausnahmegrund", "befreiungsgrund"),
    "exemption_type": ("ausnahmetyp", "art der ausnahme"),
    "expert_judgement": ("expertenurteil", "experteneinschätzung"),
    "fail_reason_code": ("fehlergrundcode", "verfehlungsgrundcode"),
    "fail_reason_other": ("sonstiger fehlergrund", "weiterer verfehlungsgrund"),
    "failed_standard": ("norm verfehlt", "grenzwert überschritten", "standard nicht erfüllt"),
    "geological_formation": ("geologische formation", "geologie"),
    "graph_version": ("graphversion", "version des graphen"),
    "groundwater_body_id": ("grundwasserkörper-id", "grundwasserkennung"),
    "habitat_status": ("habitatstatus", "lebensraumstatus"),
    "horizon": ("horizont", "grundwasserhorizont"),
    "identifier": ("kennung", "identifikator", "wasserkörpercode"),
    "identifier_key": ("kennungsschlüssel", "identifier key"),
    "identifier_type": ("kennungstyp", "art der kennung"),
    "identity_station_code": ("identitätsstationscode", "maßgeblicher stationscode"),
    "import_run_id": ("importlauf-id", "importkennung"),
    "inserted_at": ("eingefügt am", "einfügedatum"),
    "inserted_by": ("eingefügt von", "importiert von"),
    "is_valid": ("gültig", "valide", "ist gültig"),
    "issue_code": ("problemcode", "fehlercode"),
    "issue_id": ("problem-id", "fehler-id"),
    "issue_key": ("problemschlüssel", "fehlerschlüssel"),
    "latitude": ("breitengrad", "geografische breite"),
    "lawa_id": ("lawa-id", "lawa kennung"),
    "lawa_type": ("lawa-typ", "lawa gewässertyp"),
    "layered": ("geschichtet", "mehrschichtig", "stockwerk"),
    "legislation_code": ("rechtsgrundlagencode", "gesetzescode"),
    "legislation_link": ("link zur rechtsgrundlage", "gesetzeslink"),
    "length_from": ("länge von", "abschnittsbeginn"),
    "length_to": ("länge bis", "abschnittsende"),
    "level_set": ("festgelegtes niveau", "schwellenwert gesetzt"),
    "location": ("standort", "position", "koordinate"),
    "longitude": ("längengrad", "geografische länge"),
    "max_depth_m": ("maximale tiefe", "größte tiefe", "tiefe in meter"),
    "measured_at": ("messdatum", "gemessen am", "zeitpunkt der messung"),
    "measurement_id": ("messungs-id", "messwert-id"),
    "member_state_code": ("mitgliedstaatcode", "eu-staatencode"),
    "message": ("fehlermeldung", "meldung", "problemtext"),
    "metadata_reference": ("metadatenreferenz", "metadatenquelle"),
    "method": ("methode", "messmethode", "bewertungsmethode"),
    "modified": ("verändert", "erheblich verändert", "modifiziert"),
    "monitoring_program": ("monitoringprogramm", "messprogramm", "überwachungsprogramm"),
    "name": ("name", "bezeichnung", "titel"),
    "paleo_measurement_id": ("paläomessungs-id", "bohrkernmessungs-id"),
    "parameter_id": ("parameter-id", "parameterkennung"),
    "parameter_key": ("parameterschlüssel", "parameter key"),
    "planning_unit_code": ("planungseinheitscode", "code der planungseinheit"),
    "pollutant_code": ("schadstoffcode", "problemstoffcode"),
    "pollutant_id": ("schadstoff-id", "schadstoffkennung"),
    "pollutant_other": ("sonstiger schadstoff", "schadstoffbeschreibung"),
    "protected_area_id": ("schutzgebiets-id", "schutzgebietskennung"),
    "protected_area_key": ("schutzgebietsschlüssel", "schutzgebiet key"),
    "quality_component": ("qualitätskomponente", "qualitaetskomponente"),
    "quantitative_status": ("mengenmäßiger zustand", "quantitativer status"),
    "reported_x": ("gemeldete x-koordinate", "x-koordinate"),
    "reported_y": ("gemeldete y-koordinate", "y-koordinate"),
    "residence_time": ("verweilzeit", "aufenthaltszeit"),
    "risk_code": ("risikocode", "risikoklasse"),
    "river_basin_code": ("flussgebietscode", "einzugsgebietscode"),
    "sampling_section_id": ("probenabschnitts-id", "beprobungsabschnitt-id"),
    "sampling_year": ("probenjahr", "beprobungsjahr"),
    "section_code": ("abschnittscode", "probenabschnittscode"),
    "segment_code": ("segmentcode", "gewässersegmentcode"),
    "source_column": ("quellspalte", "ursprungsspalte"),
    "source_file": ("quelldatei", "ursprungsdatei", "dateiquelle"),
    "source_index": ("quellindex", "ursprungsindex"),
    "source_key": ("quellschlüssel", "herkunftsschlüssel"),
    "source_planning_unit_code": ("ursprünglicher planungseinheitscode",),
    "source_protected_area_code": ("ursprünglicher schutzgebietscode",),
    "source_row_number": ("quellzeile", "zeilennummer der quelle"),
    "source_section_code": ("ursprünglicher abschnittscode",),
    "source_station_code": ("ursprünglicher stationscode", "quellstationscode"),
    "source_value": ("quellwert", "ursprünglicher wert"),
    "source_water_body_code": ("ursprünglicher wasserkörpercode", "quellgewässercode"),
    "station_id": ("stations-id", "messstellen-id"),
    "station_key": ("stationsschlüssel", "messstellenschlüssel"),
    "station_origin": ("stationsherkunft", "herkunft der messstelle"),
    "stratification": ("schichtung", "stratifikation"),
    "surface_water_body_id": ("oberflächenwasserkörper-id", "oberflächengewässerkennung"),
    "symbol": ("symbol", "einheitensymbol", "elementsymbol"),
    "template": ("vorlage", "bewertungsvorlage"),
    "trend_code": ("trendcode", "entwicklungscode"),
    "trend_reversal_code": ("trendumkehrcode", "code der trendumkehr"),
    "unit_id": ("einheiten-id", "einheitenkennung"),
    "upward_trend_code": ("aufwärtstrendcode", "steigender trend"),
    "url": ("url", "webadresse", "link"),
    "validation_reason": ("validierungsgrund", "grund der gültigkeit"),
    "value": ("wert", "messwert", "gemessener wert"),
    "value_dimensionless": ("dimensionsloser wert", "normierter paläowert"),
    "value_normalized": ("normalisierter wert", "bereinigter bewertungswert"),
    "value_raw": ("rohwert", "ursprünglicher bewertungswert"),
    "volume_m3": ("volumen", "volumen in kubikmeter", "wasservolumen"),
    "volume_source": ("volumenquelle", "quelle des volumens"),
    "water_authority": ("wasserbehörde", "zuständige wasserbehörde"),
    "water_body_id": ("wasserkörper-id", "gewässer-id", "see-id"),
    "water_protection_zone": ("wasserschutzzone", "schutzgebietszone"),
    "water_type": ("gewässertyp", "wassertyp", "seetyp"),
    "wb_username": ("wb benutzername", "bearbeitername"),
    "work_area_code": ("arbeitsgebietscode", "arbeitsbereichscode"),
}


EXPECTED_LABELS = set(LABEL_ALIASES)
EXPECTED_PROPERTIES = {
    "abundance_type", "age_years_bp", "alias_code", "alias_id", "alias_type",
    "aquifer_type", "area_km2", "area_number", "area_status", "area_type", "artificial",
    "assessment_id", "assessment_year", "catchment_id", "chemical_status",
    "chemical_status_id", "comment", "core_code", "country_state_code", "delivery_date",
    "depth_from", "depth_to", "description", "description_from", "description_to",
    "designation_begin", "ecological_status", "element_id", "entity_type",
    "eu_station_code", "exception_code", "exemption_code", "exemption_reason",
    "exemption_type", "expert_judgement", "fail_reason_code", "fail_reason_other",
    "failed_standard", "geological_formation", "graph_version", "groundwater_body_id",
    "habitat_status", "horizon", "identifier", "identifier_key", "identifier_type",
    "identity_station_code", "import_run_id", "inserted_at", "inserted_by", "is_valid",
    "issue_code", "issue_id", "issue_key", "latitude", "lawa_id", "lawa_type", "layered",
    "legislation_code", "legislation_link", "length_from", "length_to", "level_set",
    "location", "longitude", "max_depth_m", "measured_at", "measurement_id",
    "member_state_code", "message", "metadata_reference", "method", "modified",
    "monitoring_program", "name", "paleo_measurement_id", "parameter_id", "parameter_key",
    "planning_unit_code", "pollutant_code", "pollutant_id", "pollutant_other",
    "protected_area_id", "protected_area_key", "quality_component", "quantitative_status",
    "reported_x", "reported_y", "residence_time", "risk_code", "river_basin_code",
    "sampling_section_id", "sampling_year", "section_code", "segment_code", "source_column",
    "source_file", "source_index", "source_key", "source_planning_unit_code",
    "source_protected_area_code", "source_row_number", "source_section_code",
    "source_station_code", "source_value", "source_water_body_code", "station_id",
    "station_key", "station_origin", "stratification", "surface_water_body_id", "symbol",
    "template", "trend_code", "trend_reversal_code", "unit_id", "upward_trend_code", "url",
    "validation_reason", "value", "value_dimensionless", "value_normalized", "value_raw",
    "volume_m3", "volume_source", "water_authority", "water_body_id",
    "water_protection_zone", "water_type", "wb_username", "work_area_code",
}


def normalize(text: str) -> str:
    """Normalize an alias without removing German semantic distinctions."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = text.replace("_", " ")
    text = re.sub(r"[^\wäöüß]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())

def property_owners() -> dict[str,list[str]]:
    owners : dict[str,list[str]] = defaultdict(list)
    for label, properties in LABEL_PROPERTIES.items():
        for prop in properties:
            owners[prop].append(label)
    return owners









# def property_owners() -> dict[str, list[str]]:
#     owners: dict[str, list[str]] = defaultdict(list)
#     for label, properties in LABEL_PROPERTIES.items():
#         for prop in properties:
#             owners[prop].append(label)
#     return {prop: sorted(labels) for prop, labels in sorted(owners.items())}

def cadidate_identity(candidate: dict) -> tuple:
    if candidate["kind"] == "label":
        return(
            "label",
            candidate["label"]
        )
    else:
        return(
            "property",
            candidate["property"],
            candidate["owner_label"]
        )

def _add(index: dict[str, list[dict]], alias: str, candidate: dict) -> None:
    key = normalize(alias)
    if not key:
        return

    identity = cadidate_identity(candidate)
    for existing in index[key]:
        existing_identity = cadidate_identity(existing)
        if existing_identity == identity:
            if candidate["weight"] > existing["weight"]:
                existing.update(candidate)
            return
    index[key].append(candidate)



def build_alias_index() -> dict[str,list[dict]]:
    index : dict[str,list[dict]] = defaultdict(list)

    for label, aliases in LABEL_ALIASES.items():
        _add(index, label,{"kind":"label","label":label, "weight":1.0, "source": "technical"})
        for alias in aliases:
            _add(index, alias, {"kind":"label","label":label, "weight":0.95, "source": "curated"})

    for prop, owners in property_owners().items():
        for owner in owners:
            _add(
                index, 
                prop, 
                {
                    "kind": "property",
                    "property": prop,
                    "owner_label" : owner,
                    "weight": 1.0,
                    "source":"technical"
            }
            )
            for alias in CURATED_PROPERTY_ALIASES.get(prop,()):
                _add(
                    index,
                    alias,
                    {
                        "kind":"property",
                        "property": prop,
                        "owner_label": owner,
                        "weight":0.95,
                        "source": "curated"
                    }
                )
    return dict(index)















# def build_alias_index() -> dict[str, list[dict]]:
#     """Build an inverted alias index while preserving ambiguous candidates."""
#     index: dict[str, list[dict]] = defaultdict(list)

#     for label, aliases in LABEL_ALIASES.items():
#         _add(index, label, {"kind": "label", "label": label, "weight": 1.0, "source": "technical"})
#         for alias in aliases:
#             _add(index, alias, {"kind": "label", "label": label, "weight": 0.95, "source": "curated"})

#     for prop, owners in property_owners().items():
#         automatic = {prop, prop.replace("_", " ")}
#         curated = set(CURATED_PROPERTY_ALIASES.get(prop, ()))
#         for label in owners:
#             for alias in automatic:
#                 _add(index, alias, {
#                     "kind": "property", "property": prop, "owner_label": label,
#                     "weight": 1.0 if alias == prop else 0.85, "source": "technical",
#                 })
#             for alias in curated:
#                 _add(index, alias, {
#                     "kind": "property", "property": prop, "owner_label": label,
#                     "weight": 0.95, "source": "curated",
#                 })

#     return {
#         alias: sorted(candidates, key=lambda item: (-item["weight"], item.get("owner_label", "")))
#         for alias, candidates in sorted(index.items())
#     }


def lookup(alias: str, allowed_labels: Iterable[str] | None = None) -> list[dict]:
    """Return exact normalized candidates, optionally constrained by labels."""
    candidates = build_alias_index().get(normalize(alias), [])
    if allowed_labels is None:
        return candidates
    allowed = set(allowed_labels)
    return [
        candidate for candidate in candidates
        if candidate.get("label", candidate.get("owner_label")) in allowed
    ]


def validate() -> None:
    if set(LABEL_PROPERTIES) != EXPECTED_LABELS:
        missing = EXPECTED_LABELS - set(LABEL_PROPERTIES)
        extra = set(LABEL_PROPERTIES) - EXPECTED_LABELS
        raise ValueError(f"Label coverage mismatch: missing={sorted(missing)}, extra={sorted(extra)}")

    owners = property_owners()
    if set(owners) != EXPECTED_PROPERTIES:
        missing = EXPECTED_PROPERTIES - set(owners)
        extra = set(owners) - EXPECTED_PROPERTIES
        raise ValueError(f"Property coverage mismatch: missing={sorted(missing)}, extra={sorted(extra)}")

    missing_aliases = EXPECTED_PROPERTIES - set(CURATED_PROPERTY_ALIASES)
    if missing_aliases:
        raise ValueError(f"Properties without curated aliases: {sorted(missing_aliases)}")


def export(path: Path) -> None:
    validate()
    payload = {
        "metadata": {
            "schema": "WaterKgV2",
            "language": "de",
            "ambiguity_policy": "retain_all_candidates",
            "resolution_signals": [
                "lexicon_weight", "detected_entity_type", "expected_answer_type",
                "graph_reachability", "question_context",
            ],
            "water_kg_v2_is_marker_label": True,
        },
        "label_aliases": {key: list(value) for key, value in LABEL_ALIASES.items()},
        "label_properties": {key: sorted(value) for key, value in LABEL_PROPERTIES.items()},
        "property_owners": property_owners(),
        "property_aliases": {key: list(value) for key, value in CURATED_PROPERTY_ALIASES.items()},
        "alias_index": build_alias_index(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate or export the SOLVE WaterKgV2 lexicon")
    parser.add_argument("--export", type=Path, help="Write the expanded JSON lexicon")
    args = parser.parse_args()
    validate()
    if args.export:
        export(args.export)
        print(args.export.resolve())
    else:
        print(
            f"valid: {len(EXPECTED_LABELS)} labels, "
            f"{len(EXPECTED_PROPERTIES)} properties, {len(build_alias_index())} aliases"
        )


if __name__ == "__main__":
    main()
