#!/usr/bin/env bash
# Build slice indexes on CPU at low priority (this PC must not use its GPU). Usage: scripts/build_slices.sh aoi1 aoi2 ...
set -uo pipefail
cd "$(dirname "$0")/.."
YEAR="${YEAR:-2022}"
for a in "$@"; do
  GROUNDTRUTH_DEVICE=cpu GROUNDTRUTH_THREADS="${THREADS:-8}" HF_HUB_OFFLINE=1 nice -n 15 \
    bash scripts/gt.sh python scripts/precompute_county.py --aoi "$a" --year "$YEAR" --keep-cogs 2>&1 \
    | grep -E 'plan:|scene [0-9]|index built|Error|error|Traceback'
done
