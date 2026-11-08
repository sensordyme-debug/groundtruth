# F0.2: display mosaic v1, 2022, for the demo areas Lake Norman (flagship), Myers Park, Matthews.
# Scenes come from the laptop's COG cache (scripts/heavy/jobs/mosaic_v1_fetch.sh fetched the missing ones; --fetch
# would fetch any still missing from Planetary Computer: public, anonymous, same files as the PC).
# Threads: 6 single-threaded workers (the laptop's llama-server must stay responsive).
# Pull to the PC: GROUNDTRUTH_DATA=... bash scripts/heavy/pull_mosaic.sh v1
set -e
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 GDAL_NUM_THREADS=1
nice -n 10 .venv/bin/python scripts/heavy/build_mosaic.py --year 2022 --version v1 --workers 6 --fetch \
  --areas "Lake Norman,Myers Park,Matthews"
du -sh data/tiles/mosaic/v1/*
echo "mosaic_v1 done at $(date -Is)"
