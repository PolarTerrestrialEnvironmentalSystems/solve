# Welche Skripte gehören zu welchem Ablauf?

Alle Pfade hier sind relativ zur Wurzel dieses Git-Repositories.
Die absolute Installation auf dem Server hängt vom Clone-Verzeichnis ab.

## Amtliche Basisdaten: 30-Seen-Pilot

Ordner: [`scripts/seen_basis_pilot/`](../scripts/seen_basis_pilot/README.md)

| Datei | Zweck |
|---|---|
| `lakes.json` | Fünf festgelegte WRRL-Seewasserkörper je Bundesland, amtliche IDs und Quellen |
| `download.py` | `plan`, `run`, `status`; Abruf, Format-/Identitätsprüfung, Cache, Quellenprotokolle |
| `start-linux.sh` | Linux-Einstieg; ruft Python ohne zusätzliche Pakete auf |
| `test_download.py` | Technische Tests ohne produktive Datenbankzugriffe |
| `TESTBERICHT.md` | Tatsächlich durchgeführte Tests und ihre Grenzen |

Reihenfolge: `plan` -> `run` -> `status` / Ergebnisprüfung. Derselbe Output-Pfad
setzt fort. Für einen frischen Datenstand einen neuen Output-Pfad benutzen.
Die Länder-Messreihen sind noch nicht vollständig automatisiert; kein KG-Import.

## Quellenrecherche: Elbe-Crawler

Ordner: [`scripts/SOLVE_multisite_code_v1_1_0/`](../scripts/SOLVE_multisite_code_v1_1_0/ELBE_LINUX.md)

| Datei / Ordner | Zweck |
|---|---|
| `setup-linux.sh` | Python-Umgebung und Abhängigkeiten installieren |
| `start-elbe-linux.sh` | Einstieg für die Elbe-Kampagne |
| `revised/elbe_campaign.py` | Auswahl, Vorbereitung, Start, API-Prüfung und Status |
| `revised/parallel_crawler.py` | Gruppen und Worker; Wiederanlauf nach bestimmten API-Fehlern |
| `revised/run_web_enrichment.py` | Quellenabruf, Bewertung, Aufgabenstatus und Ergebnisexport |
| `revised/llm_gate.py` | Gemeinsame Begrenzung der Blablador-Aufrufe |
| `revised/source_preflight.py` | Bewertung einer Quelle vor dem Download |
| `revised/shared_downloads.py` | Gemeinsamer Download-Cache |
| `inputs/elbe_20261110/registry/` | Gewässerregister, Kennungen und Herkunft |
| `inputs/elbe_20261110/seeds.json` | Vorbereitete Startlinks |
| `inputs/elbe_20261110/catalog/` | Listen nach Bundesland und Gewässertyp |

Neuer Lauf: Installation -> `check-api` -> `prepare` -> `run`.
Vorhandener Lauf: denselben Workspace mit `run` fortsetzen, kein neues `prepare`.
Vorher sicherstellen, dass keine anderen Worker denselben Workspace bearbeiten.
Der automatische API-Wiederanlauf repariert keine NFS-Blockaden oder Serverausfälle.

## Dokumentparser und weiterer Graph

[`solve-kg/README.md`](../solve-kg/README.md) beschreibt die aus `main` übernommene
Parser-/Graphlinie. Wichtige Dateien: `parse_directory.py`, `prepare_document.py`,
`extract_facts.py`, `merge_entities.py`, `export_vkg.py`, `validate_export.py`.
Ihre Konfiguration und Reihenfolge stehen in der eigenen Anleitung.
Diese Linie ist nicht identisch mit dem ursprünglichen Brandenburg-V2-Modell.

## Bergwitzsee und V2

Das ursprüngliche Modell bleibt in
[`water_kg_2_import.py`](../scripts/water_kg_2_import.py) und
[`neo4j_v2_import.py`](../scripts/neo4j_v2_import.py) erhalten.

