#!/usr/bin/env bash
# One-off county precompute on this PC's GPU (approved for the precompute only). 2022 then 2020.
# Caps: 4 GB VRAM, nice 15, 4 CPU threads. Resumable (re-run continues). Logs: data/logs/gpu_precompute.log
set -uo pipefail
cd "$(dirname "$0")/.."
source "$HOME/.venvs/groundtruth-gpu/bin/activate"
export PYTHONPATH="$PWD/engine:$PWD" GROUNDTRUTH_DEVICE=cuda GROUNDTRUTH_VRAM_GB=4 GROUNDTRUTH_THREADS=4 \
       OMP_NUM_THREADS=4 HF_HUB_OFFLINE=1
mkdir -p data/logs
for y in ${YEARS:-2022 2020}; do
  echo "=== $(date -Iseconds) year $y" >> data/logs/gpu_precompute.log
  nice -n 15 python scripts/precompute_county.py --aoi county --year "$y" --batch 64 >> data/logs/gpu_precompute.log 2>&1
  echo "=== $(date -Iseconds) year $y exit $?" >> data/logs/gpu_precompute.log
done
