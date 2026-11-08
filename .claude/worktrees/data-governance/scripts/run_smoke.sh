#!/usr/bin/env bash
# Contact-sheet smoke tests on slices (CPU, low priority). Usage: scripts/run_smoke.sh aoi1 [aoi2 ...]
set -uo pipefail
cd "$(dirname "$0")/.."
export GROUNDTRUTH_DEVICE=cpu GROUNDTRUTH_THREADS=6 HF_HUB_OFFLINE=1
for a in "$@"; do
  nice -n 15 bash scripts/gt.sh python scripts/smoke_slice.py --aoi "$a" ${PICK:+--pick-chip "$PICK"} 2>&1 \
    | grep -vE "HTTP Request|unauthenticated|faiss|AVX2"
done
