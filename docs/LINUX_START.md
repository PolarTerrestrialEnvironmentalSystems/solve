# Neuer Branch auf linux3

In einen neuen Codeordner klonen, damit vorhandene Läufe und manuell kopierte
Skripte unberührt bleiben. Die folgenden Befehle laufen auf Linux, nicht in
Windows PowerShell.

```bash
git clone --branch codex/scripts-und-datenwege-20261124 --single-branch \
  https://github.com/PolarTerrestrialEnvironmentalSystems/solve.git \
  /bioing/data/WaterPlace/skripts/solve/solve_scripts_20261124
```

Existiert dieser Clone bereits, stattdessen darin `git pull --ff-only` verwenden.
Nicht parallel zur Aktualisierung daraus laufenden Code ersetzen.

## Amtliche Daten: 5 Seen je Bundesland

Python >=3.10, keine Zusatzpakete und keine API-Schlüssel nötig.
Aus einem normalen SSH-Terminal `screen -S solve-basisdaten` öffnen. Falls
`echo "$STY"` bereits einen Wert ausgibt, bist du schon in Screen.

```bash
cd /bioing/data/WaterPlace/skripts/solve/solve_scripts_20261124/scripts/seen_basis_pilot

bash start-linux.sh plan \
  --output /bioing/data/WaterPlace/Basisdaten_Pilot_30_Seen/stand_20261119

bash start-linux.sh run \
  --output /bioing/data/WaterPlace/Basisdaten_Pilot_30_Seen/stand_20261119 \
  --workers 4
```

Der vorhandene Datenstand wird fortgesetzt. Für einen neuen Abrufstand
stattdessen einen neuen Ordner wie `stand_20261124` angeben. Vorher einen
eventuell noch laufenden Pilot nicht doppelt starten; der Workspace-Lock
verhindert konkurrierende Schreibzugriffe.

Status aus einem zweiten Terminal im Skriptordner:

```bash
bash start-linux.sh status \
  --output /bioing/data/WaterPlace/Basisdaten_Pilot_30_Seen/stand_20261119
```

Strg+A, dann D trennt Screen. Ein erneuter Start mit demselben Output-Pfad
verwendet vorhandene Antworten nach Hash-Prüfung wieder.

## Elbe-Crawler

Installation im neuen Clone:

```bash
cd /bioing/data/WaterPlace/skripts/solve/solve_scripts_20261124/scripts/SOLVE_multisite_code_v1_1_0
bash setup-linux.sh
```

Falls `ensurepip`/`python3-venv` fehlt, die Python-Umgebung erst nach der
Servervorgabe einrichten; der Basisdaten-Pilot benötigt diese Umgebung nicht.
Crawler-Anleitung und Vorbereitungsbefehle stehen in
[ELBE_LINUX.md](../scripts/SOLVE_multisite_code_v1_1_0/ELBE_LINUX.md).
Die darin genannten alten Clone-Pfade durch den neuen Codepfad oben ersetzen.

Das bisherige Dossier nur fortsetzen, wenn alte Worker beendet sind und der
NFS-Speicher wieder zuverlässig erreichbar ist. Kein `prepare` darüber ausführen.

```bash
bash start-elbe-linux.sh run \
  --workspace /net/pdm/home/jowals001/solve-runs/elbe_alle_20261110_130920 \
  --workers 30 \
  --search-provider none \
  --env-file "$HOME/.config/solve/keys.env"
```

Auch diesen langen Lauf innerhalb von Screen starten. Die vorhandene
Schlüsseldatei bleibt außerhalb des Git-Repositories. Kein Schlüsselinhalt
gehört in Befehlsprotokolle oder Dokumentation.

## Ergebnisansicht für Windows aktualisieren

```bash
mkdir -p /bioing/data/WaterPlace/Ergebnisse_Elbe/elbe_alle_20261110_130920
rsync -rtv --prune-empty-dirs \
  --include='/group-*/' \
  --include='/group-*/archive/***' \
  --include='/group-*/results/***' \
  --exclude='*' \
  /net/pdm/home/jowals001/solve-runs/elbe_alle_20261110_130920/ \
  /bioing/data/WaterPlace/Ergebnisse_Elbe/elbe_alle_20261110_130920/
```

Das kopiert Ergebnisdateien, keine konsistente Sicherung aktiver Datenbanken.
Während eines Laufs können Dateien noch ergänzt werden. Die Originale bleiben.
Der 30-Seen-Pilot schreibt bereits direkt unter `/bioing/data/WaterPlace`.
