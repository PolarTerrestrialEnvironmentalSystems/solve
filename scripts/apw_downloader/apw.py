#!/usr/bin/env python3
"""Standalone APW downloader. Run `python apw.py --help`. Never writes to a KG."""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from contextlib import contextmanager
from pathlib import Path

from common import Http, OutputLock, Snapshot, now, write_json
import waterbook
import wrrl


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Muss größer als 0 sein.")
    return number


def interval(value):
    number = float(value)
    if not 1 <= number <= 60:
        raise argparse.ArgumentTypeError("Anfrageabstand muss zwischen 1 und 60 Sekunden liegen.")
    return number


def metres(value):
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 5000:
        raise argparse.ArgumentTypeError("Suchpuffer: 0 bis 5000 Meter.")
    return number


def arguments(argv=None):
    parser = argparse.ArgumentParser(description="Öffentliche APW-Daten: WRRL-WFS und Wasserbuch-PDFs. Kein Datenbankimport.")
    parser.add_argument("command", choices=["wrrl", "waterbook", "all", "status", "export"])
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "data")
    parser.add_argument("--snapshot", default="default", help="Gleicher Name = fortsetzen; neuer Name = neuer Quellenstand.")
    parser.add_argument("--layers", nargs="+", choices=list(wrrl.LAYERS), default=list(wrrl.LAYERS))
    parser.add_argument("--water-body-id", nargs="+", default=[], help="Nur für wrrl: exakte Gewässer-IDs; Messstellen über ihre Gewässerzuordnung. Neuer Filter = neuer Snapshot.")
    parser.add_argument("--page-size", type=positive, default=500)
    parser.add_argument("--max-features", type=positive, help="Pilotlimit pro WRRL-Ebene; kein Vollabruf.")
    parser.add_argument("--authority", action="append", default=[], help="Öffentliche Wasserbehörde, mehrfach möglich; ohne Angabe alle gelisteten Behörden.")
    parser.add_argument("--lake-snapshot", type=Path, help="Nur waterbook: vollständiger, nach IDs gefilterter WRRL-Seen-Snapshot als Geometriereferenz.")
    parser.add_argument("--search-buffer-m", type=metres, default=None, help="Puffer um die Seen für Wasserbuch-Kandidaten, Standard 250 m; kein Beweis einer Gewässerzuordnung.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--index", type=Path, help="Bereits erfasster Wasserbuch-Linkindex (JSON); ohne Browser.")
    source.add_argument("--pdf-url", action="append", default=[], help="Bekannter öffentlicher PDF-Link; mehrfach möglich, ohne Browser.")
    source.add_argument("--search-evidence", type=Path, help="Mit --lake-snapshot: bereits erfasste öffentliche Geometrie-Suchnachweise (JSON), ohne Browser.")
    parser.add_argument("--max-documents", type=positive, help="Pilotlimit für PDF-Downloads; Discovery läuft für den gewählten Suchumfang.")
    parser.add_argument("--headed", action="store_true", help="Browser bei der Wasserbuch-Recherche sichtbar öffnen (lokaler Desktop).")
    parser.add_argument("--delay", type=interval, default=1.5, help="Sekunden zwischen HTTP-Anfragen, Standard 1.5.")
    args = parser.parse_args(argv)
    if args.page_size > 5000:
        parser.error("--page-size darf höchstens 5000 sein.")
    if args.authority and (args.index or args.pdf_url):
        parser.error("--authority ist nur mit der Browser-Recherche verwendbar.")
    if args.water_body_id and args.command != "wrrl":
        parser.error("--water-body-id ist nur mit wrrl verwendbar. Das Wasserbuch hat noch keinen Gewässerfilter; getrennt abrufen.")
    if args.authority and args.command == "wrrl":
        parser.error("--authority filtert nur das Wasserbuch, nicht WRRL. Für WRRL --water-body-id verwenden.")
    if args.lake_snapshot and (args.command != "waterbook" or args.authority or args.index or args.pdf_url):
        parser.error("--lake-snapshot nur mit waterbook und ohne --authority, --index oder --pdf-url verwenden.")
    if (args.search_evidence or args.search_buffer_m is not None) and not args.lake_snapshot:
        parser.error("--search-evidence und --search-buffer-m benötigen --lake-snapshot.")
    try:
        wrrl.selection_scope(args.water_body_id)
    except ValueError as exc:
        parser.error(str(exc))
    return args


@contextmanager
def managed_snapshot(output, name):
    with OutputLock(output):
        snapshot = Snapshot(output, name)
        try:
            yield snapshot
        except BaseException as exc:
            snapshot.state["last_error"] = {"at": now(), "message": "interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc)}
            snapshot.save()  # Still holding the output lock.
            raise


def main(argv=None):
    args = arguments(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    snapshot = None
    try:
        with managed_snapshot(args.output, args.snapshot) as snapshot:
            if args.command == "status":
                print(json.dumps({"snapshot": snapshot.state["snapshot"], "updated_at": snapshot.state.get("updated_at"),
                    "wrrl_scope": snapshot.state.get("wrrl_scope", {"kind": "all"}),
                    "wrrl": {k: {p: v.get(p) for p in ("downloaded", "expected", "complete")} for k, v in snapshot.state["wrrl"].items()},
                    "waterbook": {k: snapshot.state["waterbook"].get(k) for k in ("expected_documents", "downloaded_documents", "download_complete", "parsed_documents", "parse_complete", "index_complete_for_scope", "lake_summary")},
                    "last_error": snapshot.state.get("last_error")}, ensure_ascii=False, indent=2))
                return 0
            snapshot.state["last_error"] = None
            snapshot.save()
            http = Http(delay=args.delay)
            if args.command in {"wrrl", "all"}:
                wrrl.download(http, snapshot, args.layers, args.page_size, args.max_features, args.water_body_id)
            if args.command in {"waterbook", "all"}:
                if args.lake_snapshot:
                    import waterbook_lakes
                    scope = waterbook_lakes.prepare(snapshot, args.lake_snapshot,
                        args.search_buffer_m if args.search_buffer_m is not None else 250)
                    if args.search_evidence:
                        index = waterbook.import_search_evidence(snapshot, scope, args.search_evidence)
                    else:
                        index = waterbook.discover(snapshot, [], args.headed, args.delay, lake_scope=scope)
                elif args.index:
                    index = waterbook.load_index(args.index)
                elif args.pdf_url:
                    for value in args.pdf_url:
                        waterbook.pdf_number(value)
                    index = {"schema_version": 1, "scope": {"kind": "explicit_pdf_links"}, "complete_for_scope": True,
                             "records": [{"url": value, "fields": {}} for value in dict.fromkeys(args.pdf_url)]}
                else:
                    index = waterbook.discover(snapshot, args.authority, args.headed, args.delay)
                write_json(snapshot.root / "waterbook_index.json", index)
                waterbook.download(http, snapshot, index, args.max_documents)
            if args.command == "export":
                wrrl.export(snapshot)
                waterbook.export(snapshot)
            snapshot.state["last_success_at"] = now()
            snapshot.save()
            logging.getLogger("apw").info("Fertig für den gewählten Umfang: %s (Vollständigkeit siehe manifest.json).", snapshot.root)
            return 0
    except KeyboardInterrupt:
        logging.getLogger("apw").warning("Abgebrochen. Mit demselben Befehl und Snapshot fortsetzen.")
        return 130
    except Exception as exc:
        logging.getLogger("apw").error("%s", exc)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
