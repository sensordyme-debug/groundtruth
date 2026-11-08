#!/usr/bin/env bash
# Extra deps for v0.3 (reportlab BSD for PDF export; API deps in the GPU env). Pinned.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
uv pip install -q --python "$HOME/.venvs/groundtruth/bin/python" "reportlab==4.4.4"
uv pip install -q --python "$HOME/.venvs/groundtruth-gpu/bin/python" "reportlab==4.4.4" "fastapi==0.162.2" \
  "uvicorn[standard]==0.54.0" "pytest" "duckdb==2.0.0"
echo ok
