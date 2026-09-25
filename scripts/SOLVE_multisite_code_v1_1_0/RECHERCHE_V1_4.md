# Recherche mit geprüften Suchwegen – Version 1.6.0

**Aktuell:** Der Standardstarter recherchiert nur Arendsee, jetzt mit Serper
statt SearXNG und einer vorgelagerten Auswahl von 20 geeigneten Web-Startlinks.
Die gültige Start-, Schlüssel- und Ergebnisbeschreibung steht in
[SERPER_ARENDSEE.md](SERPER_ARENDSEE.md). Die folgenden Abschnitte dokumentieren
die früheren Suchwege und Prüfstände; der Ergebnisordner bleibt erhalten.

Version 1.5.0 verallgemeinert die wissenschaftliche Suche für alle konfigurierten
Gewässer: neun Portal-Domains, Crossref sowie die neue öffentliche DataCite-Suche
für Datensätze und Repositorien. Der Standardstarter bleibt bei Arendsee.
Für alle 33 Gewässer gibt es jetzt `.\Start-Blablador-Alle-Seen.cmd` und die
Konfiguration `inputs/sites_blablador_33_scientific.json`. Die Ergebnisse dieses
Starters liegen unter `dossier_blablador_alle_33_v1_5/results/`.
Die Such-, Download-, LLM- und Zeitbudgets gelten für den gesamten Lauf.
Details, Grenzen und Startbefehle: [Wissenschaftliche Suche](WISSENSCHAFTLICHE_QUELLEN.md).
Prüfung: **111 Tests bestanden**, zusätzlich drei echte DataCite-Abfragen;
siehe [Prüfbericht](validation/general_science_20260924/PRUEFBERICHT.md).

Prüfstand der ursprünglichen Version 1.4: **80 automatisierte Tests bestanden.** Der fortgesetzte Live-Pilot
ergab sieben relevante Dokument-Gewässer-Zuordnungen und eine unterstützende
Zuordnung, jeweils nach Inhaltsprüfung; damit gibt es Kandidaten für alle fünf
Pilotgewässer. 16 Wiederverwendungen vorhandener Originale sind protokolliert.
Details und Grenzen: [Prüfbericht](validation/retrieval_v1_4_verified/PRUEFBERICHT.md).

Die aktive Konfiguration umfasst vorerst **nur Arendsee**, weiterhin mit allen
19 Recherchethemen. Die vollständige Auswahl mit 33 Gewässern ist unter
`inputs/sites_blablador_33_v1_4.json` gesichert.

Version 1.4.2 ergänzt acht direkte wissenschaftliche Rechercheeinstiege aus
sechs Portalen. Der Crawler kann sie auch bei ausgefallenem SearXNG abrufen,
inhaltlich prüfen und passende Links auf Publikationen, PDFs und Literatur
verfolgen. Zusätzlich werden gezielte Domain-Suchen geplant, sobald ein
Suchweg verfügbar ist. Der vollständige Katalog und die Konfiguration stehen
in [WISSENSCHAFTLICHE_QUELLEN.md](WISSENSCHAFTLICHE_QUELLEN.md).

Die zusätzlichen Einstiege stehen außerhalb des Identitätsprofils, damit die
bisherigen Aufgaben und Bewertungen im vorhandenen Arendsee-Dossier aktiv
bleiben. Die Vorprüfung, Inhaltsbelege und Download-Wiederverwendung gelten
weiterhin für diese Quellen. Der Startbefehl bleibt gleich.

Die neue Strategie heißt `focused`. Der Starter verwendet ein neues Dossier,
damit der frühere Lauf mit ungeprüften Suchantworten nachvollziehbar bleibt.
Vorhandene Downloads werden über denselben `download_cache` wiederverwendet.

Version 1.4.1 erweitert den Arendsee-Lauf: Crossref plant weitere Suchvarianten
auch bei ausgefallenem SearXNG. Offene Kandidaten nach ausgeschöpftem
Erkundungsbudget oder bereits versuchter Inhaltsprüfung blockieren diese Planung
nicht mehr. Quellen mit ungeklärten Belegen bleiben weiterhin ungeklärt.
Die Begrenzungen stehen unten; sie garantieren weder eine Stunde aktive Suche
noch eine bestimmte Trefferzahl.

