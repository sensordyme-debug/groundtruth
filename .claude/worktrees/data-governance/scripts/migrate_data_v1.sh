#!/usr/bin/env bash
# One-time: move the current data products into their v1 folders (data/<kind>/v1/), as conf/data_versions.json expects.
# Run with the API stopped. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/../data"
for kind in dense lidar; do
  mkdir -p "$kind/v1"
  for p in "$kind"/*; do n=$(basename "$p"); case "$n" in v[0-9]*) ;; *) mv "$p" "$kind/v1/";; esac; done
done
mkdir -p tiles/v1; for p in tiles/*.pmtiles; do [ -e "$p" ] && mv "$p" tiles/v1/; done
mkdir -p routing/v1
for p in routing/*; do n=$(basename "$p"); case "$n" in v[0-9]*) ;; *) mv "$p" routing/v1/;; esac; done
# the Valhalla config stores an absolute tile_dir: point it at the moved folder
python3 - <<'PY'
import json, pathlib
p = pathlib.Path("routing/v1/valhalla.json")
c = json.loads(p.read_text())
c["mjolnir"]["tile_dir"] = str((pathlib.Path("routing/v1/valhalla_tiles")).resolve())
c["mjolnir"]["tile_extract"] = ""
p.write_text(json.dumps(c, indent=1))
print("valhalla tile_dir →", c["mjolnir"]["tile_dir"])
PY
ls dense lidar tiles routing
