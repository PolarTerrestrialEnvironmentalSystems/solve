#!/usr/bin/env python3
"""Haengt ZEITLICHE WRRL-Information an unsere Wasserkoerper-Knoten.

Je Wasserkoerper (ueber eu_cd_lw aus vkg_export/wb_name_to_code.csv) wird ueber die
Berichtszyklus-Ordner (2009/2015/2017/2022/2024) ein Zeitprofil gebaut:
  - eco_stat / chem_stat : amtliche Zustandsbewertung (NUR Seen -> CALC_LWBODY_WK)
  - n_qe   : Anzahl ueberwachter oekologischer Qualitaetskomponenten (QE_ECO_SWSTN)
  - n_chem : Anzahl ueberwachter Schadstoffe (CHEM_MON)
  - n_stn  : Anzahl Messstellen (WFD_SWSTN)
(Fuer Fluesse gibt es in diesem Download keine WK-Zustandsnote -> n_qe/n_chem/n_stn
 sind der zeitliche Ueberwachungs-Proxy.)

Schreibt vkg_export/pdf_wb_temporal.csv und patcht eine graph_data.js (fuegt den
Knoten `code` + `temporal` hinzu). Kein API-Aufruf.
"""
from __future__ import annotations
import csv, glob, json, re, struct, sys
from collections import defaultdict
from pathlib import Path
from common import BASE_DIR

WRRL = BASE_DIR / "WRRL"
CYCLES = ["2009", "2015", "2017", "2022", "2024"]


def read_dbf(path, want):
    with open(path, "rb") as f:
        hdr = f.read(32); nrec = struct.unpack("<I", hdr[4:8])[0]
        hlen = struct.unpack("<H", hdr[8:10])[0]; reclen = struct.unpack("<H", hdr[10:12])[0]
        flds = []
        while True:
            fd = f.read(32)
            if not fd or fd[0:1] == b"\r" or len(fd) < 32: break
            flds.append((fd[0:11].split(b"\x00")[0].decode("latin1"), fd[16]))
        f.seek(hlen); rows = []
        for _ in range(nrec):
            rec = f.read(reclen)
            if not rec or rec[0:1] == b"*": continue
            vals = {}; off = 1
            for nm, ln in flds:
                raw = rec[off:off + ln]; off += ln
                if nm in want:
                    try: vals[nm] = raw.decode("utf-8").strip()
                    except Exception: vals[nm] = raw.decode("latin1").strip()
            rows.append(vals)
        return rows


def first(folder, table, want):
    g = glob.glob(f"{folder}/{table}/*.dbf")
    return read_dbf(g[0], want) if g else []


ECO = {"2": "sehr gut", "1": "sehr gut", "3": "mäßig", "4": "unbefriedigend", "5": "schlecht", "": ""}


def short(code):   # DERW_DEST_SAL17OW01-00 -> SAL17OW01-00
    m = re.search(r"([A-Z]{2,4}\d{0,2}OW\d{2}-\d{2})", code or "")
    return m.group(1) if m else code


def main():
    graph_js = sys.argv[1] if len(sys.argv) > 1 else None
    # unsere Gewaesser -> code
    rows = list(csv.DictReader(open(BASE_DIR / "vkg_export" / "wb_name_to_code.csv", encoding="utf-8")))
    wb = {r["wb_name"]: r["eu_cd_lw"] for r in rows if r["eu_cd_lw"]}
    codes = {short(c) for c in wb.values()}

    # pro Zyklus Kennzahlen je Kurzcode
    prof = defaultdict(lambda: defaultdict(dict))   # code -> year -> metrics
    for yr in CYCLES:
        folder = WRRL / yr
        if not folder.exists(): continue
        # Seen-Zustand
        for r in first(folder, "CALC_LWBODY_WK_DEST", {"EU_CD_LW", "ECO_STAT", "ECO_POT", "CHEM_STAT"}):
            sc = short(r.get("EU_CD_LW", ""))
            if sc in codes:
                prof[sc][yr]["eco"] = r.get("ECO_STAT", "") or r.get("ECO_POT", "")
                prof[sc][yr]["chem"] = r.get("CHEM_STAT", "")
        # oekologische Qualitaetskomponenten (Anzahl)
        qe = defaultdict(set)
        for r in first(folder, "QE_ECO_SWSTN_DEST", {"EU_CD_WB", "QE_CD"}):
            sc = short(r.get("EU_CD_WB", ""))
            if sc in codes: qe[sc].add(r.get("QE_CD", ""))
        for sc, s in qe.items(): prof[sc][yr]["n_qe"] = len(s)
        # Schadstoffe (Anzahl)
        ch = defaultdict(set)
        for r in first(folder, "CHEM_MON_DEST", {"EU_CD_WB", "SUBSTANCE"}):
            sc = short(r.get("EU_CD_WB", ""))
            if sc in codes: ch[sc].add(r.get("SUBSTANCE", ""))
        for sc, s in ch.items(): prof[sc][yr]["n_chem"] = len(s)
        # Messstellen (Anzahl)
        st = defaultdict(set)
        for r in first(folder, "WFD_SWSTN_DEST", {"EU_CD_WB", "NAME_STN"}):
            sc = short(r.get("EU_CD_WB", ""))
            if sc in codes: st[sc].add(r.get("NAME_STN", ""))
        for sc, s in st.items(): prof[sc][yr]["n_stn"] = len(s)

    # CSV
    out = BASE_DIR / "vkg_export" / "pdf_wb_temporal.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["eu_cd_lw_short", "cycle_year", "eco_stat", "chem_stat", "n_eco_qe", "n_chem_sub", "n_stations"])
        for sc in sorted(prof):
            for yr in CYCLES:
                m = prof[sc].get(yr)
                if m:
                    w.writerow([sc, yr, m.get("eco", ""), m.get("chem", ""), m.get("n_qe", ""), m.get("n_chem", ""), m.get("n_stn", "")])

    covered = len(prof)
    print(f"Wasserkoerper mit Zeitprofil: {covered}/{len(codes)}")
    print("Beispiele:")
    for sc in list(prof)[:8]:
        yrs = {yr: prof[sc][yr] for yr in CYCLES if yr in prof[sc]}
        print(f"  {sc}: {yrs}")
    print(f"\nGeschrieben: {out}")

    # graph_data.js patchen: code + temporal an Gewaesser-Knoten
    if graph_js:
        p = Path(graph_js)
        data = json.loads(p.read_text()[len("window.GRAPH="):].rstrip().rstrip(";"))
        name2code = {r["wb_name"]: r["eu_cd_lw"] for r in rows if r["eu_cd_lw"]}
        patched = 0
        for n in data["nodes"]:
            if n.get("kind") == "waterbody":
                code = name2code.get(n["label"], "")
                if code:
                    n["code"] = code
                    sc = short(code)
                    if sc in prof:
                        n["temporal"] = {yr: prof[sc][yr] for yr in CYCLES if yr in prof[sc]}
                        patched += 1
        p.write_text("window.GRAPH=" + json.dumps(data, ensure_ascii=False) + ";", encoding="utf-8")
        print(f"graph_data.js gepatcht: {patched} Gewaesser-Knoten mit Zeitprofil.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
