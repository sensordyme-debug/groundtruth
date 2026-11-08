"""Step 4 smoke tests → contact sheets in data/out/ (PNG grids with rank + score + chip id).

  python scripts/smoke_slice.py --aoi unc_charlotte [--pick-chip <chip_id>]
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.tools import Engine  # noqa: E402

OUT = config.DATA / "out"


def sheet(eng: Engine, env, path: Path, cols: int = 6, title: str = "") -> None:
    feats = env.result.geojson["features"]
    tile, pad, head = 224, 4, 28
    rows = max(1, -(-len(feats) // cols))
    img = Image.new("RGB", (cols * (tile + pad) + pad, rows * (tile + pad + 16) + pad + head), (18, 20, 24))
    d = ImageDraw.Draw(img)
    d.text((pad, 8), title, fill=(230, 230, 230))
    for n, f in enumerate(feats):
        i = eng.index.pos[f["id"]]
        th = Image.open(io.BytesIO(eng.index.thumb_jpeg(i))).convert("RGB")
        x = pad + (n % cols) * (tile + pad)
        y = head + pad + (n // cols) * (tile + pad + 16)
        img.paste(th, (x, y))
        p = f["properties"]
        if "cell" in p:  # outline the matching 56 px sub-cell
            r, c = divmod(int(p["cell"]), eng.index.grid)
            cp = tile // eng.index.grid
            ImageDraw.Draw(img).rectangle([x + c * cp, y + r * cp, x + (c + 1) * cp - 1, y + (r + 1) * cp - 1],
                                          outline=(255, 70, 70), width=2)
        extra = f" {p['sense_label']}" if "sense_label" in p else f" {f['id'][5:]}"
        d.text((x + 2, y + tile + 2), f"#{p['rank']} {p['score']:.3f}{extra}", fill=(200, 200, 200))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    print("wrote", path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="unc_charlotte")
    ap.add_argument("--query", default="swimming pool")
    ap.add_argument("--pick-chip", default=None, help="chip id to use as the find_similar example")
    a = ap.parse_args()
    eng = Engine(PublicDataProvider(), aoi=a.aoi)
    slug = a.query.replace(" ", "_")
    st = eng.search_text(a.query, k=24)
    sheet(eng, st, OUT / f"{a.aoi}_search_text_{slug}_top24.png", title=f"search_text('{a.query}') top 24 · {a.aoi}")
    pc = eng.pick_candidates(a.query)
    sheet(eng, pc, OUT / f"{a.aoi}_pick_candidates_{slug}.png", cols=6, title=f"pick_candidates('{a.query}')")
    picks = pc.result.geojson["features"]
    examples = [a.pick_chip] if a.pick_chip else [
        f["properties"]["example"] for f in picks if f["properties"].get("sense_id", "").endswith("_pool")]
    fs = None
    for ex in examples or [st.result.geojson["features"][0]["id"]]:
        fs = eng.find_similar([ex], k=24)
        sense = next((f["properties"]["sense_id"] for f in picks if f["properties"].get("example") == ex), "manual")
        sheet(eng, fs, OUT / f"{a.aoi}_find_similar_{sense}.png",
              title=f"find_similar([{ex}]) from '{sense}' top 24 (red box = matching cell)")
    summary = {name: {"status": e.status, "metrics": e.result.metrics, "steps": [s.model_dump() for s in e.steps],
                      "ids": [f["id"] for f in e.result.geojson["features"]]}
               for name, e in (("search_text", st), ("pick_candidates", pc), ("find_similar", fs))}
    (OUT / f"{a.aoi}_smoke_{slug}.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
