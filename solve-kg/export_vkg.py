#!/usr/bin/env python3
"""VKG-Export (Experiment): bringt den PDF-Extraktions-Output ins DB-/Ontop-taugliche
Format, damit er als zusaetzliche .obda-Mappings an das Ontop-VKG andocken kann.

Erzeugt im Ordner vkg_export/:
  - wb_name_to_code.csv : Gewaessername -> amtlicher Code (eu_cd_lw), automatisch aus der
                          Uebersichtskarte per raeumlicher Naehe; status=auto/review/none.
  - pdf_waterbody.csv   : kanonische Gewaesser (Name, eu_cd_lw, #Events, Quellen, Kartenpos)
  - pdf_event.csv       : Ereignisse (event_id, eu_cd_lw, record_type, statement, time, Quelle, Beleg)
  - pdf_relationship.csv: Ursache->Wirkung (ids, typ, mechanism, statement, Quelle)

Kein API-Aufruf. Aufruf:
  .venv/bin/python export_vkg.py results/compact_OB results/compact_UB \
      --labels "Obere Bode,Untere Bode" --map-pdf /pfad/Anlage_1_UEbersichtskarte.pdf
"""
from __future__ import annotations

import argparse, csv, json, math, re
from collections import Counter
from pathlib import Path
from common import BASE_DIR


def norm(s): return re.sub(r"\s+", " ", (s or "").strip().casefold()).strip(" .,;:")
def load(p): return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
CODE = re.compile(r"^[A-Z]{2,4}\d{0,2}OW\d{2}-\d{2}$")   # z. B. SAL17OW22-00


def read_dbf(path):
    """Minimaler DBF-Leser (nur Attribute), UTF-8 mit latin1-Fallback."""
    import struct
    with open(path, "rb") as f:
        hdr = f.read(32); nrec = struct.unpack("<I", hdr[4:8])[0]
        hlen = struct.unpack("<H", hdr[8:10])[0]; reclen = struct.unpack("<H", hdr[10:12])[0]
        fields = []
        while True:
            fd = f.read(32)
            if not fd or fd[0:1] == b"\r" or len(fd) < 32: break
            fields.append((fd[0:11].split(b"\x00")[0].decode("latin1"), fd[16]))
        f.seek(hlen); rows = []
        for _ in range(nrec):
            rec = f.read(reclen)
            if not rec or rec[0:1] == b"*": continue
            vals = {}; off = 1
            for nm, ln in fields:
                raw = rec[off:off + ln]; off += ln
                try: vals[nm] = raw.decode("utf-8").strip()
                except Exception: vals[nm] = raw.decode("latin1").strip()
            rows.append(vals)
        return rows


def drainbasin_name2code(path: Path, prefer: str) -> dict[str, str]:
    """Amtliche Zuordnung Gewaessername -> EU_CD_WB aus DRAINBASIN; bei Mehrdeutigkeit
    Codes mit 'prefer' (z. B. Planungseinheit SAL17) bevorzugen, sonst haeufigsten."""
    from collections import Counter
    cand: dict[str, Counter] = {}
    for r in read_dbf(str(path)):
        nm = (r.get("DRAIN_NA") or r.get("B_NAME") or "").strip()
        wb = (r.get("EU_CD_WB") or "").strip()
        if nm and wb:
            cand.setdefault(norm(nm), Counter())[wb] += 1
    out = {}
    for k, c in cand.items():
        pref = [code for code in c if prefer and prefer in code]
        out[k] = (Counter({code: c[code] for code in pref}).most_common(1)[0][0]
                  if pref else c.most_common(1)[0][0])
    return out


