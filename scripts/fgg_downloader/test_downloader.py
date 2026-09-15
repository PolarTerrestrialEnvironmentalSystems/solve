import hashlib
import json
import http.cookiejar
import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from downloader import (Downloader, State, count_page, check_partition, identity,
                        parse_catalog_station, real_options, worker_lock, year_groups, format_progress,
                        check_result_selection, check_csv_period)
from portal import (Option, Page, Portal, PortalError, clean_label, inspect_csv,
                    option_count, safe_url, parse_portal_rows, UncertainExport, write_json)


def sample_page(selects="", count=10, hidden=""):
    return Page("https://www.elbe-datenportal.de/FisFggElbe/content/auswertung/Test",
                f'<form action="Test">{selects}{hidden}</form>'
                f'<p>Die aktuelle Abfrage umfasst <em>{count:,}</em> Messwerte.</p>'.replace(",", "."))


def select(name, entries):
    return '<select name="' + name + '"><option value="keine Auswahl">--- Alle ---</option>' + ''.join(
        f'<option value="{label} --- ({count} Messwerte)">{label} --- ({count} Messwerte)</option>'
        for label, count in entries) + '</select>'


class ReportWriteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = State(Path(temporary.name))
        self.addCleanup(self.state.db.close)
        with self.state.db:
            self.state.db.execute("INSERT INTO topics VALUES ('Test','Test','url',1,'date')")
            self.job_id = self.state.add_job("Test", {}, 1)
        self.state.update(self.job_id, status="complete", rows=1)
        self.state.report()

    def deny_replacement(self, name):
        original = Path.replace
        def replace(source, target):
            if Path(target).name == name:
                raise PermissionError("simulated reader denies replacement")
            return original(source, target)
        return replace

    def test_transient_replacement_error_retries_then_succeeds(self):
        original = Path.replace
        attempts = []
        def replace(source, target):
            if Path(target).name == "fortschritt.txt":
                attempts.append(target)
                if len(attempts) < 3:
                    raise PermissionError("brief file lock")
            return original(source, target)
        with patch.object(Path, "replace", new=replace), patch("downloader.time.sleep") as sleep:
            report = self.state.report()
        self.assertEqual(len(attempts), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.1, 0.2])
        self.assertEqual(report["report_write_errors"], {})

    def test_each_report_can_remain_locked_without_losing_previous_snapshot(self):
        for name in ["fortschritt.txt", "dateiuebersicht.csv", "fortschritt.json"]:
            with self.subTest(name=name):
                before = (self.state.output / name).read_bytes()
                with patch.object(Path, "replace", new=self.deny_replacement(name)), \
                     patch("downloader.time.sleep") as sleep, patch.object(State, "_report_notice"):
                    report = self.state.report()
                self.assertEqual(sleep.call_count, 4)
                self.assertEqual((self.state.output / name).read_bytes(), before)
                self.assertEqual(set(report["report_write_errors"]), {name})
                self.assertEqual(report["downloaded_rows"], 1)
                if name != "fortschritt.json":
                    snapshot = json.loads((self.state.output / "fortschritt.json").read_text(encoding="utf-8"))
                    self.assertIn(name, snapshot["report_write_errors"])
                with patch.object(State, "_report_notice"):
                    self.state.report()
                snapshot = json.loads((self.state.output / "fortschritt.json").read_text(encoding="utf-8"))
                self.assertEqual(snapshot["report_write_errors"], {})

    def test_all_report_files_can_be_locked_independently(self):
        with patch.object(Path, "replace", side_effect=PermissionError("all reports locked")), \
             patch("downloader.time.sleep"), patch.object(State, "_report_notice"):
            report = self.state.report()
        self.assertEqual(set(report["report_write_errors"]),
                         {"fortschritt.txt", "fortschritt.json", "dateiuebersicht.csv"})
        self.assertEqual(self.state.db.execute("SELECT status FROM jobs").fetchone()[0], "complete")

    def test_warning_is_persistent_and_read_only_status_needs_no_file_write(self):
        with patch.object(Path, "replace", new=self.deny_replacement("fortschritt.txt")), \
             patch("downloader.time.sleep"), patch.object(State, "_report_notice"):
            self.state.report()
        reopened = State(self.state.output)
        try:
            with patch.object(Path, "replace", side_effect=AssertionError("status must not publish files")):
                report = reopened.report(write=False)
            self.assertIn("fortschritt.txt", report["report_write_errors"])
            self.assertIn("Anzeige möglicherweise veraltet", format_progress(report))
        finally:
            reopened.db.close()

    def test_repeated_lock_warns_once_and_recovery_is_announced(self):
        with patch.object(State, "_report_notice") as notice:
            with patch.object(Path, "replace", new=self.deny_replacement("fortschritt.txt")), \
                 patch("downloader.time.sleep"):
                self.state.report()
                self.state.report()
            self.assertEqual(notice.call_count, 1)
            report = self.state.report()
            self.assertEqual(notice.call_count, 2)
            self.assertIn("wieder aktualisiert", notice.call_args.args[0])
        self.assertEqual(report["report_write_errors"], {})

    def test_permission_error_opening_temporary_report_is_also_nonfatal(self):
        original = Path.write_text
        def write_text(path, *args, **kwargs):
            if path.name == "fortschritt.txt.tmp":
                raise PermissionError("temporary file locked")
            return original(path, *args, **kwargs)
        with patch.object(Path, "write_text", new=write_text), patch("downloader.time.sleep"), \
             patch.object(State, "_report_notice"):
            report = self.state.report()
        self.assertIn("fortschritt.txt", report["report_write_errors"])

    def test_other_io_errors_still_propagate_without_retry(self):
        with patch.object(Path, "replace", side_effect=OSError(errno.ENOSPC, "disk full")), \
             patch("downloader.time.sleep") as sleep:
            with self.assertRaises(OSError) as raised:
                self.state.report()
        self.assertEqual(raised.exception.errno, errno.ENOSPC)
        sleep.assert_not_called()

    def test_critical_metadata_permission_errors_are_not_suppressed(self):
        with patch.object(Path, "replace", side_effect=PermissionError("metadata cannot be saved")):
            with self.assertRaises(PermissionError):
                write_json(self.state.output / "metadata.json", {"proof": "required"})

    def test_completed_download_stays_complete_when_report_is_locked(self):
        self.state.update(self.job_id, status="pending", rows=None)
        job = self.state.db.execute("SELECT * FROM jobs").fetchone()
        loader = Downloader(self.state, accept_terms=False)
        def export(job, page):
            self.state.update(job["id"], status="complete", rows=1, attempts=1)
        with patch.object(loader, "load_filters", return_value=sample_page(count=1)), \
             patch.object(loader, "plan", return_value=False), patch.object(loader, "export", side_effect=export) as exported, \
             patch.object(Path, "replace", new=self.deny_replacement("fortschritt.txt")), \
             patch("downloader.time.sleep"), patch.object(State, "_report_notice"):
            loader.process_job(job)
        exported.assert_called_once()
        saved = self.state.db.execute("SELECT status,rows,attempts FROM jobs").fetchone()
        self.assertEqual(tuple(saved), ("complete", 1, 1))
        self.assertIsNone(loader.next_job())

    @unittest.skipUnless(os.name == "nt", "requires Windows file sharing semantics")
    def test_real_windows_reader_denies_replace_but_download_state_survives(self):
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        path = self.state.output / "fortschritt.txt"
        before = path.read_bytes()
        # Allow readers/writers, but deliberately withhold FILE_SHARE_DELETE.
        handle = kernel32.CreateFileW(str(path), 0x80000000, 1 | 2, None, 3, 128, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            with patch("downloader.time.sleep"), patch.object(State, "_report_notice"):
                report = self.state.report()
            self.assertIn("fortschritt.txt", report["report_write_errors"])
            self.assertEqual(path.read_bytes(), before)
        finally:
            kernel32.CloseHandle(handle)
        with patch.object(State, "_report_notice"):
            self.assertEqual(self.state.report()["report_write_errors"], {})


class PortalTests(unittest.TestCase):
    def test_initial_export_uses_longer_timeout_and_is_never_retried(self):
        portal = Portal(timeout=120, export_request_timeout=3600)
        with patch.object(portal.opener, "open", side_effect=TimeoutError("slow first response")) as opened:
            with self.assertRaises(UncertainExport):
                portal.request("https://www.elbe-datenportal.de/export", {"format": "csv"}, export=True)
        opened.assert_called_once()
        self.assertEqual(opened.call_args.kwargs["timeout"], 3600)

    def test_normal_requests_keep_short_timeout_and_bounded_retries(self):
        portal = Portal(timeout=120, export_request_timeout=3600)
        with patch.object(portal.opener, "open", side_effect=TimeoutError("offline")) as opened, \
             patch("portal.time.sleep"):
            with self.assertRaises(TimeoutError):
                portal.request("https://www.elbe-datenportal.de/query")
        self.assertEqual(opened.call_count, 3)
        self.assertTrue(all(call.kwargs["timeout"] == 120 for call in opened.call_args_list))

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


class DeferredExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = State(Path(temporary.name))
        self.addCleanup(self.state.db.close)
        with self.state.db:
            self.state.db.execute("INSERT INTO topics VALUES ('Test','Test','url',615,'date')")
            self.job_id = self.state.add_job("Test", {"station": "A"}, 615)
        self.directory = self.state.output / "old-job"
        self.directory.mkdir()
        self.state.update(self.job_id, status="uncertain", attempts=1, error="first response missing",
                          directory=str(self.directory), details=json.dumps({"started_at": "2020-01-01T00:00:00+00:00"}))
        self.evidence = self.directory / "recovery_response_test.json"
        write_json(self.evidence, {"job_id": self.job_id, "expected_count": 615,
                   "probe_kind": "saved_selection_page", "actual_count": None, "links": [],
                   "refresh": None, "text": "java.lang.NullPointerException"})

    def test_explicit_deferral_preserves_job_and_selects_fresh_session(self):
        receipt = self.state.defer_uncertain(self.job_id, self.evidence, "continue remaining work")
        row = self.state.db.execute("SELECT * FROM jobs WHERE id=?", (self.job_id,)).fetchone()
        self.assertEqual(row["status"], "deferred_uncertain")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["expected"], 615)
        self.assertIsNone(row["rows"])
        self.assertFalse(receipt["resubmitted"])
        self.assertEqual(receipt["previous_cookie_filename"], "guest_session.cookies")
        self.assertNotEqual(self.state.cookie_path().name, "guest_session.cookies")
        self.assertFalse(self.state.cookie_path().exists())
        self.assertEqual(len(list(self.directory.glob("deferred_receipt_*.json"))), 1)
        report = self.state.report()
        self.assertFalse(report["all_complete"])
        self.assertEqual(report["downloaded_rows"], 0)
        self.assertEqual(report["problems"][0]["status"], "deferred_uncertain")
        loader = Downloader(self.state, accept_terms=True)
        self.assertEqual(loader.portal.cookie_file, self.state.cookie_path())
        with patch.object(loader.portal, "request") as request:
            loader.recover_inflight()
            self.assertIsNone(loader.next_job())
            request.assert_not_called()

    def test_deferral_never_happens_automatically_on_resume(self):
        loader = Downloader(self.state, accept_terms=True)
        with self.assertRaises(UncertainExport):
            loader.recover_inflight()
        self.assertEqual(self.state.db.execute("SELECT status FROM jobs").fetchone()[0], "uncertain")
        self.assertEqual(self.state.cookie_path().name, "guest_session.cookies")

    def test_deferral_rejects_saved_polling_address(self):
        self.state.update(self.job_id, details=json.dumps({"refresh_url": "https://www.elbe-datenportal.de/poll"}))
        with self.assertRaises(PortalError):
            self.state.defer_uncertain(self.job_id, self.evidence, "continue")

    def test_deferral_rejects_recent_submission(self):
        from downloader import now
        self.state.update(self.job_id, details=json.dumps({"started_at": now()}))
        with self.assertRaises(PortalError):
            self.state.defer_uncertain(self.job_id, self.evidence, "continue")

    def test_deferral_rejects_mismatched_or_successful_evidence(self):
        original = json.loads(self.evidence.read_text(encoding="utf-8"))
        for changed in [{"job_id": "another"}, {"expected_count": 1}, {"actual_count": 615},
                        {"links": [["CSV", "https://www.elbe-datenportal.de/ausgabe/found.csv"]]},
                        {"refresh": [2, "https://www.elbe-datenportal.de/poll"]}, {"text": "normal page"}]:
            with self.subTest(changed=changed):
                write_json(self.evidence, {**original, **changed})
                with self.assertRaises(PortalError):
                    self.state.defer_uncertain(self.job_id, self.evidence, "continue")

    def test_deferral_rejects_other_inflight_work(self):
        with self.state.db:
            other = self.state.add_job("Test", {"station": "B"}, 1)
        self.state.update(other, status="waiting")
        with self.assertRaises(PortalError):
            self.state.defer_uncertain(self.job_id, self.evidence, "continue")

    def test_cookie_selection_rejects_paths_outside_state(self):
        self.state.set_meta("active_cookie_filename", "../unrelated.cookies")
        with self.assertRaises(PortalError):
            self.state.cookie_path()


