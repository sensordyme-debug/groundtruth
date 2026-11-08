"""Local NAIP COG cache for offline tiles + reads.

  python scripts/fetch_cogs.py --year 2022 --estimate          # HEAD every county scene, print total GB (no download)
  python scripts/fetch_cogs.py --year 2022                     # download every county scene (resumable, atomic per scene)
  python scripts/fetch_cogs.py --year 2020 --places            # only scenes touching the 8 demo neighbourhoods

Signed URLs are never printed or logged. Files go to GROUNDTRUTH_COG_CACHE (default ~/.cache/groundtruth/naip/<year>/).
"""
import argparse
import json
import logging
import shutil
import sys
import time
from pathlib import Path

import httpx
import planetary_computer
from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402

for n in ("httpx", "httpcore", "urllib3", "azure"):
    logging.getLogger(n).setLevel(logging.WARNING)


def scenes(p: PublicDataProvider, year: int, places: bool) -> list[str]:
    if places:
        pl = json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"]
        out = []
        for v in pl.values():
            out += [s.scene_id for s in p.list_scenes(tuple(v["bbox"]), year)]
        return sorted(set(out))
    g = shape(p.get_boundary()["geometry"])
    return sorted({s.scene_id for s in p.list_scenes(g.bounds, year)
                   if (shape(s.geometry) if s.geometry else box(*s.bbox)).intersects(g)})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--places", action="store_true")
    ap.add_argument("--estimate", action="store_true")
    a = ap.parse_args()
    p = PublicDataProvider()
    ids = scenes(p, a.year, a.places)
    if a.estimate:
        tot = 0
        with httpx.Client(timeout=60) as c:
            for sid in ids:
                tot += int(c.head(planetary_computer.sign(p._href(sid, a.year))).headers["content-length"])
        print(json.dumps({"year": a.year, "places_only": a.places, "scenes": len(ids), "total_gb": round(tot / 2**30, 2),
                          "cache": str(p.cog_cache / str(a.year)),
                          "disk_free_gb": round(shutil.disk_usage(p.cog_cache.parent if p.cog_cache.parent.exists()
                                                                   else Path.home()).free / 2**30, 1)}))
        return
    t0 = time.time()
    for k, sid in enumerate(ids, 1):
        have = p.local_scene_path(sid, a.year).exists()
        path = p.ensure_local(sid, a.year)
        print(f"{k}/{len(ids)} {sid} {'cached' if have else 'downloaded'} {path.stat().st_size / 2**20:.0f} MB "
              f"({time.time() - t0:.0f} s)", flush=True)


if __name__ == "__main__":
    main()
