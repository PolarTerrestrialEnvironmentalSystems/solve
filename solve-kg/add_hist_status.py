#!/usr/bin/env python3
"""Haengt die amtliche Fließgewaesser-Guete (Gewaesserbericht 2009-2013) an die
Gewaesser-Knoten: oekologischer + chemischer Zustand je Wasserkoerper (owk_st).

Quelle: Gewaesserbericht_2009_2013/333 (oekol., flaechenhaft) + 320 (chem.).
Match: owk_st == Kurzcode unserer Wasserkoerper (SAL17OW..-00).
Patcht graph_data.js (Feld status2013 an waterbody-Knoten) und schreibt
vkg_export/pdf_wb_status_2013.csv. Kein API-Aufruf.
"""
from __future__ import annotations
import csv, json, sys
from pathlib import Path
from common import BASE_DIR

GB = BASE_DIR / "Gewaesserbericht_2009_2013"
ECO = {1: "sehr gut", 2: "gut", 3: "mäßig", 4: "unbefriedigend", 5: "schlecht"}


def props_by_owk(path):
    d = json.load(open(path, encoding="utf-8"))
    out = {}
    for f in d.get("features", []):
        p = f.get("properties", {})
        k = (p.get("owk_st") or "").strip()
        if k:
            out[k] = p
    return out


def main():
    graph_js = sys.argv[1] if len(sys.argv) > 1 else None
    eco = props_by_owk(GB / "333_owk_bewertung_okol_zustand_potential_flachenhafte_darstellung.geojson")
    chem = props_by_owk(GB / "320_owk_bewertung_chem_zustand_ohne_hg_in_biota_flachendarstellung.geojson")

    status = {}
    for k, p in eco.items():
        try: e = int(p.get("eco_stat")) if p.get("eco_stat") not in (None, "") else None
        except (TypeError, ValueError): e = None
        c = chem.get(k, {})
        def s(x): return str(x if x is not None else "").strip()
        status[k] = {
            "wb_name": s(p.get("wb_name")),
            "eco_num": e, "eco_txt": ECO.get(e, ""),
            "chem_ohg": s(c.get("c_stat_ohg_t") or p.get("c_stat_ohg_t")),
            "ausweisung": s(p.get("ausweisung")),
            "eco_stoffe": s(p.get("eco_stoffe")),
        }

    out = BASE_DIR / "vkg_export" / "pdf_wb_status_2013.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["owk_st", "wb_name", "eco_class", "eco_text", "chem_ohne_hg", "ausweisung"])
        for k in sorted(status):
            s = status[k]
            w.writerow([k, s["wb_name"], s["eco_num"] or "", s["eco_txt"], s["chem_ohg"], s["ausweisung"]])
    print(f"Fließgewaesser-Guete 2009-2013: {len(status)} Wasserkoerper -> {out}")

    if graph_js:
        import re
        p = Path(graph_js)
        data = json.loads(p.read_text()[len("window.GRAPH="):].rstrip().rstrip(";"))
        patched = 0
        for n in data["nodes"]:
            if n.get("kind") == "waterbody" and n.get("code"):
                m = re.search(r"([A-Z]{2,4}\d{0,2}OW\d{2}-\d{2})", n["code"])
                sc = m.group(1) if m else ""
                if sc in status and status[sc]["eco_num"]:
                    n["status2013"] = status[sc]
                    patched += 1
        p.write_text("window.GRAPH=" + json.dumps(data, ensure_ascii=False) + ";", encoding="utf-8")
        print(f"graph_data.js gepatcht: {patched} Knoten mit Guete 2009-2013.")
        # kleine Verteilung
        from collections import Counter
        dist = Counter(status[sc]["eco_txt"] for n in data["nodes"]
                       if n.get("kind") == "waterbody" and n.get("code")
                       for sc in [re.search(r"([A-Z]{2,4}\d{0,2}OW\d{2}-\d{2})", n["code"]).group(1)
                                  if re.search(r"([A-Z]{2,4}\d{0,2}OW\d{2}-\d{2})", n["code"]) else ""]
                       if sc in status and status[sc]["eco_num"])
        print("Ökol. Verteilung (unsere Knoten):", dict(dist))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
