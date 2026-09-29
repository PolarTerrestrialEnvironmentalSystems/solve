# SOLVE: Vergleich kompakter JSON-Antworten und GPT-OSS-120B

Dieser Ordner enthält eine unveränderte Referenzkopie (`baseline`) und eine
experimentelle Kopie (`compact`) des Fein-Batch-Parsers. Das Original auf Y: bleibt
unverändert. Der Schlüssel wird zur Laufzeit aus `GRAPHRAG_API_KEY2` der vom Nutzer
angegebenen lokalen Datei gelesen. Er wird nicht in diesen Ordner kopiert.

## Vergleich

- Referenz: nvidia/Qwen3.8-Flash-Next-NVFP4, bisheriger Prompt, Thinking aus.
- A: dieselbe Modellbasis, kompakter Prompt, Thinking aus.
- B: openai/gpt-oss-120b, kompakter Prompt, serverseitige Thinking-Vorgabe.

Die exakten angefragten Modell-IDs und Modellbasen stehen in `models.json` und in
jedem Ergebnismanifest. GPT-OSS erhält keinen Qwen-spezifischen Thinking-Schalter;
der Vergleich B gegen A umfasst daher die modelltypischen Servereinstellungen.

Die PDF ist das sechsseitige Kennblatt zum Klimafolgenindikator B10, Zufluss zur
Rappbodetalsperre, Bearbeitungsstand 08.07.2025. Drei ausgewählte Abschnitte werden
je einmal verarbeitet: Bewirtschaftungsmaßnahmen auf PDF-Seite 2, die vermutete
Kyrill-Wirkung auf Seite 3 sowie die Sommerzeile 2016–2030 auf Seite 5. Die letzten
sechs Tabellenspalten sind leer. Alle Varianten erhalten identische Quellblöcke,
Kontextblöcke, Dokumentinformationen, Ontologie und Exportvorlagen.

Eine Anfrage gleichzeitig, 30 RPM, 16.000 Ausgabetokens, Temperatur 0, Timeout 300
Sekunden, höchstens ein HTTP-Retry. Schema-Reparatur und Teilung bleiben aktiv.
Keine zusätzlichen Kontextabfragen: offene Kontextwünsche werden in Prüflisten
gehalten. Reihenfolge rotiert je Abschnitt: Referenz/A/B, A/B/Referenz, B/Referenz/A.
Alle Versuche teilen eine persistierte Ratenbegrenzung. Keine Antwort-Caches aus
früheren Läufen. Das ist eine kleine Pilotstichprobe, kein vollständiger Benchmark.

## Dateien und Reproduktion

- `run_pilot.py`: Vergleichssteuerung; vorhandene Fallresultate werden nicht überschrieben.
- `prepare_pilot.py`: PDF-Aufbereitung und Kontrollbilder.
- `prepared_all.json`: gemeinsam verwendete PDF-Aufbereitung.
- `quality_checklist.json`: fachliche Prüfpunkte und neun Sommer-Tabellenwerte.
- `results/<Variante>/`: Rohantworten, Checkpoints, normalisierte Pakete, Excel-/JSONL-Exporte und HTTP-Protokolle.
- `summarize_pilot.py`: technische Messwerte aus den gespeicherten Logs.
- `offline_tests_*.txt`: Offline-Testprotokolle.

Python-Umgebung: `.venv/Scripts/python.exe`, Python 3.14.7 mit
`compact/requirements.lock.txt`. Ein erneuter Benchmark muss neue Ergebnisordner
verwenden. Rohantwort, normalisiertes Paket und finaler Export sind getrennt.

Der geerbte Parser kann bei dieser tabellarisch gesetzten PDF Fließtext mehrfach
als Tabellenkontext ausgeben. Die Stichprobe ist fest gewählt und für alle Varianten
gleich. Diagramme werden nicht visuell ausgewertet; aus der Tabelle prüfbare Werte
werden separat kontrolliert. Diese Einschränkungen gelten für alle drei Varianten.

## Abgebrochene Kalibrierung

Der erste breiter gefasste Referenzversuch erhielt die gesamte erste Seite als
Kontext. Er generierte 16.000 Tokens in rund 287 Sekunden und wurde abgeschnitten;
die Antwort extrahierte auch zahlreiche Fakten aus dem Kontext. Die automatische
Teilung wurde begonnen, dann wurde der Versuch abgebrochen. Diese Daten stehen
separat unter `calibration_results` und sind vom endgültigen Vergleich ausgeschlossen.
Der endgültige Pilot verwendet nur den Metadatenblock der ersten Seite als
Dokumentkontext und die oben beschriebenen kürzeren Originaltextspannen. Die
Block-IDs und wörtlichen Quelltexte bleiben erhalten. Alle Varianten erhielten
dieselbe Anpassung vor dem Neustart; das Antwortbudget blieb bei 16.000 Tokens.
