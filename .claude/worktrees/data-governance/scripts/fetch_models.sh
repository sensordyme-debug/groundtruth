#!/usr/bin/env bash
# One-time download of public model weights that passed the licence gate (docs/LICENCE_GATE.md). No login needed.
HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 ~/.venvs/groundtruth-gpu/bin/python - "$@" <<'PY'
import sys
from huggingface_hub import snapshot_download
for repo in (sys.argv[1:] or ["IDEA-Research/grounding-dino-tiny"]):
    p = snapshot_download(repo, allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "*.py"])
    print(repo, "→", p)
PY
