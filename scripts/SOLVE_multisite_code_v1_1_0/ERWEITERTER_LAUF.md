# Erweiterte Laufbudgets (27.09.2026, Code 1.6.3)

## Datenbankbeschleunigung (28.09.2026, Code 1.6.5)

Bei etwa 20.000 Quellen fuehrten fehlende Indizes auf den Fundwegen zu wiederholten
vollstaendigen Tabellendurchlaeufen. Neu sind Indizes fuer Fundwege nach Gewaesser/URL
und Suchauftrag sowie Reviews nach Gewaesser/Status. Sie werden beim Oeffnen eines
vorhandenen Dossiers automatisch ergaenzt. Auswahlentscheidungen, Aufgabenstatus und
gespeicherte Suchergebnisse werden dabei nicht zurueckgesetzt.

Die Quellenauswahl prueft Startlink-Freigaben jetzt mit zwei Sammelabfragen pro
Auswahlrunde statt mit einzelnen Abfragen je Kandidat. Der Export laedt Fundwege
einmal pro Gewaesser und verwendet sie fuer Quellenlisten und Fundwegeexport wieder.
Er zeigt Fortschritt pro Gewaesser und eine Abschlussmeldung; nach Strg+C abwarten.

Messung auf einer Kopie des echten Dossiers (kein Online-Crawl): einzelne Fundweg-
Abfrage 0,032 s vorher / 0,000014 s nach Index; Quellenauswahl 0,46 s;
vollstaendiger Export 8,4 s. API-Antwortzeiten und Downloads kommen weiterhin hinzu.
Messdaten: `validation/performance_20260928/benchmark.json`.

## Fortsetzen ohne Startlink-Auswahl (28.09.2026, Code 1.6.4)

In `sites_blablador_33_serper_v1_6_2.json` ist `start_links.skip_selection=true`.
`Start-33-Seen.cmd` setzt daher jetzt direkt mit gespeicherten ausgewaehlten Quellen
und deren weiterfuehrenden Links fort. Auswahlentscheidungen und Gewaesserprofile
bleiben erhalten; abgelehnte und unbewertete Web-Startlinks werden nicht freigegeben.
Die normale Vorpruefung und Inhaltsbewertung bleiben aktiv. Wissenschaftliche
Metadatensuchen koennen weiterhin laufen; die Serper-Startlinkphase wird ausgelassen.

Einmalig wieder auswaehlen: `Start-33-Seen.cmd --select-start-links`.
Explizit ueberspringen: `Start-33-Seen.cmd --skip-start-links`.
Die Python-CLI unterstuetzt beide Optionen ebenfalls. Ohne gespeicherte Auswahl
zeigt das Protokoll `skipped_no_saved_selection`; daher denselben Dossierpfad nutzen.

Ausserdem wurde die separate Vorpruefungs-Sperre repariert: Fehler der Startlink-
Auswahl zaehlen nicht mehr gegen die Einzelquellen-Vorpruefung. Deren Grenzen sind
30 Fehler insgesamt bzw. 5 hintereinander; gueltige neue Vorpruefungen setzen die
Fehlerfolge zurueck. Voruebergehende Wartezeiten werden bei offenen geeigneten Quellen
abgewartet. Dauerhafte Ausfaelle enden sichtbar mit `preflight_api_errors`.
Das Laufprotokoll weist `preflight_errors`, `consecutive_preflight_errors` und
`start_link_errors` getrennt aus. Die Qualitaetskriterien bleiben bestehen.

Der letzte lokale 33-Seen-Lauf endete nach etwa 1 Stunde 50 Minuten mit
`review_api_errors`: drei Netzwerkfehler bei der Inhaltsbewertung wurden ueber
den gesamten Lauf aufsummiert. Das Zeitlimit von 33 Stunden war nicht erreicht.
Viele Startlink-Auswahlen endeten ausserdem mit `start_search_limit`.

Die aktiven Konfigurationen `inputs/sites_blablador.json` und
`inputs/sites_blablador_33_serper_v1_6_2.json` sind erweitert. Dateinamen und
Dossierpfade bleiben erhalten, damit die bisherigen Starter denselben Stand fortsetzen.

| Grenze pro Aufruf | 33 Seen bisher | 33 Seen jetzt | Arendsee jetzt |
|---|---:|---:|---:|
| Laufzeit | 33 Stunden | 72 Stunden | 12 Stunden |
| Suchaufrufe | 2640 | 6000 | 240 |
| Abrufversuche | 3960 | 12000 | 600 |
| Aufgaben | 33000 | 100000 | 6000 |
| LLM-Vorpruefungen | 6600 | 12000 | 800 |
| LLM-Inhaltsbewertungen | 2640 | 10000 | 500 |
| Abrufe pro Host | 660 | 1500 | 100 |

Je See: 50 statt 20 Startsuchanfragen, bis zu 5 statt 3 Startsuchseiten,
320 geplante Suchanfragen, 5000 Kandidaten und 60 statt 20 explorative Abrufe.
Ziel bleibt 20 geeignete Web-Startlinks. Crawltiefe 7 statt 5, Referenztiefe 5 statt 3.
PDF-Verarbeitung bis 400 Seiten, davon maximal 20 OCR-Seiten, Downloadgroesse bis
80 MB. Abruf-Timeout 60 Sekunden, Parser-Timeout 180 Sekunden, LLM-Timeout 120 Sekunden.
Die alten Konfigurationen liegen unter `validation/before_extended_limits_*`.

API-Fehler: Abbruch nach 5 aufeinanderfolgenden technischen Inhaltsbewertungsfehlern
oder 30 insgesamt. Erfolgreiche Antworten setzen die Fehlerfolge zurueck; reine
Validierungsfehler werden separat gezaehlt und geben Quellen weiterhin nicht frei.
`consecutive_review_api_errors` zeigt die aktuelle Fehlerfolge im Laufprotokoll.
Die gesonderten Vorpruefungs- und Suchdienst-Sperren bleiben bestehen.

Start wie bisher mit `Start-33-Seen.cmd` oder `Start-Blablador.cmd`.
Ein laufender Prozess liest geaenderte Konfigurationen nicht neu ein: einmal
Strg+C druecken, Abschlussausgabe abwarten und denselben Starter erneut ausfuehren.
Die Gewaesserprofile bleiben unveraendert; der gespeicherte Stand wird weiterverwendet.
Die aeltere Datei `sites_blablador_33_scientific.json` wurde nicht umgestellt.

Mehr Budget ermoeglicht mehr Suche und API-Verbrauch, garantiert aber keine
Vollstaendigkeit. robots-Regeln, Zugriffssperren und notwendige LLM-Freigaben bleiben
wirksam. Bereits geparste PDFs werden durch geaenderte Obergrenzen nicht automatisch
erneut vollstaendig geparst. Netzlaufwerk X: war bei dieser Aenderung nicht erreichbar;
die dortige Skriptkopie ist daher noch nicht aktualisiert.
