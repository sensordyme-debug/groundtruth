#!/usr/bin/env bash
# (PC) build the Lake Norman derived set into data/derived/v1 via the running API
cd /mnt/c/Users/orynt/GeoSpatial/gt-redesign
export GROUNDTRUTH_DATA=/mnt/c/Users/orynt/GeoSpatial/Geospatial-SearchEngine/data
free -g | sed -n 2p
bash scripts/gt.sh python scripts/build_derived.py "Lake Norman" --version v1 2>&1 | grep -v Warning | tail -5
