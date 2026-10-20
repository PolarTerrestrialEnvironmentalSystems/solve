#!/usr/bin/env python3
"""Baut aus den WRRL-Shapefiles eine echte Vektor-Karte (SVG-Geometrie als JS).

- Gewaessernetz der Planungseinheit SAL17 (Bode) aus DRAINBASIN (Polylinien),
  unsere zugeordneten Wasserkoerper hervorgehoben.
- Messstellen aus WFD_SWSTN (echte XCOORD/YCOORD).
Projektion: die gespeicherten Koordinaten sind bereits planar (UTM/GK) -> lineare
Skalierung in eine SVG-Box, Y gespiegelt. Kein API-Aufruf.

Schreibt window.MAPGEO nach <out.js>.
Aufruf: .venv/bin/python build_map_svg.py <out.js> [--prefer SAL17]
"""
from __future__ import annotations
import csv, glob, json, re, struct, sys
from pathlib import Path
from common import BASE_DIR

WRRL = BASE_DIR / "WRRL" / "current"


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
            if not rec: break
            if rec[0:1] == b"*":
                rows.append(None); continue
            vals = {}; off = 1
            for nm, ln in flds:
                raw = rec[off:off + ln]; off += ln
                if nm in want:
                    try: vals[nm] = raw.decode("utf-8").strip()
                    except Exception: vals[nm] = raw.decode("latin1").strip()
            rows.append(vals)
        return rows


def read_shp_polylines(path):
    data = open(path, "rb").read()
    pos, n, shapes = 100, len(data), []
    while pos + 8 <= n:
        _rn, clen = struct.unpack(">ii", data[pos:pos + 8]); pos += 8
        cstart = pos; pos += clen * 2
        stype = struct.unpack("<i", data[cstart:cstart + 4])[0]
        if stype == 0:
            shapes.append([]); continue
        p = cstart + 4 + 32   # skip type + bbox
        numParts, numPoints = struct.unpack("<ii", data[p:p + 8]); p += 8
        parts = struct.unpack("<%di" % numParts, data[p:p + 4 * numParts]); p += 4 * numParts
        pts = struct.unpack("<%dd" % (2 * numPoints), data[p:p + 16 * numPoints])
        coords = [(pts[2 * i], pts[2 * i + 1]) for i in range(numPoints)]
        shapes.append([coords[parts[i]:(parts[i + 1] if i + 1 < numParts else numPoints)]
                       for i in range(numParts)])
    return shapes


def short(c):
    m = re.search(r"([A-Z]{2,4}\d{0,2}OW\d{2}-\d{2})", c or ""); return m.group(1) if m else c


def main():
    out = Path(sys.argv[1])
    prefer = "SAL17"
    # unsere Codes
    rows = list(csv.DictReader(open(BASE_DIR / "vkg_export" / "wb_name_to_code.csv", encoding="utf-8")))
    ours = {short(r["eu_cd_lw"]): r["wb_name"] for r in rows if r["eu_cd_lw"]}

    # DRAINBASIN: Geometrie + Attribute (gleiche Reihenfolge)
    db_dbf = glob.glob(f"{WRRL}/DRAINBASIN_DEST/*.dbf")[0]
    db_shp = glob.glob(f"{WRRL}/DRAINBASIN_DEST/*.shp")[0]
    attrs = read_dbf(db_dbf, {"EU_CD_WB", "DRAIN_NA", "WA_CD", "PLANU_CD"})
    geoms = read_shp_polylines(db_shp)
    rivers = []
    allpts = []
    for a, g in zip(attrs, geoms):
        if a is None or not g: continue
        code = a.get("EU_CD_WB", "")
        wa = a.get("WA_CD", "") + a.get("PLANU_CD", "")
        sc = short(code)
        inplan = prefer in code or prefer in wa
        mine = sc in ours
        if not (inplan or mine): continue
        rivers.append({"sc": sc, "name": a.get("DRAIN_NA", ""), "ours": mine, "parts": g})
        for part in g: allpts.extend(part)

    # Messstellen
    st_dbf = glob.glob(f"{WRRL}/WFD_SWSTN_DEST/*.dbf")[0]
    strows = read_dbf(st_dbf, {"EU_CD_WB", "NAME_STN", "XCOORD", "YCOORD"})
    stations = []
    for r in strows:
        if not r: continue
        sc = short(r.get("EU_CD_WB", ""))
        try:
            x = float(r.get("XCOORD", "") or "nan"); y = float(r.get("YCOORD", "") or "nan")
        except ValueError:
            continue
        if x != x or y != y: continue
        if sc in ours or prefer in (r.get("EU_CD_WB", "")):
            stations.append({"name": r.get("NAME_STN", ""), "x": x, "y": y, "sc": sc, "ours": sc in ours})
            allpts.append((x, y))

    if not allpts:
        print("Keine Geometrie gefunden."); return 1
    xs = [p[0] for p in allpts]; ys = [p[1] for p in allpts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    W = 1600.0; pad = 20.0
    sx = (W - 2 * pad) / max(maxx - minx, 1)
    H = (maxy - miny) * sx + 2 * pad
    def tx(x): return round(pad + (x - minx) * sx, 1)
    def ty(y): return round(pad + (maxy - y) * sx, 1)   # Y spiegeln

    def to_d(parts):
        d = []
        for part in parts:
            if not part: continue
            d.append("M" + " L".join(f"{tx(x)} {ty(y)}" for x, y in part))
        return "".join(d)

    out_rivers = [{"name": r["name"], "sc": r["sc"], "ours": r["ours"], "d": to_d(r["parts"])}
                  for r in rivers]
    out_st = [{"name": s["name"], "sc": s["sc"], "ours": s["ours"], "x": tx(s["x"]), "y": ty(s["y"])}
              for s in stations]
    # ein Label je unserem Wasserkoerper (am laengsten Segment)
    labels = {}
    for r in rivers:
        if not r["ours"]: continue
        pts = [p for part in r["parts"] for p in part]
        if not pts: continue
        mid = pts[len(pts) // 2]
        labels.setdefault(r["sc"], {"name": ours.get(r["sc"], r["name"]), "x": tx(mid[0]), "y": ty(mid[1])})

    geo = {"w": round(W, 1), "h": round(H, 1), "rivers": out_rivers,
           "stations": out_st, "labels": list(labels.values())}
    out.write_text("window.MAPGEO=" + json.dumps(geo, ensure_ascii=False) + ";", encoding="utf-8")
    print(f"Flüsse (SAL17 + unsere): {len(out_rivers)} | davon unsere: {sum(1 for r in out_rivers if r['ours'])}")
    print(f"Messstellen: {len(out_st)} | Labels: {len(labels)} | viewBox {W:.0f}x{H:.0f}")
    print(f"Größe: {len(out.read_text())/1024:.0f} KB -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
