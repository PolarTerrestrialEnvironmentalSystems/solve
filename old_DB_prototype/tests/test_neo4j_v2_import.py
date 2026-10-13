from __future__ import annotations

import unittest

from neo4j_v2_import import (
    BUILD_MARKER,
    FINAL_MARKER,
    NODE_SPECS,
    RELATIONSHIP_SPECS,
    composite_key,
    parameter_key,
    source_key,
    station_key,
    WaterKgV2Importer,
)


class Neo4jV2ImportTests(unittest.TestCase):
    def test_composite_key_is_unambiguous(self):
        self.assertNotEqual(composite_key("a|b", "c"), composite_key("a", "b|c"))
        self.assertEqual(composite_key("DE_LW_1", "2792"), "7:DE_LW_1|4:2792")

    def test_domain_keys_are_reproducible(self):
        self.assertEqual(
            station_key({"water_body_id": "DE_LW_1", "identity_station_code": "2792"}),
            station_key({"water_body_id": "DE_LW_1", "identity_station_code": "2792"}),
        )
        self.assertNotEqual(
            station_key({"water_body_id": "DE_LW_1", "identity_station_code": "2792"}),
            station_key({"water_body_id": "DE_LW_2", "identity_station_code": "2792"}),
        )
        self.assertNotEqual(
            parameter_key({"name": "Phosphor", "quality_component": "Chemie"}),
            parameter_key({"name": "Phosphor", "quality_component": "Biologie"}),
        )
        self.assertEqual(
            source_key({"source_file": "einzeldaten.csv", "source_row_number": 12}),
            "15:einzeldaten.csv|2:12",
        )

    def test_every_relationship_uses_v2_labels(self):
        labels = {spec.label for spec in NODE_SPECS}
        for spec in RELATIONSHIP_SPECS:
            self.assertIn(spec.start_label, labels)
            self.assertIn(spec.end_label, labels)
            self.assertTrue(spec.start_build_label.endswith("V2Build"))
            self.assertTrue(spec.end_build_label.endswith("V2Build"))

    def test_dedicated_markers_protect_the_legacy_graph(self):
        self.assertEqual(FINAL_MARKER, "WaterKgV2")
        self.assertEqual(BUILD_MARKER, "WaterKgV2Build")
        self.assertTrue(all(spec.label.endswith("V2") for spec in NODE_SPECS))

    def test_identifier_relationship_builds_its_composite_target_key(self):
        importer = WaterKgV2Importer(None, None, "neo4j")
        spec = next(item for item in RELATIONSHIP_SPECS if item.name == "water_identifier")
        row = {
            "start_key": "DE_LW_1",
            "identifier_type": "EU_CD_LS",
            "identifier": "DE_LS_1",
        }
        prepared = importer.prepare_relationship_row(spec, row)
        self.assertEqual(prepared["end"], composite_key("EU_CD_LS", "DE_LS_1"))

    def test_protected_area_edges_keep_distinct_relation_methods(self):
        importer = WaterKgV2Importer(None, None, "neo4j")
        spec = next(item for item in RELATIONSHIP_SPECS if item.name == "water_protected")
        base = {
            "start_key": "DE_LW_1",
            "end_key": "AREA_1",
            "overlap_area_km2": None,
            "water_body_overlap_ratio": None,
        }
        spatial = importer.prepare_relationship_row(
            spec, {**base, "relation_method": "spatial_overlap"}
        )
        referenced = importer.prepare_relationship_row(
            spec, {**base, "relation_method": "source_reference"}
        )
        self.assertNotEqual(spatial["relationship_key"], referenced["relationship_key"])


if __name__ == "__main__":
    unittest.main()
