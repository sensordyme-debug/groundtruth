"""Phase 7: precompute the area inventory for a 1 km² centre of each demo place (laptop job; CPU is fine).

  python scripts/build_inventory.py [places…] [--km 1.0] [--sheet]
→ data/inventory/<place>/inventory.geojson + summary.json (+ data/out/inventory_<place>_sheet.png with --sheet:
  50 random masks with their labels, for the eye-check of class accuracy).
Needs: NAIP COGs for the box (fetched from Planetary Computer on demand, public), data/layers/overture_buildings.parquet,
data/lidar/<place>/ when available.
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.geo import to_measure  # noqa: E402
from groundtruth.inventory import centre_bbox, conf, run  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("places", nargs="*")
ap.add_argument("--km", type=float, default=None)
ap.add_argument("--sheet", action="store_true")
a = ap.parse_args()
places = json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"]
prov = PublicDataProvider()
eng = SimpleNamespace(provider=prov, year=prov.primary_year)
km = a.km or conf()["centre_km"]
INV_ROOT = config.next_data_dir("inventory")
for pl in a.places or list(places):
    t0 = time.time()
    bb = centre_bbox(places[pl]["bbox"], km)
    res = run(eng, bb, log=lambda s: print(s, flush=True))
    out = INV_ROOT / pl.replace(" ", "_")
    out.mkdir(parents=True, exist_ok=True)
    (out / "inventory.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": res["features"]}))
    meta = {k: v for k, v in res.items() if k != "features"}
    meta.update(place=pl, km=km, built=time.strftime("%Y-%m-%dT%H:%M:%S%z"), year=eng.year)
    (out / "summary.json").write_text(json.dumps(meta, indent=1))
    print(f"DONE {pl}: {res['masks']} masks, {res['unlabelled_pct']}% unlabelled, {time.time() - t0:.0f} s; "
          + ", ".join(f"{k} {v['cover_pct']}%" for k, v in sorted(res["summary"].items(), key=lambda t: -t[1]["cover_pct"])),
          flush=True)
    if a.sheet and res["features"]:
        im = prov.get_imagery(bb, eng.year)
        x0, y1 = im.transform.c, im.transform.f
        feats = random.Random(0).sample(res["features"], min(50, len(res["features"])))
        T, G = 140, 10
        sheet = Image.new("RGB", (G * T, 5 * (T + 26) + 16), (17, 19, 24))
        d = ImageDraw.Draw(sheet)
        d.text((4, 2), f"{pl} inventory: random {len(feats)} of {res['masks']} masks (label · how)", fill=(230, 230, 230))
        base = Image.fromarray(im.rgb)
        for k, f in enumerate(feats):
            gm = to_measure(shape(f["geometry"]))
            b = gm.bounds
            px0, py0 = (b[0] - x0) / config.GSD_M, (y1 - b[3]) / config.GSD_M
            px1, py1 = (b[2] - x0) / config.GSD_M, (y1 - b[1]) / config.GSD_M
            pad = max(10, 0.4 * max(px1 - px0, py1 - py0))
            crop = base.crop((int(px0 - pad), int(py0 - pad), int(px1 + pad), int(py1 + pad))).copy()
            dd = ImageDraw.Draw(crop)
            for pg in ([gm] if gm.geom_type == "Polygon" else list(gm.geoms)):
                dd.line([((x - x0) / config.GSD_M - (px0 - pad), (y1 - y) / config.GSD_M - (py0 - pad))
                         for x, y in pg.exterior.coords], fill=(255, 40, 40), width=2)
            crop = crop.resize((T, T))
            X, Y = (k % G) * T, 16 + (k // G) * (T + 26)
            sheet.paste(crop, (X, Y))
            p = f["properties"]
            d.text((X + 2, Y + T), f"{k + 1} {p['class']}", fill=(235, 235, 235))
            d.text((X + 2, Y + T + 12), p["label_how"][:22], fill=(160, 160, 160))
        sheet.save(config.DATA / "out" / f"inventory_{pl.replace(' ', '_')}_sheet.png")
