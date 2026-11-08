# T4 risk answers: FEMA NFHL flood zones (with attributes) → canopy/slope rasters for the PC's lidar v2 places →
# lidar for the missing demo places (NoDa, South End, Matthews) → their canopy/slope rasters. One heavy job, sequential.
# Before: push the PC's lidar v2 dtm+ndsm to data/lidar_pc_v2/ (scripts/heavy/t4_push.sh).
# Pull:   data/risk/v1 (→ PC data/risk/v1) and data/lidar/v2/{NoDa,South_End,Matthews} (→ PC data/lidar/v3).
set -u
PY=.venv/bin/python
export GROUNDTRUTH_WARP_THREADS=4
$PY scripts/build_risk_layers.py flood --out data/risk/v1 --fallback data/layers/fema_flood_zones.geojson || echo "FLOOD FAILED"
$PY scripts/build_risk_layers.py rasters --out data/risk/v1 --lidar-dir data/lidar_pc_v2 \
  --places "Myers Park" "Lake Norman" Ballantyne Uptown "University City" "CLT Airport" || echo "RASTERS (PC v2) FAILED"
$PY scripts/build_lidar.py NoDa "South End" Matthews --max-depth 9 || echo "LIDAR FAILED"
$PY scripts/build_risk_layers.py rasters --out data/risk/v1 --lidar-dir data/lidar/v2 --places NoDa "South End" Matthews \
  || echo "RASTERS (new) FAILED"
echo "t4_risk done at $(date -Is)"
