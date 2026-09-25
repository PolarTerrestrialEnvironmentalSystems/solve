from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import link_fgg_elbe_stations as linker


class EntityLinkerTests(unittest.TestCase):
    def test_normalization_handles_german_spelling_and_punctuation(self):
        self.assertEqual(linker.normalize("Humboldtbrücke"), "humboldtbruecke")
        self.assertEqual(linker.normalize(" Potsdam, Havel "), "potsdam havel")

    def test_dummy_data_links_humboldtbruecke_to_nearest_station_in_water_body(self):
        stations = linker.read_source_stations(ROOT / "dummy_data" / "messstellen.shp")
        bodies = linker.read_water_bodies(ROOT / "dummy_data" / "lakewaterbody.shp")
        fgg = linker.read_fgg_stations(linker.find_fgg_csv(ROOT / "dummy_data2"))
        references = linker.read_references(ROOT / "dummy_data2" / "fgg_station_reference.csv")

        mappings, candidates = linker.link_stations(fgg, stations, bodies, references, top_n=5)

        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0]["source_station_id"], "DESM_BB_2831")
        self.assertEqual(mappings[0]["source_water_body_id"], "DELW_DEBB8000158393")
        self.assertEqual(mappings[0]["match_status"], "review")
        self.assertEqual(len(candidates), 5)


if __name__ == "__main__":
    unittest.main()
