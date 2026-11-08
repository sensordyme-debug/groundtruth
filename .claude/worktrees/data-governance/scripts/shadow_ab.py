"""Item 3 A/B: which dense additions (demo pick, soft negatives) the shadow rule would remove, via the running API.
Sheets: data/out/shadow_<place>_additions.png (random 50 dense additions = "before") and
data/out/shadow_<place>_removed.png (every addition the rule removes, ≤ 50). Eye-check both.

  python scripts/shadow_ab.py Ballantyne Uptown
"""
import io
import json
import random
import sys
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.dense import DenseSet, dense_dir  # noqa: E402
from groundtruth.geo import to_measure  # noqa: E402
from groundtruth.rerank import concept_conf  # noqa: E402

c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=600)
sh = {"v_max": 110, "tall_m": 8, **concept_conf("swimming_pool").get("dense", {}).get("shadow", {})}
pc = c.post("/api/v1/tools/pick_candidates", json={"query": "swimming pool"}).json()
tiles = [f for f in pc["result"]["geojson"]["features"] if not f["properties"].get("no_clear_example")]
ch = next(f for f in tiles if f["properties"]["sense_id"] == "backyard_pool")
ex, neg = ch["properties"]["example"], [f["properties"]["example"] for f in tiles if f is not ch]


def sheet(feats, path, title):
    T, G = 140, 10
    rows = max(1, (len(feats) + G - 1) // G)
    im = Image.new("RGB", (G * T, rows * (T + 12) + 18), (17, 19, 24))
    d = ImageDraw.Draw(im)
    d.text((4, 3), title, fill=(230, 230, 230))
    for k, f in enumerate(feats):
        cid = f["properties"]["example"].split("#")[0]
        th = Image.open(io.BytesIO(c.get(f"/api/v1/chips/{cid}.jpg").content)).convert("RGB")
        meta = c.get(f"/api/v1/chips/{cid}").json() if False else None
        x0, y0 = f["_x0"], f["_y0"]
        g = to_measure(shape(f["geometry"]))
        dd = ImageDraw.Draw(th)
        for pg in ([g] if g.geom_type == "Polygon" else list(g.geoms)):
            dd.line([((x - x0) / config.GSD_M, (y0 + config.CHIP_M - y) / config.GSD_M) for x, y in pg.exterior.coords],
                    fill=(255, 50, 50), width=1)
        cx, cy = (g.centroid.x - x0) / config.GSD_M, (y0 + config.CHIP_M - g.centroid.y) / config.GSD_M
        crop = th.crop((int(cx - 45), int(cy - 45), int(cx + 45), int(cy + 45))).resize((T, T))
        X, Y = (k % G) * T, 18 + (k // G) * (T + 12)
        im.paste(crop, (X, Y))
        d.text((X + 2, Y + T), f"{k + 1} V{f['_v']:.0f} tall{f['_t']:.0f}", fill=(200, 200, 200))
    im.save(path)


import pyarrow.parquet as pq  # noqa: E402

chips = pq.read_table(config.precompute_dir(2022, "county") / "chips.parquet", columns=["chip_id", "x0_m", "y0_m"]).to_pydict()
xy = dict(zip(chips["chip_id"], zip(chips["x0_m"], chips["y0_m"])))
out = {}
for place in sys.argv[1:]:
    env = c.post("/api/v1/tools/find_all", json={"examples": [ex], "soft_exclude": neg, "place": place,
                                                 "concept": "swimming_pool", "outline_top": 300}).json()
    feats = env["result"]["geojson"]["features"]
    ds = DenseSet(place, "swimming_pool", 2022)
    z = np.load(dense_dir(place, "swimming_pool", 2022) / "shadow_v1.npz")
    by_id = {f["id"]: k for k, f in enumerate(ds.feats)}
    adds = []
    for f in feats:
        k = by_id.get(f["properties"].get("dense_id"))
        if k is None:
            continue
        f["_v"], f["_t"] = float(z["v_mean"][k]), float(np.nan_to_num(z["tall_within_m"][k], nan=0))
        f["_x0"], f["_y0"] = xy[f["properties"]["example"].split("#")[0]]
        adds.append(f)
    removed = [f for f in adds if f["_v"] < sh["v_max"] and f["_t"] > sh["tall_m"]]
    random.Random(0).shuffle(adds)
    sheet(adds[:50], config.DATA / "out" / f"shadow_{place}_additions.png",
          f"{place}: random {min(50, len(adds))} of {len(adds)} dense objects in the count (before the shadow rule)")
    sheet(removed[:50], config.DATA / "out" / f"shadow_{place}_removed.png",
          f"{place}: the {len(removed)} dense objects the shadow rule removes (V < {sh['v_max']} and > {sh['tall_m']} m within 30 m)")
    out[place] = {"count": len(feats), "dense_objects": len(adds), "removed_by_rule": len(removed)}
    print(place, out[place], flush=True)
(config.DATA / "out" / "shadow_ab.json").write_text(json.dumps(out, indent=1))
