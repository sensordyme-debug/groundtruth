#!/usr/bin/env bash
# Start the labelling server (port 8765) detached, logging to data/logs/label_server.log.
# It is deliberately NOT started with --reload: it must never restart on its own.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/logs
PORT="${PORT:-8765}"
if [ -f data/logs/label_server.pid ] && kill -0 "$(cat data/logs/label_server.pid)" 2>/dev/null; then
  echo "label server already running (pid $(cat data/logs/label_server.pid)) on http://localhost:$PORT/label"
  exit 0
fi
source "${GROUNDTRUTH_VENV:-$HOME/.venvs/groundtruth}/bin/activate"
export PYTHONPATH="$PWD/engine:$PWD"
setsid nohup uvicorn api.label_server:app --host 127.0.0.1 --port "$PORT" --workers 1 \
  >> data/logs/label_server.log 2>&1 < /dev/null &
echo $! > data/logs/label_server.pid
echo "label server started (pid $!) on http://localhost:$PORT/label"
