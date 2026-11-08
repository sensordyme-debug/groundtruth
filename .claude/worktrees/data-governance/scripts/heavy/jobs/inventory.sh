# Phase 7: area inventory, 1 km² centre of each demo place (CPU). Pull back: bash scripts/heavy/sync.sh pull data/inventory
export GROUNDTRUTH_DEVICE=cpu OMP_NUM_THREADS=12
.venv/bin/python scripts/build_inventory.py --sheet
