# F0.2 tuning: render ONE mosaic block over the Lake Norman seam (lon -80.875, lat 35.50; 2022-09-27 over 2022-06-02)
# three ways (raw composite / feather only / dodge + feather) with before/after PNGs + metrics into ~/mosaic_preview
# (outside data/, never pulled into data/). Extra --set overrides (tuning) are read from data/jobs/mosaic_preview.args.
set -e
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 GDAL_NUM_THREADS=1
extra=$(cat data/jobs/mosaic_preview.args 2>/dev/null || true)
nice -n 10 .venv/bin/python scripts/heavy/build_mosaic.py --year 2022 --areas "Lake Norman" --workers 6 \
  --preview=-80.875,35.50 --out-root ~/mosaic_preview/${PREVIEW_TAG:-default} $extra
echo "mosaic_preview done at $(date -Is)"
