#!/usr/bin/env bash
# (laptop) Resident llama-server for Groundtruth's chat brain. Detached + supervised; survives SSH sessions closing.
#   llm_server.sh start | stop | status | logs
# After a laptop reboot: run `llm_server.sh start` again (no Windows auto-start: that would be a settings change).
set -u
H=~/groundtruth-heavy; L=$H/llm; D=$H/data/llm; mkdir -p "$D"
source "$H/scripts/heavy/llm/llm.conf"
BIN=$L/llama.cpp/build/bin/llama-server
MODEL=$L/models/$LLM_MODEL_FILE
SUP_PID=$D/supervisor.pid; SRV_PID=$D/server.pid; LOG=$D/server.log; SLOG=$D/supervisor.log
rotate() { [ -f "$LOG" ] && [ "$(du -m "$LOG" | cut -f1)" -ge "$LLM_LOG_MAX_MB" ] && mv -f "$LOG" "$LOG.1"; true; }
alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
supervise() {
  local starts=()
  while true; do
    now=$(date +%s); starts=($(for s in "${starts[@]:-}"; do [ -n "$s" ] && [ $((now - s)) -lt "$LLM_RESTART_WINDOW_S" ] && echo "$s"; done) "$now")
    if [ "${#starts[@]}" -gt "$((LLM_MAX_RESTARTS + 1))" ]; then
      echo "$(date -Is) crash loop: ${#starts[@]} starts in ${LLM_RESTART_WINDOW_S}s, giving up" >> "$SLOG"; rm -f "$SUP_PID"; exit 1
    fi
    rotate
    echo "$(date -Is) starting llama-server" >> "$SLOG"
    "$BIN" -m "$MODEL" --host 127.0.0.1 --port "$LLM_PORT" -t "$LLM_THREADS" -c "$LLM_CTX" -np "$LLM_SLOTS" \
      --jinja --reasoning off --cache-reuse 256 \
      --metrics --no-webui >> "$LOG" 2>&1 &
    echo $! > "$SRV_PID"; wait "$(cat "$SRV_PID")"; rc=$?
    echo "$(date -Is) llama-server exited rc=$rc" >> "$SLOG"
    [ -f "$D/stopping" ] && { rm -f "$D/stopping" "$SUP_PID"; exit 0; }
    sleep 3
  done
}
case "${1:-status}" in
  start)
    alive "$SUP_PID" && { echo "already running (supervisor $(cat "$SUP_PID"))"; exit 0; }
    [ -x "$BIN" ] || { echo "no llama-server build: run the llm_build job"; exit 1; }
    [ -f "$MODEL" ] || { echo "no model $MODEL"; exit 1; }
    rm -f "$D/stopping"
    nohup setsid bash "$0" __supervise > /dev/null 2>&1 < /dev/null & echo $! > "$SUP_PID"
    for i in $(seq 120); do curl -sf "http://127.0.0.1:$LLM_PORT/health" >/dev/null && break; sleep 1; done
    "$0" status ;;
  __supervise) supervise ;;
  stop)
    touch "$D/stopping"; alive "$SRV_PID" && kill "$(cat "$SRV_PID")"; sleep 2
    alive "$SUP_PID" && kill "$(cat "$SUP_PID")"; rm -f "$SUP_PID" "$SRV_PID"; echo stopped ;;
  status)
    h=$(curl -s -m 3 "http://127.0.0.1:$LLM_PORT/health" || echo '{"status":"down"}')
    rss=$(alive "$SRV_PID" && ps -o rss= -p "$(cat "$SRV_PID")" | awk '{printf "%.0f MB", $1/1024}' || echo -)
    up=$(alive "$SRV_PID" && ps -o etime= -p "$(cat "$SRV_PID")" | tr -d ' ' || echo -)
    echo "health=$h model=$LLM_MODEL_FILE threads=$LLM_THREADS ctx=$LLM_CTX slots=$LLM_SLOTS port=127.0.0.1:$LLM_PORT uptime=$up rss=$rss supervisor=$(alive "$SUP_PID" && echo up || echo down)" ;;
  logs) tail -n 40 "$LOG"; echo "--- supervisor"; tail -n 10 "$SLOG" 2>/dev/null ;;
  *) echo "usage: llm_server.sh start|stop|status|logs"; exit 2 ;;
esac
