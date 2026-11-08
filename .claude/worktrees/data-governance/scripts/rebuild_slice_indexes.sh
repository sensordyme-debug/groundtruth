#!/usr/bin/env bash
# Rebuild slice indexes from existing shards in the locked storage format (no re-embedding).
set -euo pipefail
cd "$(dirname "$0")/.."
for a in "${@:-unc_charlotte south_charlotte}"; do
  for x in $a; do
    HF_HUB_OFFLINE=1 nice -n 15 bash scripts/gt.sh python scripts/precompute_county.py --aoi "$x" --year "${YEAR:-2022}" --index-only 2>&1 | grep "index built"
  done
done
