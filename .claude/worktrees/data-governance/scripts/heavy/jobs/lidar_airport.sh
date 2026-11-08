# Phase 6: lidar DTM/DSM/nDSM for the airport area (for "warehouses over 10 m tall near the airport"). Pull back with
#   bash scripts/heavy/sync.sh pull data/lidar/CLT_Airport
.venv/bin/python scripts/build_lidar.py --area "CLT Airport" -80.99 35.18 -80.90 35.25 --max-depth 9
