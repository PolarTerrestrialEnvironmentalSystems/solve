#!/usr/bin/env python3
"""Cross-Region-Verknüpfung: verbindet Befunde aus mehreren (benachbarten) PDFs
**räumlich** über die Fließtopologie und schlägt **dokumentübergreifende
Ursache→Wirkung-Kanten** entlang der Fließrichtung vor.

Ausgangslage: ``merge_entities.py`` verbindet zwei PDFs nur dort, wo sie denselben
Gewässer*namen* nennen. Ein Nachbar-PDF (andere Wasserkörper, aber hydrologisch
up-/downstream) bleibt unverbunden. Dieses Skript schließt die Lücke mit
``topology.py`` (Gewässerkennzahl):

  1. SPATIAL  – für jedes Wasserkörper-Paar die hydrologische Beziehung
     (same_system / drains_into / shares_catchment …); Paare aus *verschiedenen*
     Dokumenten werden als ``cross_document`` markiert.
  2. CAUSAL   – wo ein Oberlieger-Wasserkörper (Dok A) einen Unterlieger (Dok B)
     speist, werden *Vorschläge* für Ursache→Wirkung gebildet: ursächliche
     Befunde oben (Maßnahme/Prozess/Ereignis) → Zustands-/Messbefunde unten.

WICHTIG – Ehrlichkeit: Die kausalen Kanten hier sind **topologisch abgeleitete
Kandidaten** ("confidence=inferred"), KEINE aus dem Text belegten Aussagen. Sie
sind zum Sichten gedacht, nicht als Fakt. Die textbelegten Kausalkanten stehen
weiterhin ausschließlich in ``pdf_relationship.csv``.

Kein API-Aufruf. Beispiel:
  python region_link.py \
      --events vkg_export/pdf_event.csv \
      --waterbodies vkg_export/pdf_waterbody.csv \
      --drainbasin WRRL/current/DRAINBASIN_DEST/DRAINBASIN_DEST.dbf \
      --out vkg_export/
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from topology import Topology, wb_core

# Welche Befundtypen propagieren flussabwärts (Ursache) bzw. werden unten beobachtet (Wirkung)?
CAUSE_TYPES = {"Maßnahme", "Prozess", "Ereignis"}
EFFECT_TYPES = {"Zustand", "Messung", "Eigenschaft"}


def load_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def link(events: list[dict], wbs: list[dict], topo: Topology,
         max_dist: int = 4, per_pair: int = 3) -> tuple[list[dict], list[dict], dict]:
    """Reine Verknüpfungslogik (ohne Datei-I/O), damit sie testbar ist.

    events: dicts mit event_id, eu_cd_lw, record_type, statement, source_document
    wbs:    dicts mit eu_cd_lw, wb_name, sources ('|'-getrennt)
    Rückgabe: (spatial_edges, causal_candidates, summary).
    """
    # ---- Wasserkörper mit Code + GKZ auflösen ----
    wb_rows = []  # (code, name, sources_set, gkz)
    for w in wbs:
        code = (w.get("eu_cd_lw") or "").strip()
        if not code:
            continue
        gkz = topo.gkz_for_code(code) or topo.gkz_for_name(w.get("wb_name", ""))
        if not gkz:
            continue
        srcs = set((w.get("sources") or "").split("|")) - {""}
        wb_rows.append((code, w.get("wb_name", ""), srcs, gkz))

    # ---- 1) SPATIAL: alle Paare, nur hydrologisch verbundene behalten ----
    spatial = []
    for (ca, na, sa, ga), (cb, nb, sb, gb) in combinations(wb_rows, 2):
        rel = topo.relate(ga, gb)
        if not rel.connected:
            continue
        cross = sa != sb  # Paar stammt aus verschiedenen Dokument-Mengen
        spatial.append({
            "wb_a_code": wb_core(ca), "wb_a_name": na,
            "wb_b_code": wb_core(cb), "wb_b_name": nb,
            "relation": rel.relation, "direction": rel.directed,
            "via": rel.common_label, "gkz_a": ga, "gkz_b": gb,
            "distance": rel.distance,
            "cross_document": "ja" if cross else "nein",
        })

    # ---- Events je Code gruppieren ----
    ev_by_code = defaultdict(list)
    for e in events:
        code = (e.get("eu_cd_lw") or "").strip()
        if code:
            ev_by_code[wb_core(code)].append(e)

    # ---- 2) CAUSAL: Oberlieger(Dok A) -> Unterlieger(Dok B), verschiedene Dokumente ----
    candidates = []
    for (ca, na, sa, ga), (cb, nb, sb, gb) in combinations(wb_rows, 2):
        rel = topo.relate(ga, gb)
        if rel.relation not in {"drains_into", "receives"} or rel.distance > max_dist:
            continue
        # Fließrichtung: Oberlieger = das Gewässer mit längerer GKZ
        if len(ga) >= len(gb):
            up_code, up_name, down_code, down_name = wb_core(ca), na, wb_core(cb), nb
            up_srcs, down_srcs = sa, sb
        else:
            up_code, up_name, down_code, down_name = wb_core(cb), nb, wb_core(ca), na
            up_srcs, down_srcs = sb, sa
        river = rel.common_label
        if up_srcs == down_srcs:   # beide aus demselben Dokument -> kein Cross-Region-Fall
            continue
        causes = [e for e in ev_by_code.get(up_code, []) if e.get("record_type") in CAUSE_TYPES][:per_pair]
        effects = [e for e in ev_by_code.get(down_code, []) if e.get("record_type") in EFFECT_TYPES][:per_pair]
        for ce in causes:
            for fe in effects:
                candidates.append({
                    "cause_event_id": ce["event_id"], "cause_doc": ce.get("source_document", ""),
                    "cause_wb": f"{up_name} ({up_code})",
                    "effect_event_id": fe["event_id"], "effect_doc": fe.get("source_document", ""),
                    "effect_wb": f"{down_name} ({down_code})",
                    "via_river": river, "hydro_distance": rel.distance,
                    "mechanism": f"flussabwärtiger Transport über {river}",
                    "confidence": "inferred",
                    "cause_statement": (ce.get("statement") or "")[:160],
                    "effect_statement": (fe.get("statement") or "")[:160],
                })

    cross_spatial = [s for s in spatial if s["cross_document"] == "ja"]
    summary = {
        "waterbodies_resolved": len(wb_rows),
        "spatial_edges": len(spatial),
        "spatial_edges_cross_document": len(cross_spatial),
        "relations": {r: sum(1 for s in spatial if s["relation"] == r)
                      for r in {s["relation"] for s in spatial}},
        "causal_candidates": len(candidates),
        "causal_candidate_pairs": len({(c["cause_wb"], c["effect_wb"]) for c in candidates}),
    }
    return spatial, candidates, summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Cross-Region-Verknüpfung (räumlich + kausal, ohne API).")
    ap.add_argument("--events", required=True, help="pdf_event.csv")
    ap.add_argument("--waterbodies", required=True, help="pdf_waterbody.csv")
    ap.add_argument("--drainbasin", required=True, help="DRAINBASIN .dbf (GKZ-Topologie)")
    ap.add_argument("--out", default="vkg_export", help="Ausgabeordner")
    ap.add_argument("--max-dist", type=int, default=4,
                    help="max. hydrologische Distanz (GKZ-Ziffern) für Kandidaten")
    ap.add_argument("--per-pair", type=int, default=3,
                    help="max. Ursache- bzw. Wirkungs-Befunde je Wasserkörper-Paar")
    args = ap.parse_args()

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    topo = Topology.from_drainbasin(args.drainbasin)

    events = load_csv(Path(args.events))
    wbs = load_csv(Path(args.waterbodies))

    spatial, candidates, summary = link(events, wbs, topo,
                                        max_dist=args.max_dist, per_pair=args.per_pair)

    with open(out / "spatial_edges.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(spatial[0].keys()) if spatial else
                           ["wb_a_code", "wb_a_name", "wb_b_code", "wb_b_name", "relation",
                            "direction", "via", "gkz_a", "gkz_b", "distance", "cross_document"])
        w.writeheader(); w.writerows(spatial)

    with open(out / "cross_doc_causal_candidates.csv", "w", newline="", encoding="utf-8") as fh:
        cols = ["cause_event_id", "cause_doc", "cause_wb", "effect_event_id", "effect_doc",
                "effect_wb", "via_river", "hydro_distance", "mechanism", "confidence",
                "cause_statement", "effect_statement"]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader(); w.writerows(candidates)

    cross_spatial = [s for s in spatial if s["cross_document"] == "ja"]
    (out / "region_link_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("==== CROSS-REGION-VERKNÜPFUNG ====")
    print(f"aufgelöste Wasserkörper (mit GKZ): {summary['waterbodies_resolved']}")
    print(f"räumliche Kanten: {len(spatial)}  | davon DOKUMENTÜBERGREIFEND: {len(cross_spatial)}")
    for r, n in sorted(summary["relations"].items(), key=lambda x: -x[1]):
        print(f"    {r:18} {n}")
    print(f"kausale Kandidaten (inferred): {len(candidates)}  "
          f"über {summary['causal_candidate_pairs']} Gewässer-Paare")
    print(f"\ngeschrieben nach {out}/:  spatial_edges.csv, "
          f"cross_doc_causal_candidates.csv, region_link_summary.json")
    if cross_spatial:
        print("\nBeispiele dokumentübergreifender räumlicher Kanten:")
        for s in cross_spatial[:8]:
            print(f"  {s['wb_a_name'][:20]:20} ~{s['relation']:14}~ {s['wb_b_name'][:20]:20} via {s['via']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
