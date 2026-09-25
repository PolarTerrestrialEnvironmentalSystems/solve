# LLM-Vorprüfung und gemeinsamer Download-Cache

**Stand dieser Anleitung: Version 1.3.** Die aktuelle Konfiguration, Inhaltsprüfung,
Startbefehle und Budgets stehen in [RECHERCHE_V1_4.md](RECHERCHE_V1_4.md).

In `inputs/sites_blablador.json` sind beide Funktionen für die 33 ausgewählten
Gewässer aktiviert. Die historischen Pilot-Konfigurationen bleiben unverändert.

## Start

Im Projektordner in PowerShell:

```powershell
.\.venv\Scripts\python.exe revised/run_web_enrichment.py run --config inputs/sites_blablador.json --workspace dossier_blablador_auswahl_33
```

Der Prozess benötigt den API-Schlüssel in der Umgebungsvariable `BLABLADOR_KEY`.
Ein solcher Schlüssel war in der Implementierungsumgebung vorhanden. Er wird
nicht in die Konfiguration oder Ausgabedateien geschrieben. Der direkte
Python-Aufruf benötigt keine Freigabe für PowerShell-Skripte.
Zum Fortsetzen denselben Befehl und denselben Workspace verwenden.

## Vor dem Download

SearXNG liefert Kandidaten. Das Blablador-LLM (`alias-fast` an
`https://api.blablador.fz-juelich.de/v1`) erhält Gewässername, Aliasse, Region,
Recherchethemen, URL und verfügbaren Titel bzw. Linktext. Es bekommt zu diesem
Zeitpunkt keinen heruntergeladenen Dokumentinhalt und keine Such-Snippets.

Die Antwort enthält Kategorie, Wichtigkeit (0–100), Gewässerbezug, Konfidenz
und eine kurze Begründung. Kategorien sind Wissenschaft, Behörde, Daten,
Hintergrund, Navigation, sachfremd und unbekannt.
Nur die Entscheidung `defer` mit mindestens 0,85 Konfidenz stellt den Kandidaten
zurück. Unsicherheit allein verhindert keinen Download. Die Wichtigkeit wird
dokumentiert; sie sortiert derzeit nicht die Download-Warteschlange.
Diese Metadatenprüfung ist keine nachgewiesene wissenschaftliche Qualitätsprüfung.
Die bisherige regelbasierte Prüfung nach dem Download bleibt zusätzlich aktiv.

Pro Lauf sind höchstens 50 neue LLM-Aufrufe vorgesehen, mit 60 Sekunden Timeout
pro Aufruf. Bereits gespeicherte identische Bewertungen werden wiederverwendet.
Bei API-Fehlern, ungültigen Antworten oder ausgeschöpftem Budget bleiben Aufgaben
offen, ohne ungeprüft herunterzuladen. Wiederholte API-Fehler bremsen weitere
Aufrufe. Ein fehlender Schlüssel verhindert den Start mit aktivierter Vorprüfung.

Bewertungen stehen nach dem Export unter
`dossier_blablador_auswahl_33/results/llm_vorpruefung.jsonl` und zusätzlich in
der jeweiligen `research.sqlite`. Modell und Bewertungsmetadaten werden mitgeführt.

## Wiederverwendung bereits geladener Dokumente

Der gemeinsame Cache liegt unter `download_cache/` im Projektordner. Alle
Dossiers mit dieser Cache-Einstellung können ihn verwenden. Vor dem erneuten
Download wird die normalisierte URL nachgeschlagen und das vorhandene Original
anhand seines SHA-256 geprüft. Bekannte Weiterleitungsziele werden ebenfalls
wiederverwendet; die Weiterleitung selbst kann noch eine HTTP-Anfrage benötigen.
Eine Sperre schützt den Cache vor parallelen doppelten Downloads.

Jedes Dossier erhält eine lokale Kopie, damit es eigenständig nutzbar bleibt.
Ein Dokument kann weiterhin mehreren Gewässern zugeordnet und für jedes Gewässer
separat bewertet werden, ohne seinen Inhalt erneut vom Server abzurufen.
81 Quell-URLs aus dem vorhandenen `dossier_blablador` wurden ohne Netzwerkabruf
in den gemeinsamen Cache übernommen.

Eine bisher unbekannte, andere URL mit identischem Inhalt lässt sich vor dem
ersten Abruf nicht sicher erkennen. Nach dem Abruf greift die bestehende
SHA-256-Deduplizierung der Originalablage. Ein expliziter Refresh umgeht den Cache.

Weitere vorhandene Dossiers lassen sich ohne Netzwerkzugriff übernehmen:

```powershell
.\.venv\Scripts\python.exe revised/prime_download_cache.py --dossier PFAD_ZUM_DOSSIER
```

## Prüfung der Implementierung

57 automatisierte Tests bestanden, darunter LLM-Zurückstellung, API-Fehler,
Bewertungs-Cache, Wiederverwendung über zwei Dossiers, Redirects, beschädigte
Cache-Dateien und parallele Cache-Zugriffe. Protokoll:
`validation/preflight_cache_tests.txt`.

Zwei echte API-Vorprüfungen ließen einen Arendsee-Kandidaten zu und stellten einen
sachfremden GitHub-Treffer zurück, ohne Quellen herunterzuladen. Die Ergebnisse
liegen in `validation/preflight_live/`. Dieser kleine Funktionstest ist keine
Messung der Klassifikationsgüte über alle 33 Gewässer.

Code: `revised/source_preflight.py`, `revised/shared_downloads.py`,
`revised/prime_download_cache.py`; Einbindung in `revised/run_web_enrichment.py`.