class PlanningTests(unittest.TestCase):
    def test_equal_count_narrowing_of_year_range_is_allowed(self):
        filters = {"gewaehltMessstelle": "A", "gewaehltMessjahrVon": "2003", "gewaehltMessjahrBis": "2006"}
        result = check_result_selection(filters, {**filters, "gewaehltMessjahrBis": "2003"}, 155, 155)
        self.assertEqual(result["returned_years"], [2003, 2003])
        self.assertEqual(result["requested_years"], [2003, 2006])

    def test_count_change_cannot_be_excused_by_narrower_years(self):
        filters = {"gewaehltMessjahrVon": "2003", "gewaehltMessjahrBis": "2006"}
        with self.assertRaises(UncertainExport):
            check_result_selection(filters, {**filters, "gewaehltMessjahrBis": "2003"}, 150, 155)

    def test_broader_or_invalid_years_are_never_accepted(self):
        filters = {"gewaehltMessjahrVon": "2003", "gewaehltMessjahrBis": "2006"}
        for lo, hi in [("2002", "2006"), ("2003", "2007"), ("2005", "2004"), ("", "2006")]:
            with self.subTest(lo=lo, hi=hi), self.assertRaises(UncertainExport):
                check_result_selection(filters, {"gewaehltMessjahrVon": lo, "gewaehltMessjahrBis": hi}, 155, 155)

    def test_station_change_cannot_be_excused_by_narrower_years(self):
        filters = {"gewaehltMessstelle": "A", "gewaehltMessjahrVon": "2003", "gewaehltMessjahrBis": "2006"}
        with self.assertRaises(UncertainExport):
            check_result_selection(filters, {**filters, "gewaehltMessstelle": "B", "gewaehltMessjahrBis": "2003"}, 155, 155)

    def test_original_dates_are_checked_without_modification(self):
        raw = b"'Datum';'Messwert'\n'02.04.2003';'< 0,1'\n"
        result = check_csv_period(raw, "utf-8", {"returned_years": [2003, 2003]})
        self.assertEqual(result["checked_rows"], 1)
        self.assertIn(b"< 0,1", raw)

    def test_invalid_missing_or_out_of_range_csv_date_is_rejected(self):
        for value in ["", "31.02.2003", "12.03.2006", "2003"]:
            raw = f"'Datum';'Messwert'\n'{value}';'1'\n".encode()
            with self.subTest(value=value), self.assertRaises(PortalError):
                check_csv_period(raw, "utf-8", {"returned_years": [2003, 2003]})
        with self.assertRaises(PortalError):
            check_csv_period(b"'Bezugsjahr';'Messwert'\n'2003';'1'\n", "utf-8", {"returned_years": [2003, 2003]})

    def test_normalized_result_checks_csv_and_invalidates_filter_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            filters = {"gewaehltMessstelle": "A", "gewaehltMessjahrVon": "2003", "gewaehltMessjahrBis": "2006"}
            with state.db:
                state.add_job("Phytoplankton", filters, 1)
            job = state.db.execute("SELECT * FROM jobs").fetchone()
            loader = Downloader(state, accept_terms=True)
            loader.active_topic, loader.active_filters = "Phytoplankton", filters
            fields = {**filters, "gewaehltMessjahrBis": "2003"}
            hidden = ''.join(f'<input name="{k}" value="{v}">' for k, v in fields.items())
            initial = sample_page(hidden=hidden, count=1)
            link = "https://www.elbe-datenportal.de/FisFggElbe/ausgabe/period.csv"
            page = Page(initial.url, initial.html + f'<a href="{link}">CSV</a>')
            raw = b"'Datum';'Messwert'\n'02.04.2003';'1'\n"
            with patch.object(loader.portal, "request", return_value=(link, {"Content-Type": "text/csv"}, raw)):
                loader.await_export(job, page)
            self.assertIsNone(loader.active_page)
            self.assertIsNone(loader.active_topic)
            self.assertEqual(loader.active_filters, {})
            self.assertEqual(state.db.execute("SELECT status FROM jobs").fetchone()[0], "complete_with_warnings")
            meta = json.loads((loader.job_directory(job) / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["result_normalization"]["returned_years"], [2003, 2003])
            self.assertEqual(meta["files"][0]["period_validation"]["checked_rows"], 1)
            self.assertEqual((loader.job_directory(job) / "period.csv").read_bytes(), raw)
            state.db.close()

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

    def test_mismatched_result_saves_evidence_without_accepting_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                state.add_job("Phytoplankton", {"gewaehltMessstelle": "Requested"}, 155)
            job = state.db.execute("SELECT * FROM jobs").fetchone()
            loader = Downloader(state, accept_terms=True)
            page = sample_page(hidden='<input name="gewaehltMessstelle" value="Different">', count=155)
            page = Page(page.url, page.html + '<a href="/FisFggElbe/ausgabe/candidate.csv">CSV</a>')
            with patch.object(loader, "download_links") as download:
                with self.assertRaisesRegex(UncertainExport, "Requested"):
                    loader.await_export(job, page)
                download.assert_not_called()
            paths = list(loader.job_directory(job).glob("export_response_*.json"))
            self.assertEqual(len(paths), 1)
            evidence = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(evidence["differences"]["gewaehltMessstelle"],
                             {"expected": "Requested", "actual": "Different"})
            self.assertTrue(evidence["links"][0][1].endswith("candidate.csv"))
            self.assertEqual(state.db.execute("SELECT links FROM jobs").fetchone()[0], None)
            state.db.close()

    def test_stale_result_link_is_captured_but_never_downloaded(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                state.add_job("Phytoplankton", {}, 155)
            job = state.db.execute("SELECT * FROM jobs").fetchone()
            loader = Downloader(state, accept_terms=True)
            url = "https://www.elbe-datenportal.de/FisFggElbe/ausgabe/old.csv"
            page = Page("https://www.elbe-datenportal.de/poll", f'<a href="{url}">CSV</a>')
            with patch.object(loader, "download_links") as download:
                with self.assertRaises(UncertainExport):
                    loader.await_export(job, page, previous_links=[url])
                download.assert_not_called()
            self.assertEqual(len(list(loader.job_directory(job).glob("export_response_*.json"))), 1)
            state.db.close()

    def test_slow_export_is_not_stopped_after_fifteen_minutes(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                job_id = state.add_job("Hydro", {}, 1)
            job = state.db.execute("SELECT * FROM jobs").fetchone()
            loader = Downloader(state, accept_terms=True)
            waiting = Page("https://www.elbe-datenportal.de/poll", '<meta http-equiv="refresh" content="2; URL=/poll">')
            ready = Page("https://www.elbe-datenportal.de/poll", '<a href="/FisFggElbe/ausgabe/slow.csv">CSV</a>')
            with patch("downloader.time.monotonic", side_effect=[0, 901]), patch("downloader.time.sleep"), \
                 patch.object(loader.portal, "get_page", return_value=ready), patch.object(loader, "download_links"):
                loader.await_export(job, waiting)
            row = state.db.execute("SELECT * FROM jobs").fetchone()
            self.assertEqual(row["status"], "ready")
            self.assertEqual(loader.export_timeout, 3600)
            self.assertIn("last_poll_at", json.loads(row["details"]))
            state.db.close()

    def test_wait_deadline_preserves_poll_context(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                state.add_job("Hydro", {}, 1)
            job = state.db.execute("SELECT * FROM jobs").fetchone()
            loader = Downloader(state, accept_terms=True, export_timeout=60)
            waiting = Page("https://www.elbe-datenportal.de/poll", '<meta http-equiv="refresh" content="2; URL=/poll">')
            with patch("downloader.time.monotonic", side_effect=[0, 61]), patch.object(loader.portal, "submit") as submit:
                with self.assertRaises(UncertainExport):
                    loader.await_export(job, waiting)
                submit.assert_not_called()
            row = state.db.execute("SELECT * FROM jobs").fetchone()
            self.assertEqual(json.loads(row["details"])["refresh_url"], "https://www.elbe-datenportal.de/poll")
            state.db.close()

    def test_stop_during_wait_preserves_restart_context(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                state.add_job("Hydro", {}, 1)
            job = state.db.execute("SELECT * FROM jobs").fetchone()
            loader = Downloader(state, accept_terms=True)
            loader.stopping = True
            waiting = Page("https://www.elbe-datenportal.de/poll", '<meta http-equiv="refresh" content="2; URL=/poll">')
            with patch.object(loader.portal, "submit") as submit:
                with self.assertRaises(KeyboardInterrupt):
                    loader.await_export(job, waiting)
                submit.assert_not_called()
            row = state.db.execute("SELECT * FROM jobs").fetchone()
            self.assertEqual(row["status"], "waiting")
            self.assertIn("refresh_url", json.loads(row["details"]))
            state.db.close()

    def test_recovered_finished_but_invalid_export_is_review_not_uncertain(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            http.cookiejar.LWPCookieJar().save(str(state.directory / "guest_session.cookies"), ignore_discard=True)
            with state.db:
                job_id = state.add_job("Hydro", {}, 2)
            state.update(job_id, status="waiting", details=json.dumps({"refresh_url": "https://www.elbe-datenportal.de/poll"}))
            loader = Downloader(state, accept_terms=True)
            link = "https://www.elbe-datenportal.de/FisFggElbe/ausgabe/test.csv"
            ready = Page("https://www.elbe-datenportal.de/poll", f'<a href="{link}">CSV</a>')
            with patch.object(loader.portal, "get_page", return_value=ready), \
                 patch.object(loader.portal, "request", return_value=(link, {"Content-Type": "text/csv"}, b"'A';'B'\n'x';'y'\n")):
                with self.assertRaises(PortalError):
                    loader.recover_inflight()
            row = state.db.execute("SELECT * FROM jobs").fetchone()
            self.assertEqual(row["status"], "needs_review")
            self.assertIn(link, row["links"])
            state.db.close()

    def test_refinement_preserves_original_and_exact_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                job_id = state.add_job("Hydro", {"gewaehltParameter": "Durchfluss"}, 10)
            state.update(job_id, status="needs_review", error="CSV row count 12 differs from expected 10; raw files preserved")
            job = state.db.execute("SELECT * FROM jobs").fetchone()
            loader = Downloader(state, accept_terms=True)
            raw = loader.job_directory(job) / "original.csv"
            raw.write_bytes(b"unchanged")
            page = sample_page(select("gewaehltMessstelle", [("A", 4), ("B", 6)]), 10)
            with patch.object(loader, "load_filters", return_value=page), patch.object(loader.portal, "submit") as submit:
                loader.refine_review_job(job)
                submit.assert_not_called()
            self.assertEqual(raw.read_bytes(), b"unchanged")
            self.assertEqual(state.db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0], "split")
            self.assertEqual(state.db.execute("SELECT sum(expected) FROM jobs WHERE parent=?", (job_id,)).fetchone()[0], 10)
            self.assertTrue((raw.parent / "refinement.json").exists())
            state.db.close()

    def test_refinement_rejects_uncertain_submission(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                job_id = state.add_job("Hydro", {}, 10)
            state.update(job_id, status="uncertain", error="timeout")
            loader = Downloader(state, accept_terms=True)
            with patch.object(loader, "load_filters") as get:
                with self.assertRaises(PortalError):
                    loader.refine_review_job(state.db.execute("SELECT * FROM jobs").fetchone())
                get.assert_not_called()
            state.db.close()

    def test_progress_displays_stopped_process_and_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                job_id = state.add_job("Hydro", {}, 10)
            state.update(job_id, status="waiting", details=json.dumps({"wait_seconds": 950, "last_poll_at": "now"}))
            write_json(state.directory / "worker.json", {"stopped_at": "today", "reason": "timeout"})
            text = format_progress(state.report(write=False))
            self.assertIn("Prozess beendet", text)
            self.assertIn("950 Sekunden", text)
            state.db.close()

    def test_catalog_count_mismatch_is_reported_without_discarding_stations(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            loader = Downloader(state, accept_terms=True)
            page = Page("https://www.elbe-datenportal.de/catalog",
                        '<form action="catalog">' + select("gewaehltMessstelle", [("A", 4), ("B", 6)]) +
                        '</form><p>Die aktuelle Abfrage umfasst 3 Messstellen mit 10 Messwerten.</p>')
            with patch.object(loader.portal, "enter", return_value=page):
                loader.discover_stations()
            self.assertEqual(state.meta("station_count"), 3)
            self.assertEqual(state.meta("station_selectable_count"), 2)
            self.assertEqual(state.db.execute("SELECT count(*) FROM stations").fetchone()[0], 2)
            report = state.report(write=False)
            self.assertFalse(report["all_complete"])
            self.assertEqual(report["problems"][0]["id"], "station_catalog_count")
            state.db.close()

    def test_review_problem_does_not_block_other_pending_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder))
            with state.db:
                failed = state.add_job("Hydro", {"gewaehltParameter": "A"}, 2)
                next_id = state.add_job("Hydro", {"gewaehltParameter": "B"}, 5)
            loader = Downloader(state, accept_terms=True)
            with patch.object(loader, "load_filters", side_effect=PortalError("count discrepancy")):
                loader.process_job(state.db.execute("SELECT * FROM jobs WHERE id=?", (failed,)).fetchone())
            self.assertEqual(loader.next_job()["id"], next_id)
            report = state.report(write=False)
            self.assertFalse(report["all_complete"])
            self.assertEqual(report["problems"][0]["status"], "needs_review")
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