Prüfung von 1.4.1: **92 Tests bestanden**, darunter zwölf neue Tests für
Suchfortsetzung, Anbieter-Wartefristen und Abbruch beim Zeitlimit/Strg+C.
Zwei zusätzliche Crossref-Suchen wurden live geprüft, ohne Dokumentabrufe oder
LLM-Aufrufe. Details: `validation/extended_arendsee_v1_4_1/PRUEFBERICHT.md`.

## Start und Ergebnisse

In PowerShell im Projektordner:

```powershell
.\Start-Blablador.cmd
```

Dieser Starter benötigt keine PowerShell-Skriptausführungsfreigabe. Alternativ:

```powershell
.\.venv\Scripts\python.exe revised/run_web_enrichment.py run --config inputs/sites_blablador.json --workspace dossier_blablador_arendsee_v1_4
```

`BLABLADOR_KEY` muss für den Prozess als Umgebungsvariable verfügbar sein.
Der Schlüssel wird nicht in Projektdateien gespeichert. Zum Fortsetzen denselben
Befehl verwenden. Es wird keine automatische Dauerrecherche eingerichtet.

Die Ergebnisse stehen in `dossier_blablador_arendsee_v1_4/results/`:

- `RECHERCHEBERICHT.md`: Quellen und Themen je Gewässer; geplante und abgeschlossene Suchen getrennt.
- `suchdienst_status.jsonl`: Prüfung der Suchrouten, Fehler und Wartefristen.
- `llm_vorpruefung.jsonl`: ursprüngliche LLM-Bewertungen vor dem Abruf.
- `ereignisse.jsonl`: tatsächlich angewandte Entscheidungen, Metadatenkorrekturen und Cache-Treffer.
- `laufprotokoll.jsonl`: Abbruchgrund, Vorprüfungs-, Inhaltsprüfungs- und Suchaufrufe.
- `alle_quellen_inkl_verworfene.jsonl`: endgültige Kandidatenbewertungen einschließlich Fundstellen.

Originale liegen unter `archive/`. Ein positiver LLM-Befund ist eine belegte
Kandidatenbewertung, keine fachliche Freigabe oder Vollständigkeitsgarantie.

## Ablauf

1. **Suchdienst prüfen.** Google, Standardroute und Brave werden auf der
   konfigurierten SearXNG-Instanz einzeln getestet. Eine Route muss beide
   Kontrollanfragen zu Bergwitzsee und Arendsee/UFZ bestehen. Fehlgeschlagene
   Routen pausieren 30 Minuten, erfolgreiche Prüfungen gelten eine Stunde.
   Es werden höchstens zwölf Kontrollanfragen pro Arendsee-Lauf gestellt. Andere
   aktivierte Engines können in `retrieval.engines` eingetragen werden.
   Die festen Kontrollanfragen bleiben auch beim Arendsee-Lauf erhalten;
   daraus werden keine Rechercheaufgaben für Bergwitzsee erzeugt.
2. **Weitere Suchantworten prüfen.** Query-Echo und Engine-Provenienz werden
   berücksichtigt. Auffällige Antworten, insbesondere vollständig verfehlte
   Domain-Suchen, werden separat protokolliert. Sie gelangen nicht ungeprüft
   in die Download-Warteschlange. Ein unbekannter PDF-Titel allein gilt nicht
   als Ausschlussgrund.
3. **Quellen erschließen.** Geprüfte Behörden-/Forschungseinstiege und kurze
   Suchvarianten ergänzen die allgemeine Suche. Für zahlreiche Gewässer ist
   der HYDREG-Bericht 2010 als dokumentierter Einstieg hinterlegt; die fünf
   Pilotgewässer haben zusätzliche gezielte Startquellen. Sammelberichte
   werden für jeden See gesondert geprüft. Kennungen werden nur mit dokumentierter
   Herkunft übernommen; im Fall Barleber See I/II aus dem Bericht 2005–2008.
