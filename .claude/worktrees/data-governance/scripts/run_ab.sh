#!/usr/bin/env bash
# A/B CLIP sub-crops on the UNC slice (CPU, low priority).
set -euo pipefail
cd "$(dirname "$0")/.."
export GROUNDTRUTH_DEVICE=cpu GROUNDTRUTH_THREADS=6 HF_HUB_OFFLINE=1
GROUNDTRUTH_CLIP_SUBCROPS=1 nice -n 15 bash scripts/gt.sh python scripts/precompute_county.py --aoi unc_charlotte --index-only 2>&1 | grep "index built"
nice -n 15 bash scripts/gt.sh python scripts/ab_subcrops.py --aoi unc_charlotte --query "swimming pool" 2>&1 | grep -vE "HTTP Request|unauthenticated|faiss|AVX2"
