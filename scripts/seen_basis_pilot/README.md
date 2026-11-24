# Amtliche Basisdaten: Pilot mit 30 Seen

Stand: 19.11.2026. Fünf WRRL-Seewasserkörper je Bundesland: Brandenburg,
Berlin, Sachsen-Anhalt, Sachsen, Mecklenburg-Vorpommern, Thüringen.
Auswahl aus der geprüften Elbgebietsregistrierung; Talsperren eingeschlossen.
Bleiloch 2 und Hohenwarte 2 sind die amtlich bezeichneten Wasserkörper,
nicht die Behauptung einer vollständigen Abbildung sämtlicher Teilbecken.

## Was wird tatsächlich geladen?

Für alle 30 ausgewählten Kennungen:

- EEA/WISE WRRL-Meldedaten für 2010, 2016 und 2022, jeweils separat.
- Gewässergeometrien, veröffentlichte Stammdaten und Zustandsattribute.
- Über die Gewässerkennung zugeordnete Monitoring-Messstellen.
- Qualitätskomponenten, sofern unter der exakten Kennung vorhanden.
- Vollständige Antwortattribute, Metadaten, URLs, Abrufzeiten, SHA256-Hashes.

Zusätzlich: Brandenburgs Original-ZIP-Pakete und WRRL-Steckbriefe,
sächsische WRRL-Steckbriefe, MV-Steckbriefe als HTML und Thüringens
landesweite OWK-Bewertungsliste. Der Thüringer Bericht ist explizit NICHT
auf den jeweiligen See gefiltert. Für den Müggelsee wird außerdem die
zuvor zugeordnete Berliner Messstellenseite 115 als Original-HTML geladen.
Die serverseitige HTTP-Verfügbarkeit wird bei jedem neuen Abruf geprüft;
das ist noch keine fachliche Freigabe des Inhalts eines Steckbriefs.

**Das Paket lädt Rohdaten; es ist kein vollständiger Import aller KG-Klassen.**
Messstellen-Metadaten sind keine Messreihen. EEA-Bewertungen sind keine
Einzelmessungen. ZIP-Inhalte werden validiert, aber nicht automatisch in das
V2-Schema normalisiert. `target_classes` dokumentiert die spätere Zielzuordnung.
Vorhandene PostgreSQL-/Neo4j-Instanzen und Crawler-Dossiers werden nicht verändert.
Events werden hier nicht neu recherchiert.

Ein vollständiger automatischer Messreihenexport für alle sechs Länder ist
NICHT implementiert. Insbesondere die bekannten LHW-Downloads sind
sitzungsgebunden. Pro See steht deshalb `manual_exports.json` mit Portal,
Arbeitsschritten und offenen Zielklassen bereit. Für Grundwasserkörper,
Einzugsgebiete, Schutzgebiete, Paläodaten und räumliche Verknüpfungen sind
weitere Adapter nötig. Die vorhandenen V2-Klassen werden nicht umgedeutet.

## Herkunft und historische Grenzen

EEA ist die veröffentlichende europäische Stelle für die nationalen
WRRL-Meldungen. Das ist ein dokumentierter Abrufweg und kein direkter
WasserBLIcK-Download. Die Berichtsjahre stehen nicht für Probenahmedaten.
Historische EEA-Geometrien können harmonisierte, später aktualisierte
Referenzgeometrien enthalten; sie rekonstruieren keine historischen Uferlinien.

Nur exakte Kennungen werden zusammengeführt. Frühere Kennungswechsel werden
nicht geraten. `empty_exact_id` bedeutet: keine Treffer für diese konkrete
Kennung in diesem Layer. Es bedeutet NICHT: es existieren keine Daten zum See.
Es gibt keine automatische Verknüpfung aufgrund ähnlich geschriebener Namen.

## Installation auf linux3

Nur Python >= 3.10 erforderlich, keine Zusatzpakete, kein sudo, kein API-Key.
Das Archiv enthält den Ordner `seen_basis_pilot`.

Windows PowerShell, nachdem das ZIP unter dem angegebenen lokalen Pfad liegt:

```powershell
scp "C:\Users\jowals001\Documents\ChatGPT\SOLVE\output\seen_basis_pilot.zip" jowals001@linux3:/bioing/data/WaterPlace/skripts/solve/
```

Auf dem Server:

