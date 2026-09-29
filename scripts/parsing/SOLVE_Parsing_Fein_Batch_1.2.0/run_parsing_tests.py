"""Run from any working directory: python path/to/run_parsing_tests.py --dry-run."""
from __future__ import annotations

import argparse
from contextlib import closing
import importlib.metadata
import sys
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from pathlib import Path

from blablador_client import BlabladorClient, Settings, minute_report
from common import BASE_DIR, RunLock, digest, file_hash, load_env, read_json, utc_now, write_json
from extract_facts import build_messages, context_jobs, estimated_tokens, extract_chunk
from prepare_document import prepare_document
from schemas import Event, Extraction, Relationship, read_headers
from validate_export import compare, export_variant, finalize


def check_resume_compatibility(manifest, inputs, code_hashes, dependencies, config, signature):
    if manifest['input_signature'] != signature:
        raise ValueError('Eingaben, Code, Bibliotheken oder Einstellungen geändert; neuen Ausgabeordner verwenden.')
    return False


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="PDF ausschließlich mit feiner Ontologie über Blablador parsen.")
    p.add_argument("--pdf", default="GEK_Obere_Bode_Endbericht-Textteil.pdf")
    p.set_defaults(ontology="fein")
    p.add_argument("--env-file", default=".env", help="Relative Pfade beziehen sich auf parsing_tests.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Aufbereitung und Aufrufschätzung; keine API-Anfrage, kein Key nötig.")
    mode.add_argument("--list-models", action="store_true", help="Verfügbare Modell-IDs über die API anzeigen.")
    selection = p.add_mutually_exclusive_group()
    selection.add_argument("--pages", help="1-basierte PDF-Seiten, z.B. 22,31,46,90-91.")
    selection.add_argument("--pilot", action="store_true", help="Für die bereitgestellte Bode-PDF: Seiten 22,31,46,91.")
    p.add_argument("--output", help="Neuer Ausgabeordner; relativ zu parsing_tests, sonst absolut.")
    p.add_argument("--resume", action="store_true", help="Vorhandenen --output mit identischen Eingaben fortsetzen.")
    p.add_argument("--model", help="Überschreibt BLABLADOR_MODEL.")
    p.add_argument("--rpm", type=int, help="Überschreibt BLABLADOR_MAX_REQUESTS_PER_MINUTE.")
    p.add_argument("--workers", type=int, default=4, help="1–8 parallele Hauptabschnitte; alle teilen dasselbe RPM-Limit.")
    p.add_argument("--thinking-mode", choices=["auto", "on", "off"], help="Qwen/vLLM: Thinking serverseitig vorgeben; auto lässt den Server entscheiden.")
    p.add_argument("--max-output-tokens", type=int, help="Überschreibt BLABLADOR_MAX_OUTPUT_TOKENS.")
    p.add_argument("--timeout", type=float, help="HTTP-Wartezeit in Sekunden; Erhöhungen sind bei --resume mit unveränderten Extraktionsvorgaben möglich.")
    p.add_argument("--chunk-chars", type=int, default=4000, help="Weiche Zielgröße; vollständigen Absatz/Tabellenzeile bis zum Ende übernehmen.")
    p.add_argument("--max-chunks", type=int, help="Optional nur erste N Abschnitte; als Teillauf dokumentiert.")
    p.add_argument("--max-context-calls", type=int, default=10, help="Maximale zusätzliche Kontextabfragen je Ontologie.")
    p.add_argument("--source-url", help="Bekannte ursprüngliche PDF-URL, ohne automatischen Download.")
    p.add_argument("--retrieved-at", help="Bekannte Abrufzeit als ISO-8601-Zeitstempel mit Zeitzone.")
    p.set_defaults(rpm=30, timeout=600, max_output_tokens=16000, thinking_mode="off")
    return p


def local_path(value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else BASE_DIR / path).resolve()


