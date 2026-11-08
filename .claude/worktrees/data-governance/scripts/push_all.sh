#!/usr/bin/env bash
# commit leftovers in the branch worktrees (never node_modules symlinks or data/) and push every branch + tag to the
# 'groundtruth' remote (no force).
set -e
R=/mnt/c/Users/orynt/GeoSpatial
for d in "$R/gt-redesign" "$R/gt-ui-results-pop"; do
  cd "$d"
  grep -qx "web/node_modules" .gitignore || echo "web/node_modules" >> .gitignore
  git add -A .   # data/ and web/node_modules are gitignored
  git diff --cached --quiet || git commit -q -m "Sync: ignore the worktree node_modules symlink; helper scripts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  echo "$(git branch --show-current) → $(git log --oneline -1)"
done
cd "$R/Geospatial-SearchEngine"
timeout 300 git push groundtruth main checkpoint/2026-11-08 wip/2026-11-08 redesign/conversational ui/results-pop 2>&1 | grep -E "\->|up-to-date|rejected|error" || true
timeout 120 git push groundtruth --tags 2>&1 | grep -E "\->|up-to-date|rejected|error" || true
git ls-remote --heads --tags groundtruth | awk '{print substr($1,1,7), $2}'
