#!/usr/bin/env bash
# Hero flow pass/fail + timings against the running API (default http://127.0.0.1:8000). Add --record to refresh replay.
set -uo pipefail
cd "$(dirname "$0")/.."
bash scripts/gt.sh python scripts/demo_check.py "$@"
