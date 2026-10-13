"""Neo4j query execution, interpretation, and safe analyst queries."""

from __future__ import annotations

import csv
import io
import math
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from neo4j import GraphDatabase, READ_ACCESS

from .config import Settings
from .queries import QUERIES, classify_question


_WRITE_PATTERN = re.compile(
    r"\b(CREATE|DELETE|DETACH|DROP|MERGE|REMOVE|SET|LOAD\s+CSV|FOREACH|CALL|"
    r"GRANT|DENY|REVOKE|TERMINATE|START\s+DATABASE|STOP\s+DATABASE)\b",
    re.IGNORECASE,
)


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    return str(value)


class KnowledgeGraphService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_environment()
        self.driver = GraphDatabase.driver(
            self.settings.neo4j_uri,
            auth=(self.settings.neo4j_user, self.settings.neo4j_password),
        )

    def close(self) -> None:
        self.driver.close()

    def verify_connectivity(self) -> None:
        self.driver.verify_connectivity()

    def _execute(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.driver.session(
            database=self.settings.neo4j_database,
            default_access_mode=READ_ACCESS,
        ) as session:
            result = session.run(cypher, parameters or {})
            return [
                {key: json_value(value) for key, value in record.items()}
                for record in result
            ]

    def ask(self, question: str) -> dict[str, Any]:
        question = question.strip()
        if not question:
            raise ValueError("Bitte eine Frage eingeben.")
        intent = classify_question(question)
        definition = QUERIES[intent]
        rows = self._execute(definition.cypher)
        return {
            "question": question,
            "intent": intent,
            "title": definition.title,
            "answer": self._interpret(intent, rows),
            "description": definition.description,
            "columns": list(rows[0]) if rows else [],
            "rows": rows,
            "row_count": len(rows),
        }

    def execute_readonly(self, cypher: str) -> dict[str, Any]:
        cypher = cypher.strip()
        if not cypher:
            raise ValueError("Bitte eine Cypher-Abfrage eingeben.")
        if ";" in cypher.rstrip(";"):
            raise ValueError("Es ist nur eine einzelne Abfrage zulässig.")
        if _WRITE_PATTERN.search(cypher):
            raise ValueError("Im Expertenmodus sind ausschließlich lesende Abfragen zulässig.")
        if not re.match(r"^(MATCH|OPTIONAL\s+MATCH|WITH|UNWIND|RETURN)\b", cypher, re.I):
            raise ValueError("Die Abfrage muss mit MATCH, OPTIONAL MATCH, WITH, UNWIND oder RETURN beginnen.")
        rows = self._execute(cypher.rstrip(";"))
        return {
            "question": "Benutzerdefinierte Cypher-Abfrage",
            "intent": "custom_cypher",
            "title": "Ergebnis der Cypher-Abfrage",
            "answer": f"Die Abfrage liefert {len(rows)} Zeilen.",
            "description": "Direkte, nur lesende Abfrage des Knowledge Graphen.",
            "columns": list(rows[0]) if rows else [],
            "rows": rows,
            "row_count": len(rows),
        }

    @staticmethod
    def _interpret(intent: str, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "Für diese Frage wurden im Knowledge Graphen keine Daten gefunden."
        if intent == "largest_lake":
            row = rows[0]
            area = f"{row['area_km2']:.2f}".replace(".", ",")
            return f"Der größte See ist {row['lake']} mit {area} km²."
        if intent == "deepest_lake":
            row = rows[0]
            depth = f"{row['max_depth_m']:.2f}".replace(".", ",")
            return f"Der tiefste See ist {row['lake']} mit maximal {depth} m."
        if intent == "chemical_assessment_relation":
            return KnowledgeGraphService._chemical_assessment_insight(rows)
        if intent == "chemical_status":
            failed = sum(row.get("failed_standard") is True for row in rows)
            lakes = len({row.get("lake_id") for row in rows})
            return f"Es liegen {len(rows)} chemische Statusangaben für {lakes} Seen vor; {failed} davon markieren eine Überschreitung."
        if intent == "assessments":
            lakes = len({row.get("lake_id") for row in rows})
            return f"Der Graph enthält {len(rows)} Wasserkörperbewertungen für {lakes} Seen."
        if intent == "measurement_summary":
            measurements = sum(int(row.get("measurement_count") or 0) for row in rows)
            return f"Der Datamart fasst {measurements:,} Messwerte in {len(rows)} See-Parameter-Kombinationen zusammen.".replace(",", ".")
        if intent == "protected_areas":
            lakes = len({row.get("lake_id") for row in rows})
            return f"Es wurden {len(rows)} Beziehungen zwischen {lakes} Seen und Schutzgebieten gefunden."
        return "Der Knowledge Graph enthält die unten aufgeführten Entitätstypen."

    @staticmethod
    def _chemical_assessment_insight(rows: list[dict[str, Any]]) -> str:
        # Values 1..5 are ordinal assessment classes; 999 represents an unsecured result.
        by_lake: dict[str, list[float]] = defaultdict(list)
        failed_by_lake: dict[str, bool] = {}
        names: dict[str, str] = {}
        for row in rows:
            lake_id = row.get("lake_id")
            if not lake_id:
                continue
            names[lake_id] = row.get("lake") or lake_id
            if (row.get("chemical_status_count") or 0) > 0:
                failed_by_lake[lake_id] = (row.get("failed_standard_count") or 0) > 0
            value = row.get("assessment_value")
            if isinstance(value, (int, float)) and math.isfinite(value) and 1 <= value <= 5:
                by_lake[lake_id].append(float(value))

        groups: dict[bool, list[float]] = {True: [], False: []}
        for lake_id, values in by_lake.items():
            if values and lake_id in failed_by_lake:
                groups[failed_by_lake[lake_id]].append(sum(values) / len(values))

        with_fail = groups[True]
        without_fail = groups[False]
        if with_fail and without_fail:
            mean_fail = sum(with_fail) / len(with_fail)
            mean_ok = sum(without_fail) / len(without_fail)
            direction = "schlechter" if mean_fail > mean_ok else "besser"
            return (
                f"Seen mit chemischer Überschreitung haben im Mittel die Bewertung {mean_fail:.2f}, "
                f"Seen ohne erfasste Überschreitung {mean_ok:.2f} (1 = gut, 5 = schlecht). "
                f"Damit ist die mittlere Bewertung in der ersten Gruppe {direction}. "
                f"Das ist eine deskriptive Beziehung auf Basis von {len(with_fail) + len(without_fail)} Seen, kein Kausalnachweis."
            ).replace(f"{mean_fail:.2f}", f"{mean_fail:.2f}".replace(".", ",")).replace(
                f"{mean_ok:.2f}", f"{mean_ok:.2f}".replace(".", ",")
            )
        assessed = len(by_lake)
        chemically_linked = len(failed_by_lake)
        failed = sum(failed_by_lake.values())
        return (
            f"Für {assessed} Seen sind gültige Bewertungswerte vorhanden; {chemically_linked} davon sind direkt "
            f"oder über einen überlappenden Grundwasserkörper mit chemischen Statusdaten verknüpft. Bei {failed} "
            "Seen ist mindestens eine Überschreitung hinterlegt. Für einen belastbaren Gruppenvergleich fehlen "
            "Daten in einer der Gruppen."
        )

    @staticmethod
    def as_csv(result: dict[str, Any]) -> str:
        output = io.StringIO(newline="")
        columns = result.get("columns", [])
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in result.get("rows", []):
            writer.writerow({key: json_value(value) for key, value in row.items()})
        return output.getvalue()
