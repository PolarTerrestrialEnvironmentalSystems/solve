# Wo liegen die Daten?

Stand der dokumentierten Einrichtung: 24.11.2026. Serverpfade beruhen auf
den bisherigen Befehlen und Angaben; keine erneute SSH-Bestandsprüfung.
Ein Git-Clone kopiert diese Daten nicht mit.

## Linux / Windows-Netzlaufwerk

| Bestand | Linux-Pfad | Windows bei der bisherigen Z:-Zuordnung |
|---|---|---|
| Amtlicher 30-Seen-Pilot | `/bioing/data/WaterPlace/Basisdaten_Pilot_30_Seen/stand_20261119/` | `Z:\bioing\data\WaterPlace\Basisdaten_Pilot_30_Seen\stand_20261119\` |
| Pilot-Skripte, manuell kopierte Installation | `/bioing/data/WaterPlace/Ergebnisse_Elbe/seen_basis_pilot/seen_basis_pilot/` | `Z:\bioing\data\WaterPlace\Ergebnisse_Elbe\seen_basis_pilot\seen_basis_pilot\` |
| Elbe-Crawler: aktives Originaldossier | `/net/pdm/home/jowals001/solve-runs/elbe_alle_20261110_130920/` | Nicht über die oben genannte Z:-Freigabe belegt |
| Elbe-Crawler: kopierte Ergebnisansicht | `/bioing/data/WaterPlace/Ergebnisse_Elbe/elbe_alle_20261110_130920/` | `Z:\bioing\data\WaterPlace\Ergebnisse_Elbe\elbe_alle_20261110_130920\` |
| Bisheriger Crawler-Code | `/bioing/data/WaterPlace/skripts/solve/crawler_scripts/scripts/SOLVE_multisite_code_v1_1_0/` | `Z:\bioing\data\WaterPlace\skripts\solve\crawler_scripts\scripts\SOLVE_multisite_code_v1_1_0\` |
| Ältere 33-Seen-Ergebnisse nach Gruppe | `/bioing/data/WaterPlace/data/Sachsen_Anhalt/scrape_test/Ergebnisse_33_Seen/` | `Z:\bioing\data\WaterPlace\data\Sachsen_Anhalt\scrape_test\Ergebnisse_33_Seen\` |
| Ältere 33-Seen-Ergebnisse nach See | `/bioing/data/WaterPlace/data/Sachsen_Anhalt/scrape_test/Ergebnisse_33_Seen_nach_Seen/` | `Z:\bioing\data\WaterPlace\data\Sachsen_Anhalt\scrape_test\Ergebnisse_33_Seen_nach_Seen\` |

Je nach Rechner wurde die Freigabe früher auch als X: eingebunden.
Der Laufwerksbuchstabe ist kein Bestandteil des Linux-Pfades.

### Aufbau des Pilot-Dossiers

`BB/`, `BE/`, `ST/`, `SN/`, `MV/`, `TH/` enthalten pro See einen Ordner mit
Name und amtlicher Kennung. Darin:

- `2010/`, `2016/`, `2022/`: GeoJSON, Quellen- und Abrufprotokolle.
- `landesdaten/`: verfügbare Original-ZIP-, PDF- und HTML-Dateien.
- `selection.json`: ausgewählte Identität und Quellen.
- `status.json`: bisheriger Abrufstand.
- `manual_exports.json`: noch nötige manuelle Messreihenexporte.

Im Hauptordner liegen `_requests/` als wiederverwendbarer Antwort-Cache sowie
`summary.csv` und `summary.json` nach Laufabschluss. Derselbe `--output`-Pfad
setzt den Download fort; ein neuer Pfad erzeugt einen neuen Abrufstand.

### Aufbau des Crawler-Dossiers

`group-01/` bis `group-30/` enthalten `research.sqlite`, `archive/pdf/` und
`results/`. `logs/group-XX.log` protokolliert die jeweilige Gruppe.
`download_cache/` ist der gemeinsame Download-Cache.

Die kopierte Ergebnisansicht enthält nur Archive und Exporte. Sie ersetzt
keine vollständige Sicherung oder das ursprüngliche Wiederaufnahme-Dossier.
Keine aktiven SQLite-Dateien durch einfaches Kopieren als konsistente
Sicherung ausgeben. NFS-Blockaden erst klären, bevor doppelte Worker starten.

## Lokale Forschungs- und KG-Daten

Gemeinsamer lokaler Stamm:
`C:\Users\jowals001\Documents\ChatGPT\SOLVE\`

| Unterordner | Inhalt |
|---|---|
| `outputs/seen_basis_pilot_test/` | Lokaler Test des neuen 30-Seen-Downloaders, nicht der aktuelle Serverlauf |
| `outputs/seen5_schnittstellen_20261109/` | Recherche zu den Landesportalen und Schnittstellen |
| `outputs/wasserblick33_20261031/` | Älterer temporaler 33-Seen-Bestand, vorbereitete Daten und Runtime |
| `outputs/bergwitzsee_lhw_20261101/` | LHW-Originalexporte und vorbereitete Bergwitzsee-Daten |
| `outputs/bergwitzsee_neo4j_v2_events_20261101/` | Erster Bergwitzsee-V2-Graph mit direkten Eventverbindungen |
| `outputs/bergwitzsee_ergaenzung_20261102/` | Ergänzende Geodaten, Monitoringdaten und Graph-Erweiterung |
| `outputs/bergwitzsee_postgres_kg_20261108/` | Neuerer Bergwitzsee-PostgreSQL-/Neo4j-Datenstand, Prüfprotokolle |
| `outputs/bergwitzsee_postgres_kg_20261108/export/` | `bergwitzsee-v2-events-20261108.dump` und `.backup` |
| `output/pdf/` | Erstellte PDFs zur Recherche und zum Modell |

Die lokalen Laufzeitumgebungen und Datenbankinstanzen werden nicht durch
einen Clone wiederhergestellt. Auch lokale Quellenpfade innerhalb von
Event-Metadaten sind nicht automatisch Serverpfade.

## Zugangsdaten und Speicher

Crawler-Schlüssel auf dem Server: `$HOME/.config/solve/keys.env`.
Nur den Pfad dokumentieren; Datei nicht ins Git kopieren.
PostgreSQL-/Neo4j-Konfigurationen liegen lokal in den jeweiligen
`runtime/`-Verzeichnissen. Keine enthaltenen Passwörter veröffentlichen.

Auf linux3 sind sowohl `/net/pdm/home` als auch `/bioing` NFS-Freigaben von
`nfs.isipd.dmawi.de`. Ein Wechsel zwischen diesen Pfaden ist kein Wechsel
auf lokalen Speicher. Der Pilot vermeidet SQLite, benötigt aber ebenfalls
funktionierende Dateizugriffe. Lokales Scratch nur nach Klärung des dafür
freigegebenen Verzeichnisses verwenden.
