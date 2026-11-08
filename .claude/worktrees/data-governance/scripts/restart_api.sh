#!/usr/bin/env bash
# Restart the engine API (NOT the labelling server). Safe: the API holds no user data.
set -euo pipefail
cd "$(dirname "$0")/.."
PIDF=data/logs/api.pid
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  kill "$(cat "$PIDF")"
  for i in $(seq 20); do kill -0 "$(cat "$PIDF")" 2>/dev/null || break; sleep 0.5; done
fi
rm -f "$PIDF"
bash scripts/run_api.sh
