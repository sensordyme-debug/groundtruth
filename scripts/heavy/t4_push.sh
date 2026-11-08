#!/usr/bin/env bash
# (PC, inside WSL) push the inputs of jobs/t4_risk.sh to the laptop: the PC's lidar v2 dtm + ndsm (read-only copies).
set -euo pipefail
cd /mnt/c/Users/orynt/GeoSpatial/Geospatial-SearchEngine
SSH="/mnt/c/Windows/System32/OpenSSH/ssh.exe -i C:/Users/orynt/.ssh/id_ed25519_oryn -o BatchMode=yes -o ConnectTimeout=20"
for p in Myers_Park Lake_Norman Ballantyne Uptown University_City CLT_Airport; do
  mkdir -p /tmp/t4push/lidar_pc_v2/$p
  for f in dtm.tif ndsm.tif meta.json; do ln -sf "$PWD/data/lidar/v2/$p/$f" /tmp/t4push/lidar_pc_v2/$p/$f; done
done
rsync -azL -e "$SSH" --rsync-path="wsl -d Ubuntu-24.04 -- rsync" /tmp/t4push/lidar_pc_v2 ashur@quantcompute:/home/ashur/groundtruth-heavy/data/
echo "push ok"
