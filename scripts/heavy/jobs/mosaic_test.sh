# F0.2 smoke test: Lake Norman only (2022): fetch its scenes, fit, build ONE block (the Lake Norman seam) into a
# throwaway folder outside data/ (~/mosaic_test, never pulled to the PC). Then fetch the remaining 2022 + 2020 scenes.
set -e
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 GDAL_NUM_THREADS=1
rm -rf ~/mosaic_test
nice -n 10 .venv/bin/python scripts/heavy/build_mosaic.py --year 2022 --version test --out-root ~/mosaic_test \
  --areas "Lake Norman" --fetch --workers 1 --near=-80.875,35.50
echo "smoke block done at $(date -Is)"
for y in 2022 2020; do .venv/bin/python scripts/heavy/build_mosaic.py --year "$y" --version test --fetch --fetch-only; done
echo "mosaic_test done at $(date -Is)"
