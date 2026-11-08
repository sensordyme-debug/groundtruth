#!/usr/bin/env bash
# Run a command in the GPU env (master queue: up to 6.5 GB VRAM for Groundtruth; backs off when VRAM is busy).
set -euo pipefail
cd "$(dirname "$0")/.."
source "$HOME/.venvs/groundtruth-gpu/bin/activate"
export PYTHONPATH="$PWD/engine:$PWD" GROUNDTRUTH_DEVICE="${GROUNDTRUTH_DEVICE:-cuda}" GROUNDTRUTH_VRAM_GB="${GROUNDTRUTH_VRAM_GB:-6.5}" \
       GROUNDTRUTH_THREADS="${GROUNDTRUTH_THREADS:-4}" HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
exec nice -n 10 "$@"
