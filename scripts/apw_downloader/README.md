# APW Brandenburg: WRRL und elektronisches Wasserbuch

Eigenständiger, sequenzieller Python-Downloader für öffentlich zugängliche Daten.
Er liest **keine Zugangsdaten**, verändert **keine Datenbank** und greift nicht auf
einen privaten Server zu. Python 3.10 oder neuer; Windows und Linux vorgesehen.

## Was heruntergeladen wird

- **WRRL:** die fünf Feature-Typen des offiziellen WRRL3-WFS:
  Oberflächenmessstellen, Grundwassermessstellen, Fließgewässer-, See- und
  Grundwasserkörper. Original-Geometrien und sämtliche Quellattribute bleiben
  erhalten. Die Gesamteinstufungen `ECO_STAT`, `ECO_POT` und `CHEM_STAT` werden
  zusätzlich in einer Bewertungstabelle gespeichert.
- **Wasserbuch:** öffentliche Recherche nach Wasserbehörde in der APW;
  ausschließlich tatsächlich verlinkte Wasserbuch-PDFs. Keine erratenen
  PDF-Nummern, kein Login, keine privaten Schnittstellen.
- **Auswertung:** Einträge, Sachverhalte, Nutzungen, Standorte und Vorgaben
  werden getrennt gespeichert. Unklare Angaben gehen in eine Prüfliste.

**Nicht enthalten:** sämtliche anderen APW-Themen, Messwert-Zeitreihen,
WRRL-Maßnahmenprogramme/Schutzgebietsebenen außerhalb dieses WFS, vollständige
Genehmigungsbescheide oder ein Import in PostgreSQL/Neo4j. Im veröffentlichten
Grundwasserkörper-Layer fehlen die entsprechenden Zustandsattribute; sie werden
nicht ergänzt oder erfunden.

## Installation

Die Befehle in diesem Ordner (`scripts/apw_downloader`) ausführen.

### Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Falls Chromium fehlende Linux-Systembibliotheken meldet, können diese mit
`python -m playwright install-deps chromium` installiert werden. Dafür sind
ggf. Administratorrechte erforderlich. Diese Einrichtung wurde nicht auf deinem
Linux-Server ausgeführt.

### Windows / PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

In den folgenden Beispielen unter Windows `python` ggf. durch
`.\.venv\Scripts\python.exe` ersetzen. Für **WRRL allein** reicht die
Python-Standardbibliothek. Für PDFs benötigt man `pdfplumber`; Playwright wird
nur für die automatische öffentliche Link-Recherche benötigt.

## Zunächst ein kleiner Pilot

```bash
python apw.py all --snapshot potsdam-pilot --authority "UWB Stadt Potsdam" --max-features 10 --max-documents 3
```

`--max-features` gilt **pro WRRL-Ebene**. `--max-documents` begrenzt die Zahl
der heruntergeladenen PDFs, nicht die Suche nach Links. `--authority` schränkt
diese Suche ein; die Schreibweise muss der öffentlichen Datenvorschau entsprechen.
Das ist ein Pilot für diese Behörde, **kein räumlicher Ausschnitt aller Daten in
Potsdam**: Die WRRL-Stichprobe enthält die ersten Features der jeweiligen Ebene.

Standard-Ausgabe: `data/<snapshot>/` neben dem Skript. Der Ordner ist durch die
mitgelieferte `.gitignore` von Git ausgeschlossen.

Ein Test auf deinem Linux-Server mit eigenem Zielordner:

```bash
python apw.py all --snapshot potsdam-pilot --authority "UWB Stadt Potsdam" --max-features 10 --output /bioing/data/WaterPlace/data/apw_brandenburg
```

Bei Problemen mit der Browser-Oberfläche kann `--headed` auf einem Rechner mit
grafischer Oberfläche verwendet werden. Der Downloader meldet unvollständige
Suchergebnisse als Fehler, statt einen vollständigen Abruf zu behaupten.

## Gesamtabruf für den beschriebenen Umfang

```bash
python apw.py all --snapshot bestand-2026-09 --output /bioing/data/WaterPlace/data/apw_brandenburg
```

