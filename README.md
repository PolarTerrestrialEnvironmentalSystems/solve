# SOLVE Gewässer-Parser & Knowledge-Graph — Versionsübersicht

> **Neueste Version: [`solve-kg/`](solve-kg/)** — das ist der aktuelle Stand des Parsers
> **plus** die Knowledge-Graph-Schicht. Alles andere in diesem Ordner ist älter oder
> Zwischenstand. Nutzungs-Doku: [`solve-kg/README.md`](solve-kg/README.md).

Dieses Dokument sagt, **welche Version die neueste ist** und **was sich am Parser
gegenüber dem Baseline-Modell verändert hat**.

## Versionen auf einen Blick

| Version | Ort | Parser-Kern | Graph-Schicht | Status |
|---|---|---|---|---|
| **solve-kg** | `solve-kg/` | 1.0.0-Basis **+ Kompakt-Variante** | **✅ voll** (Merge, VKG, Cross-Region) | **AKTUELL** |
| Batch 1.2.0 | `p_SOLVE/parsing/SOLVE_Parsing_Fein_Batch_1.2.0` | 1.2.0 | ❌ | Parser-Experiment (nur Parsing) |
| Batch 1.1.0 | `p_SOLVE/parsing/…_1.1.0` | 1.1.0 | ❌ | Parser-Experiment |
| Batch 1.0.0 | `p_SOLVE/parsing/…_1.0.0` | 1.0.0 | ❌ | **Baseline-Release** (eingefroren, mit Prüfsummen) |

> **Hinweis zur Gabelung:** Die `solve-kg`-Linie baut auf dem **1.0.0-Release** auf und
> hat die ganze Graph-Schicht ergänzt. Die Parser-Experimente **1.1.0/1.2.0** liefen
> parallel und haben einige Parser-Features, die in `solve-kg` **noch nicht** stecken
> (siehe unten, Zeile „Schema/Prompt"). Für die Graph-Arbeit ist `solve-kg` maßgeblich.

## Was sich am Parser zum Baseline-Modell (1.0.0) verändert hat

Baseline = **Batch 1.0.0**: PDF-Parsing mit feiner Ontologie, mehrere PDFs aus einem
Eingabeordner, Checkpoints/Wiederaufnahme, 4 parallele Abschnittsabfragen, gemeinsamer
Limiter (30 rpm), dokumentbezogene Event-/Beziehungs-IDs.

| Änderung | wo | gegenüber Baseline |
|---|---|---|
| **Kompakt-Ausgabe + Code-Expansion** (`compact_variant.py`) | solve-kg | LLM liefert nur den semantischen Kern (kompaktes Schema, keine null-Felder/Metadaten); der **Code** baut daraus die vollständigen, schema-gültigen 28/30-Spalten-Datensätze. Ziel: kürzere/stabilere Antworten, Release unberührt. |
| **Merge-Schicht** (`merge_entities.py`) | solve-kg | Mehrere Läufe → ein Graph; gemeinsame Gewässer werden quellübergreifend zum Verbindungspunkt. |
| **VKG-/Ontop-Export** (`export_vkg.py`, `vkg/`) | solve-kg | Fakten → CSV + amtlicher Code `eu_cd_lw` (aus DRAINBASIN/Karte); Andockung an das bestehende Ontop-VKG. |
| **Cross-Region-Verknüpfung** (`topology.py`, `region_link.py`) | solve-kg | **neu** — verbindet benachbarte PDFs **räumlich** (GKZ-Fließtopologie) und schlägt **dokumentübergreifende** Ursache→Wirkung-Kanten vor. Vorher nur Verbindung bei gleichem Gewässernamen. |
| **Status-Anreicherung** (`enrich_status.py`, `add_hist_status.py`, `build_map_svg.py`) | solve-kg | ökol./chem. Zustand + Ausweisung pro Wasserkörper, Kartendarstellung. |
| Prompt-Tuning (`PROMPT 1.0→1.2`) | Batch 1.1/1.2 | präzisere Extraktionsanweisungen. **Noch nicht in solve-kg.** |
| Schema-Härtung (`SCHEMA 1.0→1.1`): `extraction_schema()` erzwingt Ontologie-IDs als Enums; `time_original`/`time_normalized` | Batch 1.2 | zwingt gültige Ontologie-Klassen/Topics, sauberere Zeitfelder. **Noch nicht in solve-kg.** |

**Kurzfassung:** Gegenüber dem Baseline-Modell ist der große Zugewinn die **Graph-Schicht**
(Merge → amtliche Codes → Cross-Region räumlich+kausal) und die **Kompakt-Variante** des
Parsers. Die reinen Parser-Feintunings aus 1.1.0/1.2.0 (Prompt, Schema-Enums) sind ein
separater, noch nicht eingemergter Strang.

## Daten & Zugänge

Liegen lokal, sind **nicht** im öffentliche Repo: `WRRL/…`-Fachdaten,
GeoJSON, `results/`, und vor allem **`.env`** mit dem Blablador-Key.
`solve-kg/.gitignore` schließt all das aus.
