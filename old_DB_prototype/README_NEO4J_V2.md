# Neo4j-V2-Import

`neo4j_v2_import.py` überführt das PostgreSQL-Schema `water_kg_2` in einen
versionierten Knowledge Graph. Der bestehende Graph bleibt erhalten: Neue
Knoten tragen fachliche Labels wie `WaterBodyV2` und zusätzlich den Marker
`WaterKgV2`.

## Ausführen

Die Projektumgebung verwenden:

```powershell
.\.venv\Scripts\python.exe .\neo4j_v2_import.py
```

Ein vorhandener V2-Graph wird ohne explizite Freigabe nicht verändert. Für
einen kontrollierten Neuaufbau:

```powershell
.\.venv\Scripts\python.exe .\neo4j_v2_import.py --rebuild
```

Optionale Umgebungsvariablen:

- `SOLVE_DATABASE_URL`
- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`
- `NEO4J_DATABASE`

Die Transaktionsgröße kann beispielsweise mit `--batch-size 1000` angepasst
werden.

## Schutz des bestehenden Graphen

Der Import arbeitet zunächst mit `WaterKgV2Build` und Labels wie
`MeasurementV2Build`. Erst nachdem sämtliche Knoten- und Beziehungsmengen
gegen PostgreSQL geprüft wurden, wird der staging Graph freigegeben.

Bei `--rebuild` werden ausschließlich Knoten mit dem Marker `WaterKgV2`
ersetzt. Unversionierte Labels des bisherigen Graphen werden weder gesucht
noch gelöscht. Schlägt der staging Import fehl, wird nur `WaterKgV2Build`
entfernt und der freigegebene V2-Graph bleibt erhalten.

## Fachliche Modellierung

- Messungen und Bewertungen sind eigene Knoten.
- Parameter sind unabhängig von Einheiten; `HAS_UNIT` verweist auf `UnitV2`.
- `value_raw = 999` bleibt erhalten; `value_normalized` fehlt und
  `is_valid = false` kennzeichnet die Bewertung als ungültig.
- Stationen werden durch Gewässer und lokalen beziehungsweise beobachteten
  Stationscode identifiziert. Der resultierende `station_key` ist eindeutig.
- `EU_CD_LW` und `EU_CD_LS` bleiben als `WaterBodyIdentifierV2` erhalten und
  identifizieren denselben `WaterBodyV2`-Knoten.
- Stations- und Bohrkernkoordinaten werden als WGS84-`point` gespeichert.
- Polygongeometrien verbleiben in PostGIS. Neo4j erhält die vorberechneten
  räumlichen Beziehungen samt Überlappungsfläche, Verhältnis und Methode.
- Herkunft wird über `source_file`, `source_row_number`, `source_index` und
  einen reproduzierbaren `source_key` abgebildet.
- Unterschiedliche Schutzgebietsbeziehungen desselben Gewässer-Gebiet-Paars
  bleiben durch einen eigenen `relationship_key` getrennt erhalten.

## Ergebnis des validierten Aufbaus

Der Aufbau vom 26. August 2026 ergab:

- 21 Knotentypen;
- 34 fachliche Beziehungsmappings;
- 132.986 Knoten;
- 584.695 Beziehungen;
- 35 aktive V2-Uniqueness-Constraints;
- keine staging Knoten;
- keine Beziehungen zwischen V2 und dem bisherigen Graphen.

Die 2.300 als ungültig gekennzeichneten Stationsbewertungen sind vollständig
enthalten und können in Analyseabfragen über `is_valid = true` ausgeschlossen
werden.

## Zentrale Labels

`WaterBodyV2`, `GroundwaterBodyV2`, `MonitoringStationV2`, `MeasurementV2`,
`ParameterV2`, `UnitV2`, `WaterBodyAssessmentV2`, `StationAssessmentV2`,
`ChemicalStatusV2`, `PollutantV2`, `ProtectedAreaV2`, `CatchmentV2`,
`CoreLocationV2` und `PaleoMeasurementV2`.

Die vorhandene Abfrageoberfläche verwendet noch die unversionierten Labels.
Ihre Abfragen beziehungsweise ein späterer Text-to-Cypher-Agent müssen in
einem separaten Schritt auf das V2-Schema umgestellt werden.
