#!/usr/bin/env bash
# Regenerate docs/fixtures (GPU env when available for the re-rank; falls back to CPU automatically).
set -uo pipefail
cd "$(dirname "$0")/.."
RUN=scripts/gt.sh
[ -d "$HOME/.venvs/groundtruth-gpu" ] && RUN=scripts/gtg.sh
bash "$RUN" python scripts/make_fixtures.py --aoi "${AOI:-south_charlotte}" 2>&1 | grep -vE "HTTP Request|unauthenticated|faiss|AVX2|Loading weights|sam2_video"
