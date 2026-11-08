"""Ballantyne count + precision sheets via the running API.

  python scripts/exp_ballantyne.py [--place Ballantyne] [--wait 240]
1. pick "swimming pool" → backyard pool tile = example; every other sense tile = negative ("not this").
2. find_all in the place (exhaustive scan) → count + time.
3. wait for the background outlines (poll outline cached_only), then find_all again → count after the object check.
4. sheets: BEFORE = old ranked re-rank (find_similar rerank, bbox, top 50); AFTER = new find_all top 50 (outlines drawn).
"""
import argparse
import io
import json
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("--place", default="Ballantyne")
ap.add_argument("--wait", type=int, default=240)
a = ap.parse_args()
c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=600)


def post(tool, body):
    t0 = time.perf_counter()
    e = c.post(f"/api/v1/tools/{tool}", json={"aoi": "county", **body}).json()
    return e, (time.perf_counter() - t0) * 1000


pc, _ = post("pick_candidates", {"query": "swimming pool"})
tiles = pc["result"]["geojson"]["features"]
ex = next(f["properties"]["example"] for f in tiles if f["properties"]["sense_id"] == "backyard_pool")
neg = [f["properties"]["example"] for f in tiles if f["properties"]["sense_id"] != "backyard_pool"
       and not f["properties"].get("no_clear_example")]
print("example", ex, "negatives", len(neg))
fa, ms = post("find_all", {"examples": [ex], "exclude": neg, "concept": "swimming_pool", "place": a.place, "outline_top": 300})
m = fa["result"]["metrics"]
print(f"find_all {a.place}: {ms:.0f} ms · detected {m['count_detected']} · outlined {m['outlined']} · queued {m['outlines_queued']} · "
      f"chips {m.get('chips_in_box')} cells {m.get('cells_scanned')} prefiltered {m.get('chips_prefiltered')} raw {m.get('raw_detections')}")
for s in fa["steps"]:
    print(f"   {s['phase']:8s} {s['ms']:8.0f} ms  {s['detail'][:150]}")
ids = [f["properties"]["example"] for f in fa["result"]["geojson"]["features"]][:300]
t0 = time.time()
while time.time() - t0 < a.wait:
    pend = 0
    for k in range(0, len(ids), 50):
        o, _ = post("outline", {"examples": ids[k:k + 50], "max": 50, "cached_only": True, "concept": "swimming_pool"})
        pend += o["result"]["metrics"].get("pending", 0)
    print(f"  outlines pending {pend} ({time.time() - t0:.0f}s)")
    if pend == 0:
        break
    time.sleep(15)
fa2, ms2 = post("find_all", {"examples": [ex], "exclude": neg, "concept": "swimming_pool", "place": a.place, "outline_top": 300})
m2 = fa2["result"]["metrics"]
print(f"after outlines: {ms2:.0f} ms · detected {m2['count_detected']} · outlined {m2['outlined']} · rejected {m2['rejected_by_object_check']} · "
      f"acres {m2['area_acres_total']} · median {m2['median_area_m2']} m²")
before, _ = post("find_similar", {"examples": [ex], "k": 50, "bbox": fa2["result"]["metrics"]["bbox"], "rerank": True,
                                  "concept": "swimming_pool"})


def sheet(feats, path, title):
    tile, pad, cols = 160, 4, 10
    rows = -(-len(feats) // cols)
    img = Image.new("RGB", (cols * (tile + pad) + pad, rows * (tile + 18 + pad) + 24), (18, 20, 24))
    d = ImageDraw.Draw(img)
    d.text((4, 4), title, fill=(230, 230, 230))
    for n, f in enumerate(feats):
        p = f["properties"]
        th = Image.open(io.BytesIO(c.get(p["thumb_url"]).content)).convert("RGB")
        w, s, e, nn = p["chip_bbox"]
        dd = ImageDraw.Draw(th)

        def px(lon, lat):
            return ((lon - w) / (e - w) * 224, (nn - lat) / (nn - s) * 224)

        g = f.get("geometry")
        if g and g["type"] == "Polygon":
            dd.line([px(*pt) for pt in g["coordinates"][0]], fill=(60, 255, 120) if p.get("geometry_kind") == "outline" else (255, 70, 70), width=2)
        else:
            cb = p["cell_bbox"]
            dd.rectangle([*px(cb[0], cb[3]), *px(cb[2], cb[1])], outline=(255, 70, 70), width=2)
        x = pad + (n % cols) * (tile + pad); y = 24 + (n // cols) * (tile + 18 + pad)
        img.paste(th.resize((tile, tile)), (x, y))
        d.text((x + 2, y + tile + 2), f"#{n + 1} {p.get('confidence', p.get('score', 0)):.2f}", fill=(200, 200, 200))
    img.save(path)
    print("wrote", path)


out = ROOT / "data" / "out"
slug = a.place.lower().replace(" ", "_")
sheet(before["result"]["geojson"]["features"][:50], out / f"precision_{slug}_before.png", f"BEFORE (ranked re-rank top 50) · {a.place}")
sheet(fa2["result"]["geojson"]["features"][:50], out / f"precision_{slug}_after.png",
      f"AFTER (exhaustive scan + negatives + object check) top 50 · {a.place} · {m2['count_detected']} detected")
json.dump({"ms_first": ms, "ms_after": ms2, "m_first": m, "m_after": m2}, open(out / f"ballantyne_count_{slug}.json", "w"), indent=1)
