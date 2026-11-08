"""Pre-render the NAIP tiles the demo views into the API's local tile cache (data/cache/tiles/local/v1) by requesting
them from the running API, 4 at a time: the county at z10–12 (2022) and Lake Norman at z13–15 (2022 + 2020).

  python scripts/prerender_tiles.py [--base http://127.0.0.1:8000] [--maxz 15]
"""
import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def tiles(b, z):
    def xy(lon, lat):
        n = 2 ** z
        x = int((lon + 180) / 360 * n)
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
        return x, y
    x0, y1 = xy(b[0], b[1])
    x1, y0 = xy(b[2], b[3])
    return [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8000")
ap.add_argument("--maxz", type=int, default=15)
a = ap.parse_args()
places = json.loads((ROOT / "engine/groundtruth/conf/places.json").read_text())["places"]
county = [-81.058, 35.002, -80.55, 35.515]
ln = places["Lake Norman"]["bbox"]
jobs = [(2022, t) for z in (10, 11, 12) for t in tiles(county, z)]
jobs += [(yr, t) for yr in (2022, 2020) for z in range(13, a.maxz + 1) for t in tiles(ln, z)]
c = httpx.Client(base_url=a.base, timeout=120)
t0 = time.time()


def get(j):
    yr, (z, x, y) = j
    r = c.get(f"/api/v1/tiles/naip/{yr}/{z}/{x}/{y}.png")
    return r.status_code


with ThreadPoolExecutor(4) as ex:
    codes = list(ex.map(get, jobs))
print(f"{len(jobs)} tiles in {time.time() - t0:.0f} s: " + ", ".join(f"{k}×{codes.count(k)}" for k in sorted(set(codes))))
