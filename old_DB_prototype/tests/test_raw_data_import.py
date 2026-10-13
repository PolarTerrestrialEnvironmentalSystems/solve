from __future__ import annotations

import unittest

import pandas as pd

from raw_data_import import database_name, inferred_long_name, prepare_frame


class RawImportHelpersTests(unittest.TestCase):
    def test_database_name_normalizes_source_headers(self):
        self.assertEqual(database_name("EU_CD_LW"), "eu_cd_lw")
        self.assertEqual(database_name("Unnamed: 0"), "unnamed_0")

    def test_duplicate_database_columns_get_suffix(self):
        frame, metadata = prepare_frame(pd.DataFrame([[1, 2]], columns=["A-B", "A B"]))
        self.assertEqual(list(frame.columns), ["source_row_number", "a_b", "a_b_2"])
        self.assertEqual(metadata[1]["database_column"], "a_b_2")

    def test_dbf_truncation_can_use_dictionary(self):
        lookup = {"drain_cd": "TeilgebietsKennung"}
        self.assertEqual(inferred_long_name("DRAIN_C", lookup), "TeilgebietsKennung")


if __name__ == "__main__":
    unittest.main()
