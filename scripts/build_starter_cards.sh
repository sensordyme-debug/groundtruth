#!/usr/bin/env bash
# (PC WSL) the landing's starter-card images: real NAIP 2022 crops, 960×540 at 0.6 m → web/app-v2/public/v2assets/
cd /mnt/c/Users/orynt/GeoSpatial/gt-redesign
export GROUNDTRUTH_DATA=/mnt/c/Users/orynt/GeoSpatial/Geospatial-SearchEngine/data
O=web/app-v2/public/v2assets
bash scripts/gt.sh python scripts/build_landing_bg.py --size 960x540 --centre=-80.8722,35.4968 --out $O/card_lakefront.webp
bash scripts/gt.sh python scripts/build_landing_bg.py --size 960x540 --centre=-80.8480,35.0420 --out $O/card_point.webp
bash scripts/gt.sh python scripts/build_landing_bg.py --size 960x540 --centre=-80.8822,35.5010 --out $O/card_flood.webp
