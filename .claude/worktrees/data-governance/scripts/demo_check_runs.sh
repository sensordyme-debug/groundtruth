#!/usr/bin/env bash
# Run the hero-flow check N times in a row (first run warms caches). Usage: scripts/demo_check_runs.sh [N] [--record]
cd "$(dirname "$0")/.."
N="${1:-3}"; shift || true
for n in $(seq 1 "$N"); do
  echo "== run $n"
  bash scripts/demo_check.sh "$@" 2>&1 | grep -E 'hero flow|PASS|FAIL|RESULT' | cut -c1-130
done
free -m | sed -n 2p
