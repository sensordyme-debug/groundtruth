#!/usr/bin/env bash
# Phase 6: lidar reading without PDAL (PDAL isn't packaged for Ubuntu 24.04; installing micromamba needs your OK):
# laspy (BSD-2-Clause) + lazrs (MIT) read the USGS 3DEP EPT LAZ tiles directly (scripts/build_lidar.py).
uv pip install --python ~/.venvs/groundtruth/bin/python "laspy[lazrs]>=2.5,<3" 2>&1 | tail -3
