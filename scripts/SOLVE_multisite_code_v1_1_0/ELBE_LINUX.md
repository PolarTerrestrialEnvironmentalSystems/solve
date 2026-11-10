# Elbe-Recherche auf Linux

Diese Kampagne sammelt und bewertet Quellen für Seen und Flussabschnitte im **deutschen Elbe-Einzugsgebiet innerhalb von Brandenburg, Berlin, Sachsen-Anhalt, Sachsen, Mecklenburg-Vorpommern und Thüringen**. Sie baut auf dem bisherigen Blablador-Crawler auf. Die anschließende strukturierte Ereignisextraktion und Graphverarbeitung liegt separat unter [`solve-kg/`](../../solve-kg/README.md).

## Umfang und Herkunft

Das mitgelieferte [Register](inputs/elbe_20261110/registry/waterbodies.json) enthält **2.554 eindeutige WRRL-Wasserkörper: 343 Seen-Wasserkörper und 2.211 Flussabschnitte**. Grundlage sind die deutschen Elbe-Datensätze der EEA-Berichterstattung 2022 und ein räumlicher Schnitt mit den amtlichen BKG-Landesgrenzen. Der Berichtsstand bezeichnet keine Messkampagne des Jahres 2022.

Die [Landeslisten](inputs/elbe_20261110/catalog/README.md) trennen Seen und Flüsse. Grenzgewässer können in mehreren Listen erscheinen; im Lauf wird jede amtliche ID einmal verarbeitet. Die Landeszahlen dürfen deshalb nicht addiert werden. Kleine Grenzüberschneidungen sind zur Prüfung markiert. Meldeland und räumliche Lage werden getrennt aufbewahrt.

Das Register ist eine umfangreiche, reproduzierbare **WRRL-Auswahl**, kein vollständiges Verzeichnis jedes kleinen Teichs oder Bachs. Teilbecken eines Sees und Abschnitte eines Flusses bleiben eigenständige amtliche Einträge. Gleichnamige Gewässer werden nicht zusammengelegt. Hinweise auf mögliche ganze Seen stehen in `registry/whole_lake_groups.json`; sie verändern die IDs nicht.

Die Fließgewässer-Kategorie umfasst auch amtlich als `RW` geführte künstliche Gewässer, etwa Kanäle. Die ursprüngliche Ausweisung als natürlich, künstlich oder erheblich verändert bleibt im Register erhalten.

Quellen und Abruf-/Filterverfahren stehen in [provenance.json](inputs/elbe_20261110/registry/provenance.json). Der Wiederaufbau erfolgt mit `revised/build_elbe_inventory.py`; nur dafür werden zusätzlich die Pakete aus `revised/requirements-inventory.txt` benötigt.

Historische Steckbriefe und Maßnahmenprogramme bleiben als eigene Quellen erkennbar. Ein Bewirtschaftungszyklus oder Veröffentlichungsjahr ist nicht automatisch der Messzeitraum. Die Kennungszuordnung führt außerdem keine automatische Zusammenlegung historischer Geometrien oder Messreihen durch.

## Code vom GitHub-Branch holen

Für einen neuen Ordner:

```bash
git clone --branch codex/elbe-crawler-20261110 --single-branch \
  https://github.com/PolarTerrestrialEnvironmentalSystems/solve.git \
  /bioing/data/WaterPlace/skripts/solve/crawler_scripts
cd /bioing/data/WaterPlace/skripts/solve/crawler_scripts/scripts/SOLVE_multisite_code_v1_1_0
bash setup-linux.sh
```

Voraussetzung: Python 3.10 oder neuer mit `venv`, Git und Zugriff auf die APIs sowie die Quellseiten. Kein sudo ist für die Projektinstallation erforderlich. `setup-linux.sh` erstellt die lokale Umgebung `.venv-linux`.

## Schlüsseldatei

Eine vorhandene `.env` darf weiterverwendet werden. Der Standardmodus ab 1.8.1 verwendet vorhandene Startquellen und Linkverfolgung ohne Such-API. Dafür wird nur Blablador benötigt:

```dotenv
BLABLADOR_KEY=hier_den_blablador_schluessel_eintragen
# Nur für ausdrücklich eingeschaltete zusätzliche Websuche:
# SERPER_KEY=hier_den_serper_schluessel_eintragen
# Optional: zusätzlicher Blablador-Schlüssel
# GRAPHRAG_API_KEY2=hier_den_zweiten_schluessel_eintragen
```

Zum Beispiel im persönlichen Server-Verzeichnis speichern:

