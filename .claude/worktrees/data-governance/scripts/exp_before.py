"""BEFORE sheet (demo-stable-v1 behaviour): ranked re-rank top 50 in a place, same pick example, matched cell boxed."""
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.tools import Engine  # noqa: E402

place = sys.argv[1] if len(sys.argv) > 1 else "Ballantyne"
eng = Engine(PublicDataProvider(), aoi="county", year=2022)
pc = eng.pick_candidates("swimming pool")
ex = next(f["properties"]["example"] for f in pc.result.geojson["features"] if f["properties"]["sense_id"] == "backyard_pool")
env = eng.find_similar([ex], k=50, bbox=eng.place_bbox(place), rerank=True, concept="swimming_pool")
feats = env.result.geojson["features"]
tile, pad, cols = 150, 4, 10
img = Image.new("RGB", (cols * (tile + pad) + pad, 5 * (tile + 16 + pad) + 24), (18, 20, 24))
d = ImageDraw.Draw(img)
d.text((4, 4), f"BEFORE (demo-stable-v1: ranked re-rank top 50, no negatives, no object check) · {place} · red = matched cell", fill=(230, 230, 230))
for n, f in enumerate(feats):
    p = f["properties"]
    th = Image.open(io.BytesIO(eng.index.thumb_jpeg(eng.index.pos[p["chip_id"]]))).convert("RGB")
    w, s, e, nn = p["chip_bbox"]; cb = p["cell_bbox"]
    px = lambda lon, lat: ((lon - w) / (e - w) * 224, (nn - lat) / (nn - s) * 224)  # noqa: E731
    ImageDraw.Draw(th).rectangle([*px(cb[0], cb[3]), *px(cb[2], cb[1])], outline=(255, 70, 70), width=2)
    x = pad + (n % cols) * (tile + pad); y = 24 + (n // cols) * (tile + 16 + pad)
    img.paste(th.resize((tile, tile)), (x, y))
    d.text((x + 2, y + tile + 1), f"#{n + 1} {p['score']:.2f}", fill=(200, 200, 200))
out = ROOT / "data" / "out" / f"precision_{place.lower().replace(' ', '_')}_before.png"
img.save(out)
print("wrote", out)