4. **Crossref ergänzen.** Die öffentliche Crossref-API sucht bibliografische
   Metadaten. Sie benötigt hier keinen zusätzlichen Schlüssel. Ein DOI-Fund
   garantiert keinen zugänglichen Volltext. Die LLM- und Inhaltsprüfungen
   gelten auch für diese Treffer. Crossref lässt sich mit
   `retrieval.crossref_enabled=false` abschalten.
5. **Vorprüfung und Priorisierung.** Das Blablador-Modell erhält das erweiterte
   Identitätsprofil, die Suchfrage und Metadaten der Elternquelle. Die
   Wichtigkeit steuert anschließend die Warteschlange. Unklare Kandidaten
   erhalten im erweiterten Arendsee-Lauf ein Erkundungsbudget von 20 Aufgaben je Gewässer und
   Lauf. Eindeutig benannte Hauptsatzungen, Kommunalwahlen und Veranstaltungskalender
   werden zurückgestellt. Diese domänenspezifische Regel steht in
   `source_preflight.py` und ist keine allgemeine Aussage über solche Quellen.
   Vor dem Download kennt das Modell keinen Dokumentinhalt. Es liefert
   `importance` (0–100), `confidence` (0–1), Gewässerzuordnung, Kategorie und
   Begründung. Eine LLM-Ablehnung wird erst ab `confidence >= 0.85` angewandt;
   die genannten festen Ausschlussregeln können zusätzlich zurückstellen.
   Die Zahlen sind Modellbewertungen, keine gemessenen Trefferwahrscheinlichkeiten.
6. **Nach dem Abruf belegen.** Das LLM prüft den gelesenen Inhalt. Jedes positive
   Thema benötigt eine existierende Textstelle und eine gültige Fundstelle. Das
   Modell wählt vom Programm vergebene Beleg-IDs; der zugehörige Originaltext
   wird unverändert zur Validierung verwendet. Bei direkter
   Relevanz muss der Beleg das Gewässer benennen; bei mehrdeutigen Ortsnamen
   zusätzlich einen erkennbaren Gewässerbezug. Ohne abgeschlossene Inhaltsprüfung
   bleibt die Bewertung offen. Gespeichert werden Fundstellen und Zitat-Hashes,
   keine extrahierten Volltextpassagen.
7. **Gezielt weitergehen.** Die nächsten Suchvarianten richten sich nach
   geprüften Quellen und offenen Themen. Neue sachfremde URLs zählen nicht als
   Quellengewinn. Weitere Ergebnisseiten werden erst nach positivem Quellengewinn
   abgerufen, bis maximal Seite drei. Literaturangaben können als bibliografische
   Metadaten zu neuen Suchaufträgen führen; Originalzitate bleiben transient.
   Noch nicht inhaltlich geprüfte Gewässer erhalten Vorrang für eine erste
   Inhaltsprüfung, begrenzt auf drei Abrufversuche je Gewässer und Lauf.

## Budgets und Grenzen

Pro erweitertem Arendsee-Lauf: höchstens 80 Suchanfragen/Netzwerk-Suchversuche
(zwölf Kontrollanfragen separat), 200 neue LLM-Vorprüfungen, 80 Inhaltsprüfungen,
120 Abrufaufgaben, 1000 Verarbeitungsschritte und 60 Minuten. Das Erkundungsbudget
beträgt 20 statt bisher zwei Aufgaben; pro Host sind 20 Abrufaufgaben möglich.
Bis zu 20 Treffer werden pro Suchantwort berücksichtigt. Der Planer darf bis zu
40 Suchaufträge auf verfügbaren Anbietern planen; wartende Anfragen eines
ausgefallenen Anbieters blockieren dieses Kontingent nicht. Die separate globale
Grenze beträgt weiterhin 160 geplante Suchaufträge je Gewässer.
Erst nach zehn ausgewerteten Suchen ohne Quellengewinn kann der jeweilige
Suchweg pausieren. Ein bereits begonnener Aufruf kann nach dem Zeitbudget noch
zu Ende laufen.
Budgetgrenzen sind lokale Einstellungen, keine behaupteten Dienstquoten.

