#!/usr/bin/env bash
# Run a command inside the Groundtruth WSL venv from the repo root.
#   wsl.exe -d Ubuntu-24.04 -- bash scripts/gt.sh python scripts/fetch_boundary_naip.py
set -euo pipefail
cd "$(dirname "$0")/.."
source "${GROUNDTRUTH_VENV:-$HOME/.venvs/groundtruth}/bin/activate"
export PYTHONPATH="$PWD/engine:$PWD${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$HOME/.local/share/fnm/node-versions/v26.13.0/installation/bin:$PATH"
exec "$@"
