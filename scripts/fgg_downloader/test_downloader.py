import hashlib
import json
import http.cookiejar
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from downloader import (Downloader, State, count_page, check_partition, identity,
                        parse_catalog_station, real_options, worker_lock, year_groups)
from portal import (Option, Page, Portal, PortalError, clean_label, inspect_csv,
                    option_count, safe_url, parse_portal_rows)


def sample_page(selects="", count=10, hidden=""):
    return Page("https://www.elbe-datenportal.de/FisFggElbe/content/auswertung/Test",
                f'<form action="Test">{selects}{hidden}</form>'
                f'<p>Die aktuelle Abfrage umfasst <em>{count:,}</em> Messwerte.</p>'.replace(",", "."))


def select(name, entries):
    return '<select name="' + name + '"><option value="keine Auswahl">--- Alle ---</option>' + ''.join(
        f'<option value="{label} --- ({count} Messwerte)">{label} --- ({count} Messwerte)</option>'
        for label, count in entries) + '</select>'


class PortalTests(unittest.TestCase):
    def test_label_removes_only_final_count(self):
        self.assertEqual(clean_label("Potsdam (km 26,5) --- (1.234 Messwerte)"), "Potsdam (km 26,5)")
        self.assertEqual(clean_label("A --- B (Station)"), "A --- B (Station)")
        self.assertEqual(option_count("A --- (1.234 Messwerte)"), 1234)

    def test_form_successful_controls(self):
        page = sample_page('<input name="h" type="hidden" value="ä">'
                           '<input type="checkbox" name="no" value="x">'
                           '<input type="checkbox" checked name="yes" value="y">'
                           '<input type="image" name="button">'
                           '<select name="s"><option value="0">All</option><option selected value="1">One</option></select>')
        self.assertEqual(page.fields(), {"h": "ä", "yes": "y", "s": "1"})

    def test_hidden_selected_dimension(self):
        page = sample_page(hidden='<input name="gewaehltMedium" type="hidden" value="Wasser">')
        self.assertIs(Portal().choose(page, "gewaehltMedium", "Wasser"), page)

    def test_duplicate_option_is_error(self):
        with self.assertRaises(PortalError):
            real_options(sample_page(select("x", [("A", 1), ("A", 2)])), "x")

    def test_refresh(self):
        page = Page("https://www.elbe-datenportal.de/FisFggElbe/content/auswertung/A",
                    '<meta http-equiv="refresh" content="2; URL=A_export.action">')
        self.assertEqual(page.refresh(), (2.0, "https://www.elbe-datenportal.de/FisFggElbe/content/auswertung/A_export.action"))

    def test_external_requests_rejected(self):
        for url in ["http://www.elbe-datenportal.de/", "https://evil.test/", "https://www.elbe-datenportal.de.evil.test/"]:
            with self.assertRaises(PortalError):
                safe_url(url)

    def test_terms_required(self):
        with self.assertRaises(PortalError):
            Portal().enter()

    def test_csv_keeps_qualifiers(self):
        raw = "'Gewässer';'Messwert'\r\r\n'Havel';'< 0,005'\r\r\n".encode("iso-8859-1")
        result = inspect_csv(raw, "text/csv; charset=ISO-8859-1")
        self.assertEqual(result["rows"], 1)
        self.assertEqual(result["header"], ["Gewässer", "Messwert"])
        self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())

    def test_csv_multiline_and_escaped_quote(self):
        raw = "'A';'B'\n'first\nsecond';'O''Brien'\n".encode()
        self.assertEqual(inspect_csv(raw)["rows"], 1)

    def test_live_pcb_apostrophes_are_preserved(self):
        text = "'Parameter';'Messwert'\n'PCB-153 (2,2',4,4',5,5'-Hexachlorbiphenyl)';3,25\n"
        rows, warnings = parse_portal_rows(text)
        self.assertEqual(rows[1][0], "PCB-153 (2,2',4,4',5,5'-Hexachlorbiphenyl)")
        self.assertTrue(warnings)
        self.assertEqual(inspect_csv(text.encode())["rows"], 1)

    def test_unterminated_quotes_are_rejected(self):
        with self.assertRaises(PortalError):
            inspect_csv(b"'A';'B'\n'unterminated;x")

    def test_csv_trailing_optional_warning(self):
        result = inspect_csv("'A';'B';'zusätzliche Informationen'\n'x';'y'\n".encode())
        self.assertEqual(result["row_widths"], {"2": 1})
        self.assertTrue(result["warnings"])

    def test_arbitrary_column_mismatch_rejected(self):
        with self.assertRaises(PortalError):
            inspect_csv(b"'A';'B';'C'\n'x';'y'\n")

    def test_html_download_rejected(self):
        with self.assertRaises(PortalError):
            inspect_csv(b"<!doctype html><h1>Error</h1>")

    def test_count_not_option_count(self):
        page = sample_page(select("x", [("small", 5), ("other", 5)]), 12345)
        self.assertEqual(count_page(page), 12345)


