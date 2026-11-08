"""Contact sheet of pick_candidates runs (box = localised cell; 'no clear example' tiles shown as such)."""
import io
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.tools import Engine  # noqa: E402

queries = sys.argv[1:] or ["swimming pool", "solar panels", "parking lot"]
eng = Engine(PublicDataProvider(), aoi="county", year=2022)
tile, pad = 200, 6
img = Image.new("RGB", (6 * (tile + pad) + pad, len(queries) * (tile + 40) + 30), (18, 20, 24))
d = ImageDraw.Draw(img)
d.text((6, 6), "pick_candidates: red box = localised object cell; colour senses must show their colour", fill=(230, 230, 230))
for r, q in enumerate(queries):
    t0 = time.perf_counter()
    env = eng.pick_candidates(q)
    ms = (time.perf_counter() - t0) * 1000
    y = 30 + r * (tile + 40)
    d.text((6, y), f"'{q}' ({ms:.0f} ms, confidence {env.confidence})", fill=(200, 220, 255))
    for k, f in enumerate(env.result.geojson["features"][:6]):
        p = f["properties"]; x = pad + k * (tile + pad)
        if p.get("no_clear_example"):
            t = Image.new("RGB", (224, 224), (40, 40, 44))
            ImageDraw.Draw(t).text((40, 100), "no clear example", fill=(230, 200, 120))
        else:
            t = Image.open(io.BytesIO(eng.index.thumb_jpeg(eng.index.pos[p["chip_id"]]))).convert("RGB")
            w, s, e, n = p["chip_bbox"]; cb = p["cell_bbox"]
            px = lambda lon, lat: ((lon - w) / (e - w) * 224, (n - lat) / (n - s) * 224)  # noqa: E731
            ImageDraw.Draw(t).rectangle([*px(cb[0], cb[3]), *px(cb[2], cb[1])], outline=(255, 60, 60), width=3)
        img.paste(t.resize((tile, tile)), (x, y + 14))
        d.text((x + 2, y + tile + 16), f"{p['sense_label'][:24]} {p.get('cell_colour') if p.get('cell_colour') is not None else ''}", fill=(220, 220, 220))
out = ROOT / "data" / "out" / "pick_contact_sheet.png"
img.save(out)
print("wrote", out)
