"""Contact sheet of docks ranked by how 'new' they look (2020 | 2022 side by side) → data/out/dock_change_sheet.jpg."""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.geo import to_measure, to_wgs84  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402

d = json.loads((config.data_dir("derived") / "Lake_Norman" / "houses.geojson").read_text())
rows = []
for h in d["features"]:
    p = h["properties"]
    for dg, r in zip(p.get("dock_geometries", []), p.get("dock_new_2020", [])):
        if r.get("new") is not None:
            rows.append((r["deck_old"] / max(r["deck_2022"], 1e-3), r, dg, p["id"]))
rows.sort(key=lambda x: x[0])
n = int(sys.argv[1]) if len(sys.argv) > 1 else 24
prov = PublicDataProvider(download_scenes=False)
S = 160
sheet = Image.new("RGB", (4 * (2 * S + 8), ((min(n, len(rows)) + 3) // 4) * (S + 22)), "white")
for i, (ratio, r, dg, hid) in enumerate(rows[:n]):
    bx = to_wgs84(to_measure(shape(dg)).buffer(25).envelope).bounds
    x, y = (i % 4) * (2 * S + 8), (i // 4) * (S + 22)
    for j, yr in enumerate((2020, 2022)):
        im = Image.fromarray(prov.get_imagery(bx, yr).rgb).resize((S, S))
        sheet.paste(im, (x + j * S, y))
    ImageDraw.Draw(sheet).text((x + 2, y + S + 4), f"{i} r={ratio:.2f} w20={r['water_old']:.2f} new={r['new']}", fill="black")
out = config.DATA / "out" / "dock_change_sheet.jpg"
sheet.save(out, quality=85)
print(out, len(rows))
