# Evaluation der Datenbank- und Knowledge-Graph-Erstellung

Stand: 26. August 2026

## Ergebnis

Die bisherige Struktur bildet einen fachlich ausgewählten Ausschnitt der Quellen
ab, ist aber kein verlustfreier Import. Deshalb wurde eine zweistufige Architektur
eingeführt:

1. `water_raw`: vollständige, reproduzierbare Rohdatenhaltung;
2. `water_kg`: normalisierte Fachtabellen als Quelle des Knowledge Graphen.

Die Rohdatenschicht ist vollständig geladen. Die bestehende Fachschicht und der
Knowledge Graph benötigen vor einem Neuaufbau noch ein kontrolliertes Schema-v2-
Upgrade. Sie wurden bei dieser Evaluation nicht destruktiv neu aufgebaut, damit
bestehende IDs und Graphbeziehungen nicht unbemerkt ungültig werden.

## Vollständige Rohdatenhaltung

`raw_data_import.py` lädt jede logische Quelle mit allen Zeilen und Spalten. Jede
Tabelle besitzt `source_row_number` als reproduzierbaren Primärschlüssel.

- 45 Dateien im Dateiregister `water_raw.source_file`, inklusive DBF/SHX/PRJ;
- 20 logische Datenquellen;
- 452 dokumentierte Quellspalten in `water_raw.source_column`;
- 307 Spalten konnten über `attribute_names.xlsx` mit einem Langnamen versehen
  werden;
- SHA-256-Prüfsumme, Dateigröße, Ladezeit und Zieltabellen je Datei.

| Quelle | Quellzeilen | `water_raw` | bisher in `water_kg` |
|---|---:|---:|---|
| `lakewaterbody.shp` | 6 | 6 | 6 Seen, aber nur 13 von 119 Attributen |
| `groundwaterbody.shp` | 4 | 4 | 4, aber nur ein Attributausschnitt |
| `catchments.shp` | 32 | 32 | 32, aber nur ein Attributausschnitt |
| `messstellen.shp` | 31 | 31 | 31, aber nur Code, Name, Gewässer und Geometrie |
| `ffh.shp` | 6 | 6 | Teil der 71 Schutzgebiete |
| `waterprotection.shp` | 53 | 53 | Teil der 71 Schutzgebiete |
| `recreation_areas.shp` | 12 | 12 | Teil der 71 Schutzgebiete |
| `planunits.shp` | 4 | 4 | nicht modelliert |
| `core_locations.xlsx` | 6 | 6 | nicht modelliert |
| `paleo_elements.csv` | 2.400 | 2.400 | nicht modelliert |
| `einzeldaten.csv` | 11.670 | 11.670 | gemeinsam mit Chemie in `measurement` |
| `einzeldaten_chemie.csv` | 106.729 | 106.729 | gemeinsam mit Biologie in `measurement` |
| `einzeldaten_abschnitte.csv` | 8.194 | 8.194 | 8.194 |
| `messstellen_bewertung.csv` | 2.906 | 2.906 | 2.736 |
| `wasserkoerper_bewertung.csv` | 26 | 26 | 26 |
| `GW_measures.csv` | 5 | 5 | 5 |
| `OW_measures.csv` | 20 | 20 | 0 |
| `protection_area_bewertung.csv` | 74 | 74 | nur 7 Beziehungen |
| `stammdaten.csv` | 6 | 6 | ausgewählte Eigenschaften der Seen |
| `attribute_names.xlsx` | 342 | 342 | vorher nicht gespeichert |

## Kritische Befunde in `water_kg`

### P0 – fehlende Verlustfreiheit und Provenienz

Messungen und Bewertungen besitzen keinen stabilen Schlüssel aus Quelldatei und
Quellzeile. Stattdessen werden fachliche Spalten als vermeintlicher natürlicher
Schlüssel verwendet. Dadurch werden legitime Mehrfachbeobachtungen
zusammengefasst.