```bash
cd /bioing/data/WaterPlace/skripts/solve
python3 -m zipfile -e seen_basis_pilot.zip .
cd seen_basis_pilot
python3 --version
bash start-linux.sh plan --output /bioing/data/WaterPlace/Basisdaten_Pilot_30_Seen/stand_20261119
```

## Start und Wiederaufnahme

Aus einem normalen SSH-Terminal zunächst `screen -S solve-basisdaten`.
Wenn `$STY` bereits gesetzt ist, bist du in Screen und brauchst keine weitere
verschachtelte Sitzung. Innerhalb von Screen:

```bash
cd /bioing/data/WaterPlace/skripts/solve/seen_basis_pilot
bash start-linux.sh run \
  --output /bioing/data/WaterPlace/Basisdaten_Pilot_30_Seen/stand_20261119 \
  --workers 4
```

Strg+A, dann D trennt Screen, ohne den Lauf zu beenden. Derselbe Startbefehl
setzt denselben Datenstand fort: erfolgreiche Antworten werden anhand des
Hashes wiederverwendet; fehlgeschlagene Abrufe werden erneut versucht.
Für einen frischen Abrufstand einen NEUEN Ausgabeordner verwenden.
Ein OS-Lock verhindert zwei gleichzeitige Läufe auf demselben Ausgabeordner.

Vier Threads sind für diesen Pilot ausreichend; pro Host startet höchstens
eine Anfrage pro Sekunde. Es gibt höchstens drei Versuche pro HTTP-Anfrage
mit Wartezeiten und einem Socket-Timeout von 45 Sekunden. Bei einem Fehler
arbeiten andere Seen weiter. Am Ende bedeutet Exitcode 2: mindestens ein
Abruf fehlgeschlagen. Die Details stehen in `summary.csv` und `summary.json`.
Strg+C kann auf bereits laufende Threads warten; gespeicherte Antworten bleiben.

**NFS-Hinweis:** `/bioing` ist auf deinem Server ein NFS-Mount. Dieses Paket
verwendet keine SQLite-Datenbank und keine gemeinsamen Blablador-Sperren,
aber eine NFS-Störung kann auch normale Dateizugriffe blockieren. Bei erneuten
Blockaden einen von der IT freigegebenen lokalen Scratch-Pfad als `--output`
verwenden und die Ergebnisse anschließend auf `/bioing` kopieren.

## Status und Varianten

```bash
bash start-linux.sh status --output /bioing/data/WaterPlace/Basisdaten_Pilot_30_Seen/stand_20261119
```

Nur neuester Berichtsstand: `--years 2022`.
Nur Brandenburg und Berlin: `--state BB --state BE`.
Nur ein See je Land zum Test: `--limit-per-state 1`.
Nur gemeinsame EEA-Grundlage: `--no-native`.
Die Standardauswahl bleibt 30 Wasserkörper; `lakes.json` listet sie vollständig.

## Ausgabe

```text
stand_20261119/
  campaign.json                     festgehaltene Auswahl
  summary.csv                       Excel-lesbar, Semikolon, UTF-8 BOM
  summary.json                      ausführliche Abrufübersicht
  _requests/<URL-Hash>/              wiederverwendbare Originalantworten
  BB/SCHARMUETZELSEE__DELW_DEBB.../
    selection.json                  Gewässeridentität
    2010/, 2016/, 2022/              GeoJSON und Quellenprotokolle
    landesdaten/                    PDF / ZIP / HTML-Originale
    status.json                     laufender Stand je See
    manual_exports.json             noch nötige manuelle Exporte
```

Downloads sind erst mit passender `.source.json` und Hash vollständig.
GeoJSON ist in EPSG:4326. Ein ZIP wird nicht unkontrolliert entpackt.
Leere Antworten, HTTP-/API-Fehler und heruntergeladene Rohdokumente werden
getrennt gezählt. Alte erfolgreiche Jahre bleiben beim Lauf mit `--years`
für andere Jahre in der Übersicht erhalten.

## Tests

```bash
python3 -m unittest discover -s . -v
```

Getestet werden Kennungen, abgeschnittene Antworten, falsche Dateiformate,
API-Fehler, Cache-Integrität und konkurrierende Läufe. Der beiliegende
TESTBERICHT.md nennt Umfang und Grenzen des tatsächlichen Live-Tests.
