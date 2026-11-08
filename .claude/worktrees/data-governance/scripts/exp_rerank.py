"""Re-rank A/B: find_similar top 24 with/without rerank for (a) 1 backyard, (b) 3 backyard, (c) community pool.

  python scripts/exp_rerank.py --aoi south_charlotte
Writes sheets data/out/rerank_<aoi>_<case>_<on|off>.png and prints an automatic proxy: hits whose matched cell
touches an OSM-mapped pool (OSM is incomplete, so this undercounts; the by-eye count is the real metric).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from shapely.geometry import box, shape
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from groundtruth import config  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.tools import Engine  # noqa: E402
from smoke_slice import sheet  # noqa: E402

CASES = {
    "south_charlotte": {"a_1backyard": ["2022_e4474_n33670"], "b_3backyard": ["2022_e4474_n33670", "2022_e4475_n33673", "2022_e4473_n33673"],
                        "c_community": ["2022_e4472_n33675#31"]},
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="south_charlotte")
    ap.add_argument("--cases", default=None, help="json {case: [ids]} (overrides built-in)")
    a = ap.parse_args()
    eng = Engine(PublicDataProvider(), aoi=a.aoi)
    osm = json.loads((config.DATA / "reference" / "osm_swimming_pool.geojson").read_text())["features"]
    geoms = [shape(f["geometry"]).buffer(0.00003) for f in osm]
    tree = STRtree(geoms)
    cases = json.loads(a.cases) if a.cases else CASES[a.aoi]
    # resolve ids without a cell: locate the pool cell by colour/salience via a 1-chip rerank against itself
    seed = eng.find_similar(["2022_e4472_n33675#31"], k=60, rerank=True, concept="swimming_pool")
    cell_of = {f["id"]: f["properties"]["cell"] for f in seed.result.geojson["features"]}
    for case, ids in cases.items():
        ex = [i if "#" in i else (f"{i}#{cell_of[i]}" if i in cell_of else i) for i in ids]
        for on in (False, True):
            t0 = time.perf_counter()
            env = eng.find_similar(ex, k=24, rerank=on, concept="swimming_pool")
            ms = (time.perf_counter() - t0) * 1000
            feats = env.result.geojson["features"]
            hit = sum(1 for f in feats if len(tree.query(box(*f["properties"]["cell_bbox"]), predicate="intersects")))
            tag = "on" if on else "off"
            sheet(eng, env, config.DATA / "out" / f"rerank_{a.aoi}_{case}_{tag}.png",
                  title=f"{case} rerank={tag} ex={ex} ({ms:.0f} ms)")
            dev = next((s.detail.split("·")[-1].strip() for s in env.steps if s.phase == "rerank"), "-")
            print(f"{case:14s} rerank={tag:3s} osm-proxy {hit:2d}/24  {ms:6.0f} ms  device={dev}  ex={ex}")


if __name__ == "__main__":
    main()
