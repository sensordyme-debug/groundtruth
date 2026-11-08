#!/usr/bin/env bash
# Separate CUDA env for the one-off precompute on this PC's GPU (does not touch the CPU env or ORYN's envs).
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
V="$HOME/.venvs/groundtruth-gpu"
[ -d "$V" ] || uv venv "$V" --python 3.12 -q
source "$V/bin/activate"
# Driver 572.70 supports CUDA <= 12.8; torch 2.15.0 ships only for cu132, so use 2.14.1+cu126 (same models/weights).
uv pip install -q "torch==2.14.1" torchvision --index-url "https://download.pytorch.org/whl/cu126"
uv pip install -q "open_clip_torch==4.2.1" "transformers==5.22.2" "huggingface_hub==2.4.0" "safetensors==0.8.0" \
  "timm==1.0.31" "faiss-cpu==1.15.1" "numpy==2.5.4" "scikit-learn==1.9.1" "pyarrow==26.0.1" "shapely==2.2.0" \
  "pyproj==3.8.0" "pillow==13.0.0" "rasterio==1.5.2" "pystac-client==0.9.1" "planetary-computer==1.0.0" \
  "httpx==0.28.1" "pydantic==2.14.3"
python - <<'EOF'
import torch
print("torch", torch.__version__, "cuda", torch.cuda.is_available(), torch.version.cuda,
      torch.cuda.get_device_name(0) if torch.cuda.is_available() else "-")
EOF
