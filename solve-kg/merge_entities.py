#!/usr/bin/env python3
"""Merge-Schicht / Entity-Resolution (Experiment — Release unberuehrt, kein API).

Fuehrt die lokal extrahierten Events/Relationships EINES ODER MEHRERER Laeufe (PDFs)
zu EINEM Graphen mit kanonischen Knoten zusammen:
  1. je Quelle werden event_ids mit einem Quell-Praefix global eindeutig gemacht
     (sonst kollidieren chunk_0001:e1 aus zwei PDFs); Relationship-Enden mit umgeschrieben,
  2. exakte Event-Dubletten (gleiche normalisierte Aussage) verschmelzen, Kanten umhaengen,
  3. je Gewaesser EINEN Knoten bilden -- quell-uebergreifend! -> gemeinsame Gewaesser
     (z. B. "Bode") werden zum Cross-PDF-Verbindungspunkt; Events per 'concerns' anhaengen,
  4. semantische Near-Duplicate-Namen NICHT automatisch mergen, nur als Pruef-Vorschlag.

Aufruf:
  .venv/bin/python merge_entities.py results/compact_OB [results/compact_UB ...] \
       --labels "Obere Bode,Untere Bode" --graph-out out.js [--map-pdf Karte.pdf]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from common import BASE_DIR


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().casefold()).strip(" .,;:")


def load(p: Path) -> list[dict]:
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def first_quote(x: dict) -> str:
    for e in (x.get("evidence") or []):
        if e.get("quote"):
            return e["quote"][:200]
    return ""


def map_positions(pdf: Path, names: set[str]) -> dict[str, list[float]]:
    """Label-Positionen (normalisiert) aus einer Vektor-PDF-Karte je Gewaessername."""
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextContainer, LTTextLine
    except Exception:
        return {}
    labels = []
    for page in extract_pages(str(pdf)):
        W, H = page.width, page.height
        for el in page:
            if isinstance(el, LTTextContainer):
                for line in el:
                    if isinstance(line, LTTextLine):
                        t = line.get_text().strip()
                        if t:
                            labels.append((t, (line.x0 + line.x1) / 2 / W, 1 - (line.y0 + line.y1) / 2 / H))
    out = {}
    for nm in names:
        nn = norm(nm)
        pos = None
        for t, nx, ny in labels:
            if norm(t) == nn:
                pos = [round(nx, 4), round(ny, 4)]; break
        if pos is None:
            for t, nx, ny in labels:
                if len(nn) >= 5 and nn in norm(t):
                    pos = [round(nx, 4), round(ny, 4)]; break
        if pos:
            out[nm] = pos
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Merge-Schicht / Entity-Resolution (ohne API).")
    ap.add_argument("folders", nargs="+", help="Ein oder mehrere Lauf-Ordner.")
    ap.add_argument("--labels", default=None, help="Komma-getrennte Quell-Namen, je Ordner einer.")
    ap.add_argument("--graph-out", default=None)
    ap.add_argument("--map-pdf", default=None, help="Vektor-PDF-Karte fuer Gewaesser-Positionen.")
    args = ap.parse_args()

    folders = [Path(f) if Path(f).is_absolute() else BASE_DIR / f for f in args.folders]
    labels = (args.labels.split(",") if args.labels else [f.name for f in folders])
    labels = [l.strip() for l in labels]
    tags = [re.sub(r"[^A-Za-z]", "", l)[:3].upper() or f"S{i}" for i, l in enumerate(labels)]

    all_ev, all_rel = [], []
    for folder, label, tag in zip(folders, labels, tags):
        ev = load(folder / "expanded_events.jsonl")
        rel = load(folder / "expanded_relationships.jsonl")
        for e in ev:                       # quell-eindeutige IDs + Quell-Tag
            e["event_id"] = f"{tag}:{e['event_id']}"
            e["_src"] = label
            all_ev.append(e)
        for r in rel:
            r["source_event_id"] = f"{tag}:{r['source_event_id']}"
            r["target_event_id"] = f"{tag}:{r['target_event_id']}"
            r["_src"] = label
            all_rel.append(r)

    # 1) Event-Dubletten verschmelzen (gleiche normalisierte statement, quell-uebergreifend)
    canon, seen, kept = {}, {}, []
    for e in all_ev:
        sig = (norm(e.get("statement")), e["_src"])   # innerhalb derselben Quelle dedupen
        if sig[0] and sig in seen:
            canon[e["event_id"]] = seen[sig]
        else:
            seen[sig] = e["event_id"]; canon[e["event_id"]] = e["event_id"]; kept.append(e)
    dup_events = len(all_ev) - len(kept)

    # 2) Gewaesser-Knoten (quell-uebergreifend) + concerns
    wb_nodes, concerns = {}, []
    for e in kept:
        for w in (e.get("waterbody_references") or []):
            nm = (w.get("name_original") or "").strip()
            if not nm:
                continue
            wbid = "wb:" + norm(nm)
            node = wb_nodes.setdefault(wbid, {"id": wbid, "name": nm, "events": 0, "srcs": {}})
            node["events"] += 1
            node["srcs"][e["_src"]] = node["srcs"].get(e["_src"], 0) + 1
            concerns.append({"from": e["event_id"], "to": wbid})

    # 3) Relationships auf kanonische IDs
    out_rel = []
    for r in all_rel:
        rr = dict(r)
        rr["source_event_id"] = canon.get(r["source_event_id"], r["source_event_id"])
        rr["target_event_id"] = canon.get(r["target_event_id"], r["target_event_id"])
        out_rel.append(rr)

    # 4) Positionen aus der Karte (optional)
    positions = {}
    if args.map_pdf:
        positions = map_positions(Path(args.map_pdf), {n["name"] for n in wb_nodes.values()})

    # ----- graph_data fuer die Ansicht -----
    byid = {e["event_id"]: e for e in kept}
    used_ev = {r["source_event_id"] for r in out_rel} | {r["target_event_id"] for r in out_rel}
    gnodes = []
    for e in kept:
        if e["event_id"] in used_ev:
            gnodes.append({"id": e["event_id"], "kind": "event", "rt": e.get("record_type", ""),
                           "label": (e.get("statement") or "")[:55], "full": e.get("statement") or "",
                           "quote": first_quote(e), "time": e.get("time_original") or "",
                           "ts": e.get("time_status") or "", "src": e["_src"]})
    wb_used = {c["to"] for c in concerns if c["from"] in used_ev}
    shared = 0
    for n in wb_nodes.values():
        if n["id"] in wb_used:
            node = {"id": n["id"], "kind": "waterbody", "label": n["name"], "full": n["name"],
                    "events": n["events"], "srcs": n["srcs"]}
            if len(n["srcs"]) > 1:
                shared += 1
            if n["name"] in positions:
                node["pos"] = positions[n["name"]]
            gnodes.append(node)
    gedges = [{"from": r["source_event_id"], "to": r["target_event_id"], "kind": "causal",
               "type": r.get("relationship_type", ""),
               "loop": r["source_event_id"] == r["target_event_id"],
               "stmt": (r.get("statement") or "")[:160], "mech": r.get("mechanism") or "",
               "src": r["_src"]} for r in out_rel]
    gedges += [{"from": c["from"], "to": c["to"], "kind": "concerns", "type": "betrifft",
                "loop": False, "stmt": "", "mech": ""} for c in concerns if c["from"] in used_ev]
    gdata = {"nodes": gnodes, "edges": gedges, "sources": labels,
             "stats": {"events_kept": len(kept), "dup_events": dup_events,
                       "waterbodies": len(wb_nodes), "shared_wb": shared,
                       "causal": len(out_rel), "concerns": len([c for c in concerns if c["from"] in used_ev]),
                       "located": sum(1 for n in gnodes if n.get("pos"))}}
    gpath = Path(args.graph_out) if args.graph_out else (folders[0] / "merged_graph_data.js")
    gpath.write_text("window.GRAPH=" + json.dumps(gdata, ensure_ascii=False) + ";", encoding="utf-8")

    # ----- Report -----
    print("==== MERGE-SCHICHT (Quellen: " + ", ".join(labels) + ") ====")
    print(f"Events gesamt -> kanonisch: {len(all_ev)} -> {len(kept)}  ({dup_events} Dubletten)")
    print(f"Gewaesser-Knoten: {len(wb_nodes)}  | davon QUELLUEBERGREIFEND (Cross-PDF): {shared}")
    print(f"Kausalkanten: {len(out_rel)}  | 'betrifft': {len(concerns)}  | Positionen: {gdata['stats']['located']}")
    print("\nGeteilte Gewaesser (Events je Quelle):")
    for n in sorted(wb_nodes.values(), key=lambda x: -x["events"]):
        if len(n["srcs"]) > 1:
            print(f"  {n['name']:30} " + " | ".join(f"{k}: {v}" for k, v in n["srcs"].items()))
    print(f"\nGeschrieben: {gpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
