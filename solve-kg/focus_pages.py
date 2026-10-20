#!/usr/bin/env python3
"""No-API-Analyse: Welche Seiten enthalten ueberhaupt Ontologie-Begriffe?

Idee: Statt die GANZE PDF ans LLM zu schicken, nur die Seiten, auf denen
Freshwater-/Ontologie-Vokabular vorkommt. Das senkt die Zahl der LLM-Aufrufe
(= der teure Teil). Dieses Skript MISST nur -- es ruft nichts auf und aendert
den Parser nicht. Es zeigt, wie viel wir sparen wuerden und welche Seiten
dabei herausfielen (Recall-Risiko).

Aufruf (aus dem Paketordner):
  .venv/bin/python focus_pages.py
  .venv/bin/python focus_pages.py --min-terms 2 --margin 1
  .venv/bin/python focus_pages.py --pdf input/MEINE.pdf
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from common import BASE_DIR, read_json
from prepare_document import prepare_document


def build_vocabulary(ontology: dict, min_len: int) -> list[str]:
    terms: set[str] = set()
    for t in ontology.get("topics", []):
        for s in t.get("search_terms", []) or []:
            terms.add(s)
        if t.get("label_de"):
            terms.add(t["label_de"])
    for c in ontology.get("classes", []):
        if c.get("label_de"):
            terms.add(c["label_de"])
    # normalisieren, zu kurze/rein numerische raus
    out = set()
    for term in terms:
        term = term.strip().casefold()
        if len(term) >= min_len and not term.isdigit():
            out.add(term)
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Seiten nach Ontologie-Begriffen bewerten (ohne API).")
    ap.add_argument("--pdf", default="input/GEK_Obere_Bode_Endbericht-Textteil.pdf")
    ap.add_argument("--chunk-chars", type=int, default=10000, help="Wie im echten Lauf (fuer die Abschnitts-Zaehlung).")
    ap.add_argument("--min-terms", type=int, default=1,
                    help="Ab wie vielen VERSCHIEDENEN Begriffen gilt eine Seite als relevant (Standard 1).")
    ap.add_argument("--margin", type=int, default=0,
                    help="Auch +-N Nachbarseiten um relevante Seiten behalten (Standard 0).")
    ap.add_argument("--min-len", type=int, default=4, help="Kuerzere Begriffe ignorieren (Rauschen).")
    args = ap.parse_args()

    pdf = Path(args.pdf).expanduser()
    if not pdf.is_absolute():
        pdf = BASE_DIR / pdf
    ontology = read_json(BASE_DIR / "ontologie_fein.json")
    vocab = build_vocabulary(ontology, args.min_len)
    print(f"Vokabular: {len(vocab)} Begriffe aus Themen-search_terms + Klassen-/Themen-Labels.\n")

    prepared = prepare_document(pdf, BASE_DIR / "results" / "_focus_probe", None, args.chunk_chars)
    blocks, chunks = prepared["blocks"], prepared["chunks"]
    pages = sorted({b["page"] for b in blocks})

    # Text je Seite
    page_text = {p: "" for p in pages}
    for b in blocks:
        page_text[b["page"]] += " " + (b.get("text") or "")

    # Treffer je Seite (verschiedene Begriffe + Gesamttreffer)
    page_terms: dict[int, set[str]] = {}
    page_hits: dict[int, int] = {}
    for p, text in page_text.items():
        low = text.casefold()
        found = {term for term in vocab if term in low}
        page_terms[p] = found
        page_hits[p] = sum(low.count(term) for term in found)

    relevant = {p for p in pages if len(page_terms[p]) >= args.min_terms}
    if args.margin:
        grown = set(relevant)
        for p in relevant:
            for d in range(1, args.margin + 1):
                grown.update({p - d, p + d})
        relevant = {p for p in grown if p in page_text}

    skipped = [p for p in pages if p not in relevant]

    # Verteilung: wie viele Seiten haben 0,1-2,3-5,6+ verschiedene Begriffe
    dist = Counter()
    for p in pages:
        n = len(page_terms[p])
        dist["0" if n == 0 else "1-2" if n <= 2 else "3-5" if n <= 5 else "6+"] += 1

    # Abschnitte, die wir noch schicken muessten (ein Abschnitt bleibt, wenn
    # IRGENDEINE seiner Seiten relevant ist)
    kept_chunks = [c for c in chunks if any(pg in relevant for pg in c["pages"])]

    print(f"Seiten gesamt: {len(pages)}")
    print(f"  Begriffe/Seite:  0 -> {dist['0']}   1-2 -> {dist['1-2']}   3-5 -> {dist['3-5']}   6+ -> {dist['6+']}")
    print(f"\nRelevant (>= {args.min_terms} Begriff(e), Margin +-{args.margin}): {len(relevant)} Seiten")
    print(f"Ueberspringbar:                                  {len(skipped)} Seiten")
    if skipped:
        rngs = []
        start = prev = skipped[0]
        for p in skipped[1:] + [None]:
            if p != (prev + 1 if prev is not None else None):
                rngs.append(f"{start}" if start == prev else f"{start}-{prev}")
                start = p
            prev = p
        print(f"  uebersprungene Seiten: {', '.join(rngs)}")

    print(f"\nAbschnitte (chunk-chars {args.chunk_chars}): {len(chunks)} gesamt")
    print(f"  davon noch zu schicken: {len(kept_chunks)}")
    saved = len(chunks) - len(kept_chunks)
    pct = 100 * saved / max(len(chunks), 1)
    print(f"  => Einsparung: {saved} Abschnitte ({pct:.0f}% weniger LLM-Aufrufe)")

    print("\nHinweis zum Recall-Risiko: Eine uebersprungene Seite kann Fakten")
    print("enthalten, die das Vokabular nicht woertlich nennt (Synonyme, nur")
    print("Zahlen/Tabellen). Pruefe die uebersprungenen Seiten oben stichprobenartig,")
    print("oder erhoehe --margin, bevor du Seiten im echten Lauf ausschliesst.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
