#!/usr/bin/env python3
"""No-API-Analyse: Wie viel 'Ballast' steckt in den Bloecken (vor dem Chunking)?

Kopf-/Fusszeilen entfernt prepare_document schon selbst. Dieses Skript schaetzt,
wie viel ZUSAETZLICH wegfiele, wenn wir vor dem Chunking noch

  - Inhaltsverzeichnis (Punktfuehrung ... Seitenzahl)
  - Abbildungs-/Tabellenverzeichnis
  - Literatur-/Quellenverzeichnis
  - Bildunterschriften (Abb./Tab. N: ...)

herausnehmen. Es MISST nur und ruft nichts auf. Tabellenzeilen werden NIE
gefiltert (echte Daten). Am Ende: wie viele Zeichen/Bloecke gespart und wie
viele Abschnitte dann noch zu schicken waeren -- plus Stichproben, damit du
das Recall-Risiko selbst beurteilen kannst.

Aufruf:  .venv/bin/python filter_blocks.py
         .venv/bin/python filter_blocks.py --pdf input/ANDERE.pdf --show 8
"""
from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

from common import BASE_DIR, read_json
from prepare_document import make_chunks, prepare_document

LEADER = re.compile(r"\.{4,}\s*\d{1,4}\s*$")                 # "Kapitel ........ 42"
CAPTION = re.compile(r"^(Abb\.?|Abbildung|Tab\.?|Tabelle|Foto|Karte|Fig\.?)\s*\d", re.I)
YEAR = re.compile(r"\(\s*\d{4}[a-z]?\s*\)")                   # "(1998)" in Literatur
BIB_MARK = re.compile(r"\b(Hrsg|Verlag|In:|S\.\s*\d+\s*[–-]\s*\d+|ISBN)\b")


def classify(block: dict) -> str:
    if block.get("kind") == "table_row":
        return "content"                                     # Tabellen bleiben immer
    text = block.get("text", "")
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return "noise"
    first = lines[0].strip()

    # Inhaltsverzeichnis / Verzeichnisse: viele Punktfuehrungs-Zeilen
    leader_lines = sum(bool(LEADER.search(l)) for l in lines)
    if leader_lines >= 2 or (leader_lines and leader_lines / len(lines) >= 0.4):
        return "toc"

    # Bildunterschrift: kurze Abb./Tab.-Zeile
    if CAPTION.match(first) and len(text) < 300:
        return "caption"

    # Literatur-/Quellenverzeichnis: hohe Dichte an Jahreszahlen in Klammern
    years = sum(bool(YEAR.search(l)) for l in lines)
    if (years >= 3 and years / len(lines) >= 0.3) or (years >= 2 and BIB_MARK.search(text)):
        return "bibliography"

    if len(text.strip()) < 15:
        return "noise"
    return "content"


def main() -> int:
    ap = argparse.ArgumentParser(description="Ballast-Bloecke schaetzen (ohne API).")
    ap.add_argument("--pdf", default="input/GEK_Obere_Bode_Endbericht-Textteil.pdf")
    ap.add_argument("--chunk-chars", type=int, default=10000)
    ap.add_argument("--show", type=int, default=5, help="Stichproben je Ballast-Kategorie.")
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser()
    if not pdf.is_absolute():
        pdf = BASE_DIR / pdf
    prepared = prepare_document(pdf, BASE_DIR / "results" / "_filter_probe", None, args.chunk_chars)
    blocks = prepared["blocks"]

    cats: dict[str, list[dict]] = {}
    for b in blocks:
        cats.setdefault(classify(b), []).append(b)

    total_chars = sum(len(b.get("text", "")) for b in blocks)
    print(f"Bloecke gesamt: {len(blocks)}  |  Zeichen gesamt: {total_chars:,}\n")
    print(f"{'Kategorie':<14}{'Bloecke':>9}{'Zeichen':>12}{'Anteil':>9}")
    for cat in ["content", "toc", "bibliography", "caption", "noise"]:
        bl = cats.get(cat, [])
        ch = sum(len(b.get("text", "")) for b in bl)
        print(f"{cat:<14}{len(bl):>9}{ch:>12,}{100*ch/max(total_chars,1):>8.1f}%")

    dropped = {"toc", "bibliography", "caption", "noise"}
    content = [b for b in blocks if classify(b) not in dropped]
    saved_chars = total_chars - sum(len(b.get("text", "")) for b in content)

    # Neu chunken, nur Content (gleiche Logik wie im Parser)
    sel = {b["page"] for b in blocks}
    old_chunks = make_chunks([b for b in blocks if b["page"] in sel], args.chunk_chars)
    new_chunks = make_chunks([b for b in content if b["page"] in sel], args.chunk_chars)
    print(f"\nZeichen-Einsparung (Prompt+Output-Basis): {saved_chars:,} "
          f"({100*saved_chars/max(total_chars,1):.1f}%)")
    print(f"Abschnitte: {len(old_chunks)} -> {len(new_chunks)}  "
          f"(= {len(old_chunks)-len(new_chunks)} weniger LLM-Aufrufe)")

    print("\n--- Stichproben der WEGGEFILTERTEN Bloecke (pruefe Recall!) ---")
    for cat in ["toc", "bibliography", "caption", "noise"]:
        bl = cats.get(cat, [])
        if not bl:
            continue
        print(f"\n[{cat}]  ({len(bl)} Bloecke)")
        for b in bl[:args.show]:
            snippet = " ".join(b.get("text", "").split())[:140]
            print(f"  S.{b['page']:>3} {b['block_id']}: {snippet}")
    print("\nHinweis: Das sind Heuristiken. Pruefe die Stichproben, bevor im echten")
    print("Lauf gefiltert wird -- v.a. 'bibliography' kann Fliesstext mit vielen")
    print("Jahreszahlen falsch treffen, 'noise' echte Kurzangaben.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
