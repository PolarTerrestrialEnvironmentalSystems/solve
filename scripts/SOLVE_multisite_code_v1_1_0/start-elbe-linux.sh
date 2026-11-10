#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${SOLVE_PYTHON:-$project_dir/.venv-linux/bin/python}"
if [[ ! -x "$python_bin" ]]; then
  printf '%s\n' 'Python-Umgebung fehlt. Im Projektordner zuerst: bash setup-linux.sh' >&2
  exit 1
fi
# prepare creates a new absolute workspace; run resumes that same workspace.
# Defaults in elbe_campaign.py: 30 processes and 2 shared Blablador slots.
exec "$python_bin" -u "$project_dir/revised/elbe_campaign.py" "$@"
