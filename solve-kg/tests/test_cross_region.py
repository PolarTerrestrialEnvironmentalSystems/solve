#!/usr/bin/env python3
"""Beweis-Test: Kann der KG ein PDF aus einer *benachbarten* Region kausal UND
räumlich an ein bestehendes (Bode-)Netz anbinden?

Kein API, keine großen Daten: wir bauen eine Mini-Topologie mit **echten**
Gewässerkennzahlen (aus DRAINBASIN verifiziert) und simulieren zwei Dokumente:
  - "Bode-Bericht"  -> Wasserkörper der unteren Bode (GKZ 568)
  - "Selke-Bericht" -> Nachbar-PDF, nennt nur die Selke (GKZ 5684, PlanungseinheitSAL20),
                       das Wort "Bode" kommt darin NICHT vor.

Erwartung: region_link verbindet beide (a) räumlich (Selke drains_into Bode) und
(b) kausal (eine Belastung/Maßnahme an der Selke -> Zustand unten an der Bode),
rein über die Fließtopologie.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from topology import Topology           # noqa: E402
from region_link import link            # noqa: E402


def make_topo() -> Topology:
    """Mini-Topologie mit echten GKZ (gegen DRAINBASIN_DEST verifiziert)."""
    t = Topology(min_shared=2)
    gkz = {
        "bode": "568", "selke": "5684", "holtemme": "5686",
        "rappbode": "56832", "wipper": "5676", "aller": "48", "saale": "56",
    }
    codes = {  # wb_core -> gkz  (Selke liegt in PlanungseinheitSAL20, NICHT SAL17!)
        "SAL17OW01-00": "568", "SAL20OW03-00": "5684",
        "SAL17OW30-00": "5686", "SAL07OW06-00": "5676",
    }
    names = {  # gkz -> repräsentativer Name
        "568": "Bode", "5684": "Selke", "5686": "Holtemme",
        "5676": "Wipper", "56": "Saale", "48": "Aller",
    }
    t.by_name.update(gkz)
    t.by_code.update(codes)
    t.name_of_gkz.update(names)
    return t


def test_spatial_relations():
    """Reine Topologie: Nachbar-Gewässer werden korrekt eingeordnet."""
    t = make_topo()
    assert t.relate_names("Selke", "Bode").relation == "drains_into"
    assert t.relate_names("Holtemme", "Bode").relation == "drains_into"
    assert t.relate_names("Rappbode", "Bode").relation == "drains_into"
    # Wipper mündet in die Saale -> echter Nachbar der Bode (gemeinsames EZG Saale)
    wip = t.relate_names("Wipper", "Bode")
    assert wip.relation == "shares_catchment" and wip.common_label == "Saale"
    # Aller gehört zum Weser-Gebiet -> KEINE Verbindung
    assert t.relate_names("Aller", "Bode").relation == "distant"
    assert not t.relate_names("Aller", "Bode").connected


def test_neighbor_pdf_attaches():
    """Integration: ein Selke-Nachbar-PDF dockt räumlich + kausal an die Bode an,
    obwohl 'Bode' im Selke-Dokument nie vorkommt."""
    t = make_topo()

    # Zwei Dokumente, disjunkte Wasserkörper, KEIN gemeinsamer Name:
    wbs = [
        {"eu_cd_lw": "DERW_DEST_SAL17OW01-00", "wb_name": "Bode",  "sources": "Bode-Bericht"},
        {"eu_cd_lw": "DERW_DEST_SAL20OW03-00", "wb_name": "Selke", "sources": "Selke-Bericht"},
    ]
    events = [
        # Ursache im Nachbar-PDF (Selke, oberhalb): eine Belastung/Maßnahme
        {"event_id": "SEL:e1", "eu_cd_lw": "DERW_DEST_SAL20OW03-00",
         "record_type": "Prozess", "source_document": "Selke-Bericht",
         "statement": "Diffuse Nährstoffeinträge aus der Landwirtschaft im Selke-Einzugsgebiet."},
        # Wirkung im bestehenden PDF (Bode, unterhalb): ein Zustand
        {"event_id": "BOD:e1", "eu_cd_lw": "DERW_DEST_SAL17OW01-00",
         "record_type": "Zustand", "source_document": "Bode-Bericht",
         "statement": "Die untere Bode erreicht den guten ökologischen Zustand nicht."},
    ]

    spatial, candidates, summary = link(events, wbs, t)

    # (a) RÄUMLICH: genau eine dokumentübergreifende Kante Selke drains_into Bode
    cross = [s for s in spatial if s["cross_document"] == "ja"]
    assert len(cross) == 1, cross
    edge = cross[0]
    assert {edge["wb_a_name"], edge["wb_b_name"]} == {"Selke", "Bode"}
    assert edge["relation"] in {"drains_into", "receives"}
    assert edge["via"] == "Bode"

    # (b) KAUSAL: ein dokumentübergreifender Kandidat Selke-Ursache -> Bode-Wirkung
    assert len(candidates) == 1, candidates
    c = candidates[0]
    assert c["cause_doc"] == "Selke-Bericht" and c["effect_doc"] == "Bode-Bericht"
    assert c["cause_event_id"] == "SEL:e1" and c["effect_event_id"] == "BOD:e1"
    assert c["confidence"] == "inferred"            # Kandidat, kein belegter Fakt
    assert "flussabwärtiger Transport" in c["mechanism"]


def test_same_document_not_crosslinked():
    """Zwei Wasserkörper aus DEMSELBEN Dokument erzeugen keinen Cross-Region-Kandidaten."""
    t = make_topo()
    wbs = [
        {"eu_cd_lw": "DERW_DEST_SAL20OW03-00", "wb_name": "Selke", "sources": "Selke-Bericht"},
        {"eu_cd_lw": "DERW_DEST_SAL17OW01-00", "wb_name": "Bode",  "sources": "Selke-Bericht"},
    ]
    events = [
        {"event_id": "a", "eu_cd_lw": "DERW_DEST_SAL20OW03-00", "record_type": "Prozess",
         "source_document": "Selke-Bericht", "statement": "x"},
        {"event_id": "b", "eu_cd_lw": "DERW_DEST_SAL17OW01-00", "record_type": "Zustand",
         "source_document": "Selke-Bericht", "statement": "y"},
    ]
    _, candidates, _ = link(events, wbs, t)
    assert candidates == []  # gleiche Quelle -> kein dokumentübergreifender Vorschlag


def _run_all():
    tests = [test_spatial_relations, test_neighbor_pdf_attaches, test_same_document_not_crosslinked]
    for fn in tests:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\nALLE {len(tests)} CROSS-REGION-TESTS BESTANDEN ✓")
    # kleine, lesbare Demo-Ausgabe
    t = make_topo()
    print("\nBeispiel-Einordnung (echte Gewässerkennzahlen):")
    for nm in ["Selke", "Holtemme", "Rappbode", "Wipper", "Aller"]:
        r = t.relate_names(nm, "Bode")
        print(f"  {nm:9} ({t.gkz_for_name(nm):>5}) ~ Bode (568): {r.relation:16} via {r.common_label or '—'}")


if __name__ == "__main__":
    _run_all()
