"""Visual precision check of a planner request via the API: random 30 counted objects → data/out/planner_<slug>.png."""
import io
import random
import re
import sys
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

text = sys.argv[1] if len(sys.argv) > 1 else "solar on warehouses"
c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=600)
r = c.post("/api/v1/agent/run", json={"text": text}).json()
fa = next(s["envelope"] for s in r["steps"] if s["tool"] == "find_all")
cur = [s["envelope"] for s in r["steps"] if s["tool"] == "filter"]
feats = (cur[-1] if cur else fa)["result"]["geojson"]["features"]
random.Random(0).shuffle(feats)
feats = feats[:30]
T, G = 160, 6
sheet = Image.new("RGB", (G * T, ((len(feats) + G - 1) // G) * (T + 14) + 16), (17, 19, 24))
d = ImageDraw.Draw(sheet)
d.text((4, 2), f"'{text}': random {len(feats)} of {len(feats) if not cur else len(cur[-1]['result']['geojson']['features'])} counted", fill=(220, 220, 220))
for k, f in enumerate(feats):
    ex = f["properties"]["example"]
    cid, rest = ex.split("#")
    cell, patch = (int(x) for x in rest.split(":")) if ":" in rest else (int(rest), None)
    im = Image.open(io.BytesIO(c.get(f"/api/v1/chips/{cid}.jpg").content)).convert("RGB").resize((T, T))
    dd = ImageDraw.Draw(im)
    s = T / 224
    if patch is not None:
        py, px = divmod(patch, 16)
        dd.rectangle([px * 14 * s, py * 14 * s, (px + 1) * 14 * s, (py + 1) * 14 * s], outline=(255, 60, 60), width=2)
    else:
        cy, cx = divmod(cell, 8)
        dd.rectangle([cx * 28 * s, cy * 28 * s, (cx + 1) * 28 * s, (cy + 1) * 28 * s], outline=(255, 60, 60), width=2)
    x, y = (k % G) * T, 16 + (k // G) * (T + 14)
    sheet.paste(im, (x, y))
    d.text((x + 2, y + T), f"{k + 1} c{f['properties'].get('confidence')}", fill=(200, 200, 200))
out = Path(__file__).resolve().parents[1] / "data" / "out" / f"planner_{re.sub(r'[^a-z0-9]+', '_', text.lower())}.png"
sheet.save(out)
print(out)