Ohne `--authority` liest das Programm die öffentlich angebotene Behördenliste
und fragt jede Behörde einmal ab. Vollständigkeit bezieht sich **nur auf diese
öffentliche Liste und die angezeigten Ergebnisse**, nicht auf alle tatsächlich
erteilten Wasserrechte. Der Registerbestand und die öffentliche Ansicht können
unvollständig sein. Enthält eine Behördenabfrage mehr Objekte als auslesbare
Ergebnisverweise, bricht die Discovery mit gespeicherter Diagnose ab; es gibt
keine ungetestete Umgehung von Trefferlimits oder versteckten Seiten.

Die Abfragen laufen bewusst nacheinander, mit mindestens einer Sekunde Abstand
(Standard 1,5 Sekunden). `--delay 3` vergrößert den Abstand. Bei temporären
HTTP-Fehlern erfolgen begrenzte Wiederholungen und Pausen; `Retry-After` wird
beachtet. Eine länger als fünf Minuten geforderte Pause führt zu einem sauberen
Abbruch mit der Bitte, später fortzusetzen. Kein Hintergrundmonitor wird angelegt.

## Getrennte Abrufe / bereits bekannte öffentliche PDFs

```bash
python apw.py wrrl --snapshot wrrl-2026-09
python apw.py waterbook --snapshot wasserbuch-2026-09 --authority "UWB Stadt Potsdam"
python apw.py waterbook --snapshot einzelblatt --pdf-url https://apw.brandenburg.de/project/cardoMap/Documents/ewabu/wasserbuchblatt_10010000030.pdf
```

`--pdf-url` ist wiederholbar und benötigt keinen Browser. Ein vorhandener
`waterbook_index.json` kann mit `--index PFAD` wiederverwendet werden. Das
Indexformat ist:

```json
{
  "schema_version": 1,
  "scope": {"kind": "explicit_pdf_links"},
  "complete_for_scope": true,
  "records": [
    {
      "url": "https://apw.brandenburg.de/project/cardoMap/Documents/ewabu/wasserbuchblatt_10010000030.pdf",
      "fields": {}
    }
  ]
}
```

`complete_for_scope` besagt nur, dass die aufgeführten Links den angegebenen
Suchumfang abdecken. Es ist keine Aussage über das gesamte Wasserbuch.

## Fortsetzen, neue Datenstände und Fortschritt

- Abbrechen: `Strg+C`.
- Fortsetzen: **denselben Befehl mit demselben Ausgabeordner und Snapshotnamen**
  ausführen. Fertige Rohdateien werden anhand ihrer SHA-256-Prüfsumme geprüft
  und nicht erneut heruntergeladen.
- Ein Pilot-WRRL-Limit kann erhöht oder entfernt werden. Es wird ab der nächsten
  noch fehlenden Position weitergelesen. Der Wasserbuch-Suchumfang/Linkindex darf
  dagegen nicht mitten im Snapshot verändert werden.
- **Neuer Datenstand:** neuer `--snapshot`-Name. Ein Wiederanlauf ist kein
  Aktualisierungsabruf bereits gespeicherter Dokumente.
- `manifest.json` enthält je WRRL-Ebene Anzahl/erwartete Gesamtzahl und
  Vollständigkeit; beim Wasserbuch werden Download und PDF-Auslesung getrennt
  gezählt. Eine vorhandene Prüfliste ist kein Downloadfehler.
- Eine Betriebssystem-Sperre verhindert zwei gleichzeitige Schreiber im selben
  Ausgabeordner. Sie wird nach einem Absturz automatisch freigegeben.

```bash
python apw.py status --snapshot potsdam-pilot
python apw.py export --snapshot potsdam-pilot
```

`status` ist nach Ende/Abbruch des Downloads nutzbar. Während ein anderer Prozess
den Ausgabeordner gesperrt hat, kann `manifest.json` direkt gelesen werden.
`export` erzeugt Tabellen erneut **offline** aus den gespeicherten Rohdaten bzw.
PDF-Auswertungen. Erneutes `waterbook` liest auch bereits gespeicherte PDFs erneut
aus; so lassen sich Parserkorrekturen ohne erneuten Download anwenden.