class PlanningTests(unittest.TestCase):
    def test_groups_cover_all_years(self):
        opts = [Option(str(y), str(y), n) for y, n in [(2025, 6000), (2024, 3000), (2023, 4000), (2020, 12000), (2019, 100)]]
        self.assertEqual(year_groups(opts, 8000), [(2025, 2025, 6000), (2023, 2024, 7000), (2020, 2020, 12000), (2019, 2019, 100)])

    def test_partition_gap_rejected(self):
        with self.assertRaises(PortalError):
            check_partition([Option("A", "A", 9)], 10)

    def test_undated_year_is_not_dropped(self):
        with self.assertRaises(PortalError):
            year_groups([Option("-", "-", 5)], 8000)

    def test_stable_job_key_ignores_dict_order(self):
        self.assertEqual(identity({"a": 1, "b": 2}), identity({"b": 2, "a": 1}))

    def test_split_and_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                job_id = state.add_job("Test", {}, 10)
            job = state.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            state.split(job, [({"gewaehltMedium": "A"}, 4), ({"gewaehltMedium": "B"}, 6)], "medium")
            self.assertEqual(state.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 3)
            state.split(job, [({"gewaehltMedium": "A"}, 4), ({"gewaehltMedium": "B"}, 6)], "medium")
            self.assertEqual(state.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 3)
            state.db.close()

    def test_bad_split_rolls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                state.add_job("Test", {}, 10)
            job = state.db.execute("SELECT * FROM jobs").fetchone()
            with self.assertRaises(PortalError):
                state.split(job, [({}, 10)], "same")
            self.assertEqual(state.db.execute("SELECT status FROM jobs").fetchone()[0], "pending")
            state.db.close()

    def test_missing_data_does_not_become_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                state.db.execute("INSERT INTO topics VALUES ('Test','Test','url',10,'date')")
                state.add_job("Test", {}, 10)
            result = state.report(write=False)
            self.assertFalse(result["all_complete"])
            self.assertFalse(result["topics"][0]["coverage_complete"])
            state.db.close()

    def test_limit_target_validation(self):
        with self.assertRaises(ValueError):
            Downloader(None, accept_terms=False, target=10001)

    def test_small_query_still_requires_anchor(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                state.add_job("Phytoplankton", {}, 10)
            job = state.db.execute("SELECT * FROM jobs").fetchone()
            page = sample_page(select("gewaehltMessstelle", [("A", 4), ("B", 6)]), 10)
            self.assertTrue(Downloader(state, accept_terms=False).plan(job, page))
            self.assertEqual(state.db.execute("SELECT count(*) FROM jobs WHERE parent IS NOT NULL").fetchone()[0], 2)
            state.db.close()

    def test_indivisible_over_limit_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                state.add_job("ChemWas", {"gewaehltMessstelle": "A", "gewaehltMessjahrVon": "2020", "gewaehltMessjahrBis": "2020"}, 10001)
            job = state.db.execute("SELECT * FROM jobs").fetchone()
            with self.assertRaises(PortalError):
                Downloader(state, accept_terms=False).plan(job, sample_page(count=10001))
            state.db.close()

    def test_exact_hard_limit_is_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                state.add_job("ChemWas", {"gewaehltMessstelle": "A", "gewaehltMessjahrVon": "2020", "gewaehltMessjahrBis": "2020"}, 10000)
            job = state.db.execute("SELECT * FROM jobs").fetchone()
            self.assertFalse(Downloader(state, accept_terms=False).plan(job, sample_page(count=10000)))
            state.db.close()

    def test_restart_reuses_verified_file(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                state.add_job("ChemWas", {"gewaehltMessstelle": "A", "gewaehltMessjahrVon": "2020", "gewaehltMessjahrBis": "2020"}, 1)
            job = state.db.execute("SELECT * FROM jobs").fetchone()
            loader = Downloader(state, accept_terms=False)
            link = "https://www.elbe-datenportal.de/FisFggElbe/ausgabe/test.csv"
            with patch.object(loader.portal, "request", return_value=(link, {"Content-Type": "text/csv; charset=utf-8"}, b"'A';'B'\n'x';'y'\n")) as request:
                loader.download_links(job, [("Test", link)])
                loader.download_links(job, [("Test", link)])
                self.assertEqual(request.call_count, 1)
            self.assertEqual(state.db.execute("SELECT rows FROM jobs").fetchone()[0], 1)
            state.db.close()

    def test_raw_preserved_on_count_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                state.add_job("ChemWas", {"gewaehltMessstelle": "A", "gewaehltMessjahrVon": "2020", "gewaehltMessjahrBis": "2020"}, 2)
            job = state.db.execute("SELECT * FROM jobs").fetchone()
            loader = Downloader(state, accept_terms=False)
            link = "https://www.elbe-datenportal.de/FisFggElbe/ausgabe/test.csv"
            with patch.object(loader.portal, "request", return_value=(link, {"Content-Type": "text/csv; charset=utf-8"}, b"'A';'B'\n'x';'y'\n")):
                with self.assertRaises(PortalError):
                    loader.download_links(job, [("Test", link)])
            self.assertEqual(len(list(Path(folder).rglob("test.csv"))), 1)
            state.db.close()

    def test_restart_resumes_polling_without_export_post(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            jar = http.cookiejar.LWPCookieJar()
            jar.save(str(state.directory / "guest_session.cookies"), ignore_discard=True)
            with state.db:
                job_id = state.add_job("ChemWas", {}, 1)
            state.update(job_id, status="waiting", details=json.dumps({"refresh_url": "https://www.elbe-datenportal.de/poll"}))
            loader = Downloader(state, accept_terms=True)
            link = "https://www.elbe-datenportal.de/FisFggElbe/ausgabe/test.csv"
            page = Page("https://www.elbe-datenportal.de/poll", f'<a href="{link}">CSV</a>')
            with patch.object(loader.portal, "get_page", return_value=page) as get, patch.object(loader.portal, "submit") as submit, patch.object(loader.portal, "request", return_value=(link, {"Content-Type": "text/csv"}, b"'A';'B'\n'x';'y'\n")):
                loader.recover_inflight()
                get.assert_called_once_with("https://www.elbe-datenportal.de/poll")
                submit.assert_not_called()
            self.assertEqual(state.db.execute("SELECT status FROM jobs").fetchone()[0], "complete")
            state.db.close()

    def test_missing_poll_context_never_resubmits(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                job_id = state.add_job("ChemWas", {}, 1)
            state.update(job_id, status="submitting")
            loader = Downloader(state, accept_terms=True)
            with patch.object(loader.portal, "submit") as submit:
                with self.assertRaises(PortalError):
                    loader.recover_inflight()
                submit.assert_not_called()
            state.db.close()

    def test_worker_lock_exclusive(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "lock"
            with worker_lock(path):
                with self.assertRaises(PortalError):
                    with worker_lock(path):
                        self.fail("Second worker unexpectedly acquired the same lock")

    def test_catalog_detail_excludes_dropdown(self):
        html = '<form action="s"><input name="idDerGewaehltenMessstelle" value=""></form>'
        html += '<div><div class="label">Land</div><div class="inhalt"><select><option>All</option></select></div></div>'
        for key, value in [("Bezeichnung", "Humboldt"), ("Land", "Brandenburg"), ("Koordinaten Ost", "777057"), ("Nord", "5813404")]:
            html += f'<div><div class="label">{key}</div><div class="inhalt">{value}</div></div>'
        html += '<p>Zu dieser Messstelle gibt es 382 Messwerte.</p>'
        result = parse_catalog_station(Page("https://www.elbe-datenportal.de/s", html), "Humboldt", 382)
        self.assertEqual(result["fields"]["Land"], "Brandenburg")
        self.assertEqual(result["fields"]["Nord"], "5813404")


if __name__ == "__main__":
    unittest.main()
