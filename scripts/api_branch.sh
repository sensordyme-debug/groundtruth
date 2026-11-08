#!/usr/bin/env bash
# (PC) restart the API from this worktree on the shared data (GPU env, full warm-up), local LLM via the tunnel
cd /mnt/c/Users/orynt/GeoSpatial/gt-redesign
mkdir -p data/logs
export GROUNDTRUTH_DATA=/mnt/c/Users/orynt/GeoSpatial/Geospatial-SearchEngine/data GROUNDTRUTH_GPU=1
export LOCAL_LLM_URL=http://127.0.0.1:8081/v1
# authentication (docs/AUTH.md): on unless GROUNDTRUTH_AUTH is set; "off" is an explicit local development mode
export GROUNDTRUTH_AUTH="${GROUNDTRUTH_AUTH:-off}"
bash scripts/restart_api.sh | tail -1 | cut -c1-260
