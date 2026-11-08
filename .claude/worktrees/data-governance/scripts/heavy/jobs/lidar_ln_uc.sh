# Redesign prerequisite: lidar for Lake Norman, then University City (one heavy job, sequential).
# Pull: bash scripts/heavy/sync.sh pull data/lidar/v1 (laptop numbering) → stage into the PC's data/lidar/v2
.venv/bin/python scripts/build_lidar.py "Lake Norman" "University City" --max-depth 9
