#!/usr/bin/env bash
# (PC WSL) demo_check v3 at the three desktop sizes, screenshots to docs/media/v3/ui
cd /mnt/c/Users/orynt/GeoSpatial/gt-redesign
until curl -s localhost:8000/api/v1/health | grep -q '"warming":false'; do sleep 5; done
rc=0
for s in 1440x900 1920x1080 1280x800; do
  echo "== $s"
  ~/.venvs/gt-browser/bin/python scripts/demo_check_v3.py --shots docs/media/v3/ui --size "$s" --report "data/out/demo_check_v3_$s.json" 2>&1 | grep -E "FAIL|ALL PASS|FAILED|Error" || rc=1
done
exit $rc
