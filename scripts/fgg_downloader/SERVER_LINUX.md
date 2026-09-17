# Parallelabruf auf einem Linux-Server

`parallel_download.py` unterstützt **1 bis 30 gleichzeitig arbeitende
Download-Worker**. Technisch sind dies Threads in einem Python-Prozess, keine
Jupyter-Kernel und keine 30 CPU-Prozesse. Die Arbeit wartet überwiegend auf HTTP
und serverseitige CSV-Erstellung. Dafür werden keine 30 CPU-Kerne benötigt.
Der bisherige sequenzielle Einstieg `downloader.py` bleibt vorhanden.

Es wurde kein Server gesucht, verbunden oder eingerichtet. Die Tests laufen
offline auf dem lokalen Windows-Rechner; Linux-Betrieb und Portal-Durchsatz
mit 30 Workern sind noch nicht live geprüft.

## Voraussetzungen

- Python 3.10 oder neuer; nur Standardbibliothek, kein `pip install` nötig.
- Schreibbarer Datenordner auf **lokaler Serverplatte**. Nicht auf NFS/SMB und
  nicht von mehreren Rechnern gleichzeitig verwenden: SQLite und die
  Prozesssperre sind keine verteilte Warteschlange.
- Zustimmung zu den [Portalbedingungen](https://www.elbe-datenportal.de/FisFggElbe/content/statisch/nutzungsbedingungen.jsp).
  `--accept-terms` bestätigt die Zustimmung für diesen Abruf. Die Daten dürfen
  dadurch nicht automatisch veröffentlicht oder an Dritte weitergegeben werden.
- Ausreichend Platz für Originalexporte, SQLite und Protokolle. Es gibt keine
  automatische Löschung alter Daten.

Mindestens diese fünf Dateien zusammen in einen Serverordner kopieren:
`parallel_download.py`, `parallel_recovery.py`, `downloader.py`, `portal.py`,
`SERVER_LINUX.md`. Für Tests zusätzlich `test_downloader.py`, `test_parallel.py`
und `test_parallel_recovery.py` übernehmen.
Alternativ den veröffentlichten Branch aus GitHub klonen:

```bash
git clone --branch codex/fgg-downloader-server https://github.com/PolarTerrestrialEnvironmentalSystems/solve.git solve
cd solve/scripts/fgg_downloader
python3 -m unittest -v
```

Dieser Ordner enthält Code, Tests und Dokumentation, keine bereits geladenen
FGG-Exporte oder Sitzungscookies. Für die Wiederaufnahme eines bestehenden
Downloads den Datenordner weiterhin separat und privat übertragen (siehe unten).
Die übrigen Dateien des SOLVE-Repositories bleiben von diesem Downloader
unverändert; es findet kein Import in SOLVE oder Neo4j statt.

Der gewünschte Datenordner ist `/bioing/data/WaterPlace/data/fgg_elbe`.
Er ist unter Linux der Standard für `parallel_download.py`; mit `--output`
lässt er sich weiterhin überschreiben. Auf Windows bleibt ein ausdrücklicher
`--output`-Pfad erforderlich, damit dort kein Linux-Pfad fehlinterpretiert wird.
`/srv/fgg_downloader` ist weiterhin ein **Beispiel** für den Skriptordner.
Auf dem Server wurden keine Ordner angelegt und keine Daten verschoben.

## Vorhandenen Fortschritt übernehmen

1. Den alten Downloader beenden und warten, bis sein Prozess wirklich beendet
   ist. Bei einer synchronen Exportantwort kann das bis zur HTTP-Zeitgrenze dauern.
   Ein STOP-Signal alleine beweist noch nicht, dass der Prozess bereits beendet ist.
2. Den **gesamten** Ordner `dummy_data/fgg_data` einschließlich `_state`,
   `jobs.sqlite`, eventuell vorhandener `jobs.sqlite-wal`/`jobs.sqlite-shm`,
   eigener Sitzungscookies, Abfragebelege und Originaldateien privat auf den
   Server übertragen. Die Quelle muss während der vollständigen Kopie ruhen.
   Nicht nur die CSV-Dateien kopieren; keine zwei unabhängig weitergelaufenen
   Zustände zusammenmischen. Sitzungscookies nicht in Git oder öffentliche Ablagen.
3. Die gespeicherten Windows-Verzeichnispfade lokal im **kopierten** Stand auf
   Linux umstellen. Das stellt noch keine Portalanfrage:

   ```bash
   cd /srv/fgg_downloader
   python3 parallel_download.py relocate --output /bioing/data/WaterPlace/data/fgg_elbe
   python3 downloader.py verify --output /bioing/data/WaterPlace/data/fgg_elbe
   ```

   `relocate` prüft die kopierten Auftragsordner und Metadaten, schreibt einen
   Umzugsbeleg und ändert nur Verzeichnispfade in SQLite. Es verschiebt oder
   verändert keine Originaldateien. `verify` prüft anschließend Prüfsummen und
   Zeilenzahlen aller bisher akzeptierten CSVs; das ist kein Nachweis, dass
   schon der gesamte Abruf abgeschlossen ist.
4. Erst dann den Download auf dem Server fortsetzen. Den alten Rechner nicht
   gleichzeitig mit demselben Stand weiterladen lassen.

Ungeklärte Exporte bleiben ungeklärt. Besonders beim aktuellen Altstand gibt es
einen am 11.09. unterbrochenen Pollingvorgang. Der Parallelstarter versucht zuerst
die bekannte Warteadresse in der ursprünglichen eigenen Sitzung. Ist diese
nicht mehr verwendbar, stoppt er vor neuen Exporten zur manuellen Prüfung.
Ein Serverumzug repariert keine abgelaufene Portalsitzung und wiederholt keinen
ungeklärten Export automatisch.
Für alte parallele Aufträge mit einer neu empfangenen, bekannten Fehlerseite
gibt es den unten beschriebenen ausdrücklichen Wiederherstellungsmodus.

Für einen **neuen** vollständigen Abruf ist keine Pfadumstellung nötig. Ein neuer
Datenordner beginnt aber bei null und kann bereits lokal geladene Daten erneut
abfragen; nur derselbe übernommene Zustand kennt die bisherigen Erfolge.

## Start und Wiederaufnahme

Im Skriptordner, zunächst beispielsweise mit zwei Workern:

```bash
python3 -u parallel_download.py resume \
  --output /bioing/data/WaterPlace/data/fgg_elbe --accept-terms --workers 2
```

Mit bis zu 30 gleichzeitig arbeitenden Workern:

```bash
python3 -u parallel_download.py resume \
  --output /bioing/data/WaterPlace/data/fgg_elbe --accept-terms --workers 30
```

Ohne `--workers` bleibt es bei einem Worker. `resume` entfernt ausdrücklich die
eigene STOP-Markierung; `run` respektiert eine bestehende STOP-Markierung.
Ein bereits laufender Downloader im selben Ordner führt zu einer Fehlermeldung,
nicht zum Start einer zweiten Konkurrenzinstanz.

Da der Datenordner unter Linux voreingestellt ist, genügt dort auch:

```bash
python3 -u parallel_download.py resume --accept-terms --workers 30
```

Die Hilfsbefehle in `downloader.py` behalten ihre bisherigen Standardwerte;
für `verify`, `progress` und `stop` deshalb weiterhin den Datenordner ausdrücklich
mit `--output` angeben, wie in den Beispielen unten bzw. oben.

Nach Verbindungsende weiterlaufen lassen, wenn `nohup` auf dem Server vorhanden ist:

```bash
nohup python3 -u parallel_download.py resume \
  --output /bioing/data/WaterPlace/data/fgg_elbe --accept-terms --workers 30 \
  > parallel-console.log 2>&1 < /dev/null &
```

Dieser Befehl ersetzt `parallel-console.log`; das eigentliche dauerhafte
Downloadprotokoll wird unter `/bioing/data/WaterPlace/data/fgg_elbe/_state/download.log` angehängt.
Es gibt keinen automatischen Neustart nach einem Server-Neustart oder Fehler.
Eine SSH-Verbindung zum Server wurde hier nicht eingerichtet.

## Fortschritt, Stoppen und Fortsetzen

```bash
python3 downloader.py progress --output /bioing/data/WaterPlace/data/fgg_elbe
python3 downloader.py stop --output /bioing/data/WaterPlace/data/fgg_elbe
```

Fortschritt steht auch in `fortschritt.txt` und `fortschritt.json`, inklusive
Aufträgen und Worker-Nummern. Der Koordinator aktualisiert die Anzeige während
paralleler Aufträge ungefähr alle fünf Sekunden; Initialisierung und einzelne
Katalogabfragen können länger ohne neue Anzeige dauern. Eine unveränderte
CSV-Zahl bedeutet nicht zwingend Stillstand.

Stoppen geht alternativ mit Strg+C oder SIGTERM. Es werden keine neuen Aufträge
mehr verteilt. Laufende HTTP-Aufrufe dürfen erst ihre Antwort bzw. Zeitgrenze
erreichen; bekannte Warte-/Ergebnisadressen werden gespeichert. Standardmäßig
gelten 3.600 Sekunden für die erste Exportantwort und separat für das Polling.
Kein erzwungenes `kill -9`, wenn der sichere Haltepunkt noch erreichbar ist.

Die nächste Wiederaufnahme erfolgt erneut mit `parallel_download.py resume`.
Eine kleinere Worker-Anzahl ist möglich: Alle gespeicherten Sitzungs-Slots werden
zuerst mit begrenzter Parallelität abgearbeitet, auch wenn ihre Nummer größer
als die neue Worker-Anzahl ist. Erst danach werden neue Aufträge verteilt.
Der sequenzielle Starter und dessen `retry-errors`/`refine`/`defer-uncertain`
blockieren nach Nutzung paralleler Sitzungen schreibende Aktionen, damit kein
Auftrag mit der falschen Sitzung bearbeitet wird. Solche Problemkorrekturen
benötigen dann eine sitzungsbezogene manuelle Prüfung. `progress`, `status`,
`stop` und `verify` bleiben nutzbar; `verify` nur ohne laufenden Downloader.

## Alte parallele Exporte mit Fehlerseite zurückstellen

Wenn `resume` beim Abrufen einer alten Warteadresse eine Portal-Fehlerseite mit
`java.lang.NullPointerException` oder `Sitzung verloren` erhält, hilft ein höheres
Timeout nicht gegen diese konkrete Antwort. Eine NullPointerException beweist
allerdings weder eine abgelaufene Sitzung noch den Abbruch der Serverberechnung.
Der Standard bleibt daher: stoppen, nicht erneut beauftragen.

Ein zweiter beobachteter Fall ist eine zurückgesetzte Auswahlseite statt des
Exportergebnisses: Millionen Treffer, fehlende Pflichtauswahlen und die Meldung
zur Grenze von 10.000 Messwerten. Der Modus erkennt dies nur bei einer eindeutigen
bekannten Exportmaske, einer Trefferzahl über 10.000, mindestens zwei bekannten
Pflichtfeldmeldungen und mindestens einem ursprünglichen Filter, dessen Feld
nun ausdrücklich leer bzw. `keine Auswahl` ist. Ein nur fehlendes/umbenanntes
Formularfeld, eine andere Filterauswahl oder eine geänderte Trefferzahl allein
reichen nicht. Der ursprüngliche Auftrag muss höchstens 10.000 Werte erwarten.
Ein solcher Befund erhält den Belegtyp `saved_selection_reset`; erwartete und
aktuelle Trefferzahl sowie die verlorenen Filter werden mitgesichert.
Auch daraus wird **kein neuer Export der ungefilterten Millionen Treffer** erzeugt.

Seit der Wiederherstellungserweiterung kann man **ausdrücklich erlauben**, solche
Altaufträge zurückzustellen und die übrigen Aufträge in frischen Sitzungen
weiterzuladen. Nur starten, wenn kein anderer Downloader denselben Datenordner
benutzt. Im aktuellen Git-Checkout auf Branch `codex/fgg-downloader-server`:

```bash
git pull --ff-only
python3 -m unittest -q
python3 downloader.py verify --output /bioing/data/WaterPlace/data/fgg_elbe
```

Bei Fehlern erst die Meldung prüfen, nicht mit dem nächsten Befehl fortfahren.
Dann im Ordner `scripts/fgg_downloader`:

```bash
python3 -u parallel_download.py resume \
  --output /bioing/data/WaterPlace/data/fgg_elbe \
  --accept-terms --workers 2 --export-timeout 10800 \
  --defer-unavailable-exports
```

Der Schalter gilt nur für `resume` und verlangt eine bereits vorhandene
`_state/jobs.sqlite`. Der Ablauf ist:

1. Vor Portalzugriffen wird unter `_state/recovery_backups/<Zeitstempel>_<ID>/`
   ein konsistenter SQLite-Snapshot einschließlich WAL-Inhalt und eine Kopie
   aller downloader-eigenen Cookie-Dateien gesichert. Eine Prüfsummenliste
   dokumentiert die Dateien. Das ist **keine Kopie aller Original-CSVs**.
   Scheitert die Sicherung, wird nicht weitergemacht.
2. Jeder gespeicherte parallele Auftrag wird zuerst über seine bisherige
   Warteadresse und Sitzung abgefragt, auch Worker-Slots größer als 2.
3. Nur bei einer **frisch empfangenen** der genannten Fehlerseiten oder einer
   wie oben abgesicherten zurückgesetzten Auswahlseite, ohne
   Ergebnislink und ohne Fortsetzungsadresse, kommt eine Zurückstellung infrage.
   Der ursprüngliche Export muss mindestens eine Stunde alt sein; gespeicherte
   Ergebnislinks, bereits geladene Zeilen und unklare Zeitstempel schließen sie
   aus. HTTP-/Netzwerkfehler, normale Wartezeiten, unbekannte Fehlerseiten sowie
   Fehler bei **neuen** Exporten werden nicht auf diese Weise übersprungen.
4. Fehlerantwort (inklusive HTML), Auftragsdaten und Sitzungszuordnung werden
   als `recovery_response_*.json` und `deferred_receipt_*.json` im Auftragsordner
   gesichert. Die alte Cookie-Datei bleibt erhalten. Eine atomare SQLite-Änderung
   setzt den Auftrag auf `deferred_uncertain` und wählt für den Worker eine neue,
   eigene Cookie-Datei. Ein Absturz vor dem Freigeben des Slots ist wiederaufnehmbar.
5. Erst nach der Behandlung der Altaufträge werden weitere Downloads verteilt.
   Fertige Exporte bleiben unangetastet; zurückgestellte Exporte werden **nicht**
   erneut angefordert. Der Versuchszähler bleibt unverändert.

`fortschritt.txt` nennt Zahl und erwartete Messwerte der zurückgestellten Exporte.
In `fortschritt.json` stehen sie unter `deferred_exports`,
`deferred_expected_rows` und weiterhin einzeln in `problems`; auch
`dateiuebersicht.csv` enthält die Aufträge. Sie erhöhen **nicht** den Fortschritt
und verhindern weiterhin `all_complete: true`.

Dieser Modus lädt also die **übrigen** Daten weiter, er schließt die offenen
Lücken nicht automatisch. Deren spätere Prüfung oder erneute Beauftragung ist
eine separate, ausdrückliche Entscheidung. Nicht per SQL auf `pending` setzen,
nicht mit `retry-errors` umgehen und nicht `_state` oder Cookies löschen.
Eine Zurückstellung storniert keine möglicherweise noch laufende Portalberechnung.

Sicherungen und Belege können Sitzungsinformationen enthalten: privat aufbewahren,
nicht in Git aufnehmen oder öffentlich teilen. Nach einer Sitzungsrotation nicht
auf den alten Downloader zurückwechseln, der die neue Zuordnung noch nicht kennt.
Ein normaler späterer `resume` verwendet mit dem aktualisierten Code die gespeicherte
Zuordnung und lässt bereits zurückgestellte Aufträge als offene Lücken bestehen.

## Lastbegrenzung und Aufbau

- Eine atomare SQLite-Transaktion reserviert einen Auftrag für genau einen Slot.
  Es gibt keine zeitbasierte Freigabe vermeintlich langsamer Worker.
- Jeder Slot besitzt eine getrennte eigene Cookie-Datei, einen eigenen
  HTTP-Client und eine im jeweiligen Thread geöffnete SQLite-Verbindung.
- Auftragsaufteilung, Originaldateien, Zeilenzählung, Filterprüfung und
  Prüfsummen nutzen dieselbe vorhandene Downloadlogik.
- Nur der Koordinator schreibt die gemeinsamen Fortschritts- und Indexdateien.
  Den Messstellenkatalog bearbeitet er nach den Messwertaufträgen seriell.
- Der Mindestabstand `--delay 1.5` gilt **global für alle Worker zusammen**, auch
  für Wiederholungen und Weiterleitungen. Zum vorsichtigeren Betrieb lässt sich
  beispielsweise `--delay 3` setzen; weniger als eine Sekunde ist gesperrt.
- Bei HTTP 429/503 gilt eine gemeinsame Pause von mindestens 60 Sekunden bzw.
  längerem `Retry-After` (Sekunden oder HTTP-Datum). Bereits laufende Serverexporte
  werden dadurch nicht storniert. Ein Export-POST wird nicht automatisch wiederholt.
- Ungeklärte Exportantworten stoppen standardmäßig die weitere Verteilung; andere laufende
  Worker erreichen zuerst sichere Haltepunkte. Drei aufeinanderfolgende als
  Netzwerkfehler abgeschlossene Aufträge stoppen ebenfalls den Pool.
  Nur der ausdrücklich aktivierte Modus oben behandelt bestimmte Altaufträge anders.

Die Portalbedingungen nennen keine konkrete erlaubte Zahl paralleler Exporte.
30 ist eine technische Obergrenze dieser Implementierung, keine Zusicherung des
Betreibers oder eine garantierte Beschleunigung um Faktor 30. Die globale Drossel
begrenzt Anfragestarts, aber nicht die Last von bereits laufenden CSV-Berechnungen.
Vor einem dauerhaft hohen Parallelbetrieb Kapazität/Bulk-Zugang mit dem Betreiber
klären und zunächst mit wenigen Workern prüfen. Es werden keine Begrenzungen des
Portals umgangen; die Obergrenze von 10.000 Messwerten pro Export bleibt bestehen.

## Offline-Tests auf dem Server

```bash
cd /srv/fgg_downloader
python3 -m unittest -v
```

Die Paralleltests prüfen unter anderem 30 gleichzeitig aktive simulierte Worker,
eindeutige SQLite-Reservierungen, getrennte Sitzungen, Wiederaufnahme ohne neuen
Export, Stoppen, globale Drosselung und den Datenordner-Umzug. Sie kontaktieren
weder das Portal noch andere Server. Ein echter 30-Worker-Lasttest wurde nicht
durchgeführt.

Die Wiederherstellungstests simulieren zusätzlich Fehlerseiten, alle 30 alten Slots
mit zwei Workern, das Weiterladen in frischen Sitzungen, unveränderte CSVs,
sichtbare Datenlücken, SQLite-Backups, Schreibfehler und Absturzzeitpunkte.
Zusätzlich prüfen sie zurückgesetzte Auswahlseiten gegen die ursprünglichen
Filter, unveränderte bzw. lediglich andere Filter, unklare Formulare,
abweichende Trefferzahlen und vorhandene Ergebnis-/Warteadressen.
Sie kontaktieren weder das Portal noch den Linux-Server.
