import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import check_blablador as checker


class Response(io.BytesIO):
    status = 200


class Client:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, **kwargs):
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class ApiCheckTests(unittest.TestCase):
    def test_read_only_listing_and_model_check(self):
        client = Client(Response(json.dumps({"data": [{"id": "alias-fast"}]}).encode()))
        result = checker.inspect_models("https://example.org/v1", "fixture", {"alias-fast", "alias-large"}, client)
        self.assertFalse(result["ok"])
        self.assertEqual(result["missing_models"], ["alias-large"])
        self.assertEqual(client.requests[0].method, "GET")
        self.assertIsNone(client.requests[0].data)

    def test_auth_error_body_is_not_exposed(self):
        error = urllib.error.HTTPError("https://example.org/v1/models", 401, "PRIVATE_FIXTURE",
                                       {}, io.BytesIO(b"PRIVATE_FIXTURE"))
        result = checker.inspect_models("https://example.org/v1", "PRIVATE_FIXTURE", set(), Client(error))
        self.assertEqual(result["reason"], "authentication_rejected")
        self.assertNotIn("PRIVATE_FIXTURE", json.dumps(result))

    def test_invalid_endpoint_and_redirect_refused(self):
        for url in ("http://example.org/v1", "https://a:b@example.org/v1", "https://example.org/v1?key=fixture"):
            self.assertEqual(checker.inspect_models(url, "fixture", set())["reason"], "invalid_https_api_base")
        self.assertIsNone(checker.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))

    def test_two_sections_are_grouped_but_expired_secondary_blocks(self):
        common = dict(base_url="https://example.org/v1", base_url_env="TEST_BASE", model_env="TEST_MODEL",
                      api_key_env="BLABLADOR_KEY")
        config = {"preflight": dict(common, enabled=True, model="alias-fast"),
                  "review": dict(common, mode="api", required=True, model="alias-large")}
        calls = []
        def inspect(base, key, models):
            calls.append((base, key, models))
            return {"ok": key == "primary", "reason": "fixture"}
        with patch.dict(os.environ, {"BLABLADOR_KEY": "primary", "GRAPHRAG_API_KEY2": "expired"}, clear=True):
            result = checker.check_config(config, inspect=inspect)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][2], {"alias-fast", "alias-large"})
        self.assertFalse(result["ok"])
        self.assertNotIn("expired", json.dumps(result))

    def test_campaign_refuses_invalid_auth_before_any_worker_and_accepts_uneven_budgets(self):
        import elbe_campaign as campaign
        from test_elbe_campaign import body, accepted
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = [body(f"DERW_DEBB{i}", f"River{i}", "river") for i in range(3)]
            registry, seeds = root / "registry.json", root / "seeds.json"
            registry.write_text(json.dumps(rows), encoding="utf8")
            seeds.write_text(json.dumps([{"waterbody_id": row["waterbody_id"], "accepted": accepted(1)}
                                         for row in rows]), encoding="utf8")
            workspace = root / "campaign"
            campaign.main(["prepare", "--registry", str(registry), "--seeds", str(seeds),
                           "--workspace", str(workspace), "--groups", "2"])
            with patch("check_blablador.check_config", return_value={"ok": False, "checks": []}), \
                    patch("parallel_crawler.run") as run:
                self.assertEqual(campaign.main(["run", "--workspace", str(workspace)]), 1)
                run.assert_not_called()
            with patch("check_blablador.check_config", return_value={"ok": True, "checks": []}), \
                    patch("parallel_crawler.run", return_value=0) as run:
                self.assertEqual(campaign.main(["run", "--workspace", str(workspace)]), 0)
                run.assert_called_once()
                self.assertEqual(run.call_args.args[1], 30)
                self.assertEqual(run.call_args.args[4], 6)


if __name__ == "__main__":
    unittest.main()
