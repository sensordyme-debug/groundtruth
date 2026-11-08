#!/usr/bin/env bash
# status of every worktree (main repo + branch worktrees)
for d in /mnt/c/Users/orynt/GeoSpatial/Geospatial-SearchEngine /mnt/c/Users/orynt/GeoSpatial/gt-redesign /mnt/c/Users/orynt/GeoSpatial/gt-ui-results-pop; do
  echo "== $d [$(git -C "$d" branch --show-current)]"
  git -C "$d" status --short | head -20
done
