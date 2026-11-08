#!/usr/bin/env bash
# Restart the labelling server. Only run with the human's go-ahead, between clicks.
set -euo pipefail
cd "$(dirname "$0")/.."
PIDF=data/logs/label_server.pid
echo "before: $(curl -s 127.0.0.1:8765/api/health || echo down)"
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  kill "$(cat "$PIDF")"
  for i in $(seq 20); do kill -0 "$(cat "$PIDF")" 2>/dev/null || break; sleep 0.5; done
fi
rm -f "$PIDF"
[ "${CLEAR_GEOCODE_CACHE:-0}" = "1" ] && rm -f data/cache/geocode.json
bash scripts/run_label_server.sh
for i in $(seq 30); do curl -sf 127.0.0.1:8765/api/health >/dev/null && break; sleep 0.5; done
echo "after: $(curl -s 127.0.0.1:8765/api/health)"