def model_identity(snapshot: dict, model: str) -> dict:
    models = snapshot.get("data")
    if not isinstance(models, list):
        raise ValueError("Die Modellabfrage lieferte keine data-Liste.")
    selected = next((m for m in models if isinstance(m, dict) and m.get("id") == model), None)
    if selected is None:
        raise ValueError("Konfiguriertes Modell nicht verfügbar. --list-models aufrufen und BLABLADOR_MODEL anpassen.")
    return {k: selected.get(k) for k in ["id", "root", "owned_by", "max_model_len"]}


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    load_env(local_path(args.env_file))
    settings = Settings.from_env()
    if args.model:
        settings.model = args.model
    if args.rpm is not None:
        settings.rpm = args.rpm
    if args.thinking_mode is not None:
        settings.thinking_mode = args.thinking_mode
    if args.max_output_tokens is not None:
        settings.max_output_tokens = args.max_output_tokens
    if args.timeout is not None:
        settings.timeout = args.timeout
    settings.validate(require_key=not args.dry_run)
    if not 1 <= args.workers <= 8:
        raise ValueError("--workers muss zwischen 1 und 8 liegen.")
    if args.max_chunks is not None and args.max_chunks < 1:
        raise ValueError("--max-chunks muss positiv sein.")
    if args.max_context_calls < 0:
        raise ValueError("--max-context-calls darf nicht negativ sein.")
    if args.resume and not args.output:
        raise ValueError("--resume benötigt --output mit dem bisherigen Laufordner.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    kind = "models" if args.list_models else "dry_run" if args.dry_run else "pilot" if args.pilot else "run"
    output = local_path(args.output) if args.output else BASE_DIR / "results" / f"{kind}_{stamp}"
    if output.exists() and any(output.iterdir()) and not args.resume:
        raise ValueError("Ausgabeordner enthält bereits Dateien. --resume mit gleichen Einstellungen oder neuen Ordner wählen.")
    output.mkdir(parents=True, exist_ok=True)
    run_id = output.name
    with RunLock(BASE_DIR / "results" / ".parser.lock"):
        if args.list_models:
            client = BlabladorClient(settings, output / "logs", BASE_DIR / "results" / ".api_attempts.jsonl", run_id)
            try:
                snapshot = client.models()
                write_json(output / "models.json", snapshot)
                for model in snapshot.get("data", []):
                    print(f"{model.get('id')} | Modellbasis: {model.get('root', 'nicht angegeben')} | Kontext: {model.get('max_model_len', 'nicht angegeben')}")
                print(f"Modellabfrage gespeichert: {output}")
                return 0
            finally:
                client.close()
        return run_experiment(args, settings, output, run_id)


def parallel_chunks(chunks, work, workers, cancel):
    """Bound queued work; drain in-flight checkpoint writes before closing the client."""
    remaining = iter(chunks)
    pending = {}
    error = None
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="parsing") as pool:
        def submit():
            chunk = next(remaining, None)
            if chunk is not None:
                pending[pool.submit(work, chunk)] = chunk
        try:
            for _ in range(workers):
                submit()
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                completed = []
                for future in done:
                    chunk = pending.pop(future)
                    try:
                        completed.append((chunk, future.result()))
                    except BaseException as exc:
                        error = error or exc
                        cancel()
                for item in completed:
                    yield item
                if error is None:
                    for _ in done:
                        submit()
            if error is not None:
                raise error
        finally:
            cancel_on_exit = bool(pending)
            if cancel_on_exit:
                cancel()
                for future in pending:
                    future.cancel()


