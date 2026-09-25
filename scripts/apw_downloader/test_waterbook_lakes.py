"""Offline tests for public search captures, resume and conservative associations."""
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import apw
import common
import waterbook
import waterbook_lakes as lake

HAS_GEO = all(importlib.util.find_spec(m) for m in ("shapely", "pyproj"))
URL = "https://apw.brandenburg.de/project/cardoMap/Documents/ewabu/wasserbuchblatt_10010000030.pdf"


def payload(number=1):
    if not number:
        return {"text": "Die Anfrage ergab keine Treffer.", "records": []}
    return {"text": "1 Objekt", "records": [{"url": URL, "fields": {"Wasserbuchblattnummer:": "10010000030"}}]}


def parsed_site(**values):
    return {"entry": {"entry_id": "entry", "waterbook_number": "10010000030", "title": None},
            "cases": [{"case_id": "case1"}], "uses": [],
            "sites": [{"site_id": "site1", "case_id": "case1", "easting": 367050, "northing": 5805050,
                       "crs": "EPSG:25833", "name": None, "water_category": "Oberflächengewässer", **values}]}


@unittest.skipUnless(HAS_GEO, "pyproj/shapely fehlen; requirements.txt installieren")
class LakeTests(unittest.TestCase):
    def source(self, folder):
        from shapely.geometry import box, mapping
        from shapely.ops import transform
        from pyproj import Transformer
        inverse = Transformer.from_crs(25833, 4326, always_xy=True)
        source = common.Snapshot(Path(folder), "source")
        data = json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "id": "lake1",
            "properties": {"EU_CD_LW": "LW1", "S_NAME": "Tiefer See"},
            "geometry": mapping(transform(inverse.transform, box(367000, 5805000, 367100, 5805100)))}]}).encode()
        source.record("lakes1", source.root / "raw/lakes.geojson", data, "https://maps.brandenburg.de/services/wfs/wrrl3bwz_wfs")
        source.state.update(wrrl_scope={"kind": "water_body_ids", "water_body_ids": ["LW1"]})
        source.state["wrrl"]["lakes"] = {"complete": True, "pages": ["lakes1"], "expected": 1, "downloaded": 1}
        source.save()
        return source

    def lakes(self):
        from shapely.geometry import box
        return [{"water_body_id": "LW1", "name": "Tiefer See", "geometry": box(367000, 5805000, 367100, 5805100)}]

    def evidence(self, scope, response=None):
        return {"schema_version": 1, "source_url": waterbook.APW_URL,
                "queries": [{"query": q, "payload": payload() if response is None else response} for q in scope["queries"]]}

    def test_reference_hashes_and_source_unchanged_on_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = self.source(folder), common.Snapshot(Path(folder), "target")
            before = source.path.read_bytes()
            scope = lake.prepare(output, source.root)
            self.assertEqual(scope["queries"][0]["name"], "Tiefer See")
            self.assertTrue(scope["queries"][0]["geometry_input"].endswith(";25833"))
            self.assertEqual(lake.prepare(output, source.root), scope)
            self.assertEqual(source.path.read_bytes(), before)
            common.atomic_bytes(source.root / "raw/lakes.geojson", b"tampered")
            with self.assertRaisesRegex(RuntimeError, "verändert"):
                lake.prepare(output, source.root)

    def test_partial_unfiltered_or_missing_lake_references_rejected(self):
        for change in ("partial", "unfiltered", "missing_id", "count"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                source = self.source(folder)
                if change == "partial":
                    source.state["wrrl"]["lakes"]["complete"] = False
                elif change == "unfiltered":
                    source.state["wrrl_scope"] = {"kind": "all"}
                elif change == "missing_id":
                    source.state["wrrl_scope"]["water_body_ids"].append("LW2")
                else:
                    source.state["wrrl"]["lakes"]["expected"] = 2
                source.save()
                with self.assertRaises(ValueError):
                    lake.read_reference(source.root)

    def test_new_buffer_or_authority_scope_requires_new_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = self.source(folder), common.Snapshot(Path(folder), "target")
            lake.prepare(output, source.root, 250)
            with self.assertRaisesRegex(ValueError, "neuen --snapshot"):
                lake.prepare(output, source.root, 500)
            with self.assertRaises(ValueError):
                waterbook.configure_scope(output, {"kind": "public_authority_list"})

    def test_search_evidence_zero_resume_without_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = self.source(folder), common.Snapshot(Path(folder), "target")
            scope = lake.prepare(output, source.root)
            path = Path(folder) / "evidence.json"
            common.write_json(path, self.evidence(scope, payload(0)))
            index = waterbook.import_search_evidence(output, scope, path)
            self.assertEqual(index["records"], [])
            self.assertTrue(index["complete_for_scope"])
            self.assertEqual(output.state["waterbook"]["queries"]["LW1"]["objects"], 0)
            hashes = {k: v["sha256"] for k, v in output.state["artifacts"].items()}
            waterbook.import_search_evidence(output, scope, path)
            with patch.dict("sys.modules", {"playwright.sync_api": None}):
                resumed = waterbook.discover(output, [], lake_scope=scope)
            self.assertEqual(resumed["records"], [])
            self.assertEqual(hashes, {k: v["sha256"] for k, v in output.state["artifacts"].items()})
            lake.export(output)
            self.assertEqual(output.state["waterbook"]["lake_summary"][0]["discovered_documents"], 0)
            coverage = common.read_json(output.root / "tables/waterbook/lake_coverage.json")
            self.assertFalse(coverage["all_water_rights_complete"])

    def test_wrong_missing_duplicate_or_loading_evidence_rejected(self):
        for problem in ("geometry", "missing", "duplicate", "loading", "source", "number"):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as folder:
                source, output = self.source(folder), common.Snapshot(Path(folder), "target")
                scope = lake.prepare(output, source.root)
                evidence = copy.deepcopy(self.evidence(scope))
                if problem == "geometry":
                    evidence["queries"][0]["query"]["geometry_input"] = "gBox:0|0|1|1;25833"
                elif problem == "missing":
                    evidence["queries"] = []
                elif problem == "duplicate":
                    evidence["queries"] *= 2
                elif problem == "loading":
                    evidence["queries"][0]["payload"]["text"] += "\nLade Ergebnisse"
                elif problem == "number":
                    evidence["queries"][0]["payload"]["text"] = "2 Objekte"
                else:
                    evidence["source_url"] = "https://example.org"
                path = Path(folder) / "evidence.json"
                common.write_json(path, evidence)
                with self.assertRaises(ValueError):
                    waterbook.import_search_evidence(output, scope, path)

    def test_changed_evidence_cannot_replace_completed_search(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = self.source(folder), common.Snapshot(Path(folder), "target")
            scope = lake.prepare(output, source.root)
            path = Path(folder) / "evidence.json"
            common.write_json(path, self.evidence(scope))
            waterbook.import_search_evidence(output, scope, path)
            common.write_json(path, self.evidence(scope, payload(0)))
            with self.assertRaisesRegex(ValueError, "geändert"):
                waterbook.import_search_evidence(output, scope, path)
            self.assertEqual(output.state["waterbook"]["queries"]["LW1"]["objects"], 1)

    def test_named_and_located_surface_use_is_inferred_not_official(self):
        row = lake.associations(parsed_site(name="Entnahme am Tiefen See"), self.lakes(), {"LW1"}, 250)[0]
        self.assertEqual(row["status"], "matched")
        self.assertTrue(row["inside_lake"])
        self.assertFalse(row["is_official_link"])

    def test_spatial_only_is_review_and_municipality_is_not_lake_evidence(self):
        row = lake.associations(parsed_site(municipality="Tiefer See"), self.lakes(), {"LW1"}, 250)[0]
        self.assertEqual(row["status"], "review")
        self.assertEqual(row["name_evidence"], [])

    def test_nearby_groundwater_is_not_surface_lake_use(self):
        row = lake.associations(parsed_site(name="Tiefer See", water_category="Grundwasser"), self.lakes(), {"LW1"}, 250)[0]
        self.assertEqual(row["status"], "excluded")
        self.assertIn("groundwater_use_not_direct_lake_use", row["reasons"])

    def test_bbox_hit_far_from_lake_is_excluded_but_named_conflict_reviewed(self):
        for name, expected in [(None, "excluded"), ("Tiefer See", "review")]:
            row = lake.associations(parsed_site(name=name, easting=368000), self.lakes(), {"LW1"}, 250)[0]
            self.assertEqual(row["status"], expected)
            self.assertEqual(row["distance_m"], 900)

    def test_unknown_crs_missing_coordinates_and_swapped_axes_never_match(self):
        for changes in [{"crs": None}, {"crs": "EPSG:9999"}, {"easting": None}, {"easting": float("nan")},
                        {"easting": 5805050, "northing": 367050}]:
            with self.subTest(changes=changes):
                row = lake.associations(parsed_site(name="Tiefer See", **changes), self.lakes(), {"LW1"}, 250)[0]
                self.assertEqual(row["status"], "review")
                self.assertIsNone(row["distance_m"])

    def test_multi_case_title_and_other_case_use_not_copied_to_site(self):
        parsed = parsed_site()
        parsed["entry"]["title"] = "Entnahme Tiefer See"
        parsed["cases"].append({"case_id": "case2"})
        parsed["uses"] = [{"use_id": "use2", "case_id": "case2", "purpose": "Tiefer See"}]
        row = lake.associations(parsed, self.lakes(), {"LW1"}, 250)[0]
        self.assertEqual(row["status"], "review")
        self.assertEqual(row["name_evidence"], [])

    def test_ambiguous_named_lakes_or_duplicate_site_keys_never_match(self):
        second = {**self.lakes()[0], "water_body_id": "LW2", "name": "Schwielowsee"}
        rows = lake.associations(parsed_site(name="Tiefer See und Schwielowsee"), self.lakes() + [second], {"LW1"}, 250)
        self.assertEqual({r["status"] for r in rows}, {"review"})
        parsed = parsed_site(name="Tiefer See")
        parsed["sites"] *= 2
        self.assertEqual({r["status"] for r in lake.associations(parsed, self.lakes(), {"LW1"}, 250)}, {"review"})

    def test_missing_sites_and_pdf_parse_errors_stay_in_review(self):
        parsed = parsed_site()
        parsed["sites"] = []
        self.assertEqual(lake.associations(parsed, self.lakes(), {"LW1"}, 250)[0]["status"], "review")
        with tempfile.TemporaryDirectory() as folder:
            source, output = self.source(folder), common.Snapshot(Path(folder), "target")
            scope = lake.prepare(output, source.root)
            waterbook.record_query(output, "LW1", payload(), scope["queries"][0])
            output.record("pdf", output.root / "raw/a.pdf", b"%PDF-mocked", URL)
            output.state["waterbook"]["documents"] = {"10010000030": {"artifact": "pdf", "parsed_path": None, "parse_status": "error"}}
            lake.export(output)
            rows = common.read_json(output.root / "parsed/waterbook/lake_associations.json")
            self.assertEqual(rows[0]["reasons"], ["pdf_parse_failed"])
            summary = output.state["waterbook"]["lake_summary"][0]
            self.assertEqual((summary["parsed_documents"], summary["review_documents"]), (0, 1))


class LakeCliAndCaptureTests(unittest.TestCase):
    def test_lake_cli_scope_guards(self):
        valid = apw.arguments(["waterbook", "--lake-snapshot", "path", "--search-buffer-m", "250"])
        self.assertEqual(valid.search_buffer_m, 250)
        for args in [["waterbook", "--search-buffer-m", "250"], ["waterbook", "--search-evidence", "path"],
                     ["all", "--lake-snapshot", "path"], ["waterbook", "--lake-snapshot", "path", "--authority", "Potsdam"],
                     ["waterbook", "--lake-snapshot", "path", "--index", "i.json"],
                     ["waterbook", "--lake-snapshot", "path", "--search-buffer-m", "nan"],
                     ["waterbook", "--lake-snapshot", "path", "--search-buffer-m", "-1"]]:
            with self.subTest(args=args), patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
                apw.arguments(args)

    def test_stale_zero_and_error_not_accepted_as_complete(self):
        for message in ["Die Anfrage ergab keine Treffer.\nLade Ergebnisse", "Die Anfrage ergab keine Treffer.\nUnbekannter Fehler"]:
            with self.assertRaises(ValueError):
                waterbook.validate_result({"text": message, "records": []})


class LocalLakePdfTests(unittest.TestCase):
    def parse(self, number):
        path = Path(__file__).parent / f"data/waterbook-lake-check/raw/waterbook/pdf/{number}.pdf"
        if not path.is_file() or not importlib.util.find_spec("pdfplumber"):
            self.skipTest("Optionales lokales Wasserbuch-PDF/pdfplumber nicht vorhanden")
        import waterbook_pdf
        return waterbook_pdf.parse_pdf(path, number)

    def test_groundwater_pdf_borderless_header_and_coordinates(self):
        result = self.parse("10070000217")
        self.assertEqual(result["entry"]["authority"], "UWB Landkreis Dahme-Spreewald")
        self.assertEqual(result["entry"]["file_reference"], "67/3-30-40-006/1500")
        self.assertEqual(result["sites"][0]["water_category"], "Grundwasser")
        self.assertEqual(result["sites"][0]["easting"], 444854)
        self.assertEqual(result["limits"][1]["value"], 3000)
        self.assertEqual(result["limits"][1]["valid_to"], "2028-12-31")

    def test_neighbouring_lake_pdf_keeps_its_true_title(self):
        result = self.parse("10000000132")
        self.assertEqual(result["entry"]["title"], "Brauchwasserwerk am Glindower See")
        self.assertEqual(result["entry"]["authority"], "Obere Wasserbehörde")
        self.assertEqual(result["limits"][1]["value"], 4274700)
        self.assertEqual(result["sites"][0]["northing"], 5802662)


if __name__ == "__main__":
    unittest.main()
