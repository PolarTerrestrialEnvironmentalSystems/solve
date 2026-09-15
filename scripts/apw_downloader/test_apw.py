"""Offline unit tests; optional regression tests use locally downloaded pilot PDFs."""
from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import apw
import common
import waterbook
import waterbook_pdf as pdf
import wrrl


def collection(ids, properties=None, coords=None):
    return json.dumps({"type": "FeatureCollection", "crs": {"properties": {"name": "EPSG:4326"}},
        "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": coords or [13.0, 52.0]},
          "properties": {"GmlID": f"source.{i}", "OBJECTID": i, "EU_CD_LW": f"LW{i}", "S_NAME": f"See {i}", **(properties or {})}} for i in ids]}).encode()


class FakeHttp(common.Http):
    def __init__(self, total=5, repeat=False, empty=False):
        self.total, self.repeat, self.empty = total, repeat, empty
        self.requests = []

    def get(self, address, max_bytes=100_000_000):
        self.requests.append(address)
        query = parse_qs(urlparse(address).query)
        request = query["request"][0]
        if request == "GetCapabilities":
            names = "".join(f"<Name>{wrrl.PREFIX}{v[0]}</Name>" for v in wrrl.LAYERS.values())
            return f'<Capabilities xmlns="http://www.opengis.net/wfs/2.0">{names}</Capabilities>'.encode()
        if request == "DescribeFeatureType":
            return b"<schema/>"
        if query.get("resultType") == ["hits"]:
            return f'<FeatureCollection numberMatched="{self.total}"/>'.encode()
        start, count = int(query["startIndex"][0]), int(query["count"][0])
        if self.empty:
            return collection([])
        if self.repeat:
            start = 0
        return collection(range(start, min(start + count, self.total)))

    def page_calls(self):
        return [u for u in self.requests if "startIndex=" in u]


