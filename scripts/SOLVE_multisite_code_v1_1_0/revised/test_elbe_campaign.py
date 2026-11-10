import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

import elbe_campaign as campaign
import run_web_enrichment as core


def body(identifier, name, kind="lake", states=None):
    return {"waterbody_id": identifier, "name_original": name, "waterbody_type": kind,
            "river_basin_code": "DE5000", "country_code": "DE", "reporting_year": 2022,
            "reporting_state": "BB", "spatial_states_target": states or ["BB"],
            "spatial_states": states or ["BB"], "low_overlap_states_review": []}


def accepted(count):
    return [{"url": f"https://example.org/report-{number}.pdf", "title": f"Report {number}"}
            for number in range(count)]


class CampaignTests(unittest.TestCase):
    def test_coverage_never_includes_unseeded_even_with_shortfall(self):
        rows = [body("DELW_DEBB1", "Lake One"), body("DELW_DEBB2", "Lake Two"),
                body("DERW_DEBB3", "River3", "river")]
        seeds = {"DELW_DEBB1": accepted(1), "DERW_DEBB3": accepted(1)}
        normal, report = campaign.assess_seed_coverage(rows, seeds)
        self.assertEqual([row[0]["waterbody_id"] for row in normal], ["DERW_DEBB3"])
        shortfall, report = campaign.assess_seed_coverage(rows, seeds, allow_shortfall=True)
        self.assertEqual(len(shortfall), 2)
        self.assertEqual(report[1]["status"], "excluded_unseeded")

    def test_transliteration_and_segment_alias_preserve_identity(self):
        lake = body("DELW_DEBB1", "GROSSER MUEGGELSEE")
        self.assertIn("GROSSER MÜGGELSEE", campaign.name_aliases(lake))
        self.assertIn("GROSSER MUGGELSEE", campaign.name_aliases(lake))
        river = campaign.make_waterbody(body("DERW_DEBB3", "ELBE3", "river"), accepted(1))
        self.assertEqual(river["id"], "DERW_DEBB3")
        self.assertEqual(river["name"], "ELBE3")
        self.assertIn("ELBE", river["aliases"])
        self.assertEqual(river["waterbody_kind"], "river_segment")
        self.assertFalse(river["identity_requires_confirmation"])

    def test_seed_manifest_dedup_and_privacy(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "seed.json"
            file.write_text(json.dumps({"waterbody_id": "DELW_DEBB1", "accepted": [
                {"url": "https://example.org/redirect", "final_url": "https://example.org/report.pdf",
                 "evidence": "LONG FULL TEXT", "prompt": "MODEL PROMPT", "title": "Report"},
                {"url": "https://example.org/report.pdf"}, {"url": "http://127.0.0.1/private"}]}), encoding="utf8")
            seeds, _, issues = campaign.load_seeds([Path(temp), file])
            self.assertEqual(len(seeds["DELW_DEBB1"]), 1)
            self.assertNotIn("evidence", seeds["DELW_DEBB1"][0])
            self.assertNotIn("prompt", seeds["DELW_DEBB1"][0])
            self.assertEqual(len(issues), 1)

    def test_collector_http_metadata_and_sanitization_round_trip(self):
        raw = {"url": "https://example.org/report.pdf", "title": "Report", "topic_evidence": "WRRL",
               "catalogue_verified_waterbody_id": "DERW_DEBB1", "catalogue_pages": [7, 8],
               "context_pages": [3],
               "locator": "PDF-Seite(n) 7, 8", "source_url": "https://example.org/catalogue",
               "reason": "Exact waterbody identifier", "evidence": "Do not retain this passage",
               "http": {"http_status": 200, "status": "fetched", "content_type": "application/pdf",
                        "checked_at": "2026-11-10T10:00:00Z", "sample_sha256": "a" * 64}}
        sanitized = campaign.sanitized_seed(raw)
        self.assertEqual(sanitized["http_status"], 200)
        self.assertEqual(sanitized["hash_scope"], "http_response_sample")
        self.assertEqual(sanitized["catalogue_pages"], [7, 8])
        self.assertEqual(sanitized["context_pages"], [3])
        self.assertEqual(sanitized["catalogue_verified_waterbody_id"], "DERW_DEBB1")
        self.assertEqual(sanitized, campaign.sanitized_seed(sanitized))
        raw["http"].pop("http_status")
        self.assertNotIn("http_status", campaign.sanitized_seed(raw))
        self.assertIsNone(campaign.public_seed_url("https://example.org/file?access_token=secret"))

    def test_merged_snapshot_includes_missing_and_nonadditive_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = [body("DELW_DEBB1", "Lake", states=["BB", "BE"]), body("DERW_DEBB2", "River2", "river")]
            registry, seeds, merged = root / "registry.json", root / "seeds.json", root / "merged.json"
            registry.write_text(json.dumps(rows), encoding="utf8")
            seeds.write_text(json.dumps([{"waterbody_id": "DELW_DEBB1", "accepted": accepted(2)}]), encoding="utf8")
            campaign.main(["merge-seeds", "--registry", str(registry), "--seeds", str(seeds), "--output", str(merged)])
            self.assertNotIn(b"\r\n", merged.read_bytes())
            coverage = campaign.read_json(root / "seed_coverage.json")
            self.assertEqual(coverage["totals"]["waterbodies"], 2)
            self.assertEqual(coverage["totals"]["missing_all_sources"], 1)
            self.assertEqual(coverage["by_spatial_state_nonadditive"]["BB"]["waterbodies"], 2)
            self.assertEqual(coverage["by_spatial_state_nonadditive"]["BE"]["waterbodies"], 1)
            reloaded, _, _ = campaign.load_seeds([merged])
            self.assertEqual(len(reloaded["DELW_DEBB1"]), 2)
            self.assertNotIn("DERW_DEBB2", reloaded)

    def test_review_provenance_privacy_and_idempotent_round_trip(self):
        raw = dict(url="https://example.org/paper", llm_content_review={
            "status": "completed", "policy": "llm-content-veto-2:alias-fast", "checked_at": "2026-11-10T10:00:00Z",
            "input_sha256": "b" * 64, "scope": "do not keep arbitrary text", "prompt": "private prompt",
            "decision": {"action": "accept", "identity": "match", "category": "research", "reason": "unwanted free reasoning"}})
        sanitized = campaign.sanitized_seed(raw)
        metadata = sanitized["review_metadata"]
        self.assertEqual(metadata["model_alias"], "alias-fast")
        self.assertTrue(metadata["llm_review_completed"])
        self.assertEqual(metadata["action"], "accept")
        self.assertNotIn("reason", json.dumps(sanitized))
        self.assertNotIn("prompt", json.dumps(sanitized))
        self.assertEqual(sanitized, campaign.sanitized_seed(sanitized))
        raw["llm_content_review"]["status"] = "not_required_verified_official_identifier"
        official = campaign.sanitized_seed(raw)["review_metadata"]
        self.assertFalse(official["llm_review_completed"])
        self.assertNotIn("model_alias", official)
        self.assertNotIn("action", official)

    def test_prepare_filters_and_scales_by_actual_group_size(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = [body(f"DELW_DEBB{index}", f"Lake {index}") for index in range(4)]
            registry, seeds, workspace = root / "registry.json", root / "seeds.json", root / "new_run"
            registry.write_text(json.dumps(rows), encoding="utf8")
            seeds.write_text(json.dumps([{"waterbody_id": row["waterbody_id"], "accepted": accepted(10)}
                                         for row in rows[:3]]), encoding="utf8")
            self.assertEqual(campaign.main(["prepare", "--workspace", str(workspace.resolve()),
                "--registry", str(registry), "--seeds", str(seeds), "--groups", "2"]), 0)
            report = campaign.read_json(workspace / "preparation_report.json")
            self.assertEqual(report["included_count"], 3)
            self.assertEqual(report["status_counts"], {"ready": 3, "excluded_unseeded": 1})
            for name, count in [("group-01", 2), ("group-02", 1)]:
                config = core.load_config(workspace / name / "config.json")
                self.assertEqual(config["limits"]["max_tasks_per_run"], count * 500)
                self.assertEqual(config["preflight"]["max_calls_per_run"], count * 500)
                self.assertEqual(config["limits"]["max_seconds_per_run"], 604800)
                self.assertTrue(config["start_links"]["prepared"])
                self.assertFalse(config["scientific_discovery"]["enabled"])
                self.assertFalse(config["retrieval"]["crossref_enabled"])
                self.assertNotIn("credentials_env_file", config)
                self.assertEqual(len(config["topics"]), 19)
            with self.assertRaisesRegex(ValueError, "existiert"):
                campaign.main(["prepare", "--workspace", str(workspace.resolve()),
                    "--registry", str(registry), "--seeds", str(seeds)])

    def test_zero_ready_writes_report_without_runnable_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry, seeds, workspace = root / "registry.json", root / "seeds.json", root / "new_run"
            registry.write_text(json.dumps([body("DELW_DEBB1", "Lake")]), encoding="utf8")
            seeds.write_text("[]", encoding="utf8")
            with self.assertRaisesRegex(ValueError, "Kein Gewässer"):
                campaign.main(["prepare", "--workspace", str(workspace.resolve()),
                    "--registry", str(registry), "--seeds", str(seeds), "--allow-shortfall"])
            self.assertTrue((workspace / "preparation_report.json").exists())
            self.assertFalse((workspace / "parallel.json").exists())

    def test_status_ignores_foreign_profiles_and_historical_totals(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            config = campaign.campaign_config([(body("DELW_DEBB1", "Lake"), accepted(10))], campaign.DEFAULT_CONFIG)
            profile = core.stable(dict(waterbody=config["waterbodies"][0], topics=config["topics"], context=config["context"]))
            with closing(sqlite3.connect(directory / "research.sqlite")) as database:
                for table in ["tasks", "searches"]:
                    database.execute(f"CREATE TABLE {table}(profile TEXT, status TEXT)")
                    database.executemany(f"INSERT INTO {table} VALUES(?,?)", [(profile, "pending"),
                        ("foreign_or_old_profile", "pending"), ("foreign_or_old_profile", "retry")])
                database.commit()
            campaign.write_json(directory / "last_run.json", {"pending_tasks": 900, "pending_searches": 800})
            status = campaign.current_group_status(directory, config)
            self.assertEqual(status["current_pending_tasks"], 1)
            self.assertEqual(status["current_pending_searches"], 1)
            self.assertEqual(status["last_run"]["pending_tasks"], 900)


if __name__ == "__main__":
    unittest.main()
