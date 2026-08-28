# Aufbau von `water_kg_2`

`water_kg_2_import.py` baut das normalisierte Schema parallel zu `water_kg` aus
der verlustfreien Schicht `water_raw` auf. Neo4j und `water_kg` werden nicht
verändert.

## Fachliche Festlegungen

- Parameteridentität: Name und Qualitätskomponente; die Einheit gehört zur
  Messung und nicht zur Parameteridentität.
- Fehlende Stationen werden als `observed_only` ohne Geometrie angelegt.
- EU-Stationscodes und lokale Codes werden über `station_alias` verbunden.
- Bewertungswert 999 wird als NA gespeichert: Rohwert bleibt 999,
  `value_normalized` ist NULL und `is_valid` ist false.
- Bewertungsskala vorläufig 1 bis 5, wobei 1 gut und 5 schlecht bedeutet.
- `EU_CD_LW` und `EU_CD_LS` werden über `water_body_identifier` äquivalent
  auf einen Wasserkörper aufgelöst.
- Grundwasser-/Oberflächengewässerbeziehungen speichern nur die räumliche
  Überlappung; daraus wird kein chemischer Status abgeleitet.
- Unbekannte Schutzgebiets- und Wasserkörperreferenzen bleiben als Originalcode
  erhalten; ihre Foreign Keys sind NULL.
- Einzugsgebietsbeziehungen werden räumlich abgeleitet und mit Methode sowie
  Überlappungsflächen dokumentiert.
- Bohrkernkoordinaten stammen aus WGS84 und werden zusätzlich nach EPSG:25833
  transformiert.
- Paläoalter wird als `age_years_bp`, Elementwerte als einheitsfrei gespeichert.

## Ausführung

Voraussetzung ist ein aktuelles `water_raw`:

```powershell
.\.venv\Scripts\python.exe .\raw_data_import.py
.\.venv\Scripts\python.exe .\water_kg_2_import.py
```

Existiert `water_kg_2` bereits, beendet sich das Skript ohne Änderung. Ein
expliziter, kontrollierter Neuaufbau erfolgt mit:

```powershell
.\.venv\Scripts\python.exe .\water_kg_2_import.py --rebuild
```

Das Skript baut zunächst `water_kg_2_build` auf und validiert dort Zeilenzahlen
und Constraints. Erst nach erfolgreicher Prüfung ersetzt beziehungsweise
veröffentlicht es `water_kg_2`.

## Ergebnis des ersten validierten Aufbaus

- 118.399 Messungen, exakt eine Zeile pro Quellzeile;
- 2.906 Messstellenbewertungen;
- 26 Wasserkörperbewertungen;
- 25 chemische Statuszeilen, davon 20 Oberflächen- und 5 Grundwasserzeilen;
- 2.400 Paläomessungen und 6 Bohrkernpositionen;
- 74 Schutzgebietsbewertungen;
- 93 Stationen und 124 Stationsaliase;
- 581 fachliche Parameter und 15 Einheiten;
- 50 aktive Foreign-Key-Constraints;
- keine Messung, kein Probenahmeabschnitt und keine Stationsbewertung ohne
  aufgelöste Station.

18.469 Messungen besitzen keinen passenden Probenahmeabschnitt. Die Messungen
bleiben erhalten und `sampling_section_id` ist NULL.

`import_issue` enthält zwei nicht aufgelöste Planungseinheiten aus
`groundwaterbody.shp`: `STH` und `HAV_PE06`. Die Originalcodes stehen in
`source_planning_unit_code`; der nullable Foreign Key bleibt leer.

## Nächster Schritt

Der Neo4j-V2-Import ist inzwischen in `neo4j_v2_import.py` umgesetzt und liest
vollständig aus `water_kg_2`. Details zum versionierten Graphmodell, zum sicheren
Neuaufbau und zu den validierten Mengen stehen in `README_NEO4J_V2.md`.
