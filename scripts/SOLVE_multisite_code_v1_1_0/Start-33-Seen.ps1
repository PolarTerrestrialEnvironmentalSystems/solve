param(
    [string]$Workspace = (Join-Path $PSScriptRoot 'dossier_33_seen_serper_v1_6_2_20260925')
)
$ErrorActionPreference = 'Stop'
$pythonExe = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'Python-Umgebung fehlt. Im Projektordner: python -m venv .venv; danach ./.venv/Scripts/python.exe -m pip install -r revised/requirements.txt'
}
& $pythonExe (Join-Path $PSScriptRoot 'revised/run_web_enrichment.py') run --config (Join-Path $PSScriptRoot 'inputs/sites_blablador_33_serper_v1_6_2.json') --workspace $Workspace
exit $LASTEXITCODE
