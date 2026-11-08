"""In-process calibration of the exhaustive scan (run with the API STOPPED to avoid loading models twice).

  python scripts/exp_calib.py --place Ballantyne
pick → backyard tile (colour-checked) → scan the place → outline all objects (SAM, GPU) → object check →
counts + sheets: top 50 after check, a random 50 of the counted objects (to judge the whole count, not only the head),
and the rejected ones.
"""
import argparse
import io
import json
import random
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.tools import Engine  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--place", default="Ballantyne")
ap.add_argument("--max-outline", type=int, default=400)
a = ap.parse_args()
eng = Engine(PublicDataProvider(), aoi="county", year=2022)
t0 = time.perf_counter()
pc = eng.pick_candidates("swimming pool")
print(f"pick {1000 * (time.perf_counter() - t0):.0f} ms:",
      [(f["properties"]["sense_id"], f["properties"].get("example"), f["properties"].get("cell_colour"),
        f["properties"]["no_clear_example"]) for f in pc.result.geojson["features"]])
tiles = pc.result.geojson["features"]
ex = next(f["properties"]["example"] for f in tiles if f["properties"]["sense_id"] == "backyard_pool" and not f["properties"]["no_clear_example"])
neg = [f["properties"]["example"] for f in tiles if f["properties"]["sense_id"] != "backyard_pool" and not f["properties"]["no_clear_example"]]
t0 = time.perf_counter()
env = eng.find_all([ex], neg, place=a.place, concept="swimming_pool", outline_top=0)
ms_scan = (time.perf_counter() - t0) * 1000
m = env.result.metrics
print(f"scan {a.place}: {ms_scan:.0f} ms · objects {m['count_detected']} · {m.get('chips_prefiltered')} of {m.get('chips_in_box')} chips "
      f"at full res · raw {m.get('raw_detections')}")
ids = [f["properties"]["example"] for f in env.result.geojson["features"]][: a.max_outline]
t0 = time.perf_counter()
for k in range(0, len(ids), 50):
    eng.outline(ids[k:k + 50], max=50)
ms_ol = (time.perf_counter() - t0) * 1000
print(f"outlined {len(ids)} in {ms_ol / 1000:.1f} s ({ms_ol / max(len(ids), 1):.0f} ms each)")
t0 = time.perf_counter()
env2 = eng.find_all([ex], neg, place=a.place, concept="swimming_pool", outline_top=0)
m2 = env2.result.metrics
print(f"after object check: {(time.perf_counter() - t0) * 1000:.0f} ms · counted {m2['count_detected']} · rejected "
      f"{m2['rejected_by_object_check']} · acres {m2['area_acres_total']} · median {m2['median_area_m2']} m²")
rej_ids = [i for i in ids if i not in {f["properties"]["example"] for f in env2.result.geojson["features"]}]


def sheet(feats, path, title):
    tile, pad, cols = 150, 4, 10
    rows = max(1, -(-len(feats) // cols))
    img = Image.new("RGB", (cols * (tile + pad) + pad, rows * (tile + 16 + pad) + 24), (18, 20, 24))
    d = ImageDraw.Draw(img)
    d.text((4, 4), title, fill=(230, 230, 230))
    for n, f in enumerate(feats):
        p = f["properties"]
        i = eng.index.pos[p["chip_id"]]
        th = Image.open(io.BytesIO(eng.index.thumb_jpeg(i))).convert("RGB")
        w, s, e, nn = p["chip_bbox"]
        dd = ImageDraw.Draw(th)
        px = lambda lon, lat: ((lon - w) / (e - w) * 224, (nn - lat) / (nn - s) * 224)  # noqa: E731
        g = f.get("geometry")
        if g and g["type"] == "Polygon":
            dd.line([px(*pt) for pt in g["coordinates"][0]], fill=(60, 255, 120) if p.get("geometry_kind") == "outline" else (255, 70, 70), width=2)
        x = pad + (n % cols) * (tile + pad); y = 24 + (n // cols) * (tile + 16 + pad)
        img.paste(th.resize((tile, tile)), (x, y))
        chk = p.get("object_check") or {}
        d.text((x + 2, y + tile + 1), f"#{p.get('rank', n + 1)} {p.get('confidence', 0):.2f} {chk.get('reason', '')[:22]}", fill=(200, 200, 200))
    img.save(path)
    print("wrote", path)


out = ROOT / "data" / "out"
slug = a.place.lower().replace(" ", "_")
feats = env2.result.geojson["features"]
sheet(feats[:50], out / f"calib_{slug}_top50.png", f"{a.place}: counted {m2['count_detected']}, top 50")
random.seed(0)
sheet(random.sample(feats, min(50, len(feats))), out / f"calib_{slug}_random50.png", f"{a.place}: random 50 of {m2['count_detected']} counted")
rej = []
for rid in rej_ids[:50]:
    o = eng.outline([rid], max=1, concept="swimming_pool").result.geojson["features"]
    if o:
        o[0]["properties"].update(chip_bbox=eng.index.feature(eng.index.pos[o[0]["properties"]["chip_id"]])["properties"]["chip_bbox"],
                                  geometry_kind="outline", rank=len(rej) + 1)
        rej.append(o[0])
sheet(rej, out / f"calib_{slug}_rejected.png", f"{a.place}: rejected by object check (first 50)")
json.dump({"pick": [f["properties"] for f in tiles], "scan_ms": ms_scan, "m_scan": m, "m_after": m2},
          open(out / f"calib_{slug}.json", "w"), indent=1, default=str)
