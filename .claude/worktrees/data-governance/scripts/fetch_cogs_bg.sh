#!/usr/bin/env bash
# Detached local-COG download: 2022 county, then 2020 for the 8 demo neighbourhoods. Stops if C: free < 30 GB.
cd "$(dirname "$0")/.."
LOG=data/logs/fetch_cogs.log
run() {
  free_gb=$(df --output=avail -BG /mnt/c | tail -1 | tr -dc 0-9)
  if [ "$free_gb" -lt 30 ]; then echo "STOP: C: free ${free_gb} GB < 30" >> "$LOG"; exit 1; fi
  bash scripts/gt.sh python scripts/fetch_cogs.py "$@" >> "$LOG" 2>&1
}
echo "=== $(date -Is) start" >> "$LOG"
run --year 2022 && run --year 2020 --places
echo "=== $(date -Is) done (exit $?)" >> "$LOG"
