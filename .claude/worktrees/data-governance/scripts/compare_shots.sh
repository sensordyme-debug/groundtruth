#!/usr/bin/env bash
# (PC WSL) mockup vs build screenshots at both sizes → docs/media/v2/compare (the build side also runs demo_check v2)
cd /mnt/c/Users/orynt/GeoSpatial/gt-redesign
PY=~/.venvs/gt-browser/bin/python
for s in 1440x900 1920x1080; do
  $PY scripts/mockup_shots.py --size "$s" 2>&1 | tail -2
  [ "${1:-}" = "mockup" ] || $PY scripts/demo_check_v2.py --shots docs/media/v2/compare --size "$s" --label "compare_$s" 2>&1 | grep -v "^PASS" | tail -4
done
