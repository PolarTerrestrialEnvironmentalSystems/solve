$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.env')) {
    python init_env.py
    if ($LASTEXITCODE -ne 0) { throw 'Credential initialization failed.' }
}
docker compose up -d --wait postgres neo4j
if ($LASTEXITCODE -ne 0) { throw 'Database startup failed.' }
docker compose --profile tools build tools
if ($LASTEXITCODE -ne 0) { throw 'Tools image build failed.' }
docker compose --profile tools run --rm tools python run_pipeline.py --bundle /data/prepared/bundle.json --apply
if ($LASTEXITCODE -ne 0) { throw 'Import failed; existing snapshots were preserved.' }
Write-Output 'Neo4j Browser: http://127.0.0.1:7475 (user neo4j; password in local .env)'
