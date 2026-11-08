#!/usr/bin/env bash
# Read-only memory report for WSL: free memory + top processes by RSS (marks Groundtruth's own).
cd "$(dirname "$0")/.."
date +%H:%M
free -m | sed -n 1,2p
echo "--- top 10 by RSS (MB, pid, cmd)"
ps -eo rss=,pid=,args= --sort=-rss | head -10 | while read -r rss pid args; do
  mine=""
  case "$args" in *precompute_county*|*api.app*|*api.label_server*|*groundtruth*) mine="  <- groundtruth";; esac
  printf "%7d  %7s  %s%s\n" $((rss/1024)) "$pid" "$(echo "$args" | cut -c1-80)" "$mine"
done
echo "--- precompute"
pgrep -af "precompute_county.py --aoi county" | cut -c1-90 || echo "not running"
grep -E "scene [0-9]+/|index built|done:|=== " data/logs/gpu_precompute.log | tail -3 | cut -c1-140
ls data/precompute/chips224s192-g0.6-v3c8p192/county/*/shards 2>/dev/null | grep -c parquet
ls data/precompute/chips224s192-g0.6-v3c8p192/county/*/index.json 2>/dev/null
