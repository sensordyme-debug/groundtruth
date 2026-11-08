#!/usr/bin/env bash
# Read-only diagnostics for the labelling server.
cd "$(dirname "$0")/.."
echo "== pid file: $(cat data/logs/label_server.pid 2>/dev/null)"
pid=$(cat data/logs/label_server.pid 2>/dev/null)
if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then echo "process $pid alive: $(ps -o etime=,rss=,args= -p "$pid")"; else echo "process NOT running"; fi
echo "== listeners"; ss -ltnp 2>/dev/null | grep -E ':8765\b' || echo "nothing on 8765"
echo "== last 30 log lines"; tail -30 data/logs/label_server.log
echo "== endpoints"
for u in /api/health /api/config /label /api/boundary "/api/labels" "/api/tiles/naip/2022/16/18071/25890.png"; do
  curl -s -o /dev/null -m 60 -w "$u -> %{http_code} %{content_type} %{size_download}B %{time_total}s\n" "http://127.0.0.1:8765$u"
done
