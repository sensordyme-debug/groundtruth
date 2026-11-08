#!/usr/bin/env bash
# (PC WSL) the full regression on the running API: unit tests, UI journey at 3 sizes, investigation benchmark,
# Find-like-this eval, Validation Studio smoke. Results → data/out/*.json
cd /mnt/c/Users/orynt/GeoSpatial/gt-redesign
export GROUNDTRUTH_DATA=/mnt/c/Users/orynt/GeoSpatial/Geospatial-SearchEngine/data
echo "== pytest"; bash scripts/gt.sh pytest -q 2>&1 | tail -1
echo "== demo_check v3"; bash scripts/demo_check_v3.sh
echo "== bench"; python3 scripts/bench_investigations.py --out "$GROUNDTRUTH_DATA/out/bench_investigations.json" 2>&1 | tail -1
echo "== studio"; ~/.venvs/gt-browser/bin/python scripts/studio_check.py 2>&1 | tail -6
echo "== point find"; bash scripts/gt.sh python scripts/eval_point_find.py 2>&1 | grep -E "parking_lot|swimming_pool|warehouse" | grep "True" | tail -9
