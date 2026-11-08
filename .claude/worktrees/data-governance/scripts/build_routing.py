"""Phase 5: Valhalla routing graph for Mecklenburg + 5 km (gt-routing env: pyvalhalla MIT, pyosmium BSD-2).

  ~/.venvs/gt-routing/bin/python scripts/build_routing.py
1. Clip the NC OSM extract (data/osm/north-carolina.osm.pbf, ODbL) to the county bbox + 5 km: highway ways with at least
   one node inside, their nodes (BackReferenceWriter) and turn-restriction relations → data/routing/meck_roads.osm.pbf.
2. valhalla_build_tiles → data/routing/valhalla_tiles (+ tar extract) and data/routing/valhalla.json (config).
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import osmium

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
_r = DATA / "routing"
_ns = [int(p.name[1:]) for p in _r.glob("v*") if p.name[1:].isdigit()] if _r.exists() else []
out = _r / f"v{(max(_ns) if _ns else 0) + 1}"  # rebuilds go to a NEW version (conf/data_versions.json picks it)
out.mkdir(parents=True, exist_ok=True)
boundary = json.loads((DATA / "boundary" / "mecklenburg.geojson").read_text())
from shapely.geometry import shape  # noqa: E402

w, s, e, n = shape(boundary["geometry"]).bounds
pad_lat, pad_lon = 5000 / 111_320, 5000 / (111_320 * 0.82)  # 5 km at ~35.2° N
W, S, E, N = w - pad_lon, s - pad_lat, e + pad_lon, n + pad_lat
src = DATA / "osm" / "north-carolina.osm.pbf"
clip = out / "meck_roads.osm.pbf"
t0 = time.time()
if not clip.exists():
    tmp = clip.with_suffix(".tmp.pbf")
    tmp.unlink(missing_ok=True)

    class Keep(osmium.SimpleHandler):
        def __init__(self, writer):
            super().__init__()
            self.w = writer
            self.n = 0

        def way(self, wy):
            if "highway" not in wy.tags:
                return
            for nd in wy.nodes:
                if nd.location.valid() and W <= nd.location.lon <= E and S <= nd.location.lat <= N:
                    self.w.add_way(wy)
                    self.n += 1
                    return

        def relation(self, r):
            if r.tags.get("type") in ("restriction",) and any(m.type == "w" for m in r.members):
                self.w.add_relation(r)

    with osmium.BackReferenceWriter(str(tmp), ref_src=str(src), overwrite=True) as bw:
        h = Keep(bw)
        h.apply_file(str(src), locations=True, idx="flex_mem")
    tmp.rename(clip)
    print(f"clip: {h.n:,} highway ways → {clip.name} {clip.stat().st_size / 2**20:.0f} MB ({time.time() - t0:.0f} s)", flush=True)
tiles = out / "valhalla_tiles"
cfg_path = out / "valhalla.json"
import valhalla  # noqa: E402

tiles.mkdir(parents=True, exist_ok=True)
cfg = valhalla.get_config(tile_extract="", tile_dir=str(tiles), verbose=False)
cfg["mjolnir"]["tile_extract"] = str(out / "valhalla_tiles.tar")
cfg["mjolnir"]["concurrency"] = 4
cfg_path.write_text(json.dumps(cfg, indent=1))
t1 = time.time()
vbin = Path(valhalla.__file__).parent / "bin"
import os  # noqa: E402

env = {**os.environ, "PATH": f"{vbin}:{os.environ.get('PATH', '')}"}
subprocess.run([sys.executable, "-m", "valhalla", "valhalla_build_tiles", "-c", str(cfg_path), str(clip)], check=True, env=env)
print(f"tiles built ({time.time() - t1:.0f} s)", flush=True)
subprocess.run([sys.executable, "-m", "valhalla", "valhalla_build_extract", "-c", str(cfg_path), "-v"], check=False, env=env)
print(f"done in {time.time() - t0:.0f} s; config {cfg_path}", flush=True)
