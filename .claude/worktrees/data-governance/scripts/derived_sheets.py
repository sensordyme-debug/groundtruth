"""Eye-check sheets for the derived concepts: random 30 each of house_with_pool, lakefront, has_dock (seeded).
House outline yellow, its pool(s) magenta, docks orange + link line, shore point cyan dot. Local NAIP COGs.
  python scripts/derived_sheets.py <derived dir>   → data/out/derived_<concept>.png
"""
import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from shapely.geometry import Point, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.geo import to_measure, to_wgs84  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402

d = Path(sys.argv[1])
houses = json.loads((d / "houses.geojson").read_text())["features"]
prov = PublicDataProvider(download_scenes=False)
g = config.GSD_M


def tile(h, half=60.0, T=200):
    c = to_measure(shape(h["geometry"])).centroid
    b = (c.x - half, c.y - half, c.x + half, c.y + half)
    b = tuple(round(v / g) * g for v in b)
    im = prov.read_grid(b, prov.primary_year)
    img = Image.fromarray(im.rgb)
    dr = ImageDraw.Draw(img)

    def px(x, y):
        return ((x - b[0]) / g, (b[3] - y) / g)

    def poly(gj, col, w=2):
        gm = to_measure(shape(gj))
        for pg in ([gm] if gm.geom_type == "Polygon" else list(gm.geoms)):
            dr.line([px(x, y) for x, y in pg.exterior.coords], fill=col, width=w)

    p = h["properties"]
    poly(h["geometry"], (255, 230, 0))
    for pg in p.get("pool_geometries", []):
        poly(pg, (255, 43, 214))
    for dg in p.get("dock_geometries", []):
        poly(dg, (255, 138, 0), 3)
        dc, hc = to_measure(shape(dg)).centroid, to_measure(shape(h["geometry"])).centroid
        dr.line([px(hc.x, hc.y), px(dc.x, dc.y)], fill=(255, 138, 0), width=1)
    if p.get("shore_point"):
        s = to_measure(Point(*p["shore_point"]))
        x, y = px(s.x, s.y)
        dr.ellipse([x - 4, y - 4, x + 4, y + 4], outline=(0, 230, 255), width=2)
    return img.resize((T, T))


for name, sel in (("house_with_pool", lambda p: True), ("lakefront", lambda p: p.get("lakefront")),
                  ("has_dock", lambda p: p.get("lakefront") and p.get("has_dock"))):
    pool = [h for h in houses if sel(h["properties"])]
    sample = random.Random(7).sample(pool, min(30, len(pool)))
    T = 200
    sheet = Image.new("RGB", (6 * T, 5 * (T + 14) + 18), (17, 19, 24))
    dd = ImageDraw.Draw(sheet)
    dd.text((4, 3), f"{name}: random {len(sample)} of {len(pool)} (house yellow · pool magenta · dock orange · shore cyan)",
            fill=(230, 230, 230))
    for k, h in enumerate(sample):
        X, Y = (k % 6) * T, 18 + (k // 6) * (T + 14)
        sheet.paste(tile(h), (X, Y))
        p = h["properties"]
        dd.text((X + 2, Y + T), f"{k + 1} {p.get('building_class') or '-'} {p['footprint_m2']:.0f}m² shore {p.get('dist_shore_m')}",
                fill=(200, 200, 200))
    sheet.save(config.DATA / "out" / f"derived_{name}.png")
    print(name, len(pool))