class CommonTests(unittest.TestCase):
    def test_snapshot_name_safety(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ["..", "../secret", "a/b", "a\\b", "", "a" * 81]:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    common.Snapshot(Path(folder), name)

    def test_atomic_file_and_hash_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            snap = common.Snapshot(Path(folder), "test")
            snap.record("key", snap.root / "raw/a.json", b"{}", "https://apw.brandenburg.de/")
            self.assertEqual(snap.cached("key")[0], b"{}")
            common.atomic_bytes(snap.root / "raw/a.json", b"changed")
            with self.assertRaisesRegex(RuntimeError, "verändert"):
                snap.cached("key")

    def test_cache_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            snap = common.Snapshot(Path(folder), "test")
            snap.state["artifacts"]["bad"] = {"path": "../../secret", "sha256": "bad"}
            with self.assertRaises(ValueError):
                snap.cached("bad")

    def test_csv_preserves_decimals_and_guards_formulas(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.csv"
            common.write_csv(path, [{"name": "=BAD()", "value": 1116.0, "raw": {"x": "ä"}}], ["name", "value", "raw"])
            with path.open(encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream, delimiter=";"))
            self.assertEqual(row["name"], "'=BAD()")
            self.assertEqual(float(row["value"]), 1116)
            self.assertEqual(json.loads(row["raw"]), {"x": "ä"})

    def test_double_writer_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with common.OutputLock(Path(folder)):
                with self.assertRaises(RuntimeError):
                    with common.OutputLock(Path(folder)):
                        pass
            with common.OutputLock(Path(folder)):
                pass

    def test_http_url_whitelist(self):
        for url in ["http://apw.brandenburg.de/", "https://apw.brandenburg.de.evil/", "https://u:p@apw.brandenburg.de/", "https://apw.brandenburg.de:8000/", "file:///etc/passwd"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                common.validate_url(url)
        common.validate_url("https://maps.brandenburg.de/services/wfs/")

    def test_retry_after(self):
        self.assertEqual(common.retry_delay("120", 1), 120)
        self.assertEqual(common.retry_delay("nonsense", 2), 4)
        self.assertEqual(common.retry_delay(None, 3), 8)


class WfsTests(unittest.TestCase):
    def test_metadata_date_not_download_date(self):
        xml = b'<Capabilities xmlns="http://www.opengis.net/ows/1.1"><Abstract>Stand der Daten: 22.12.2021</Abstract></Capabilities>'
        self.assertEqual(wrrl.reference_date(xml), "2021-12-22")
        self.assertIsNone(wrrl.reference_date(b"<Capabilities/>"))

    def test_numeric_zero_feature_id(self):
        self.assertEqual(wrrl.feature_id({"id": 0, "properties": {}}), "0")
    def test_xml_error_not_data(self):
        with self.assertRaises(ValueError):
            wrrl.xml(b"<ExceptionReport><ExceptionText>bad</ExceptionText></ExceptionReport>")

    def test_axis_swap_rejected(self):
        with self.assertRaisesRegex(ValueError, "Koordinaten"):
            wrrl.feature_collection(collection([1], coords=[52.0, 13.0]))

    def test_duplicate_feature_rejected(self):
        with self.assertRaisesRegex(ValueError, "Doppelte"):
            wrrl.feature_collection(collection([1, 1]))

    def test_bad_json_and_crs(self):
        for data in [b"<html>error</html>", b'{"type":"FeatureCollection","features":{},"crs":null}', b'{"type":"FeatureCollection","features":[],"crs":{"properties":{"name":"EPSG:25833"}}}']:
            with self.subTest(data=data), self.assertRaises(ValueError):
                wrrl.feature_collection(data)

    def test_pilot_resume_full_and_idempotency(self):
        with tempfile.TemporaryDirectory() as folder:
            snap = common.Snapshot(Path(folder), "test")
            http = FakeHttp()
            wrrl.download(http, snap, ["lakes"], page_size=2, max_features=3)
            self.assertFalse(snap.state["wrrl"]["lakes"]["complete"])
            self.assertEqual(len(http.page_calls()), 2)
            snap = common.Snapshot(Path(folder), "test")
            wrrl.download(http, snap, ["lakes"], page_size=2)
            self.assertTrue(snap.state["wrrl"]["lakes"]["complete"])
            self.assertEqual(snap.state["wrrl"]["lakes"]["downloaded"], 5)
            self.assertEqual(len(http.page_calls()), 3)
            wrrl.download(http, snap, ["lakes"], page_size=2)
            self.assertEqual(len(http.page_calls()), 3)
            self.assertFalse(any("sortBy" in u or "srsName" in u for u in http.page_calls()))
            with (snap.root / "tables/wrrl/water_bodies.csv").open(encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream, delimiter=";"))
            self.assertEqual(len(rows), 5)
            self.assertEqual(len({r["identifier"] for r in rows}), 5)

    def test_changed_count_stops_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            snap = common.Snapshot(Path(folder), "test")
            wrrl.download(FakeHttp(5), snap, ["lakes"], max_features=1)
            with self.assertRaisesRegex(RuntimeError, "Bestand hat sich geändert"):
                wrrl.download(FakeHttp(6), snap, ["lakes"])

    def test_overlapping_or_empty_pages_fail(self):
        for http in [FakeHttp(repeat=True), FakeHttp(empty=True)]:
            with self.subTest(http=http), tempfile.TemporaryDirectory() as folder:
                snap = common.Snapshot(Path(folder), "test")
                with self.assertRaises(RuntimeError):
                    wrrl.download(http, snap, ["lakes"], page_size=2)
                self.assertFalse(snap.state["wrrl"]["lakes"]["complete"])

    def test_empty_dataset_is_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            snap = common.Snapshot(Path(folder), "test")
            wrrl.download(FakeHttp(0), snap, ["lakes"])
            self.assertTrue(snap.state["wrrl"]["lakes"]["complete"])

    def test_assessment_codes_retained_not_invented(self):
        with tempfile.TemporaryDirectory() as folder:
            snap = common.Snapshot(Path(folder), "test")
            data = collection([1], {"ECO_STAT": "3", "ECO_POT": " ", "CHEM_STAT": "999"})
            snap.record("p", snap.root / "page.geojson", data, "https://maps.brandenburg.de/")
            snap.state["wrrl"]["lakes"] = {"pages": ["p"], "complete": True}
            wrrl.export(snap)
            with (snap.root / "tables/wrrl/assessments.csv").open(encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream, delimiter=";"))
            self.assertEqual([r["value_raw"] for r in rows], ["3", "999"])
            self.assertEqual(rows[1]["is_valid"], "False")
            self.assertEqual(rows[0]["label"], "")


