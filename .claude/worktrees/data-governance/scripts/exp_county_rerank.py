"""County re-rank numbers: 3 backyard pools → top 24 in a place, with/without re-rank (sheets for by-eye counting).

  python scripts/exp_county_rerank.py
Ballantyne: examples = 3 small OSM-mapped pools (backyard size, 20–80 m²) inside the place.
Lake Norman: examples = 3 of Monish's true-match pool labels (read-only).
Also prints a proxy: hits whose matched cell touches an OSM pool or a labelled pool (lower bound, examples excluded).
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
from shapely.geometry import Point, box, shape
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate import label_cell  # noqa: E402
from groundtruth import config  # noqa: E402
from groundtruth.geo import to_measure  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.tools import Engine  # noqa: E402
from smoke_slice import sheet  # noqa: E402

eng = Engine(PublicDataProvider(), aoi="county", year=2022)
osm = [shape(f["geometry"]) for f in json.loads((config.DATA / "reference" / "osm_swimming_pool.geojson").read_text())["features"]]
labels = [f for f in json.loads((config.DATA / "labels" / "labels.geojson").read_text())["features"]
          if f["properties"]["concept"] == "swimming_pool" and f["properties"]["kind"] == "true_match"]
pos_m = [to_measure(g if g.geom_type != "Point" else g.buffer(0.00004)) for g in osm] + \
        [to_measure(Point(f["geometry"]["coordinates"][:2]).buffer(0.00004)) for f in labels]
tree = STRtree(pos_m)


def examples_for(place: str) -> list[str]:
    bb = box(*eng.place_bbox(place))
    if place == "Lake Norman":
        pts = [f["geometry"]["coordinates"][:2] for f in labels if bb.contains(Point(f["geometry"]["coordinates"][:2]))]
        rng = np.random.default_rng(0)
        pts = [pts[i] for i in rng.choice(len(pts), 3, replace=False)]
    else:
        small = [g for g in osm if g.geom_type != "Point" and bb.contains(g.centroid) and 20 <= to_measure(g).area <= 80]
        pts = [(g.centroid.x, g.centroid.y) for g in small[:3]]
    out = []
    for lon, lat in pts:
        lc = label_cell(eng.index, lon, lat)
        if lc:
            out.append(f"{eng.index.ids[lc[0]]}#{lc[1]}")
    return out


for place in ("Ballantyne", "Lake Norman"):
    ex = examples_for(place)
    ex_boxes = [to_measure(box(*eng.index.cell_bounds_m(eng.index.pos[e.split('#')[0]], int(e.split('#')[1])))) for e in ex]
    for on in (False, True):
        t0 = time.perf_counter()
        env = eng.find_similar(ex, k=24, bbox=eng.place_bbox(place), rerank=on, concept="swimming_pool")
        ms = (time.perf_counter() - t0) * 1000
        feats = env.result.geojson["features"]
        hit = 0
        for f in feats:
            cb = to_measure(box(*f["properties"]["cell_bbox"]))
            if any(cb.intersects(b) for b in ex_boxes):
                continue
            hit += len(tree.query(cb, predicate="intersects")) > 0
        tag = "on" if on else "off"
        slug = place.lower().replace(" ", "_")
        sheet(eng, env, config.DATA / "out" / f"county_rerank_{slug}_{tag}.png",
              title=f"{place}: 3 backyard pools, rerank={tag}, top 24 ({ms:.0f} ms)")
        print(f"{place:12s} rerank={tag:3s} proxy(OSM/labels) {hit:2d}/24  {ms:6.0f} ms  ex={ex}")
