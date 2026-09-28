# Parallele Recherche auf Linux

Voreinstellung: **30 Worker für 33 Seen**. Drei Gruppen enthalten jeweils zwei
Seen, die übrigen 27 jeweils einen. Jeder Worker
bearbeitet eine feste Gruppe von Seen in einem eigenen Dossier. LLM-Prüfungen,
Suchanfragen und Dokumentverarbeitung können gleichzeitig laufen. Originaldownloads
werden über einen gemeinsamen Cache absichtlich nacheinander ausgeführt. So werden
bereits bekannte identische URLs und Weiterleitungen wiederverwendet; Wartezeiten
zwischen Abrufen desselben Hosts gelten gruppenübergreifend. Unterschiedliche URLs
mit identischen Bytes sind erst nach dem Abruf als inhaltsgleich erkennbar.

## 1. Übertragen und installieren

Den Projektordner mit `revised/`, `inputs/` und den beiden `.sh`-Dateien auf die VM
kopieren, beispielsweise nach `~/solve-crawler`. Die Windows-`.venv` wird nicht
benötigt. Das **gestoppte** bisherige Dossier vollständig einschließlich
`research.sqlite`, eventuell vorhandener `-wal`/`-shm`-Dateien, `archive/` und
`metadata/` übertragen. Keine laufende SQLite-Datei mit Dateikopieren übertragen.
Quell- und neues Paralleldossier müssen getrennte Ordner sein. Dossiers und Cache
auf der lokalen VM-Platte ablegen, nicht auf einem SMB-/NFS-Netzlaufwerk.

```bash
sudo apt update
sudo apt install python3-venv poppler-utils tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng tmux
cd ~/solve-crawler
bash setup-linux.sh
```

Die vorhandene `.env` mit `SERPER_KEY` und `BLABLADOR_KEY` separat z. B. nach
`~/.config/solve/keys.env` kopieren und mit `chmod 600 ~/.config/solve/keys.env`
schützen. Schlüssel werden nicht in die erzeugten Konfigurationen geschrieben.
Der bisherige Windows-Pfad zur `.env` wird im Parallelmodus ersetzt.

## 2. Bestehenden Stand einmalig übernehmen

Im Beispiel liegt das bisherige Dossier direkt im Projektordner:

```bash
cd ~/solve-crawler
bash start-parallel-linux.sh prepare \
  --source-workspace ./dossier_33_seen_serper_v1_6_2_20260925 \
  --workspace ./dossier_33_seen_parallel \
  --groups 30
```

`prepare` benötigt keine API-Aufrufe. Es erstellt eine konsistente Kopie und
übernimmt Suchaufträge, Aufgabenstatus, ausgewählte Startlinks, Bewertungen und
Originaldateien. Die ursprüngliche Datenbank wird nicht verändert. Das Programm
lehnt eine Übernahme ab, solange der ursprüngliche Crawler das Dossier sperrt.
Für die Kopien ausreichend freien Platz einplanen: zusätzlich ungefähr
`(Gruppenzahl + 1) × (Datenbank + Originale + Metadaten)`.

Die Arbeitsdatenbanken enthalten jeweils den vollständigen Ausgangsstand, bearbeiten
und exportieren aber nur ihre zugeteilten Seen. Deshalb die Zahl der Zeilen in allen
Gruppendatenbanken **nicht addieren**. Eine Datei `parallel.json` markiert die
abgeschlossene Vorbereitung. Bei einem Vorbereitungsfehler ein neues Ziel verwenden.

Ohne `--source-workspace` entsteht eine neue Recherche ohne den bisherigen Stand.
Die 33-Seen-Konfiguration überspringt die Startlink-Auswahl weiterhin. Für eine ganz
neue Recherche bei Bedarf eine eigene Konfiguration mit `skip_selection: false`
über `--config` verwenden.

## 3. Starten, beobachten und fortsetzen

```bash
tmux new -s solve
cd ~/solve-crawler
bash start-parallel-linux.sh run \
  --workspace ./dossier_33_seen_parallel \
  --workers 30 \
  --llm-workers 2 \
  --env-file ~/.config/solve/keys.env
```

Mit **Strg+B**, dann **D** tmux verlassen: Der Lauf läuft nach dem Schließen von
PuTTY weiter. Zurück mit `tmux attach -t solve`.

In einem zweiten Terminal:

```bash
cd ~/solve-crawler
tail -f dossier_33_seen_parallel/logs/group-*.log
```

Mit **Strg+C im Starter** alle aktiven Worker zum Stoppen auffordern und bis zum
Abschluss der Exporte warten. Die gerade laufende Aufgabe wird noch abgeschlossen;
bei langsamen API-Aufrufen kann das bis zum jeweiligen Timeout dauern.
Danach mit exakt demselben `run`-Befehl fortsetzen.
Nicht erneut `prepare` ausführen. Bei einem harten Prozessabbruch bleiben bereits
committete Daten erhalten; eine gerade laufende Aufgabe kann wiederholt werden.
Das alte Windows-Dossier wird nicht mit den neuen Ergebnissen zurücksynchronisiert.

