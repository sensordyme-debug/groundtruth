#!/usr/bin/env bash
# Is main green? tests + imports of every entry point + UI type-check/build.
set -uo pipefail
cd "$(dirname "$0")/.."
export HF_HUB_OFFLINE=1
bash scripts/gt.sh pytest -q 2>&1 | tail -1
bash scripts/gt.sh python - <<'EOF'
import importlib
for m in ("api.app", "api.label_server", "groundtruth.tools", "groundtruth.outline", "groundtruth.embed",
          "groundtruth.index", "groundtruth.labels", "groundtruth.providers.public"):
    importlib.import_module(m)
print("imports ok")
EOF
bash scripts/web.sh build 2>&1 | tail -1
