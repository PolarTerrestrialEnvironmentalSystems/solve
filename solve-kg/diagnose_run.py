#!/usr/bin/env python3
"""Diagnose: Was dauert beim ECHTEN Parsen pro Abschnitt wie lange?

Dieses Skript geht den exakt gleichen Weg wie der echte Lauf (Blablador-LLM),
aber nur fuer GENAU EINEN Abschnitt ("chunk"). Daraus rechnet es hoch, wie
lange die ganze PDF brauchen wuerde -- ohne 1,5 h zu verbrennen.

Was passiert beim echten Lauf (und was hier gemessen wird):
  1. PDF aufbereiten      -> Text/Tabellen pro Seite, in "chunks" zerlegen   (lokal, schnell)
  2. Modell-Liste holen   -> ein GET an Blablador                             (schnell)
  3. Je chunk EIN LLM-Aufruf (client.complete -> POST chat/completions)      <-- der teure Teil
       ggf. + Reparatur (JSON kaputt) + Split (zu lang) -> mehr Aufrufe
Nur Schritt 3 skaliert mit Seiten/Textmenge. Das messen wir an einem chunk.

Kosten: EIN bezahlter LLM-Aufruf (plus evtl. Reparatur/Split) + eine Modell-Liste.

Aufruf (aus dem Paketordner, mit dessen venv):
  .venv/bin/python diagnose_run.py                         # Bode-PDF, mittlerer Abschnitt
  .venv/bin/python diagnose_run.py --chunk-index 0         # erster Abschnitt
  .venv/bin/python diagnose_run.py --page 31               # nur Seite 31, deren 1. Abschnitt
  .venv/bin/python diagnose_run.py --pdf input/MEINE.pdf --page 12
"""
from __future__ import annotations

import argparse
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from blablador_client import BlabladorClient, Settings
from common import BASE_DIR, digest, file_hash, load_env, read_json, read_jsonl
from extract_facts import build_messages, estimated_tokens, extract_chunk
from prepare_document import prepare_document
from run_parsing_tests import model_identity, parallel_chunks
from schemas import Event, Extraction, Relationship, read_headers


def human(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.1f} s"
    return f"{seconds/60:.1f} min"