def map_features(pdf: Path):
    """Liefert (codes, names): je Liste (text, x, y) normalisiert."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTTextContainer, LTTextLine
    codes, names = [], []
    for page in extract_pages(str(pdf)):
        W, H = page.width, page.height
        for el in page:
            if isinstance(el, LTTextContainer):
                for line in el:
                    if isinstance(line, LTTextLine):
                        t = line.get_text().strip()
                        if not t:
                            continue
                        x, y = (line.x0 + line.x1) / 2 / W, 1 - (line.y0 + line.y1) / 2 / H
                        if CODE.match(t.replace(" ", "")):
                            codes.append((t.replace(" ", ""), x, y))
                        elif re.search(r"[A-Za-zÄÖÜäöü]{4,}", t) and not t[0].isdigit():
                            names.append((t, x, y))
    return codes, names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--labels", default=None)
    ap.add_argument("--map-pdf", default=None)
    ap.add_argument("--max-dist", type=float, default=0.05, help="Max. Normdistanz Code<->Name (Karte).")
    ap.add_argument("--drainbasin", default=None, help="DRAINBASIN .dbf fuer amtliche Name->Code-Zuordnung.")
    ap.add_argument("--prefer", default="SAL17", help="Code-Teilstring bevorzugen (Planungseinheit Bode).")
    args = ap.parse_args()

    folders = [Path(f) if Path(f).is_absolute() else BASE_DIR / f for f in args.folders]
    labels = [l.strip() for l in (args.labels.split(",") if args.labels else [f.name for f in folders])]
    tags = [re.sub(r"[^A-Za-z]", "", l)[:3].upper() or f"S{i}" for i, l in enumerate(labels)]

    # ---- Events/Relationships einlesen, quell-eindeutige IDs ----
    events, rels = [], []
    for folder, label, tag in zip(folders, labels, tags):
        for e in load(folder / "expanded_events.jsonl"):
            e["event_id"] = f"{tag}:{e['event_id']}"; e["_src"] = label; events.append(e)
        for r in load(folder / "expanded_relationships.jsonl"):
            r["source_event_id"] = f"{tag}:{r['source_event_id']}"
            r["target_event_id"] = f"{tag}:{r['target_event_id']}"; r["_src"] = label; rels.append(r)

    def wbname(e):
        ns = [w.get("name_original") for w in (e.get("waterbody_references") or []) if w.get("name_original")]
        return ns[0].strip() if ns else ""

    # ---- Name -> Code: AMTLICH aus DRAINBASIN (primaer) + Karte (Position + Fallback) ----
    name2code, name2pos, code_source = {}, {}, {}
    for k, code in (drainbasin_name2code(Path(args.drainbasin), args.prefer) if args.drainbasin else {}).items():
        name2code[k] = code; code_source[k] = "drainbasin"
    code_assign = []
    if args.map_pdf:
        codes, names = map_features(Path(args.map_pdf))
        for nm_text, nx, ny in names:
            name2pos.setdefault(norm(nm_text), (round(nx, 4), round(ny, 4)))
        for code, cx, cy in codes:
            best, bd = None, 1e9
            for nm_text, nx, ny in names:
                d = math.hypot(cx - nx, cy - ny)
                if d < bd:
                    bd, best = d, nm_text
            status = "auto" if bd <= args.max_dist else ("review" if bd <= 2 * args.max_dist else "none")
            code_assign.append((code, best, round(bd, 3), status))
            if status == "auto":
                k = norm(best)
                if k not in name2code:            # nur wo DRAINBASIN nichts liefert
                    name2code[k] = code; code_source[k] = "karte"

    out = BASE_DIR / "vkg_export"; out.mkdir(exist_ok=True)

    # ---- kanonische Gewaesser ----
    wb = {}
    for e in events:
        nm = wbname(e)
        if not nm:
            continue
        k = norm(nm)
        node = wb.setdefault(k, {"name": nm, "events": 0, "srcs": set(),
                                 "code": name2code.get(k, ""), "src": code_source.get(k, "")})
        node["events"] += 1; node["srcs"].add(e["_src"])

    # ---- wb_name_to_code.csv : unsere Gewaesser -> amtlicher Code + Herkunft (Pruef-Tabelle) ----
    with open(out / "wb_name_to_code.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["wb_name", "eu_cd_lw", "code_source", "n_events"])
        for k, n in sorted(wb.items(), key=lambda x: (x[1]["code"] == "", -x[1]["events"])):
            w.writerow([n["name"], n["code"], n["src"], n["events"]])

    # ---- pdf_waterbody.csv ----
    with open(out / "pdf_waterbody.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["wb_name", "eu_cd_lw", "code_source", "n_events", "sources", "map_x", "map_y"])
        for k, n in sorted(wb.items(), key=lambda x: -x[1]["events"]):
            pos = name2pos.get(k, ("", ""))
            w.writerow([n["name"], n["code"], n["src"], n["events"], "|".join(sorted(n["srcs"])), pos[0], pos[1]])

    # ---- pdf_event.csv ----
    with open(out / "pdf_event.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["event_id", "eu_cd_lw", "record_type", "statement", "ontology_concept",
                    "time_original", "source_document", "quote"])
        for e in events:
            k = norm(wbname(e))
            q = ""
            for ev in (e.get("evidence") or []):
                if ev.get("quote"): q = ev["quote"]; break
            w.writerow([e["event_id"], name2code.get(k, ""), e.get("record_type", ""),
                        (e.get("statement") or "").replace("\n", " "), e.get("ontology_concept", ""),
                        e.get("time_original", ""), e["_src"], q.replace("\n", " ")])

    # ---- pdf_relationship.csv ----
    with open(out / "pdf_relationship.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["relationship_id", "source_event_id", "target_event_id", "relationship_type",
                    "statement", "mechanism", "source_document"])
        for i, r in enumerate(rels):
            w.writerow([f"{r['_src'][:2].upper()}-rel-{i}", r["source_event_id"], r["target_event_id"],
                        r.get("relationship_type", ""), (r.get("statement") or "").replace("\n", " "),
                        (r.get("mechanism") or "").replace("\n", " "), r["_src"]])

    coded = [n for n in wb.values() if n["code"]]
    by_src = Counter(n["src"] for n in coded)
    print("==== VKG-EXPORT ====")
    print(f"Events: {len(events)} | Relationships: {len(rels)} | kanonische Gewaesser: {len(wb)}")
    print(f"Gewaesser mit AMTLICHEM Code: {len(coded)}/{len(wb)}  "
          f"(DRAINBASIN {by_src.get('drainbasin', 0)}, Karte {by_src.get('karte', 0)})")
    print(f"\nGeschrieben nach: {out}/")
    for f in ["wb_name_to_code.csv", "pdf_waterbody.csv", "pdf_event.csv", "pdf_relationship.csv"]:
        print("  -", f)
    print("\nZugeordnete Gewaesser (Name -> Code [Quelle], nach #Events):")
    for n in sorted(coded, key=lambda x: -x["events"]):
        print(f"  {n['name']:28} -> {n['code']:24} [{n['src']}]  ({n['events']} Ev.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
