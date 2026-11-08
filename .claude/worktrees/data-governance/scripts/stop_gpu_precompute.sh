#!/usr/bin/env bash
# Stop the GPU precompute (our own job only; resumable at scene granularity). Never touches ORYN processes.
cd "$(dirname "$0")/.."
PIDF=data/logs/gpu_precompute.pid
if [ -f "$PIDF" ]; then
  pkill -TERM -P "$(cat "$PIDF")" 2>/dev/null   # python child
  kill -TERM "$(cat "$PIDF")" 2>/dev/null
  rm -f "$PIDF"
fi
sleep 2
pgrep -af "scripts/precompute_county.py" || echo "stopped"
