#!/usr/bin/env bash
# Data the inventory job needs on the laptop.
cd "$(dirname "$0")/../.."
for p in data/naip data/boundary data/layers/overture_buildings.parquet data/lidar/Ballantyne data/lidar/Uptown data/lidar/Myers_Park; do
  bash scripts/heavy/sync.sh push "$p" 2>&1 | grep -v "post-quantum\|store now\|openssh.com/pq" | tail -1
done
