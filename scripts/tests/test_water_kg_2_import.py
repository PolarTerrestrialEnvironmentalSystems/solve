from __future__ import annotations

import unittest

from water_kg_2_import import bool_code, key_text, schema_ddl


class WaterKg2HelpersTests(unittest.TestCase):
    def test_station_codes_are_normalized_without_float_suffix(self):
        self.assertEqual(key_text(2792.0), "2792")
        self.assertEqual(key_text("80001581473_HM"), "80001581473_HM")

    def test_boolean_source_codes(self):
        self.assertIs(bool_code("Y"), True)
        self.assertIs(bool_code("N"), False)
        self.assertIsNone(bool_code(None))

    def test_schema_contains_owner_and_source_constraints(self):
        ddl = "\n".join(schema_ddl("test_schema"))
        self.assertIn("ck_chemical_owner", ddl)
        self.assertIn("uq_measurement_source", ddl)
        self.assertIn("fk_measurement_parameter", ddl)


if __name__ == "__main__":
    unittest.main()
