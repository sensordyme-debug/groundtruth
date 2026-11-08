#!/usr/bin/env bash
# Restart the API (GPU env), wait for the hero-flow warm-up (healthy) and then for the background place warm-up.
cd "$(dirname "$0")/.."
GROUNDTRUTH_GPU=1 bash scripts/restart_api.sh | tail -1 | cut -c1-220
for i in $(seq 180); do
  curl -s localhost:8000/api/v1/health | grep -q '"places_ms":[0-9]' && break
  sleep 5
done
curl -s localhost:8000/api/v1/health | head -c 260; echo
