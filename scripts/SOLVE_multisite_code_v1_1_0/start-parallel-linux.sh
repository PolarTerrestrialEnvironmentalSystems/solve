#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "$project_dir/.venv-linux/bin/python" -u "$project_dir/revised/parallel_crawler.py" "$@"
