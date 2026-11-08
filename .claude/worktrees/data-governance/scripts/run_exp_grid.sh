#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
export GROUNDTRUTH_DEVICE=cpu GROUNDTRUTH_THREADS=8 HF_HUB_OFFLINE=1
for g in ${GRIDS:-8}; do
  nice -n 15 bash scripts/gt.sh python scripts/exp_cell_grid.py --aoi "${AOI:-south_charlotte}" --example "${EX:-2022_e4472_n33675}" --grid "$g" ${PCA:+--pca "$PCA"} 2>&1 | grep -vE "HTTP Request|unauthenticated|faiss|AVX2"
done