def run_experiment(args, settings: Settings, output: Path, run_id: str, expected_model_identity=None) -> int:
    pdf = local_path(args.pdf)
    variants = ["fein"]
    if args.pilot and pdf.name != "GEK_Obere_Bode_Endbericht-Textteil.pdf":
        raise ValueError("--pilot ist für die bereitgestellte Bode-PDF definiert. Für andere PDFs --pages verwenden.")
    page_spec = "22,31,46,91" if args.pilot else args.pages
    files = {"pdf": pdf, "event_headers": BASE_DIR / "Event_headers.xlsx",
             "relationship_headers": BASE_DIR / "relationship_headers.xlsx",
             **{name: BASE_DIR / f"ontologie_{name}.json" for name in ["fein"]}}
    inputs = {name: {"path": str(path), "sha256": file_hash(path)} for name, path in files.items()}
    code_hashes = {p.name: file_hash(p) for p in sorted(BASE_DIR.glob("*.py"))}
    dependencies = {name: importlib.metadata.version(name) for name in ["pdfplumber", "pypdf", "pydantic", "openpyxl"]}
    config = {"settings": settings.public(), "variants": variants, "pages": page_spec,
              "workers": args.workers,
              "chunk_chars": args.chunk_chars, "max_chunks": args.max_chunks,
              "max_context_calls": args.max_context_calls, "source_url": args.source_url, "retrieved_at": args.retrieved_at}
    if args.retrieved_at:
        try:
            parsed = datetime.fromisoformat(args.retrieved_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
        except ValueError:
            raise ValueError("--retrieved-at muss ein ISO-Zeitstempel mit Zeitzone sein.") from None
    signature = digest({"inputs": inputs, "code": code_hashes, "dependencies": dependencies, "config": config})
    manifest_path = output / "manifest.json"
    if args.resume:
        if not manifest_path.exists():
            raise ValueError("Kein manifest.json im angegebenen Fortsetzungsordner.")
        manifest = read_json(manifest_path)
        check_resume_compatibility(manifest, inputs, code_hashes, dependencies, config, signature)
        signature = manifest["input_signature"]
    else:
        manifest = {"run_id": run_id, "created_at": utc_now(), "input_signature": signature,
                    "inputs": inputs, "config": config, "code_hashes": code_hashes, "dependencies": dependencies}
    manifest.update(status="preparing", updated_at=utc_now())
    write_json(manifest_path, manifest)
    headers = {"events": read_headers(files["event_headers"], Event),
               "relationships": read_headers(files["relationship_headers"], Relationship)}
    ontologies = {name: read_json(files[name]) for name in ["fein"]}
    from schemas import extraction_schema
    write_json(output / "extraction_schema.json", extraction_schema(ontologies['fein']))
    prepared = prepare_document(pdf, output / "prepared", page_spec, args.chunk_chars)
    document = {**prepared["document"], "url": args.source_url, "retrieved_at": args.retrieved_at}
    chunks = prepared["chunks"][:args.max_chunks] if args.max_chunks else prepared["chunks"]
    cover = [b for b in prepared["blocks"] if b["page"] == 1]
    coverage = {**prepared["coverage"], "scheduled_chunks": len(chunks),
                "not_scheduled_chunks": [c["chunk_id"] for c in prepared["chunks"][len(chunks):]]}
    write_json(output / "coverage.json", coverage)
    estimates = {v: max((estimated_tokens(build_messages(c, cover, ontologies[v], headers)) for c in chunks), default=0) for v in variants}
    manifest.update(coverage=coverage, max_estimated_prompt_tokens=estimates,
                    planned_base_extraction_calls=len(chunks)*len(variants), updated_at=utc_now())
    if args.dry_run:
        manifest["status"] = "dry_run"
        write_json(manifest_path, manifest)
        print(f"Lokaler Probelauf abgeschlossen: {len(chunks)} Abschnitte, {len(variants)} Ontologien.")
        print(f"Basisbedarf: {len(chunks)*len(variants)} Extraktionsaufrufe, zuzüglich Modellprüfung, Kontext und Reparaturen.")
        print(f"Größter Prompt, geschätzte Tokens: {estimates}. Antwortbudget: {settings.max_output_tokens}.")
        print(f"Ergebnisse: {output}")
        return 0
    client = BlabladorClient(settings, output / "logs", BASE_DIR / "results" / ".api_attempts.jsonl", run_id)
    results = {}
    current_packages = []
    try:
        snapshot = client.models()
        identity = model_identity(snapshot, settings.model)
        if expected_model_identity is not None and identity != expected_model_identity:
            raise ValueError("Modellbasis hat sich innerhalb des PDF-Batches geändert; keine Vermischung erlaubt.")
        if manifest.get("model_identity") and manifest["model_identity"] != identity:
            raise ValueError("Modellstand hat sich seit dem letzten Lauf geändert. Neuen Vergleichslauf beginnen.")
        manifest.update(model_identity=identity, status="running")
        write_json(output / "models.json", snapshot)
        client.configure_models(snapshot)
        client.observed_model = manifest.get("response_model")
        client.accepted_model_labels.update(manifest.get("accepted_response_model_labels", []))
        if manifest.get("response_format"):
            client.format_mode = manifest["response_format"]
        advertised = identity.get("max_model_len")
        if isinstance(advertised, int) and advertised > 0:
            settings.context_tokens = min(settings.context_tokens, advertised)
        run_signature = digest({"inputs": signature, "model_identity": identity})
        manifest["run_signature"] = run_signature
        write_json(manifest_path, manifest)
        for variant in variants:
            current_packages, visited, extra_review = [], [], []
            primary_packages = {}
            destination = output / variant
            try:
                consecutive_failures = 0
                def work(chunk):
                    print(f"{variant}: Abschnitt {chunk['chunk_id']} (PDF-Seiten {chunk['pages']}).", flush=True)
                    return extract_chunk(client, chunk, cover, ontologies[variant], headers,
                                         destination, run_signature, variant)

                with closing(parallel_chunks(chunks, work, args.workers, client.cancel)) as completed:
                    for chunk, packages in completed:
                        primary_packages[chunk["chunk_id"]] = packages
                        current_packages[:] = [p for c in chunks for p in primary_packages.get(c["chunk_id"], [])]
                        visited.append(chunk["chunk_id"])
                        consecutive_failures = consecutive_failures+1 if all(p["status"] == "failed" for p in packages) else 0
                        manifest.update(current_variant=variant, completed_primary_chunks=len(visited),
                                        completed_primary_chunk_ids=list(visited), workers=args.workers,
                                        response_model=client.observed_model, response_format=client.format_mode, updated_at=utc_now())
                        write_json(manifest_path, manifest)
                        if consecutive_failures >= 3:
                            client.cancel()
                            raise RuntimeError("Drei Abschnitte in Folge fehlgeschlagen. Checkpoints behalten; Konfiguration/Logs prüfen.")
                jobs, extra_review = context_jobs(current_packages, prepared["blocks"], args.max_context_calls)
                for job in jobs:
                    print(f"{variant}: ergänzende Kontextabfrage {job['chunk']['chunk_id']}.", flush=True)
                    context_packages = extract_chunk(client, job["chunk"], cover, ontologies[variant], headers,
                                                     destination, run_signature, variant, job["existing_events"], purpose="context_resolution")
                    current_packages.extend(context_packages)
                    for package in context_packages:
                        if package["status"] == "complete":
                            extra_review.extend({"kind": "unresolved_context", "chunk_id": package["chunk_id"],
                                                 "reason": "second_context_pass_not_automatic", "request": r}
                                                for r in package["result"]["context_requests"])
            finally:
                pending = [c["chunk_id"] for c in chunks if c["chunk_id"] not in visited]
                extra_review.extend({"kind": "pending_chunk", "chunk_id": c} for c in pending)
                events, relationships, review, summary = finalize(current_packages, ontologies[variant], document,
                                                                 run_signature, variant, extra_review)
                summary.update(pending_chunks=pending, scheduled_primary_chunks=len(chunks),
                               visited_primary_chunks=len(visited), completed_at=utc_now())
                export_variant(destination, events, relationships, review, summary, headers)
                results[variant] = {"events": events, "relationships": relationships, "summary": summary}
        manifest["status"] = "complete_with_failures" if any(r["summary"]["failed_chunks"] for r in results.values()) else "complete"
    except BaseException:
        manifest["status"] = "interrupted_or_failed"
        raise
    finally:
        client.close()
        api_stats = minute_report(client.log_path, client.minute_path, client.clock.now())
        manifest.update(updated_at=utc_now(), response_model=client.observed_model,
                        response_format=client.format_mode, api_stats=api_stats,
                        requested_model=client.request_model, model_root=client.expected_model_root,
                        accepted_response_model_labels=sorted(client.accepted_model_labels))
        write_json(manifest_path, manifest)
        if results:
            compare(output, results, ontologies["fein"], coverage, api_stats)
    print(f"Lauf abgeschlossen ({manifest['status']}): {output}")
    print("Events, Beziehungen, Prüflisten und API-Minutenstatistik sind gespeichert.")
    return 2 if manifest["status"] == "complete_with_failures" else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Abgebrochen. Erfolgreiche Abschnitte sind als Checkpoints gespeichert.", file=sys.stderr)
        raise SystemExit(130)
    except (ValueError, RuntimeError, OSError) as error:
        print(f"Fehler: {error}", file=sys.stderr)
        raise SystemExit(1)
