# Prüfstand vom 19.11.2026

Live-Test auf dem Windows-Arbeitsrechner, nicht auf linux3:

- Alle 30 ausgewählten WRRL-Seewasserkörper für 2022 abgerufen.
- 30 Gewässerdatensätze, 64 Messstellen-Datensätze und 24 Datensätze mit
  Qualitätskomponenten. Datensatzzahlen sind keine Zahl von Einzelmessungen.
- 84 erfolgreiche nichtleere Layerabfragen, sechs leere Qualitätsabfragen,
  keine HTTP-/API-Fehler im abgeschlossenen Test.
- 26 gewässerbezogene Ablagen von Landesdokumenten erfolgreich:
  10 Brandenburg, 1 Berlin, 5 Sachsen, 5 Mecklenburg-Vorpommern, 5 Thüringen.
  Thüringens fünf Ablagen enthalten denselben landesweiten Bericht. Dieser
  wird über den URL-Cache nur einmal abgerufen. Daher sind 26 Ablagen nicht
  gleichbedeutend mit 26 unabhängigen Quellen.
- Historische Abrufe 2010 und 2016 für Scharmützelsee zusätzlich geprüft:
  je eine Gewässer-, Messstellen- und Qualitätskomponenten-Antwort erhalten.
- Historische Abrufe aller anderen 29 Wasserkörper noch nicht live geprüft.
- Erneuter Lauf hat vorhandene Antworten wiederverwendet; ergänzende Jahre
  haben die bestehenden 2022-Statuszeilen erhalten.
- Neun automatisierte Tests erfolgreich: Auswahl, Dateiformate, API-Fehler,
  falsche Identität, abgeschnittene Antwort, Cache-Hash und exklusiver Lock.
- Bash-Syntax des Linux-Starters mit `bash -n` erfolgreich geprüft.
- Python-Standardbibliothek; kein API-Schlüssel und keine Zusatzpakete nötig.

Leere Qualitätsabfragen 2022: Lehnitzsee, Bergwitzsee, Süßer See,
Talsperre Bautzen, Fleesensee, Talsperre Bleiloch 2.
Die Gewässerdatensätze können trotzdem übergeordnete Zustandsattribute
enthalten. Eine leere Qualitätsabfrage beweist kein generelles Fehlen von Daten.

Die Tests prüfen Abruf, Identität und technische Vollständigkeit. Sie ersetzen
keine fachliche Prüfung der Landesdokumente, keine vollständige Normalisierung
der Originaltabellen und keine Garantie zukünftiger Portalverfügbarkeit.
