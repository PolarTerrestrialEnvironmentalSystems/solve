from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "kg_interface" / "lexicon_v2.py"
SPEC = importlib.util.spec_from_file_location("lexicon_v2", MODULE_PATH)
lexicon_v2 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(lexicon_v2)

EXPECTED_LABELS = lexicon_v2.EXPECTED_LABELS
EXPECTED_PROPERTIES = lexicon_v2.EXPECTED_PROPERTIES
lookup = lexicon_v2.lookup
property_owners = lexicon_v2.property_owners
validate = lexicon_v2.validate


class WaterKgV2LexiconTests(unittest.TestCase):
    def test_complete_schema_coverage(self):
        validate()
        self.assertEqual(len(EXPECTED_LABELS), 22)
        self.assertEqual(set(property_owners()), EXPECTED_PROPERTIES)

    def test_ambiguous_name_is_retained(self):
        candidates = lookup("Name")
        owners = {item["owner_label"] for item in candidates if item["kind"] == "property"}
        self.assertIn("WaterBodyV2", owners)
        self.assertIn("MonitoringStationV2", owners)
        self.assertIn("ProtectedAreaV2", owners)

    def test_label_context_resolves_name(self):
        candidates = lookup("Bezeichnung", allowed_labels={"WaterBodyV2"})
        self.assertEqual({item["owner_label"] for item in candidates}, {"WaterBodyV2"})
        self.assertEqual({item["property"] for item in candidates}, {"name"})

    def test_domain_alias(self):
        candidates = lookup("maximale Tiefe", allowed_labels={"WaterBodyV2"})
        self.assertEqual(candidates[0]["property"], "max_depth_m")


if __name__ == "__main__":
    unittest.main()
