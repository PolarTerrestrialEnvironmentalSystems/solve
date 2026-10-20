#!/usr/bin/env python3
"""Hydrologische Topologie aus der Gewässerkennzahl (GKZ / LAWA-Schema).

Warum: Zwei PDFs aus *benachbarten* Regionen teilen oft **keinen** gleichen
Gewässernamen. Die reine Namens-Verschmelzung (merge_entities.py) verbindet sie
dann nicht. Die amtliche **Gewässerkennzahl** (Feld ``GEW_KZ`` in DRAINBASIN)
kodiert aber die Fließtopologie hierarchisch:

    5        Elbe            (Flussgebietseinheit)
    56       Saale
    568      Bode            (Hauptlauf)
    5684     Selke           (mündet in die Bode)
    56832    Rappbode        (mündet in die Bode)
    5676     Wipper          (mündet in die Saale -> Nachbar der Bode)

=> Präfix-Verschachtelung = up-/downstream, gemeinsames Präfix = gemeinsames
Einzugsgebiet. Damit kann ein Nachbar-PDF **räumlich** andocken, auch wenn es
andere Wasserkörper nennt (die Selke in PlanungseinheitSAL20 hängt über GKZ
``5684`` an der Bode ``568``, ohne dass "Bode" im Selke-PDF stehen muss).

Dieses Modul ist API- und geometriefrei: nur die GKZ-Strings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from dbf import read_dbf

# Oberste LAWA-Ebene (1. Ziffer) = Flussgebietseinheit – nur für lesbare Labels.
BASIN_DISTRICT = {
    "1": "Donau", "2": "Rhein", "3": "Ems", "4": "Weser",
    "5": "Elbe", "6": "Oder", "9": "Küste",
}


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().casefold()).strip(" .,;:")


def gkz_core(code: str) -> str:
    """Nur die Ziffern einer Gewässerkennzahl (z. B. '568' aus '568' oder '5681.3')."""
    return re.sub(r"\D", "", code or "")


def wb_core(eu_cd: str) -> str:
    """Kern eines Wasserkörper-Codes: 'DERW_DEST_SAL17OW01-00' -> 'SAL17OW01-00'."""
    return (eu_cd or "").split("_")[-1].strip()


@dataclass
class Relation:
    """Hydrologische Beziehung zweier Gewässer, aus ihren GKZ abgeleitet."""
    relation: str           # same_system | drains_into | receives | shares_catchment | distant | unknown
    common: str             # gemeinsames GKZ-Präfix
    common_label: str       # lesbarer Name dieses Präfixes (z. B. "Saale", "Bode")
    distance: int           # Summe der nicht geteilten GKZ-Ziffern (0 = identisch)
    directed: str = ""      # für drains_into/receives: 'a->b' bzw. 'a<-b'

    @property
    def connected(self) -> bool:
        return self.relation in {"same_system", "drains_into", "receives", "shares_catchment"}


class Topology:
    """GKZ-Index über DRAINBASIN: Name/Code -> GKZ und die Beziehungsrechnung."""

    def __init__(self, min_shared: int = 2):
        # min_shared: ab wie vielen gemeinsamen GKZ-Ziffern wir von einem echten
        # gemeinsamen Einzugsgebiet sprechen (2 = gleiches Flusssystem wie Saale '56';
        # 1 = nur gleiche Flussgebietseinheit, das ist uns zu schwach -> 'distant').
        self.min_shared = min_shared
        self.by_name: dict[str, str] = {}     # norm(name) -> kürzeste GKZ
        self.by_code: dict[str, str] = {}      # wb_core(EU_CD_WB) -> kürzeste GKZ
        self.name_of_gkz: dict[str, str] = {}  # GKZ -> repräsentativer Gewässername
        self.code_name: dict[str, str] = {}    # wb_core -> Name

    # ---------- Aufbau ----------
    @classmethod
    def from_drainbasin(cls, dbf_path: str | Path, **kw) -> "Topology":
        self = cls(**kw)
        for row in read_dbf(dbf_path):
            gkz = gkz_core(row.get("GEW_KZ") or row.get("GEB_KZ") or "")
            name = (row.get("DRAIN_NA") or row.get("B_NAME") or "").strip()
            code = wb_core(row.get("EU_CD_WB") or "")
            if not gkz:
                continue
            if name:
                k = norm(name)
                # kürzeste GKZ je Name behalten (= Hauptlauf, nicht ein Zufluss-Segment)
                if k not in self.by_name or len(gkz) < len(self.by_name[k]):
                    self.by_name[k] = gkz
                if gkz not in self.name_of_gkz or len(name) < len(self.name_of_gkz[gkz]):
                    self.name_of_gkz[gkz] = name
            if code:
                if code not in self.by_code or len(gkz) < len(self.by_code[code]):
                    self.by_code[code] = gkz
                if name and code not in self.code_name:
                    self.code_name[code] = name
        return self

    # ---------- Auflösung ----------
    def gkz_for_code(self, eu_cd: str) -> str | None:
        return self.by_code.get(wb_core(eu_cd))

    def gkz_for_name(self, name: str) -> str | None:
        k = norm(name)
        if k in self.by_name:
            return self.by_name[k]
        # Teilnamen-Fallback (>=5 Zeichen), damit "Warme Bode" nicht an "Bode" scheitert
        for nm, gkz in self.by_name.items():
            if len(k) >= 5 and (k in nm or nm in k):
                return gkz
        return None

    def label(self, gkz: str) -> str:
        if gkz in self.name_of_gkz:
            return self.name_of_gkz[gkz]
        if len(gkz) == 1:
            return BASIN_DISTRICT.get(gkz, f"FGE {gkz}")
        return f"GKZ {gkz}"

    # ---------- Beziehung ----------
    def relate(self, gkz_a: str, gkz_b: str) -> Relation:
        """Hydrologische Beziehung zweier GKZ. a, b sind Ziffern-Strings."""
        a, b = gkz_core(gkz_a), gkz_core(gkz_b)
        if not a or not b:
            return Relation("unknown", "", "", -1)
        if a == b:
            return Relation("same_system", a, self.label(a), 0)
        if b.startswith(a):           # a ist kürzer -> a nimmt b auf (b liegt oberhalb)
            return Relation("receives", a, self.label(a), len(b) - len(a), directed="a<-b")
        if a.startswith(b):           # a mündet in b (a liegt oberhalb)
            return Relation("drains_into", b, self.label(b), len(a) - len(b), directed="a->b")
        # gemeinsames Präfix bestimmen
        lcp = []
        for ca, cb in zip(a, b):
            if ca == cb:
                lcp.append(ca)
            else:
                break
        common = "".join(lcp)
        dist = (len(a) - len(common)) + (len(b) - len(common))
        if len(common) >= self.min_shared:
            return Relation("shares_catchment", common, self.label(common), dist)
        return Relation("distant", common, self.label(common) if common else "", dist)

    def relate_codes(self, code_a: str, code_b: str) -> Relation | None:
        ga, gb = self.gkz_for_code(code_a), self.gkz_for_code(code_b)
        if ga is None or gb is None:
            return None
        return self.relate(ga, gb)

    def relate_names(self, name_a: str, name_b: str) -> Relation | None:
        ga, gb = self.gkz_for_name(name_a), self.gkz_for_name(name_b)
        if ga is None or gb is None:
            return None
        return self.relate(ga, gb)


if __name__ == "__main__":  # Mini-Demo gegen DRAINBASIN
    import sys
    dbf_path = sys.argv[1] if len(sys.argv) > 1 else \
        "WRRL/current/DRAINBASIN_DEST/DRAINBASIN_DEST.dbf"
    topo = Topology.from_drainbasin(dbf_path)
    print(f"geladen: {len(topo.by_name)} Namen, {len(topo.by_code)} Wasserkörper-Codes")
    pairs = [("Selke", "Bode"), ("Rappbode", "Bode"), ("Holtemme", "Bode"),
             ("Wipper", "Bode"), ("Aller", "Bode")]
    for a, b in pairs:
        r = topo.relate_names(a, b)
        if r:
            print(f"  {a:10} ({topo.gkz_for_name(a):>6}) ~ {b:6} ({topo.gkz_for_name(b):>4}): "
                  f"{r.relation:16} via {r.common_label:8} dist={r.distance}")
