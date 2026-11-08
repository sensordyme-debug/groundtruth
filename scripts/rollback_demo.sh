#!/usr/bin/env bash
# Roll the PC back to the demo-final state and prove it works (run inside WSL, repo root or anywhere).
#   bash scripts/rollback_demo.sh [tag]        (default tag: demo-final)
# 1. refuses if the working tree has uncommitted changes (commit or stash them on a branch first)
# 2. checks out the tag (detached HEAD; `git checkout main` returns)
# 3. pins the data versions recorded in data/DEMO_MANIFEST.json (GROUNDTRUTH_DATA_<KIND> env overrides)
# 4. restarts the API with the full warm-up, then runs demo_check (which also verifies the manifest)
# The label server is never touched.
set -euo pipefail
cd "$(dirname "$0")/.."
TAG="${1:-demo-final}"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "refused: uncommitted changes; commit them on a branch first (upgrades happen on branches)"; exit 1
fi
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || { echo "no tag $TAG"; exit 1; }
echo "== checkout $TAG"
git checkout -q "$TAG"
git log --oneline -1
eval "$(python3 - <<'PY'
import json
m = json.load(open("data/DEMO_MANIFEST.json"))
for k, v in m["data_versions"].items():
    if not k.startswith("_"):
        print(f"export GROUNDTRUTH_DATA_{k.upper()}={v}")
PY
)"
env | grep '^GROUNDTRUTH_DATA_' | sort
echo "== build the UI of $TAG (web/dist is not in git)"
(cd web && bash -lic "npm run build" 2>&1 | tail -1)
echo "== restart API with warm-up"
GROUNDTRUTH_GPU=1 bash scripts/restart_and_warm.sh | tail -1 | cut -c1-160
echo "== demo_check"
bash scripts/demo_check.sh
echo "rolled back to $TAG. Return to development with: git checkout main && bash scripts/restart_and_warm.sh"
