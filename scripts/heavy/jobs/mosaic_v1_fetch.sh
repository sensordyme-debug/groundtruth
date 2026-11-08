# F0.2: fetch the 2022 scene COGs the v1 display mosaic needs (Lake Norman, Myers Park, Matthews) into the laptop's COG
# cache (public Planetary Computer files, anonymous, same as the PC). No build.
set -e
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 GDAL_NUM_THREADS=1
nice -n 10 .venv/bin/python scripts/heavy/build_mosaic.py --year 2022 --version v1 --fetch --fetch-only \
  --areas "Lake Norman,Myers Park,Matthews"
echo "mosaic_v1_fetch done at $(date -Is)"
