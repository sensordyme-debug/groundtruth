#!/usr/bin/env bash
# Start the engine API detached (default 127.0.0.1:8000; set HOST=0.0.0.0 only on the designated API host).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/logs
HOST="${HOST:-127.0.0.1}"; PORT="${PORT:-8000}"
PIDF=data/logs/api.pid
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  echo "api already running (pid $(cat "$PIDF")) on http://$HOST:$PORT"; exit 0
fi
if [ "${GROUNDTRUTH_GPU:-0}" = "1" ]; then  # query-time GPU (approved window), 4 GB cap, backs off when busy
  source "$HOME/.venvs/groundtruth-gpu/bin/activate"
  export GROUNDTRUTH_DEVICE=cuda GROUNDTRUTH_VRAM_GB="${GROUNDTRUTH_VRAM_GB:-6.5}"
else
  source "${GROUNDTRUTH_VENV:-$HOME/.venvs/groundtruth}/bin/activate"
  export GROUNDTRUTH_DEVICE="${GROUNDTRUTH_DEVICE:-cpu}"
fi
export PYTHONPATH="$PWD/engine:$PWD" HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export GROUNDTRUTH_THREADS="${GROUNDTRUTH_THREADS:-4}"
setsid nohup nice -n 5 uvicorn api.app:app --host "$HOST" --port "$PORT" --workers 1 \
  >> data/logs/api.log 2>&1 < /dev/null &
echo $! > "$PIDF"
for i in $(seq 40); do curl -sf "http://127.0.0.1:$PORT/api/v1/health" >/dev/null && break; sleep 0.5; done
# wait for the warm-up (models + index + hero flow + typed demo chain) before reporting healthy (max ~15 min)
for i in $(seq 900); do curl -s "http://127.0.0.1:$PORT/api/v1/health" | grep -q '"ok":true' && break; sleep 1; done
echo "api started (pid $(cat "$PIDF")) → $(curl -s "http://127.0.0.1:$PORT/api/v1/health" | head -c 300)"