| Phase | Datei / Ordner | Eingaben / Ergebnis |
|---|---|---|
| LHW vorbereiten | `scripts/prepare_bergwitzsee_lhw.py` | Manuell exportierte LHW-Archive -> normalisierte Arbeitsdateien |
| PostgreSQL-V2 aufbauen | `scripts/bergwitzsee_v2/build_postgres.py` | Vorbereitete LHW-Daten und WRRL-Bundle -> neue lokale DB |
| Event-Zuordnung | `scripts/bergwitzsee_v2/event_extension.py` | Recherchierte Events, Belege und Quellen |
| Graph importieren | `scripts/bergwitzsee_v2/import_graph.py`, `import_extensions.py` | V2-Klassen plus direkte `WaterBodyV2-HAS_EVENT-Event`-Verbindung |
| Zusätzliche Quellen | `scripts/bergwitzsee_enrichment/acquire_geodata.py`, `prepare_monitoring_supplement.py` | Geodaten und ergänzende Monitoringquellen |
| Dokumentierter neuerer Gesamtstand | `scripts/bergwitzsee_enrichment/refresh_20261108.py` | Phasen `acquire`, `postgres`, `setup_graph`, `graph`, `export`, `document` |
| Prüfungen | `validate_delivery.py`, `validate_kg_peer.py`, `validate_additive.py` in den jeweiligen Ordnern | Herkunft, Zuordnung, Erhalt des bisherigen Graphen |
| Sicherungen | `export_dump.py`, `export_postgres.py` in den jeweiligen Ordnern | Neo4j-Dump bzw. PostgreSQL-Sicherung |

**Historische lokale Abläufe, keine fertigen Linux-Startbefehle:** Diese Dateien
enthalten feste Snapshot-Namen, lokale Ports und zum Teil Windows-/Java-Pfade.
Sie benötigen externe Eingabedaten unter `outputs/`, teilweise Kopien der
ursprünglichen Importe unter `source_scripts/` sowie bestehende Runtime-Helfer.
Diese Bestände werden nicht mitgeklont. Die Dateien werden zur Nachvollziehbarkeit
und Weiterentwicklung gebündelt, nicht als sofort lauffähiger Server-Neuaufbau.
`scripts/dummy_v2/` enthält ältere Hilfsskripte; Dummy-Messungen sind kein
Ersatz für fehlende Bergwitzsee-Daten und sollen nicht zusätzlich importiert werden.

## Älterer temporaler 33-Seen-Prototyp

[`scripts/wasserblick33/`](../scripts/wasserblick33/README.md) enthält:

- `download.py`: EEA-Abruf mit Auswahl und Quellenprotokollen.
- `prepare.py`: Aufbereitung zum geprüften Bundle.
- `schema.sql`, `pg_import.py`: PostgreSQL-Modell und Import.
- `neo4j_import.py`: eigener temporaler Graph, nicht das spätere V2-Modell.
- `compose.yaml`, `Dockerfile`, `init_env.py`: isolierte Container und lokale Kennwörter.
- `run_pipeline.py`, `verify_delivery.py`, `tests/`: Importkoordination und Prüfungen.

Die alte README beschreibt ein damals vollständiges Lieferpaket einschließlich
`data/`. In diesem Git-Branch fehlen diese Ergebnisdaten ausdrücklich.
`start.sh` setzt `/data/prepared/bundle.json` voraus; ein frischer Clone allein
genügt daher nicht für diesen Import.

## Weitere schon in main enthaltene Werkzeuge

`scripts/apw_downloader/`, `scripts/fgg_downloader/`, `scripts/kg_interface/`
und `scripts/graphrag/` bleiben unverändert erhalten. Ihre eigenen Anleitungen
und Eingabeanforderungen gelten. Sie wurden im Rahmen dieses Branches nicht
als vollständige Pipeline neu ausgeführt.