Bei fehlender sofort ausführbarer Arbeit wartet der Starter auf fällige
Suchwiederholungen (`retrieval.wait_for_search_routes=true`). Die 30 Minuten
SearXNG-Abkühlzeit werden eingehalten. Nach Ablauf kann derselbe Prozess den
Suchdienst erneut prüfen, solange Prüf- und Zeitbudget reichen. Wartezeit zählt
zum Stundenlimit. Mit Strg+C lässt sich der Lauf unter Speicherung des
Zwischenstands beenden; `wait_for_search_routes=false` schaltet das Warten ab.
Crossref-Anfragen laufen nacheinander mit Suchabstand; eine API-Wartefrist gilt
auch für andere Suchvarianten. [Crossref: Zugriff und Limits](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/).

`tasks` im Laufprotokoll zählt Verarbeitungsschritte einschließlich Vorprüfung
und Einordnung in die Warteschlange. Es ist keine Anzahl heruntergeladener
Dokumente. `unique_tasks_attempted`, `pending_tasks`, `exploration_deferred`,
`pending_searches` und `search_routes_unavailable` machen die Restarbeit sichtbar.
Die Vorprüfung verwendet standardmäßig `alias-fast`, die Inhaltsprüfung
`alias-large`. Die gemeinsame Umgebungsvariable
`BLABLADOR_MODEL` kann beide Modellvorgaben überschreiben;
`BLABLADOR_BASE_URL` überschreibt die Basisadresse.
Die Inhaltsprüfung verwendet ein Antwortlimit von 6000 Tokens und
`reasoning_effort=low`. Ungültige Inhaltsbelege werden gesondert von technischen
API-Fehlern gezählt. Unbelegte optionale Literaturangaben werden verworfen.

OCR ist für spärlich lesbare PDF-Seiten aktiviert und auf acht Seiten begrenzt.
Sie benötigt `pdftoppm` und `tesseract` mit den Sprachdaten `deu+eng` im PATH.
In der geprüften Umgebung fehlt Tesseract; solche Fälle bleiben mit
`ocr_tools_missing`/OCR-Status sichtbar. Es wurde kein systemweites OCR-Programm
installiert.

Die Suchdienstprüfung ist bewusst konservativ und kann eine eingeschränkt
funktionierende Route vorübergehend ausschließen. Sie ist keine allgemeine
Bewertung eines Suchmaschinenanbieters. Auch Quellen ohne klare Metadaten,
schlecht erkannte Tabellen oder Namensvarianten können weitere Prüfung benötigen.
UCB/Contextual Bandit sind weiterhin nicht aktiviert: Die neue Strategie nutzt
belegte Quellenrückmeldungen und eine faire Verteilung über die Gewässer.

## Fünf-Gewässer-Pilot

```powershell
.\.venv\Scripts\python.exe revised/run_web_enrichment.py run --config inputs/pilot_5_retrieval_v1_4.json --workspace dossier_pilot_5_v1_4
```

Der Pilot umfasst Arendsee, Bergwitzsee, Barleber See I, Barleber See II und
Vorsperre Hassel. Er erlaubt zehn Suchen, 15 Abrufaufgaben, 25 Vorprüfungen,
15 Inhaltsprüfungen und sieben Minuten. Der bei der Implementierung ausgeführte
erste Funktionstest liegt unter `validation/retrieval_v1_4_live/`; der Test der
überarbeiteten Beleg-ID-Prüfung unter `validation/retrieval_v1_4_verified/`.

Die neuen Startquellen sind kuratiert. Ein Vergleich mit dem alten Lauf zeigt
deshalb die Wirkung des gesamten Ablaufs und ist kein isolierter Nachweis,
dass ein bestimmtes Modell oder ein Suchalgorithmus überlegen ist.

Technik: `revised/quality_search.py`, `revised/focused_search.py`,
`revised/source_preflight.py`, `revised/run_web_enrichment.py`.
Konfigurationsherkunft: `revised/configure_retrieval.py` und `seed_evidence`
in den Gewässerprofilen. Die vorherigen Dateien liegen unter
`validation/pre_retrieval_backup/`.