Rückgabecodes: `0` = gewählter Umfang bearbeitet (kann ein Pilot sein),
`1` = Fehler/Unvollständigkeit, `130` = Benutzerabbruch. Fehler werden im Manifest
protokolliert. Bei einem nicht auswertbaren PDF werden weitere PDFs trotzdem
bearbeitet; am Ende meldet der Lauf Fehler, und das Original bleibt erhalten.

## Ausgabestruktur

```text
data/<snapshot>/
  manifest.json
  waterbook_index.json
  raw/
    wrrl/capabilities.xml, schema.xsd, <layer>/<page>.geojson
    waterbook/search/<query>.json
    waterbook/pdf/<waterbook_number>.pdf
  parsed/waterbook/<waterbook_number>.json
  tables/
    wrrl/water_bodies.csv
    wrrl/monitoring_stations.csv
    wrrl/assessments.csv
    wrrl/coverage.json
    waterbook/entries.csv
    waterbook/cases.csv
    waterbook/uses.csv
    waterbook/sites.csv
    waterbook/limits.csv
    waterbook/review.csv
    waterbook/coverage.json
```

CSV: UTF-8 mit BOM, Semikolon, Dezimalpunkt. Quellkennungen sind **Text**;
beim Öffnen in Excel die Spaltentypen entsprechend einstellen. Textwerte mit
Formelpräfix werden zur Sicherheit mit einem Apostroph versehen. Für einen
verlustfreien späteren Maschinenimport Original-GeoJSON und `parsed/*.json`
verwenden. JSON enthält Originalattribute, vollständigen PDF-Text, Fundseiten
und die Positionsnachweise der extrahierten Felder/Tabellen.

## Fachliche Regeln für den späteren Graphimport

- **Keine Messwerte aus Limits erzeugen.** Die Wasserbuch-Vorgaben bleiben
  Mengen-, Konzentrations- oder sonstige Vorgaben. Eine leere numerische Angabe
  wird `null`, nicht `0`.
- **Bewertungscodes bleiben Rohcodes.** WRRL-Statuswerte werden noch nicht in
  Klartext übersetzt; der jeweilige offizielle Codekatalog muss vor dem Import
  zugeordnet werden. `999` wird als ungültiger Sonderwert markiert. Die übrigen
  Codes sind nicht automatisch gegen eine vollständige Codeliste validiert;
  `is_valid` bleibt für sie zunächst leer, nicht `true`.
- Der WRRL3-Dienst beschreibt den Bewirtschaftungszyklus **2022–2027** und nennt
  den Datenstand **22.12.2021**. Das Abrufdatum ist kein Bewertungsdatum.
- WFS-Paging ist unterstützt, `sortBy` wurde beim Live-Test aber abgewiesen.
  Das Programm prüft deshalb Quellkennungen auf Überschneidungen und vergleicht
  die Gesamtanzahl vor/nach dem Abruf. Ein atomarer serverseitiger Snapshot ist
  damit nicht garantiert; Änderungen mit gleichbleibender Anzahl bleiben möglich.
- Der Dienst liefert ohne `srsName` GeoJSON mit Längengrad/Breitengrad in
  `EPSG:4326`. Eine explizite 4326-URN lieferte beim Test vertauschte Achsen.
  Der Downloader verwendet daher die überprüfte Anfrageform und kontrolliert
  die Koordinaten anhand eines großzügigen Brandenburg-Umgebungsbereichs.
- PDF-Koordinaten behalten ihr ausdrücklich angegebenes CRS. Keine automatische
  räumliche Verknüpfung und insbesondere keine Gleichsetzung mit einer Messstelle.
- Ein Blatt kann mehrere Sachverhalte, Nutzungen, Standorte und Vorgaben enthalten.
  Die Zwischenebene `cases` verhindert, dass Vorgaben willkürlich einer Nutzung
  zugeordnet werden. `use_id` am Limit wird nur bei genau einer Nutzung im
  betreffenden Sachverhalt gesetzt; sonst bleibt sie leer und landet in der Prüfung.
- Gültigkeitsdaten der Erlaubnis und einzelner Vorgaben bleiben getrennt.
  Fehlendes Enddatum bedeutet **nicht angegeben**, nicht automatisch unbefristet.
