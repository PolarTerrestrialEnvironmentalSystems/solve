#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
python3 -m venv .venv-linux
.venv-linux/bin/python -m pip install -r revised/requirements.txt
echo 'Installation abgeschlossen. Siehe PARALLEL_LINUX.md.'
