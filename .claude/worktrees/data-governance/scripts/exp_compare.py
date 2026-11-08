"""compare_years check: find_all pools (2022) → compare_years vs 2020 → sheet of 2020|2022 pairs with the verdict."""
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.index import ChipIndex  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.tools import Engine  # noqa: E402

AOI = sys.argv[1] if len(sys.argv) > 1 else "south_charlotte"
EX = sys.argv[2] if len(sys.argv) > 2 else "2022_e4472_n33675#31"
eng = Engine(PublicDataProvider(), aoi=AOI, year=2022)
fa = eng.find_all([EX], k=40, concept="swimming_pool", outline_top=0)
exs = [f["properties"]["example"] for f in fa.result.geojson["features"]][:24]
cy = eng.compare_years(exs, from_year=2020, concept="swimming_pool")
print(cy.status, cy.result.metrics)
old = ChipIndex(config.precompute_dir(2020, AOI))
t, pad = 160, 4
sheet = Image.new("RGB", (6 * (2 * t + 3 * pad), 4 * (t + 22) + 24), (18, 20, 24))
d = ImageDraw.Draw(sheet)
d.text((4, 4), f"compare_years {AOI}: left 2020 | right 2022, red = cell, verdict below", fill=(230, 230, 230))
col = {"new": (80, 220, 120), "existing": (180, 180, 180), "unsure": (240, 190, 60), "no_data": (200, 80, 80)}
for n, f in enumerate(cy.result.geojson["features"]):
    p = f["properties"]
    i = eng.index.pos[p["chip_id"]]
    oid = "2020_" + p["chip_id"].split("_", 1)[1]
    imgs = [old.thumb_jpeg(old.pos[oid]) if oid in old.pos else None, eng.index.thumb_jpeg(i)]
    x0 = (n % 6) * (2 * t + 3 * pad) + pad
    y0 = 24 + (n // 6) * (t + 22)
    for k, b in enumerate(imgs):
        im = Image.open(io.BytesIO(b)).convert("RGB").resize((t, t)) if b else Image.new("RGB", (t, t), (40, 0, 0))
        g = eng.index.grid; cp = t / g; r, c = divmod(p["cell"], g)
        ImageDraw.Draw(im).rectangle([c * cp, r * cp, (c + 1) * cp, (r + 1) * cp], outline=(255, 60, 60), width=2)
        sheet.paste(im, (x0 + k * (t + pad), y0))
    d.text((x0, y0 + t + 3), f"{p['change']}  sim {p.get('change_sim')}  col {p.get('colour_then')}→{p.get('colour_now')}",
           fill=col.get(p["change"], (255, 255, 255)))
out = config.DATA / "out" / f"compare_{AOI}.png"
sheet.save(out)
print("wrote", out)