- 118.399 Messzeilen stehen 140.713 Datenbankzeilen gegenüber;
- 11.328 Messsignaturen treten in 33.984 Datenbankzeilen mehrfach auf;
- 2.906 Messstellenbewertungen wurden auf 2.736 Zeilen reduziert;
- 170 Bewertungen gingen durch den aktuellen zusammengesetzten Schlüssel verloren.

Empfehlung: Jede Faktentabelle erhält `source_dataset` und `source_row_number` mit
einem Unique Constraint. Fachliche Signaturen werden nur noch als Suchindex
verwendet.

### P0 – keine Fremdschlüssel

Im Schema `water_kg` existiert aktuell kein einziger Foreign-Key-Constraint.
Beziehungen wie `measurement.parameter_id`, `measurement.station_id` oder
`water_body_assessment.water_body_id` werden ausschließlich durch Python-Code
angenommen.

Empfehlung: Nach einem Staging-Import Orphans protokollieren und alle gültigen
Beziehungen mit Foreign Keys absichern.

### P0 – fehlende Oberflächengewässer-Chemie

`OW_measures.csv` nennt die Spalte `EU_CD_LS`, enthält in den Beispieldaten aber
die Codes aus `EU_CD_LW`. Der bisherige Import suchte ausschließlich in
`water_body.eu_cd_ls`; deshalb wurden alle 20 Zeilen verworfen. Der Importcode
akzeptiert nun beide Identifikatoren. Die Fachschicht muss kontrolliert neu
aufgebaut werden, um die Zeilen zu übernehmen.

### P1 – falsche Flächeneinheit

`FLAECHE_ATKIS` liegt in Hektar vor, wurde aber unverändert als `area_km2`
gespeichert. Die Geometrieprüfung bestätigt ungefähr den Faktor 100:

- Schwielochsee: gespeichert 1.327,29; Geometrie etwa 13,22 km²;
- Scharmützelsee: gespeichert 1.210,48; Geometrie etwa 12,11 km².

Der Importcode rechnet nun Hektar durch 100 in km² um. Die bestehenden Werte in
`water_kg` und Neo4j sind noch nicht migriert; die Abfrageoberfläche kompensiert
dies derzeit temporär.

### P1 – unvollständiges Messstellenmodell

- 19.712 Messungen haben keine zugeordnete `station_id`;
- 548 Probenahmeabschnitte haben keine Station;
- 888 Messstellenbewertungen haben keine Station.

Die Shapefile-Stationen verwenden EU-Codes, andere Quellen häufig lokale Codes.
Die teilweise Zuordnung über den Stationsnamen ist nicht ausreichend.

Empfehlung: `monitoring_station` plus `station_alias` modellieren. Aliase erhalten
Quellsystem, lokalen Code und Gewässerbezug. Nicht räumlich bekannte, aber in
Messdaten beobachtete Stationen werden als eigene Stationen ohne Geometrie
angelegt statt als NULL-Verweis verworfen.

### P1 – Parametermodell

`parameter` verwendet aktuell `(name, quality_component, unit)` als Identität.
Dadurch kann derselbe fachliche Parameter mehrfach vorkommen, wenn eine Einheit
abweicht. Bewertungen besitzen häufig keine Einheit und lassen sich deshalb
nicht eindeutig verbinden.

- Alle 26 `WaterBodyAssessment`-Knoten haben keine `FOR_PARAMETER`-Kante;
- auch StationAssessments besitzen aktuell keine `FOR_PARAMETER`-Kanten.

Empfehlung: Parameteridentität aus Name und Qualitätskomponente; Einheit an der
Messung beziehungsweise in einer getrennten Unit-Dimension. Bewertungsparameter
müssen beim Aufbau der Parameterdimension mit berücksichtigt werden.

### P1 – Schutzgebietsbewertungen

