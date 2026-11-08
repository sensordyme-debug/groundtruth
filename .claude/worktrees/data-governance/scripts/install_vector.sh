#!/usr/bin/env bash
# Phase 1.2/1.3 deps (licences in docs/LICENCE_GATE.md): tippecanoe (BSD-2, apt), duckdb (MIT), pmtiles (BSD-3).
sudo -n apt-get install -y -q tippecanoe >/tmp/apt_tippecanoe.log 2>&1 && tippecanoe --version 2>&1 | head -1
for e in groundtruth groundtruth-gpu; do
  uv pip install --python ~/.venvs/$e/bin/python "duckdb==2.0.0" "pmtiles>=3.4,<4" 2>&1 | tail -3
done
