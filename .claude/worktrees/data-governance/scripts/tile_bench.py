"""Phase 1.1/1.2: tile latency via the API. NAIP raster tiles (source from X-Tile-Source) and vector tiles, Ballantyne.
  python scripts/tile_bench.py [--year 2022] [--n 120] [--fresh]   (--fresh: tiles not requested before in this process)"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

import httpx
import morecantile
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--year", type=int, default=2022)
ap.add_argument("--n", type=int, default=120)
ap.add_argument("--place", default="Ballantyne")
ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
bb = json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"][a.place]["bbox"]
tms = morecantile.tms.get("WebMercatorQuad")
tiles = [t for z in (14, 15, 16, 17) for t in tms.tiles(*bb, zooms=[z])]
random.Random(a.seed).shuffle(tiles)
tiles = tiles[: a.n]
c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=120)
for kind, url in (("naip", f"/api/v1/tiles/naip/{a.year}/{{z}}/{{x}}/{{y}}.png"),
                  ("vt buildings", "/api/v1/vt/overture_buildings/{z}/{x}/{y}.pbf"),
                  ("vt flood", "/api/v1/vt/fema_flood_zones/{z}/{x}/{y}.pbf")):
    ms, src = [], {}
    for t in tiles:
        z = min(t.z, 15) if kind.startswith("vt") else t.z
        tt = t if z == t.z else tms.parent(t, zoom=z)[0]
        t0 = time.perf_counter()
        r = c.get(url.format(z=tt.z, x=tt.x, y=tt.y))
        ms.append((time.perf_counter() - t0) * 1000)
        s = r.headers.get("x-tile-source", str(r.status_code))
        src[s] = src.get(s, 0) + 1
    print(f"{kind:13s} n={len(ms)} p50 {np.percentile(ms, 50):6.1f} ms  p95 {np.percentile(ms, 95):7.1f} ms  sources {src}")
