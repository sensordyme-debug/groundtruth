"""Eye-check of 'new pools since 2020 in Ballantyne': every object called new, 2020 chip vs 2022 chip side by side."""
import io, json, sys
import httpx
from PIL import Image, ImageDraw
c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=600)
r = c.post("/api/v1/agent/run", json={"text": "new pools since 2020 in Ballantyne"}).json()
cy = next(s for s in r["steps"] if s["tool"] == "compare_years")["envelope"]
new = [f for f in cy["result"]["geojson"]["features"] if f["properties"].get("change") == "new"]
T = 150
sheet = Image.new("RGB", (6 * T * 2 // 2 * 2, ((len(new) + 2) // 3) * (T + 14) + 16), (17, 19, 24))
d = ImageDraw.Draw(sheet)
d.text((4, 2), f"new since 2020 in Ballantyne: {len(new)} objects (left 2020, right 2022)", fill=(230, 230, 230))
for k, f in enumerate(new):
    p = f["properties"]
    cid = p["chip_id"]
    now = Image.open(io.BytesIO(c.get(f"/api/v1/chips/{cid}.jpg").content)).convert("RGB").resize((T, T))
    then_url = p.get("thumb_url_then")
    then = Image.open(io.BytesIO(c.get(then_url).content)).convert("RGB").resize((T, T)) if then_url else Image.new("RGB", (T, T))
    X, Y = (k % 3) * 2 * T, 16 + (k // 3) * (T + 14)
    sheet.paste(then, (X, Y)); sheet.paste(now, (X + T, Y))
    d.text((X + 2, Y + T), f"{k + 1} sim {p.get('change_sim')} col {p.get('colour_then')}→{p.get('colour_now')}", fill=(200, 200, 200))
sheet.save("data/out/new_since_2020_Ballantyne.png")
print(len(new), "new; sheet data/out/new_since_2020_Ballantyne.png")