## Ergebnisse und Grenzen

- Ergebnisse: `dossier_33_seen_parallel/group-01/results/` bis `group-30/results/`.
  Darunter liegen wie bisher die CSVs pro See einschließlich Human-Review-Liste.
- Originaldateien: `group-XX/archive/`; gemeinsamer Cache: `download_cache/`.
- Fortschritt/Fehler: `logs/group-XX.log`; letzter abgeschlossener Gruppenlauf:
  `group-XX/last_run.json`; Exit-Codes: `parallel_last_run.json`.
- Gruppenzuordnung bleibt für das Fortsetzen fest. `--workers 1` verarbeitet die
  vorhandenen Gruppen nacheinander; mehr Worker als Gruppen beschleunigen nichts.
  Ein bereits mit zwei Gruppen vorbereitetes Dossier erhält durch `--workers 30`
  keine zusätzlichen Gruppen. Dafür einmalig ein neues Ziel mit `--groups 30`
  aus dem ursprünglichen Dossier vorbereiten, bevor dort weitergearbeitet wurde.
- Gesamtbudgets für Aufgaben, Abrufe, Suchaufträge und LLM-Aufrufe werden beim
  Vorbereiten aufgeteilt. Ungenutztes Budget wird nicht automatisch umverteilt.
  Laufzeitlimits und Fehlergrenzen gelten je Gruppe; eine API-Störung kann deshalb
  mehrere Gruppen betreffen. Mehr Worker erhöhen die gleichzeitige API-Last und
  garantieren keine proportionale Beschleunigung.
- Die Voreinstellung ist für den Linux-Server mit mehr CPU-Kernen vorgesehen.
  Der Speicherbedarf hängt insbesondere von den gleichzeitig verarbeiteten PDFs
  und OCR-Aufträgen ab. RAM und Swap z. B. mit `free -h` beobachten. Bei Engpässen
  sauber stoppen und mit kleinerem `--workers` fortsetzen; die 30 Gruppen bleiben
  dabei erhalten. API-Limits gelten auch bei 30 Workern weiterhin.
- Bei Budgetänderungen die jeweilige `group-XX/config.json` im gestoppten Zustand
  anpassen; die alte Windows-Konfiguration steuert diese Gruppen danach nicht mehr.

## Gemeinsame Blablador-Begrenzung und Fortsetzen nach HTTP 429

Die Workerzahl ist unabhängig von der API-Parallelität. Standardmäßig laufen
höchstens zwei Chat-Anfragen gleichzeitig (`--llm-workers 2`), mit mindestens zwei
Sekunden Abstand zwischen Anfragestarts (`--llm-interval 2`). Diese Begrenzung gilt
für Startlink-Auswahl, Download-Vorprüfung und Inhaltsbewertung gemeinsam.
HTTP 429 setzt eine gemeinsame Pause von mindestens 60 Sekunden, die bei
wiederholtem 429 exponentiell bis auf 900 Sekunden steigt. Ein längeres vom Dienst
gemeldetes Retry-After wird ebenfalls eingehalten. Laufende Anfragen werden nicht
abgebrochen; danach starten bis zum Ablauf der Pause keine weiteren.

Slots und Wartezeit liegen unter `~/.cache/solve/llm-gate`. Aktualisierte Starter
mit denselben Einstellungen auf demselben Server und unter demselben Benutzer
teilen diese Begrenzung auch zwischen Dossiers. Alte Prozesse ohne diese Änderung
müssen zuvor gestoppt werden. Andere Nutzer, Rechner oder Anwendungen mit demselben
API-Schlüssel sind damit nicht abgedeckt. Die Einstellungen sind Startwerte und
garantieren nicht, dass keine weiteren 429-Antworten auftreten.

Zum Aktualisieren den Starter mit Strg+C beenden, die Worker einschließlich Export
fertig werden lassen und den neuen Code kopieren. Danach dasselbe Dossier mit
`run --workspace ... --workers 30 --llm-workers 2 --env-file ...` starten.
**Nicht erneut prepare ausführen.** Die offenen Aufgaben werden wieder berücksichtigt;
die Fehlerzähler gelten pro neuem Lauf. Ungültige optionale Suchvorschläge werden
mit Warnung verworfen, ohne ansonsten gültige Quellenbewertungen zu verlieren.
Gewässerzuordnung und Belegprüfung bleiben unverändert streng.

Die Linux-Starter verwenden portable Python-Prozesse und POSIX-Dateisperren.
Die Tests werden lokal ausgeführt; ein echter Lauf auf der Ziel-VM ist separat zu
prüfen. Der normale Windows-Einzelprozess-Starter bleibt verwendbar.
