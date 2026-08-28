"""Dependency-light local HTTP server for the knowledge-graph interface."""

from __future__ import annotations

import argparse
import json
import mimetypes
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Timer
from urllib.parse import urlparse

from .config import Settings
from .service import KnowledgeGraphService


STATIC_DIRECTORY = Path(__file__).with_name("static")


class KnowledgeGraphHandler(BaseHTTPRequestHandler):
    service: KnowledgeGraphService

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Ungültige Anfragegröße.") from exc
        if size > 1_000_000:
            raise ValueError("Die Anfrage ist zu groß.")
        try:
            return json.loads(self.rfile.read(size).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Die Anfrage enthält kein gültiges JSON.") from exc

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        if path == "/api/health":
            try:
                self.service.verify_connectivity()
                self._json({"status": "ok", "database": self.service.settings.neo4j_database})
            except Exception as exc:  # driver exceptions differ by server version
                self._json({"status": "error", "error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        filename = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC_DIRECTORY / filename).resolve()
        if STATIC_DIRECTORY.resolve() not in target.parents and target != STATIC_DIRECTORY.resolve():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/ask":
                result = self.service.ask(str(payload.get("question", "")))
            elif path == "/api/cypher":
                result = self.service.execute_readonly(str(payload.get("cypher", "")))
            else:
                self._json({"error": "Endpunkt nicht gefunden."}, HTTPStatus.NOT_FOUND)
                return
            self._json(result)
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # Return a useful UI error instead of dropping the connection.
            self._json({"error": f"Abfrage fehlgeschlagen: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)


def create_server(settings: Settings | None = None) -> tuple[ThreadingHTTPServer, KnowledgeGraphService]:
    settings = settings or Settings.from_environment()
    service = KnowledgeGraphService(settings)
    handler = type("ConfiguredKnowledgeGraphHandler", (KnowledgeGraphHandler,), {"service": service})
    return ThreadingHTTPServer((settings.host, settings.port), handler), service


def main() -> None:
    defaults = Settings.from_environment()
    parser = argparse.ArgumentParser(description="SOLVE Knowledge-Graph-Interface")
    parser.add_argument("--host", default=defaults.host)
    parser.add_argument("--port", default=defaults.port, type=int)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    settings = Settings(
        neo4j_uri=defaults.neo4j_uri,
        neo4j_user=defaults.neo4j_user,
        neo4j_password=defaults.neo4j_password,
        neo4j_database=defaults.neo4j_database,
        host=args.host,
        port=args.port,
    )
    server, service = create_server(settings)
    url = f"http://{settings.host}:{settings.port}"
    print(f"Knowledge-Graph-Interface läuft unter {url}")
    print("Beenden mit Strg+C")
    if not args.no_browser:
        Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()


if __name__ == "__main__":
    main()