def main() -> int:
    ap = argparse.ArgumentParser(description="Zeitdiagnose fuer EINEN Abschnitt des echten Parsers.")
    ap.add_argument("--pdf", default="input/GEK_Obere_Bode_Endbericht-Textteil.pdf",
                    help="PDF relativ zum Paket oder absolut.")
    ap.add_argument("--page", help="Optional nur diese PDF-Seite(n) aufbereiten, z.B. 31 oder 22,31.")
    ap.add_argument("--chunk-index", type=int, default=None,
                    help="Ab welchem vorbereiteten Abschnitt messen (0=erster). Standard: mittig (repraesentativ).")
    ap.add_argument("--chunks", type=int, default=1,
                    help="Wie viele Abschnitte echt laufen lassen (Standard 1). >1 misst, ob der Server parallel arbeitet.")
    ap.add_argument("--chunk-chars", type=int, default=10000, help="Wie im echten Lauf (Standard 10000).")
    ap.add_argument("--rpm", type=int, default=10, help="Nur fuer die Hochrechnung relevant (Standard 10).")
    ap.add_argument("--workers", type=int, default=4, help="Nur fuer die Hochrechnung (Standard 4).")
    ap.add_argument("--max-output-tokens", type=int, default=16000, help="Wie im echten Lauf (Standard 16000).")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--thinking-mode", choices=["off", "on", "auto"], default="off")
    ap.add_argument("--model", help="Ueberschreibt BLABLADOR_MODEL.")
    args = ap.parse_args()

    # --- Umgebung + Einstellungen wie im echten Lauf ---
    load_env(BASE_DIR / ".env")
    settings = Settings.from_env()
    settings.rpm = args.rpm
    settings.timeout = args.timeout
    settings.max_output_tokens = args.max_output_tokens
    settings.thinking_mode = args.thinking_mode
    if args.model:
        settings.model = args.model
    settings.validate(require_key=True)

    pdf = (Path(args.pdf).expanduser() if Path(args.pdf).is_absolute() else BASE_DIR / args.pdf).resolve()
    if not pdf.is_file():
        raise SystemExit(f"PDF nicht gefunden: {pdf}")

    headers = {"events": read_headers(BASE_DIR / "Event_headers.xlsx", Event),
               "relationships": read_headers(BASE_DIR / "relationship_headers.xlsx", Relationship)}
    ontology = read_json(BASE_DIR / "ontologie_fein.json")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    out = BASE_DIR / "results" / f"diagnose_{stamp}"
    out.mkdir(parents=True, exist_ok=True)

    # --- Phase 1: PDF aufbereiten (lokal, kein API) ---
    print("== Phase 1: PDF aufbereiten (lokal) ==", flush=True)
    t0 = time.monotonic()
    prepared = prepare_document(pdf, out / "prepared", args.page, args.chunk_chars)
    t_prepare = time.monotonic() - t0
    chunks = prepared["chunks"]
    cover = [b for b in prepared["blocks"] if b["page"] == 1]
    total = len(chunks)
    if total == 0:
        raise SystemExit("Keine Abschnitte erzeugt (leere Auswahl?).")
    pages_prepared = len({p for b in prepared["blocks"] for p in [b["page"]]})
    print(f"  {pages_prepared} Seiten aufbereitet in {human(t_prepare)} -> {total} Abschnitte.")
    print(f"  (Das ist der schnelle Teil: {t_prepare/max(pages_prepared,1):.2f} s/Seite)")

    n = max(1, min(args.chunks, total))
    index = args.chunk_index if args.chunk_index is not None else max(0, (total - n) // 2)
    index = max(0, min(index, total - n))
    selected = chunks[index:index + n]
    est = max(estimated_tokens(build_messages(c, cover, ontology, headers)) for c in selected)
    print(f"\n  Messe {n} Abschnitt(e) ab #{index} (von {total}).")
    print(f"  Groesster geschaetzter Prompt: ~{est} Tokens, Antwortbudget: {settings.max_output_tokens}.")

    # --- Phase 2+3: Modell-Liste + echte LLM-Aufrufe ---
    print("\n== Phase 2: Modell-Liste holen (ein GET) ==", flush=True)
    client = BlabladorClient(settings, out / "logs", BASE_DIR / "results" / ".api_attempts.jsonl", out.name)
    all_packages = []
    try:
        snapshot = client.models()
        identity = model_identity(snapshot, settings.model)
        client.configure_models(snapshot)
        advertised = identity.get("max_model_len")
        if isinstance(advertised, int) and advertised > 0:
            settings.context_tokens = min(settings.context_tokens, advertised)
        run_signature = digest({"inputs": file_hash(pdf), "model_identity": identity})

        label = "EIN echter LLM-Aufruf" if n == 1 else f"{n} Abschnitte mit {args.workers} Threads (echt, parallel)"
        print(f"\n== Phase 3: {label} ==", flush=True)

        def work(chunk):
            print(f"  -> Abschnitt {chunk['chunk_id']} (PDF-Seiten {chunk['pages']}) gestartet.", flush=True)
            return extract_chunk(client, chunk, cover, ontology, headers,
                                 out / "fein", run_signature, "fein")

        t1 = time.monotonic()
        if n == 1:
            all_packages.extend(work(selected[0]))
        else:
            with closing(parallel_chunks(selected, work, args.workers, client.cancel)) as completed:
                for _chunk, packages in completed:
                    all_packages.extend(packages)
        t_wall = time.monotonic() - t1
    finally:
        client.close()

    # --- Auswertung aus dem Anfragelog (die echten Dauern) ---
    rows = read_jsonl(out / "logs" / "api_requests.jsonl", tolerate_partial_tail=True)
    starts = {r["request_id"]: r for r in rows if r.get("event") == "request_started"}
    ends = {r["request_id"]: r for r in rows if r.get("event") == "request_finished"}
    calls = []
    for rid, s in starts.items():
        e = ends.get(rid, {})
        usage = e.get("usage") or {}
        calls.append({
            "endpoint": s.get("endpoint"), "purpose": s.get("purpose"),
            "attempt": s.get("attempt"), "status": e.get("http_status"),
            "ms": e.get("duration_ms"), "format": s.get("response_format"),
            "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
            "started": s.get("started_at_utc"),
        })
    calls.sort(key=lambda c: c["started"] or "")

    print("\n== Ergebnis ==")
    print(f"PDF aufbereiten (lokal):       {human(t_prepare)}")
    posts = [c for c in calls if c["endpoint"] == "chat/completions"]
    gets = [c for c in calls if c["endpoint"] == "models"]
    for c in gets:
        print(f"Modell-Liste (GET):            {human((c['ms'] or 0)/1000)}  (Status {c['status']})")
    print(f"\nLLM-Aufrufe insgesamt: {len(posts)}  (fuer {n} Abschnitt(e))"
          + ("  <- enthaelt Reparatur/Split!" if len(posts) > n else ""))
    for i, c in enumerate(posts, 1):
        print(f"  Aufruf {i}: {human((c['ms'] or 0)/1000):>8}  "
              f"Status {c['status']}  "
              f"prompt={c['prompt_tokens']} completion={c['completion_tokens']}  "
              f"format={c['format']} purpose={c['purpose']}")
        if c["completion_tokens"] and settings.max_output_tokens and \
           c["completion_tokens"] >= settings.max_output_tokens * 0.98:
            print(f"     ! Antwort am Token-Limit ({settings.max_output_tokens}) -> wird gesplittet -> langsamer.")
    ok = all(p["status"] == "complete" for p in all_packages)
    print(f"\nWanduhr Phase 3:               {human(t_wall)}   Ergebnis: "
          + ("alles vollstaendig" if ok else "mit Problem/Split -> siehe fein/review_items.jsonl"))

    # --- Parallelitaet (nur sinnvoll bei mehreren Abschnitten) ---
    durations = [(c["ms"] or 0) / 1000 for c in posts]
    sum_d, max_d = sum(durations), max(durations, default=0.0)
    if n > 1:
        concurrency = sum_d / t_wall if t_wall > 0 else 0.0
        print("\n== Parallelitaet (die entscheidende Frage) ==")
        print(f"  Summe aller Einzelaufrufe:   {human(sum_d)}   (so lange waere es strikt SERIELL)")
        print(f"  Laengster Einzelaufruf:      {human(max_d)}   (so kurz waere es perfekt PARALLEL)")
        print(f"  Tatsaechliche Wanduhr:       {human(t_wall)}")
        print(f"  => effektiv ~{concurrency:.1f} gleichzeitig bedient (angefordert: {args.workers} Threads)")
        if concurrency >= args.workers * 0.7:
            print("  => Der Server arbeitet weitgehend PARALLEL. Threads bringen also wirklich Tempo.")
        elif concurrency <= 1.3:
            print("  => Der Server arbeitet praktisch SERIELL. Mehr Threads bringen kaum etwas.")
        else:
            print("  => Teilweise parallel (Server begrenzt die Gleichzeitigkeit).")

    # --- Hochrechnung aus dem GEMESSENEN Durchsatz ---
    rate_floor = total / max(1, args.rpm) * 60.0
    est_measured = total * t_wall / n          # real beobachteter Durchsatz hochskaliert
    est_full = max(est_measured, rate_floor)
    print("\n== Hochrechnung auf die ganze PDF ==")
    print(f"  Abschnitte gesamt:           {total}")
    print(f"  Gemessen:                    {n} Abschnitt(e) in {human(t_wall)}")
    print(f"  Hochgerechnet (gleicher Durchsatz): {human(est_measured)}")
    print(f"  Raten-Schranke ({args.rpm}/min):     {human(rate_floor)}")
    print(f"  => realistische Erwartung:   ~{human(est_full)}")
    print("  Hinweis: Kontextabfragen, Reparaturen/Splits und dichtere Seiten erhoehen das.")
    print(f"\nLogs/Rohantworten dieses Laufs: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