Die 74 Zeilen aus `protection_area_bewertung.csv` werden auf sieben Beziehungen
reduziert, weil nur Datensätze behalten werden, deren Schutzgebiet und
Wasserkörper zu den wenigen geladenen Geometrien passen. Die Statusattribute
werden dabei größtenteils verworfen.

Empfehlung: eigene Faktentabelle `protected_area_assessment` für alle 74 Zeilen.
Eine Beziehung zu bekannten Gebieten oder Gewässern ist optional und darf nicht
Voraussetzung für die Speicherung sein.

## Befunde im Knowledge Graphen

Der Graph spiegelt die bisherige Fachschicht weitgehend exakt – einschließlich
ihrer Fehler und Auslassungen.

| Knoten | Anzahl | Befund |
|---|---:|---|
| WaterBody | 6 | vollständig für die sechs Seen |
| GroundWaterBody | 4 | vollständig für die ausgewählten Attribute |
| Measurement | 140.713 | enthält die relationalen Duplikate |
| Station | 31 | nur räumlich gelieferte Stationen |
| StationAssessment | 2.736 | 170 Quellzeilen fehlen |
| WaterBodyAssessment | 26 | keine Parameterbeziehungen |
| ChemicalStatus | 5 | 20 Oberflächenstatuszeilen fehlen |
| ProtectedArea | 71 | Geometriequellen vollständig |
| SamplingSection | 8.194 | 548 ohne Stationsbezug |

Weitere Graphbefunde:

- 19.712 Measurement-Knoten ohne eingehende `RECORDED`-Kante;
- nur 121.001 von 140.713 Messungen sind mit einer Station verbunden;
- 116.330 Messungen sind mit einem SamplingSection-Knoten verbunden;
- keine `FOR_PARAMETER`-Beziehung für Bewertungen vorhanden;
- PlanUnit, CoreLocation und PaleoElement fehlen vollständig;
- nur sieben `HAS_PROTECTED_AREA`-Beziehungen aus der CSV-Referenz.

## Empfohlenes Zielschema v2

### Stammdaten

- `water_body`
- `groundwater_body`
- `planning_unit`
- `catchment`
- `monitoring_station`
- `station_alias`
- `core_location`
- `protected_area`
- `parameter`
- `unit`
- `pollutant`

### Fakten

- `measurement`
- `sampling_section`
- `water_body_assessment`
- `station_assessment`
- `chemical_status`
- `protected_area_assessment`
- `paleo_measurement`

### Beziehungen

- `water_body_groundwater_body`
- `catchment_groundwater_body`
- `station_catchment`
- `station_groundwater_body`
- `water_body_protected_area`

Jede Faktentabelle benötigt Quellprovenienz. Rohwerte, normalisierte Werte und
fachliche Gültigkeitskennzeichen sollten getrennt gespeichert werden. Beispiel:

```text
assessment_value_raw = 999
assessment_value_normalized = NULL
is_valid_assessment = false
validation_reason = "ungesichertes Ergebnis"
```

## Reihenfolge für den kontrollierten Neuaufbau

1. Neues normalisiertes Schema parallel als `water_kg_v2` erstellen.
2. Ausschließlich aus `water_raw` laden.
3. Zeilenzahlen, Orphans, Einheiten und Unique Constraints validieren.
4. Referenzabfragen gegen v1 und v2 vergleichen.
5. Neo4j aus v2 vollständig neu aufbauen.
6. Erst danach die Abfrageoberfläche auf v2 umstellen.

Ein paralleles Schema verhindert, dass die derzeitige Anwendung während der
Migration inkonsistente IDs oder teilweise neue Beziehungen sieht.

## Umsetzungsstatus

`water_kg_2_import.py` setzt dieses Zielschema inzwischen parallel um. Der erste
Aufbau wurde mit vollständigen Quellzeilenzahlen und 50 Foreign-Key-Constraints
validiert. Details und Ausführung stehen in `README_WATER_KG_2.md`.
