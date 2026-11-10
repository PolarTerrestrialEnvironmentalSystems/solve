"""Check authentication/model listing before spawning a large crawler campaign.

Only GET /models is called. No search or completion is submitted, and neither
credential values nor server response/error bodies are printed or persisted.
Successful listing is not a guarantee that subsequent inference will succeed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

import credential_env


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A credential must never follow a redirect to another endpoint.
        return None


def inspect_models(base_url, key, required, opener=None, timeout=15):
    try:
        parsed = urllib.parse.urlsplit(base_url)
    except ValueError:
        return {"ok": False, "reason": "invalid_https_api_base"}
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment):
        return {"ok": False, "reason": "invalid_https_api_base"}
    if not key:
        return {"ok": False, "reason": "credential_missing"}
    endpoint = base_url.rstrip("/") + "/models"
    request = urllib.request.Request(endpoint, headers={"Authorization": "Bearer " + key,
        "Accept": "application/json", "User-Agent": "SOLVE-api-check/1.0"}, method="GET")
    client = opener or urllib.request.build_opener(NoRedirect())
    try:
        with client.open(request, timeout=timeout) as response:
            status = response.status
            body = response.read(1024 * 1024 + 1)
        if status != 200:
            return {"ok": False, "http_status": status, "reason": "unexpected_http_status"}
        if len(body) > 1024 * 1024:
            return {"ok": False, "http_status": status, "reason": "response_too_large"}
        data = json.loads(body)
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise ValueError()
        available = {item["id"] for item in data["data"]
                     if isinstance(item, dict) and isinstance(item.get("id"), str)}
        if not available:
            raise ValueError()
        missing = sorted(set(required) - available)
        return {"ok": not missing, "http_status": status,
                "reason": "required_models_missing" if missing else "models_endpoint_ok",
                "required_models": sorted(required), "missing_models": missing}
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        return {"ok": False, "http_status": code,
                "reason": "authentication_rejected" if code == 401 else "api_http_error"}
    except (OSError, ValueError, TypeError, urllib.error.URLError):
        return {"ok": False, "reason": "network_or_invalid_models_response"}


def check_config(config, env_file=None, inspect=inspect_models):
    credential_env.load({"credentials_env_file": env_file or config.get("credentials_env_file", "")})
    grouped = {}
    for section in ("preflight", "review"):
        settings = config[section]
        needed = settings["enabled"] if section == "preflight" else settings["mode"] == "api" or settings["required"]
        if not needed:
            continue
        base = os.getenv(settings["base_url_env"], settings["base_url"]).rstrip("/")
        model = os.getenv(settings["model_env"], settings["model"])
        key_name = settings["api_key_env"]
        key = os.getenv(key_name, "")
        # The credentials are dictionary keys in memory only, never output.
        grouped.setdefault((base, key_name, key), set()).add(model)
        secondary = os.getenv("GRAPHRAG_API_KEY2", "")
        if key_name == "BLABLADOR_KEY" and secondary and secondary != key:
            grouped.setdefault((base, "GRAPHRAG_API_KEY2", secondary), set()).add(model)
    checks = []
    for (base, name, key), models in grouped.items():
        checks.append({"credential_name": name, **inspect(base, key, models)})
    return {"ok": all(item["ok"] for item in checks), "checks": checks,
            "scope": "Authentication and required model listing only; no completions or Serper calls."}


def print_report(report):
    for item in report["checks"]:
        status = "OK" if item["ok"] else "FEHLER"
        http = f" HTTP {item['http_status']}" if "http_status" in item else ""
        print(f"Blablador {item['credential_name']}: {status}{http} ({item['reason']})")
        if item.get("missing_models"):
            print("Nicht in /models aufgeführt: " + ", ".join(item["missing_models"]))
    if not report["ok"]:
        print("Kein Worker gestartet. Zugang/Modellkonfiguration prüfen; einen abgelaufenen zweiten Schlüssel erneuern oder aus der Umgebung/Datei entfernen.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parent.parent /
                        "inputs/sites_blablador_33_serper_v1_6_2.json")
    parser.add_argument("--env-file")
    args = parser.parse_args(argv)
    import run_web_enrichment as core
    report = check_config(core.load_config(args.config), args.env_file)
    print_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