class WaterbookTests(unittest.TestCase):
    URL = "https://apw.brandenburg.de/project/cardoMap/Documents/ewabu/wasserbuchblatt_10010000030.pdf"

    def test_pdf_url(self):
        self.assertEqual(waterbook.pdf_number(self.URL), "10010000030")
        for bad in [self.URL + "?x=1", self.URL.replace("https:", "http:"), self.URL.replace("apw.brandenburg.de", "evil.example"), self.URL.replace("10010000030", "../secret")]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                waterbook.pdf_number(bad)

    def test_search_count_is_feature_count_not_unique_pdf_count(self):
        record = {"url": self.URL, "fields": {"Wasserbuchblattnummer:": "10010000030"}}
        self.assertEqual(waterbook.validate_result({"text": "2 Objekte", "records": [record, record]}), 2)

    def test_truncated_search_rejected(self):
        with self.assertRaises(ValueError):
            waterbook.validate_result({"text": "10 Objekte", "records": [{"url": self.URL}]})

    def test_zero_and_ambiguous_results(self):
        self.assertEqual(waterbook.validate_result({"text": "Die Anfrage ergab keine Treffer.", "records": []}), 0)
        with self.assertRaises(ValueError):
            waterbook.validate_result({"text": "Fehler", "records": []})

    def test_metadata_id_mismatch(self):
        with self.assertRaises(ValueError):
            waterbook.validate_result({"text": "1 Objekt", "records": [{"url": self.URL, "fields": {"Wasserbuchblattnummer:": "123"}}]})

    def test_locale_numbers(self):
        for raw, value in [("1.116,00", 1116), ("91.000,00", 91000), ("0,05", .05), ("-0,5", -.5)]:
            with self.subTest(raw=raw):
                self.assertEqual(pdf.german_number(raw), value)
        with self.assertRaises(ValueError):
            pdf.german_number("1.5")

    def test_values(self):
        self.assertEqual(pdf.parse_value("<= 50,00 mg/l"), ("<=", 50, "mg/l"))
        self.assertEqual(pdf.parse_value("≤ 0,05 µg/l"), ("<=", .05, "µg/l"))
        self.assertEqual(pdf.parse_value("220.000,00 m³/a"), (None, 220000, "m³/a"))
        for raw in ["siehe Bemerkung", "", "1 - 2 mg/l", "50,00 99", "1e3 mg/l"]:
            with self.subTest(raw=raw):
                self.assertEqual(pdf.parse_value(raw), (None, None, None))

    def test_maximum_and_mean_are_distinct(self):
        kwargs = {"entry_id": "e", "case_id": "c", "row_number": "1", "page": 1, "table_kind": "extent"}
        row = pdf.limit_row(["Einleitmenge, max. täglich", "1.116,00 m³/d", "", "", "", "immer", ""], **kwargs)
        self.assertEqual((row["operator"], row["statistic"], row["time_basis"]), ("<=", "maximum", "day"))
        row = pdf.limit_row(["Entnahmemenge, mittl. stündlich", "138,00 m³/h", "", "", "", "immer", ""], **kwargs)
        self.assertIsNone(row["operator"])
        self.assertEqual(row["statistic"], "mean")

    def test_limit_validity_not_entry_validity(self):
        row = pdf.limit_row(["max. jährlich", "220.000,00 m³/a", "", "01.04.2014", "31.08.2023", "von April bis August", ""], entry_id="e", case_id="c", row_number="1", page=2, table_kind="extent")
        self.assertEqual(row["valid_to"], "2023-08-31")
        self.assertEqual(row["period_raw"], "von April bis August")
        self.assertIsNone(pdf.date(""))
        self.assertIsNone(pdf.date("31.02.2026"))

    def test_cached_download_and_parse_failure_progress(self):
        class PdfHttp(common.Http):
            def __init__(self):
                self.calls = []
            def get(self, url, max_bytes=100_000_000):
                self.calls.append(url)
                return b"%PDF-fake-for-mocked-parser"
        def parsed(path, number):
            entry = {key: None for key in pdf.FIELDS["entries"]}
            entry.update(entry_id="apw:ewabu:" + number, waterbook_number=number)
            return {"entry": entry, "cases": [], "uses": [], "sites": [], "limits": [], "warnings": [], "parse_status": "extracted"}
        records = [{"url": self.URL, "fields": {}}, {"url": self.URL.replace("10010000030", "10190000003"), "fields": {}}]
        index = {"schema_version": 1, "scope": {"kind": "test"}, "complete_for_scope": True, "records": records}
        with tempfile.TemporaryDirectory() as folder:
            snapshot = common.Snapshot(Path(folder), "test")
            http = PdfHttp()
            with patch.object(pdf, "parse_pdf", side_effect=parsed):
                waterbook.download(http, snapshot, index, max_documents=1)
                self.assertFalse(snapshot.state["waterbook"]["download_complete"])
                waterbook.download(http, snapshot, index)
                self.assertEqual(len(http.calls), 2)
                self.assertTrue(snapshot.state["waterbook"]["parse_complete"])
                waterbook.download(http, snapshot, {**index, "records": list(reversed(records))})
                self.assertEqual(len(http.calls), 2)
            def fail_first(path, number):
                if number == "10010000030":
                    raise ValueError("unknown layout")
                return parsed(path, number)
            with patch.object(pdf, "parse_pdf", side_effect=fail_first), self.assertRaises(RuntimeError):
                waterbook.download(http, snapshot, index)
            state = snapshot.state["waterbook"]
            self.assertTrue(state["download_complete"])
            self.assertFalse(state["parse_complete"])
            self.assertEqual(state["parsed_documents"], 1)
            self.assertIn("unknown layout", (snapshot.root / "tables/waterbook/review.csv").read_text(encoding="utf-8-sig"))


