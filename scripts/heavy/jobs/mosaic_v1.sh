# F0.2: colour-balanced, feathered NAIP display mosaic v1 for the places in conf/places.json (2022, then 2020).
# Scenes missing from the laptop's COG cache are fetched from Planetary Computer (public, anonymous; same files as the PC).
# Threads: 6 single-threaded workers (the laptop's llama-server must stay responsive).
# Pull to the PC: bash scripts/heavy/pull_mosaic.sh v1
set -e
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 GDAL_NUM_THREADS=1
for y in 2022 2020; do
  nice -n 10 .venv/bin/python scripts/heavy/build_mosaic.py --year "$y" --version v1 --workers 6 --fetch
done
du -sh data/tiles/mosaic/v1/*
echo "mosaic_v1 done at $(date -Is)"
