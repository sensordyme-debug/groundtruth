"""Phase 4 pre-test: 10 never-configured concepts, 12 hits each → contact sheets in data/out/openvocab_<concept>.png.
Eye-check each sheet, then record the right-out-of-12 in conf/openvocab.json → eye_precision.

  python scripts/openvocab_pretest.py [--concepts "tennis court,trampoline"] [--place Ballantyne | --county]
Runs in-process (GPU env). One heavy job: stop the API first if RAM is short.
"""
import argparse
import io
import json
import re
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.geo import to_measure  # noqa: E402
from groundtruth.openvocab import conf, find_anything  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.tools import Engine  # noqa: E402
from shapely.geometry import shape  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--concepts", default=None)
ap.add_argument("--place", default=None)
ap.add_argument("--tag", default="", help="suffix for sheet names (A/B runs)")
a = ap.parse_args()
eng = Engine(PublicDataProvider(download_scenes=False), aoi="county")
cons = a.concepts.split(",") if a.concepts else list(conf()["concepts"])
bb = eng.place_bbox(a.place) if a.place else None
summary = {}
for con in cons:
    t0 = time.time()
    feats, info = find_anything(eng, con, bb, a.place)
    top = feats[:12]
    T = 200
    sheet = Image.new("RGB", (6 * T, 2 * (T + 16) + 20), (17, 19, 24))
    d = ImageDraw.Draw(sheet)
    d.text((4, 3), f"'{con}' ({info['prompt']}): {len(feats)} found, top 12 · {info['chips_searched']} chips · "
                   f"{info['ms_total'] / 1000:.0f} s", fill=(230, 230, 230))
    for k, f in enumerate(top):
        p = f["properties"]
        i = eng.index.pos[p["chip_id"]]
        im = Image.open(io.BytesIO(eng.index.thumb_jpeg(i))).convert("RGB")
        dd = ImageDraw.Draw(im)
        x0, y0 = float(eng.index.meta["x0_m"][i]), float(eng.index.meta["y0_m"][i])
        gm = to_measure(shape(f["geometry"]))
        polys = [gm] if gm.geom_type == "Polygon" else list(gm.geoms)
        for pg in polys:
            pts = [((x - x0) / config.GSD_M, (y0 + config.CHIP_M - y) / config.GSD_M) for x, y in pg.exterior.coords]
            dd.line(pts, fill=(255, 60, 60), width=2)
        im = im.resize((T, T))
        x, y = (k % 6) * T, 20 + (k // 6) * (T + 16)
        sheet.paste(im, (x, y))
        d.text((x + 2, y + T + 1), f"{k + 1} det {p['detector_score']:.2f} {p['area_m2']:.0f} m²", fill=(200, 200, 200))
    out = config.DATA / "out" / f"openvocab_{re.sub(r'[^a-z0-9]+', '_', con)}{a.tag}.png"
    sheet.save(out)
    summary[con] = {"found": len(feats), "sheet": str(out.relative_to(config.ROOT)), **{k: info[k] for k in (
        "chips_searched", "raw_boxes", "boxes_after_dedupe", "rejected_by_area", "ms_total", "device")}}
    print(json.dumps({con: summary[con]}), flush=True)
(config.DATA / "out" / f"openvocab_pretest{a.tag}.json").write_text(json.dumps(summary, indent=1))
