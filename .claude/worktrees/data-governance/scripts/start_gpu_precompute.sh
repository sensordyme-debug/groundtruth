#!/usr/bin/env bash
# Start run_gpu_precompute.sh detached (survives the calling shell). PID in data/logs/gpu_precompute.pid
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/logs
PIDF=data/logs/gpu_precompute.pid
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then echo "already running (pid $(cat "$PIDF"))"; exit 0; fi
setsid nohup bash scripts/run_gpu_precompute.sh >> data/logs/gpu_precompute.log 2>&1 < /dev/null &
echo $! > "$PIDF"
sleep 2
kill -0 "$(cat "$PIDF")" && echo "gpu precompute started (pid $(cat "$PIDF"))"
