# SOLVE: Fein-Parsing, Releases und Versuchsberichte

Dieser Ordner bündelt die Parserentwicklung und die dokumentierten Versuche bis
Version **1.2.0** (29.09.2026). Die produktiven Parserpakete sind eigenständig;
historische Versuchsantworten dienen der Nachvollziehbarkeit, nicht als vollständiger
oder fachlich freigegebener Wissensgraph.

## Aktuelle Version

- [Parser 1.2.0: Einrichtung und Verwendung](SOLVE_Parsing_Fein_Batch_1.2.0/README.md)
- [ZIP 1.2.0](SOLVE_Parsing_Fein_Batch_1.2.0.zip)
- [Änderungsprotokoll](SOLVE_Parsing_Fein_Batch_1.2.0/CHANGELOG.md)
- [Live-Testbericht](SOLVE_Parsing_Fein_Batch_1.2.0/LIVE_TEST_REPORT.md)
- [Maschinenlesbarer Prüfbericht](SOLVE_Parsing_Fein_Batch_1.2.0/TEST_REPORT.json)
- [Vorversion 1.1.0](SOLVE_Parsing_Fein_Batch_1.1.0/README.md) und [ZIP](SOLVE_Parsing_Fein_Batch_1.1.0.zip)

Version 1.2.0 nutzt 4.000 Zeichen als **weiche Zielgröße** und übernimmt den
begonnenen Absatz bzw. die Tabellenzeile vollständig. Wertbezogene Zeiträume,
Ontologie-ID-Einschränkungen und eine begrenzte Qualitätsnachprüfung ergänzen den
Selbstbeziehungsschutz. Die 59 Offline-Tests bestehen. Der kleine Live-Pilot zeigt
eine bessere Zuordnung bestimmter Vergleichswerte, aber **keinen nachgewiesenen
Gewinn an Gesamtvollständigkeit**: weiterhin 6–8 von 19 Prüfpunkten vollständig.

## Studien und Dokumentation

- [Ursprünglicher Stand und Testplan](docs/Parsing_Optimierung_Stand_und_Testplan.md)
- [Modell- und JSON-Vergleich: Übersicht](parsing_model_comparison/README.md)
- [Pilotbericht](parsing_model_comparison/Pilotbericht.md)
- [JSON-Beispiele vorher/nachher](parsing_model_comparison/JSON_vorher_nachher.md)
- [Wiederholungstests zu Selbstbeziehungen](parsing_model_comparison/repeated_self_relations/Testbericht.md)
- [Abschnittsgrößenvergleich](parsing_model_comparison/chunk_size_study/Testbericht.md)
- [Absatzaufteilung und Qualitätsprüfung](parsing_model_comparison/paragraph_quality_study/Testbericht.md)

`parsing_model_comparison/` enthält die Vergleichsskripte, die Referenzkopie 1.0.0
(`baseline`), den kompakten experimentellen Parser (`compact`), Testpläne, Messwerte,
Rohantworten, Exporte und Review-Protokolle. Abgebrochene Kalibrierungen und der
verworfene Absatz-Prototyp sind getrennt aufbewahrt und ausdrücklich nicht als
erfolgreiche Abschlussmessungen zu interpretieren.

## Reproduktion

Für den regulären Parser die README des gewünschten Releases verwenden. API-Zugänge
ausschließlich lokal konfigurieren; `.env.example` enthält nur Platzhalter.
Vorlagen (`Event_headers.xlsx`, `relationship_headers.xlsx`) und gesperrte
Abhängigkeiten sind Bestandteil jedes Pakets. Neue Versionen benötigen einen neuen
Ergebnisordner.

Im Release-Verzeichnis:

```powershell
python verify_release.py
python -m unittest discover -s tests -v
```

Die historischen **Studien- und Paketierungsskripte** sind Originalnachweise der
lokalen Versuche. Sie enthalten damalige Windows-Pfade für PDFs, Arbeitsordner und
die lokale Datei mit `GRAPHRAG_API_KEY2`. Vor einem neuen Versuch müssen diese Pfade
an die eigene Umgebung angepasst und frische Ergebnisordner gewählt werden.
Einige ursprüngliche `.venv`-Pfade und lokale Dateiverweise in Berichten sind deshalb
keine direkt im GitHub-Checkout verwendbaren Links. Bestehende Rohantworten nicht
mit neuen Ergebnissen überschreiben. Live-Versuche verwenden den konfigurierten API-Dienst.

## Umfang des Uploads

Enthalten sind Skripte, Dokumentationen, Release-ZIPs, Excel-Vorlagen und ausgewertete
Testartefakte einschließlich der für die Studien verwendeten Textauszüge. Nicht
enthalten sind Zugangsschlüssel, `.env`-Dateien, Python-Umgebungen, Bytecode,
Quelldokumente als PDF, Kontrollbilder, temporäre Testverzeichnisse oder redundante
entpackte Release-Kopien. Die eingebetteten Testfixtures bleiben erhalten.

Die Release-Prüfsummen beziehen sich unverändert auf die jeweiligen ausgelieferten
Pakete. `UPLOAD_INVENTORY.json` dokumentiert zusätzlich die SHA-256-Prüfsummen der
hier übernommenen Dateien (ohne die Inventardatei selbst).