class CliTests(unittest.TestCase):
    def test_bad_arguments(self):
        for args in [["wrrl", "--max-features", "0"], ["wrrl", "--delay", "0"], ["wrrl", "--page-size", "5001"], ["waterbook", "--authority", "a", "--pdf-url", WaterbookTests.URL]]:
            with self.subTest(args=args), patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
                apw.arguments(args)

    def test_error_journal_written_inside_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(wrrl, "download", side_effect=RuntimeError("deliberate test")):
                self.assertEqual(apw.main(["wrrl", "--output", folder]), 1)
            state = common.read_json(Path(folder) / "default/manifest.json")
            self.assertEqual(state["last_error"]["message"], "deliberate test")

    def test_interrupt_journal(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(wrrl, "download", side_effect=KeyboardInterrupt()):
                self.assertEqual(apw.main(["wrrl", "--output", folder]), 130)
            self.assertEqual(common.read_json(Path(folder) / "default/manifest.json")["last_error"]["message"], "interrupted")


class LocalPdfRegressionTests(unittest.TestCase):
    """No network: skip when the explicitly fetched public pilot PDFs are absent."""
    def parse(self, number):
        path = Path(__file__).parent / f"data/pilot/raw/waterbook/pdf/{number}.pdf"
        if not path.exists():
            self.skipTest("Pilot-PDF nicht lokal vorhanden")
        return pdf.parse_pdf(path, number)

    def test_potsdam_pdf(self):
        result = self.parse("10010000030")
        self.assertEqual(result["entry"]["authority"], "UWB Stadt Potsdam")
        self.assertIsNone(result["entry"]["valid_to"])
        self.assertEqual(len(result["uses"]), 1)
        self.assertEqual(len(result["sites"]), 1)
        self.assertEqual(result["sites"][0]["easting"], 367680)
        self.assertEqual(result["sites"][0]["source_page"], 2)
        self.assertEqual([r["value"] for r in result["limits"]], [50, 1116, 138, 91000])
        self.assertTrue(any("withdrawal_label" in w for w in result["warnings"]))

    def test_brandenburg_pdf_multiline_and_named_site(self):
        result = self.parse("10190000003")
        self.assertEqual(result["entry"]["valid_to"], "2023-12-31")
        self.assertIn("Plauer See", result["entry"]["title"])
        self.assertEqual(len(result["sites"]), 1)
        self.assertEqual(result["sites"][0]["name"], "Entnahme")
        self.assertEqual(result["sites"][0]["northing"], 5809216)
        self.assertEqual(result["limits"][1]["value"], 220000)
        self.assertEqual(result["limits"][1]["valid_to"], "2023-08-31")
        self.assertEqual(result["limits"][0]["remarks"], "Überwachung der entnommenen Menge")
        self.assertIsNone(result["limits"][0]["value"])


if __name__ == "__main__":
    unittest.main()
