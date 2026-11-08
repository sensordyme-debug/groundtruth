#!/usr/bin/env bash
# One-time / idempotent laptop env: ~/groundtruth-heavy/.venv (CPU torch, pinned to the PC env).
set -e
cd ~/groundtruth-heavy
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q "torch==2.15.0+cpu" "torchvision==0.29.2+cpu" --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -q -r scripts/heavy/requirements-heavy.txt "torch==2.15.0+cpu" "torchvision==0.29.2+cpu" --extra-index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -c "import torch, torchvision, laspy, rasterio, duckdb, transformers, open_clip; print('env ok', torch.__version__, torchvision.__version__)"
