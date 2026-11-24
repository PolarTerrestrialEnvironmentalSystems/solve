# SOLVE: Skripte und Datenwege

Stand: 24.11.2026. Branch `codex/scripts-und-datenwege-20261124`.

Dieser Branch führt den aktuellen Stand von `origin/main`, den Crawler-Stand
`14a5a5a` aus `codex/elbe-crawler-20261110` und die lokalen Pilot-/V2-Skripte
zusammen. Die bestehende Parserlinie unter `solve-kg/` bleibt erhalten.
Keine Serverprozesse oder bestehenden Datenbanken wurden hierfür verändert.

## Welcher Einstieg passt?

| Aufgabe | Einstieg | Voraussetzungen / Grenzen |
|---|---|---|
| Amtliche Basisdaten für 5 Seen je Bundesland | [seen_basis_pilot](../scripts/seen_basis_pilot/README.md) | Python 3.10+, keine Schlüssel; Rohdaten, kein vollständiger Messreihenimport |
| Quellen und PDFs für Elbe-Gewässer recherchieren | [Elbe-Crawler](../scripts/SOLVE_multisite_code_v1_1_0/ELBE_LINUX.md) | Blablador, vorbereitete Startlinks, 30 Worker; kein Serper im Modus `none` |
| Dokumente strukturiert auswerten / Parser-KG | [solve-kg](../solve-kg/README.md) | Eigenständige Parser-/Graphlinie aus main, nicht dasselbe Schema wie Brandenburg-V2 |
| Brandenburg-V2-Schema verstehen | [V2 PostgreSQL](../scripts/README_WATER_KG_2.md), [Neo4j](../scripts/README_NEO4J_V2.md) | Ursprüngliches Klassenmodell |
| Bergwitzsee mit Messwerten und Events nachvollziehen | [Skriptübersicht](SCRIPT_INDEX.md#bergwitzsee-und-v2) | Historische lokale Abläufe; benötigen externe Rohdaten und Windows-Runtime |
| Älteren temporalen 33-Seen-Prototyp nachvollziehen | [wasserblick33](../scripts/wasserblick33/README.md) | Separates Modell; vorbereiteter Daten-Bundle nötig, nicht im Git |

Die typischen Arbeitsschritte sind:

1. Amtliche Basisdaten herunterladen **und/oder** Quellen mit dem Crawler sammeln.
2. Originale, Kennungen, Einheiten und Herkunft prüfen; Messreihen gegebenenfalls manuell exportieren.
3. Dokumente parsen und Events fachlich prüfen.
4. Normalisierte Daten mit expliziten Kennungszuordnungen in PostgreSQL laden.
5. Aus dem geprüften Datenstand Neo4j erzeugen und einen Dump exportieren.

Diese Schritte sind nicht zu einer einzigen automatisch ausführbaren Pipeline
für alle Bundesländer verbunden. Insbesondere macht ein erfolgreicher
Pilot-Download noch keinen vollständigen Knowledgegraphen.

## Navigation

- [Skriptübersicht und Reihenfolge](SCRIPT_INDEX.md)
- [Datenpfade auf Windows und Linux](DATA_LOCATIONS.md)
- [Branch holen und auf Linux starten](LINUX_START.md)
- [Vollständiges Dateiverzeichnis der neu gebündelten Skripte](SCRIPT_FILES.md)
- [Klassenmodell mit Diagrammen als PDF](model/SOLVE_Klassenmodell_Diagramme_20261119.pdf)

## Was im Git liegt und was nicht

Mitgeliefert werden Code, Tests, Konfigurationen, ausgewählte Gewässerkennungen,
Startlink-Register und Dokumentation. Diese kleinen Register sind Eingaben des
Crawlers, keine kopierten Ergebnis-Dossiers. Die schon in main vorhandenen
Beispieldaten bleiben Bestandteil der geerbten Repository-Historie.

Neu hinzugefügte Inhalte enthalten keine `.env`-Zugangsdaten, Datenbank-Dumps,
Arbeitsdatenbanken, heruntergeladenen Forschungs-PDFs oder Python-Umgebungen.
Die Modell-PDF ist eine ausdrücklich mitgelieferte Dokumentation.

Nicht alle historischen Skripte sind direkt auf Linux ausführbar. Lokale
Pfadbindungen sind dokumentiert und wurden nicht ohne Datenbanktests umgebaut.
Die hier beschriebenen Serverpfade stammen aus der bisherigen Einrichtung;
ihre heutige Erreichbarkeit wurde nicht per SSH verifiziert.
