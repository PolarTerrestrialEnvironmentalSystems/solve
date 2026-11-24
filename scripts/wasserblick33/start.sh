#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ ! -f .env ]]; then
  python3 init_env.py
fi
docker compose up -d --wait postgres neo4j
docker compose --profile tools build tools
docker compose --profile tools run --rm tools python run_pipeline.py --bundle /data/prepared/bundle.json --apply
printf '\nNeo4j Browser: http://127.0.0.1:7475 (user neo4j; password in local .env)\n'
