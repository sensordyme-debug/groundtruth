#!/usr/bin/env bash
# (laptop) Detached heavy jobs. The laptop's .wslconfig keeps the distro alive (vmIdleTimeout/instanceIdleTimeout = -1),
# so jobs no longer depend on any SSH session.
#   run_job.sh start <name>   start scripts/heavy/jobs/<name>.sh (one heavy job at a time; refuses if another runs)
#   run_job.sh status [name]  running/finished + last log line
#   run_job.sh log <name>     follow the log until the job ends
# Files: data/jobs/<name>.pid, data/logs/<name>.log, data/jobs/<name>.exit (exit code when finished)
set -u
cd ~/groundtruth-heavy && mkdir -p data/logs data/jobs
cmd="${1:-status}"; name="${2:-}"
running() { [ -f "data/jobs/$1.pid" ] && kill -0 "$(cat "data/jobs/$1.pid")" 2>/dev/null; }
case "$cmd" in
  start)
    for p in data/jobs/*.pid; do [ -e "$p" ] || continue; n=$(basename "$p" .pid)
      if running "$n"; then echo "refused: heavy job '$n' is running (one at a time)"; exit 1; fi; done
    rm -f "data/jobs/$name.exit"
    nohup setsid bash -c "bash scripts/heavy/jobs/$name.sh; echo \$? > data/jobs/$name.exit" \
      > "data/logs/$name.log" 2>&1 < /dev/null &
    echo $! > "data/jobs/$name.pid"; echo "started $name pid $(cat data/jobs/$name.pid) at $(date -Is)" ;;
  status)
    for p in data/jobs/*.pid; do [ -e "$p" ] || continue; n=$(basename "$p" .pid); [ -n "$name" ] && [ "$n" != "$name" ] && continue
      if running "$n"; then s=running; else s="finished (exit $(cat data/jobs/$n.exit 2>/dev/null || echo ?))"; fi
      echo "$n: $s · $(tail -1 data/logs/$n.log 2>/dev/null | cut -c1-140)"; done ;;
  log) tail -n 20 -f "data/logs/$name.log" --pid="$(cat data/jobs/$name.pid)" ;;
  *) echo "usage: run_job.sh start|status|log <name>"; exit 2 ;;
esac
