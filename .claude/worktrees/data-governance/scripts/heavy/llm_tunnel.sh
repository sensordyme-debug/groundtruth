#!/usr/bin/env bash
# (PC, inside WSL) SSH local-forward PC 127.0.0.1:8081 → laptop localhost:8081 (llama-server) over Tailscale.
#   llm_tunnel.sh start | stop | status
# Detached keep-alive loop (reconnects; ExitOnForwardFailure; ServerAliveInterval). Uses WSL's own ~/.ssh key (never
# printed). No Windows network changes. The PC API reads LOCAL_LLM_URL=http://127.0.0.1:8081/v1.
set -u
cd "$(dirname "$0")/../.."
D=data/llm; mkdir -p "$D"; PID=$D/tunnel.pid; LOG=$D/tunnel.log
PORT=${LLM_PORT:-8081}; HOST=${LLM_HOST:-100.111.202.123}; USER_=${LLM_USER:-ashur}
alive() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }
loop() {
  while [ ! -f "$D/tunnel.stop" ]; do
    echo "$(date -Is) connecting" >> "$LOG"
    ssh -N -o BatchMode=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$HOME/.ssh/known_hosts /mnt/c/Users/orynt/.ssh/known_hosts" -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
      -o ConnectTimeout=15 -L "127.0.0.1:$PORT:127.0.0.1:$PORT" "$USER_@$HOST" >> "$LOG" 2>&1
    echo "$(date -Is) tunnel exited rc=$?; retry in 5 s" >> "$LOG"
    sleep 5
  done
  rm -f "$D/tunnel.stop" "$PID"
}
case "${1:-status}" in
  start) alive && { echo "tunnel already up (pid $(cat "$PID"))"; exit 0; }
         rm -f "$D/tunnel.stop"; nohup setsid bash "$0" __loop >/dev/null 2>&1 </dev/null & echo $! > "$PID"
         for i in $(seq 20); do curl -sf -m 2 "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 1; done
         "$0" status ;;
  __loop) loop ;;
  stop) touch "$D/tunnel.stop"; pkill -f "ssh -N .*-L 127.0.0.1:$PORT:127.0.0.1:$PORT" ; alive && kill "$(cat "$PID")"
        rm -f "$PID"; echo stopped ;;
  status) h=$(curl -s -m 3 "http://127.0.0.1:$PORT/health" || true)
          echo "tunnel=$(alive && echo up || echo down) llm_health=${h:-unreachable} reconnects=$(grep -c connecting "$LOG" 2>/dev/null || echo 0)" ;;
  *) echo "usage: llm_tunnel.sh start|stop|status"; exit 2 ;;
esac
