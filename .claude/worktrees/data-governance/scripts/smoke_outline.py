"""Outline smoke test: SAM 2.1 on the picked pool + its top find_similar hits; overlay sheet in data/out/."""

from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.geo import to_measure  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.tools import Engine  # noqa: E402
from shapely.geometry import shape  # noqa: E402

AOI = sys.argv[1] if len(sys.argv) > 1 else "south_charlotte"
EX = sys.argv[2] if len(sys.argv) > 2 else "2022_e4472_n33675#30"
eng = Engine(PublicDataProvider(), aoi=AOI)
fs = eng.find_similar([EX], k=11)
ids = [EX] + [f"{f['id']}#{f['properties']['cell']}" for f in fs.result.geojson["features"]]
env = eng.outline(ids)
print(env.status, env.result.metrics, [s.model_dump() for s in env.steps])
env2 = eng.outline(ids)  # cached
print("cached run:", env2.result.metrics, env2.steps[0].ms, "ms")
tile, pad = 224, 4
sheet = Image.new("RGB", (6 * (tile + pad) + pad, 2 * (tile + pad + 16) + pad + 20), (18, 20, 24))
d = ImageDraw.Draw(sheet)
d.text((pad, 4), f"outline() SAM 2.1 tiny on {EX} + top find_similar hits (green = outline, red = prompt cell)", fill=(230, 230, 230))
for n, f in enumerate(env.result.geojson["features"]):
    p = f["properties"]; i = eng.index.pos[p["chip_id"]]
    img = Image.open(io.BytesIO(eng.index.thumb_jpeg(i))).convert("RGB")
    dd = ImageDraw.Draw(img)
    g = eng.index.grid; cp = config.CHIP_PX // g; r, c = divmod(p["cell"], g)
    dd.rectangle([c * cp, r * cp, (c + 1) * cp - 1, (r + 1) * cp - 1], outline=(255, 70, 70), width=1)
    if f["geometry"]:
        x0, y0 = float(eng.index.meta["x0_m"][i]), float(eng.index.meta["y0_m"][i])
        poly = to_measure(shape(f["geometry"]))
        for ring in [poly.exterior] if poly.geom_type == "Polygon" else [q.exterior for q in poly.geoms]:
            pts = [((x - x0) / config.GSD_M, (y0 + config.CHIP_M - y) / config.GSD_M) for x, y in ring.coords]
            dd.line(pts + [pts[0]], fill=(60, 255, 120), width=2)
    x = pad + (n % 6) * (tile + pad); y = 20 + pad + (n // 6) * (tile + pad + 16)
    sheet.paste(img, (x, y))
    d.text((x + 2, y + tile + 2), f"#{n} {p['area_m2']:.0f} m2  iou {p['sam_score']:.2f}  {p['ms']:.0f}ms", fill=(200, 200, 200))
out = config.DATA / "out" / f"{AOI}_outline.png"
sheet.save(out)
print("wrote", out)