```bash
mkdir -p "$HOME/.config/solve"
nano "$HOME/.config/solve/keys.env"
chmod 600 "$HOME/.config/solve/keys.env"
```

Die Datei bleibt außerhalb von Git. Zwei Schlüssel bedeuten nicht automatisch zwei unabhängige Kontingente. Der gemeinsame Blablador-Limiter gilt weiterhin für alle Worker.

Vorhandene Dateien mit `GRAPHRAG_API_KEY` funktionieren ebenfalls: Dieser ältere Name dient als Fallback für `BLABLADOR_KEY`. Ein ausdrücklich gesetzter `BLABLADOR_KEY` hat Vorrang — zuerst aus der Prozessumgebung, dann aus der angegebenen Datei. Nur wenn dieser fehlt oder leer ist, wird `GRAPHRAG_API_KEY` verwendet (Umgebung vor Datei). `GRAPHRAG_API_KEY2` bleibt der optionale zweite Schlüssel.

Neue Blablador-Tokens werden unter [Helmholtz Codebase → Personal Access Tokens](https://codebase.helmholtz.cloud/-/user_settings/personal_access_tokens) mit dem Scope `read_user` erstellt; siehe [offizielle Anleitung](https://sdlaml.pages.jsc.fz-juelich.de/ai/guides/blablador_api_access/). HTTP 401 bedeutet, dass der vorgelegte Token nicht akzeptiert wurde; weitere Worker beheben diesen Fehler nicht.

Zugang separat prüfen:

```bash
bash start-elbe-linux.sh check-api --env-file "$HOME/.config/solve/keys.env"
```

Dabei wird nur die Modellliste abgefragt, keine Such- oder Generierungsanfrage gestellt. Auch `run` prüft diesen Zugang, bevor Worker starten. Ein abgelaufener optionaler Zweitschlüssel muss erneuert oder aus Datei und Prozessumgebung entfernt werden. Eine erfolgreiche Modellliste garantiert noch keine erfolgreiche Generierung oder dauerhaft freie API-Kapazität.

## Vorbereitete Startquellen

Die gelieferten Startquellen stehen in `inputs/elbe_20261110/seeds.json`. Sie stammen aus amtlichen Katalogen, fachlichen Webseiten, Forschung und Publikationen. Die Recherche prüft Erreichbarkeit, Gewässerbezug und fachliche Relevanz. Gesperrte, unklare oder touristische Treffer füllen keine fehlenden Plätze auf. HTTP-Erreichbarkeit ist keine Garantie für die spätere fachliche Annahme einer Quelle.

**Erreichter Stand:** 10.097 Zuordnungen zu 2.774 verschiedenen URLs. Alle 343 Seen und 2.211 Flussabschnitte haben Quellen. Nur **sieben Seen erreichen zehn oder mehr**; die übrigen 336 haben zwei bis neun. Der unten gezeigte Start umfasst mit `--allow-shortfall` **alle 2.554 Gewässer**. Ohne diese Option gilt die strenge Mindestzahl; dann starten 2.211 Flussabschnitte und sieben Seen. [Rechercheverfahren und Grenzen](inputs/elbe_20261110/RESEARCH_NOTES.md) · [Startlinks je See](inputs/elbe_20261110/startlinks/README.md).

Die Vorbereitung verlangt standardmäßig **mindestens zehn angenommene Start-URLs pro See**, maximal zwanzig. Für Flussabschnitte gilt mindestens eine konkrete Fachquelle mit passender Kennung; die Vorgabe von zehn bis zwanzig bezog sich auf die Seen. Der Abdeckungsbericht nennt auch Gewässer unterhalb der Mindestzahl. Diese bleiben vollständig in der Liste, werden im Standardlauf aber ausgeschlossen.

Jeder Link ist eine Einstiegsmöglichkeit, nicht zwingend eine PDF-Datei oder eine unabhängige Studie. Ein amtlicher Bericht kann mehrere Gewässer enthalten. Seine gewässerspezifische Zuordnung ist dokumentiert; der gemeinsame Download-Cache vermeidet wiederholte Abrufe derselben URL.

Bei Sammelberichten mit nachgewiesener Kennung und Seitenzuordnung liest der Parser gezielt die betreffende(n) PDF-Seite(n) sowie ausdrücklich hinterlegte Kontextseiten, beispielsweise die Statuslegende auf Seite 3 des Maßnahmenanhangs 2015. Das funktioniert nur, wenn die Seitenangabe genau das verlinkte Dokument betrifft; eine Verzeichnisseite wird nicht auf ein anderes PDF übertragen. Das vollständige Original bleibt archiviert. `partial_selected_pages` dokumentiert den begrenzten Analyseumfang und ist keine Behauptung, der ganze Bericht sei gelesen worden. Quellen ohne passenden Seitenbeleg nutzen den normalen Parser; für dessen Rückfallpfad sind in der Elbe-Kampagne bis zu 600 Sekunden pro PDF vorgesehen.

## Einmal vorbereiten und mit 30 Workern starten

In einer `screen`- oder `tmux`-Sitzung bleibt der Lauf auch nach Trennen von PuTTY bestehen. Beispielsweise zuerst `screen -S solve-elbe` öffnen und darin:

```bash
cd /bioing/data/WaterPlace/skripts/solve/crawler_scripts/scripts/SOLVE_multisite_code_v1_1_0
lauf="$HOME/solve-runs/elbe_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$HOME/solve-runs"

bash start-elbe-linux.sh prepare \
  --workspace "$lauf" \
  --seeds inputs/elbe_20261110/seeds.json \
  --kind all \
  --groups 30 \
  --allow-shortfall \
  --search-provider none &&

printf '%s\n' "$lauf" > "$HOME/solve-runs/elbe-current.txt" &&

bash start-elbe-linux.sh run \
  --workspace "$lauf" \
  --workers 30 \
  --llm-workers 2 \
  --llm-interval 6 \
  --search-provider none \
  --env-file "$HOME/.config/solve/keys.env"
```

`prepare` arbeitet lokal und macht keine Such- oder LLM-Anfragen. Es erzeugt `preparation_report.json`, `parallel.json` und feste Gruppenkonfigurationen. Es überschreibt keinen vorhandenen Workspace. Die gemeldete Anzahl ist die tatsächlich startfähige Auswahl, nicht automatisch die Größe des Registers.

Mit `--kind lakes` oder `--kind rivers` nur einen Typ auswählen. Mit `--state ST` beispielsweise Sachsen-Anhalt wählen; mehrere Länder lassen sich durch Wiederholen von `--state` kombinieren. Für diese Varianten jeweils einen neuen Workspace verwenden.

Wenn bewusst auch Seen mit weniger als zehn fachlich angenommenen Links starten sollen, `prepare` zusätzlich `--allow-shortfall` übergeben. Das Defizit wird weiterhin ausgewiesen. Gewässer ohne eine einzige angenommene Quelle werden immer ausgeschlossen.

**Die Startlink-Auswahl läuft nicht erneut.** Mit `--search-provider none` sind außerdem alle späteren Such- und Metadaten-API-Anfragen deaktiviert; es wird kein Serper-Schlüssel verlangt. Die normalen Vorprüfungen und Inhaltsbewertungen durch Blablador bleiben aktiv. Der Crawler lädt die vorhandenen Startquellen und verfolgt passende ausgehende Links, einschließlich tatsächlich gefundener PDF- und DOI-Links. Zusätzliche Quellen findet er in diesem Modus über diese Links; unabhängige neue Suchtreffer kommen nicht hinzu.

`prepare` verwendet ab 1.8.1 standardmäßig `none`. Bei `run` gilt ohne Override die gespeicherte Konfiguration, die bei älteren Workspaces noch Serper enthalten kann. Deshalb für bestehende Läufe ausdrücklich `run --search-provider none` verwenden. Die Gruppenkonfigurationen, Profilkennungen, Downloads und Quellenbewertungen bleiben dabei erhalten. Offene Suchaufträge werden als `suspended` aufbewahrt und im Status getrennt ausgewiesen; ihre Wartezeiten und Suchbudgets stoppen den Crawl nicht. Mit `run --search-provider serper` und gültigem `SERPER_KEY` können passende gespeicherte Suchaufträge später wieder aufgenommen werden. Auch dann bleibt die vorbereitete Startlink-Auswahl übersprungen.

30 Worker bedeuten bis zu 30 gleichzeitig bearbeitete Gruppen. Blablador-Anfragen bleiben standardmäßig auf zwei gemeinsame Slots und mindestens sechs Sekunden Abstand zwischen neuen Anfragen begrenzt. Das orientiert sich am dokumentierten externen Limit von zehn Anfragen je 60 Sekunden für `alias-large`; für `alias-fast` nennt der Anbieter 30 je 60 Sekunden ([Blablador-API-Dokumentation](https://sdlaml.pages.jsc.fz-juelich.de/ai/guides/blablador_api_access/), geprüft am 10.11.2026). Dieser gemeinsame Abstand ist bewusst auch auf die schnellere Vorprüfung angewendet. Die Slots erlauben überlappende Antworten, erhöhen aber nicht die Startrate. Weitere Anwendungen mit demselben Benutzerkontingent können trotzdem Wartezeiten auslösen. Suchabstände, Webseitenregeln, Download-Cache und API-Wartezeiten begrenzen die tatsächliche Abrufrate zusätzlich. Mehr CPU-Kerne umgehen keine API-Limits.

Pro Gewässer und Lauf sind zunächst 500 Prüfaufgaben, 100 Abrufversuche, 40 Suchaufträge, 500 Vorprüfungen und 100 Inhaltsbewertungen budgetiert; die Gruppen erhalten entsprechend ihrer Gewässerzahl ein Budget. Die Zeitgrenze beträgt sieben Tage pro Worker-Lauf. Die vollständige Ausschöpfung aller Gewässer kann erhebliche API-Kontingente benötigen. Es werden keine kostenpflichtigen Kontingente automatisch nachgekauft.

## Logs, Ergebnisse und Fortsetzen

In einem zweiten Terminal:

```bash
cd /bioing/data/WaterPlace/skripts/solve/crawler_scripts/scripts/SOLVE_multisite_code_v1_1_0
lauf="$(cat "$HOME/solve-runs/elbe-current.txt")"
bash start-elbe-linux.sh status --workspace "$lauf"
tail -n 30 -F "$lauf"/logs/group-*.log
```

Nur Gruppe 09: `tail -n 80 -F "$lauf/logs/group-09.log"`. Strg+C beendet dabei lediglich die Logansicht. Gruppen enthalten mehrere Gewässer; die Zuordnung steht in `parallel.json` und den jeweiligen `config.json`.

| Datei/Verzeichnis im Workspace | Inhalt |
|---|---|
| `preparation_report.json` | eingeschlossene/ausgeschlossene IDs und Startquellen-Defizite |
| `accepted_start_sources.json` | tatsächlich übernommene Quellen pro Gewässer |
| `logs/group-XX.log` | laufende Meldungen einer Gruppe |
| `group-XX/research.sqlite` | persistente Aufgaben, Suchaufträge und Bewertungen |
| `group-XX/results/` | Recherchebericht und Ergebnisse pro Gewässerprofil |
| `group-XX/results/*/03b_manuelle_pruefung.csv` | unklare oder technisch blockierte Quellen zur manuellen Prüfung |
| `download_cache/` | gemeinsamer Download-Cache |
| `group-XX/last_run.json` | Abschlusszahlen des letzten Gruppenlaufs |
| `parallel_last_run.json` | Abschlussstatus der Worker |

Zum kontrollierten Beenden im **Laufterminal** einmal Strg+C drücken und auf Speichern/Export aller Worker warten. Keine zweite Kampagne gleichzeitig in demselben Workspace starten. Zum Fortsetzen denselben `run`-Befehl mit demselben `lauf` verwenden, **kein neues `prepare`**. Die gespeicherten Aufgaben bleiben erhalten. `status` trennt aktuelle Aufgabenstände von historischen Laufzählern.

Konfiguration und Gewässerprofile im begonnenen Workspace nicht nachträglich umschreiben: Die Profilkennung hängt auch von Themen und Kontext ab. Neue Startquellen oder eine andere Auswahl gehören in einen neuen Workspace.

## Startquellen gezielt nachrecherchieren

Das ist für den gelieferten Lauf nicht notwendig. Die Werkzeuge bleiben aber reproduzierbar im Repository:

- `revised/prepare_official_elbe_seeds.py`: amtliche Kataloge und genaue Kennungszuordnung.
- `revised/prepare_scientific_elbe_candidates.py`: öffentliche DataCite-/OpenAlex-Metadaten mit Titelabgleich. Das Ergebnis enthält ausschließlich Kandidaten; Erreichbarkeit und fachliche Eignung müssen anschließend geprüft werden. Provider-Pausen und echte Nulltreffer bleiben getrennt sichtbar.
- `revised/prepare_elbe_seeds.py`: Serper-Recherche, Inhaltsprüfung, optional Blablador, Fortsetzung aus gespeicherten Zwischenständen.
- `revised/elbe_campaign.py`: geprüfte Manifeste zusammenführen, Auswahl vorbereiten und Laufstatus lesen.
- `revised/check_blablador.py`: Zugang und benötigte Modell-Aliase vor dem Worker-Start prüfen.

Die jeweiligen `--help`-Ausgaben erläutern die Optionen. Rohantworten, PDF-Proben, persönliche Schlüssel und Laufdaten gehören in ein separates Arbeitsverzeichnis und werden nicht ins Repository übertragen.
