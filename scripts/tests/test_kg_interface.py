from __future__ import annotations

import unittest

from kg_interface.queries import classify_question
from kg_interface.service import KnowledgeGraphService, _WRITE_PATTERN


class QuestionClassifierTests(unittest.TestCase):
    def test_largest_lake(self):
        self.assertEqual(classify_question("Welcher ist der größte See?"), "largest_lake")

    def test_chemical_assessment_relationship(self):
        self.assertEqual(
            classify_question("Wie hängen chemischer Zustand und Bewertung zusammen?"),
            "chemical_assessment_relation",
        )

    def test_measurement_datamart(self):
        self.assertEqual(
            classify_question("Erzeuge einen Datamart der Messungen je Parameter."),
            "measurement_summary",
        )

    def test_unknown_question_gets_overview(self):
        self.assertEqual(classify_question("Was ist vorhanden?"), "graph_overview")


class InterpretationTests(unittest.TestCase):
    def test_largest_lake_answer(self):
        answer = KnowledgeGraphService._interpret(
            "largest_lake", [{"lake": "Testsee", "area_km2": 12.345}]
        )
        self.assertIn("Testsee", answer)
        self.assertIn("12,35", answer)

    def test_invalid_assessment_value_is_excluded(self):
        rows = [
            {"lake_id": "A", "lake": "A-See", "chemical_status_count": 1, "failed_standard_count": 1, "assessment_value": 999},
            {"lake_id": "B", "lake": "B-See", "chemical_status_count": 0, "failed_standard_count": 0, "assessment_value": 2},
        ]
        answer = KnowledgeGraphService._chemical_assessment_insight(rows)
        self.assertIn("Für 1 Seen", answer)

    def test_write_keywords_are_rejected(self):
        self.assertIsNotNone(_WRITE_PATTERN.search("MATCH (n) DELETE n"))
        self.assertIsNone(_WRITE_PATTERN.search("MATCH (n) RETURN n LIMIT 1"))


if __name__ == "__main__":
    unittest.main()
