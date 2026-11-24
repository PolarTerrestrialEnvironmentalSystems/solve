import unittest

from shapely.geometry import LineString, Polygon

from build_elbe_inventory import group_proposals, intersect_states, reporting_state, validate_completeness


class InventoryTests(unittest.TestCase):
    def test_foreign_reporting_state_can_cross_target_state(self):
        self.assertEqual(reporting_state("DERW_DENI_MEL08OW01-00"), "NI")
        states = {"NI": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                  "MV": Polygon([(1, 0), (2, 0), (2, 1), (1, 1)])}
        measures, _, _ = intersect_states(LineString([(.5, .5), (1.5, .5)]), states, "RW")
        self.assertEqual(set(measures), {"NI", "MV"})
        self.assertEqual(measures["MV"]["fraction_of_source_geometry"], .5)

    def test_point_touch_is_not_positive_area_and_small_overlap_is_retained(self):
        lake = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        states = {"BB": Polygon([(9.99, 0), (11, 0), (11, 10), (9.99, 10)]),
                  "BE": Polygon([(10, 10), (11, 10), (11, 11), (10, 11)])}
        measures, touches, review = intersect_states(lake, states, "LW")
        self.assertIn("BB", measures)
        self.assertEqual(touches, ["BE"])
        self.assertEqual(review, ["BB"])

    def test_completeness_rejects_missing_and_duplicate_records(self):
        attr = [{"OBJECTID": 1, "euSurfaceWaterBodyCode": "a"}]
        geom = [{"properties": attr[0]}]
        self.assertEqual(validate_completeness([1], attr, geom)["count"], 1)
        for ids, attrs, geoms in [([1, 2], attr, geom), ([1], attr * 2, geom), ([1], attr, geom * 2)]:
            with self.assertRaises(ValueError):
                validate_completeness(ids, attrs, geoms)

    def test_code_mismatch_is_not_silently_joined(self):
        with self.assertRaises(ValueError):
            validate_completeness([1], [{"OBJECTID": 1, "euSurfaceWaterBodyCode": "a"}],
                                  [{"properties": {"OBJECTID": 1, "euSurfaceWaterBodyCode": "b"}}])

    def test_whole_lake_proposal_does_not_merge_same_named_other_lake(self):
        records = [{"waterbody_id": "DELW_DEBB80001582849239", "name_original": "SCHWERINER SEE",
                    "reporting_state": "BB", "selected_spatial_6states": True}]
        proposal = next(row for row in group_proposals(records) if row["group_id"] == "candidate_schweriner_see_mv")
        self.assertEqual(proposal["members"], [])
        self.assertEqual(len(proposal["missing_expected_ids_review"]), 2)


if __name__ == "__main__":
    unittest.main()