- Der PDF-Parser verwendet die beobachtete öffentliche eWaBu-Vorlage. Andere
  Layouts, Scans und unlesbare/ungewöhnliche Tabellen benötigen Prüfung; sie
  werden nicht mittels erfundener Werte vervollständigt. `extracted` ist keine
  behördliche oder rechtliche Validierung. Unbekannte Details bleiben im Original.
- Nach einem neuen Datenstand Quellobjekte anhand Fachkennungen und Herkunft
  zusammenführen, nicht anhand lokaler `OBJECTID`-Werte. Quell-IDs dieser Tabellen
  müssen im Graphimport zusammen mit Dataset/Snapshot betrachtet werden.

## Aufbau des Codes

| Datei | Aufgabe |
|---|---|
| `apw.py` | Befehle, Optionen, Fortschritt und Fehlerbehandlung |
| `common.py` | HTTPS-Abrufe, Pausen, Prüfsummen, atomisches Speichern, Sperre |
| `wrrl.py` | WFS-Seiten laden, prüfen und Tabellen erzeugen |
| `waterbook.py` | öffentliche Link-Recherche, PDF-Downloads, Tabellenexport |
| `waterbook_pdf.py` | PDF-Felder und Tabellen anhand ihres Layouts auswerten |
| `test_apw.py` | Offline-Tests und optionale lokale PDF-Regressionstests |

## Tests / überprüfter Stand

```bash
python -m unittest -v test_apw
```

Die Tests benötigen keine Netzverbindung. Zwei PDF-Regressionstests werden
übersprungen, wenn die während der Entwicklung ausdrücklich abgerufenen
Pilot-PDFs nicht unter `data/pilot/raw/waterbook/pdf/` vorhanden sind. Die PDFs
werden nicht mit Git verteilt.

Live auf Windows geprüft: insgesamt 20 Features aus allen fünf WFS-Ebenen und
zwei öffentliche PDFs (`10010000030`, `10190000003`). Ein Wiederanlauf erweiterte
die WRRL-Stichprobe ohne Änderung der zuvor gespeicherten Rohdateien. Die
PDF-Ergebnisse wurden auch visuell gegen beide Originaldokumente geprüft.
Die öffentlichen Browser-Schritte und Ergebnisfelder wurden in der APW geprüft.
Der eigenständige Python-Playwright-Prozess ist noch **nicht** Ende-zu-Ende
getestet. Ebenso sind ein landesweiter Discovery-Lauf und die Linux-Ausführung
noch ungetestet. Deshalb zuerst den Pilot starten. Die lokale Testsuite umfasst
33 erfolgreiche Tests (einschließlich der beiden lokal vorhandenen PDF-Stichproben).

## Quellen und Nutzung

- [Offizieller Datenkatalog](https://umweltdaten.brandenburg.de/open-data/wasser)
- [WRRL-WFS](https://maps.brandenburg.de/services/wfs/wrrl3bwz_wfs?request=getcapabilities&service=wfs)
- [Öffentliches Wasserbuch](https://apw.brandenburg.de/?feature=showNodesInTree%7Cwabuwre%2Ctrue&th=wabuwre)
- [Informationen des LfU zum Wasserbuch](https://lfu.brandenburg.de/lfu/de/aufgaben/wasser/genehmigungen-abgaben-foerderung/wasserbuch/)
- [APW-Bedienungshinweise](https://apw.brandenburg.de/content.aspx?method=Help,ApplicationContent&t=63743330873442)
- [Playwright-Installation](https://playwright.dev/python/docs/browsers)

Die öffentliche APW nennt **Datenlizenz Deutschland – Namensnennung 2.0**.
Quelle: **Landesamt für Umwelt Brandenburg**. Quelle, Lizenzverweis, Rohdatei,
Prüfsumme und Abrufdatum werden aufbewahrt. Quellen-/Layerhinweise und besondere
Zugriffsbeschränkungen bleiben zu beachten. Das Skript akzeptiert keine zusätzlichen
Dialogbedingungen automatisch und erschließt keine geschützten Daten.
