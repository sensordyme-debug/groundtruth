#!/usr/bin/env bash
# Block until the county index for YEAR exists (or the precompute dies), then print a status line. Max 4 h.
cd "$(dirname "$0")/.."
Y="${1:-2022}"
F="data/precompute/chips224s192-g0.6-v3c8p192/county/$Y/index.json"
for i in $(seq 1 480); do
  if [ -f "$F" ]; then echo "INDEX READY $Y $(date +%H:%M) $(cat "$F" | tr -d '\n ' | cut -c1-200)"; exit 0; fi
  if ! pgrep -f "precompute_county.py --aoi county" >/dev/null; then
    echo "PRECOMPUTE NOT RUNNING at $(date +%H:%M); last: $(grep -E 'scene [0-9]+/|Error|Traceback' data/logs/gpu_precompute.log | tail -2 | cut -c1-200)"; exit 1
  fi
  sleep 30
done
echo "TIMEOUT waiting for $F"; exit 2
