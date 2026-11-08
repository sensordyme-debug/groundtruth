"""Read-only QA of the human labels (never writes to data/labels/).

  python scripts/label_qa.py [--concept swimming_pool] [--radius-m 300] [--crop-m 60]
Reports clusters (labels within --radius-m of another, single-linkage), spread across named areas, and renders
data/out/label_check_<concept>.png: a --crop-m NAIP 2022 crop centred on each click (id, T/L, timestamp).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Point, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.geo import MEASURE_CRS, WGS84, transformer  # noqa: E402

# rough named areas (EPSG:4326 boxes) for the spread count; anything else → "other"
AREAS = {
    "UNC Charlotte": (-80.760, 35.290, -80.715, 35.325),
    "Ballantyne": (-80.880, 35.020, -80.820, 35.070),
    "Myers Park": (-80.840, 35.180, -80.810, 35.210),
    "Lake Norman": (-80.960, 35.400, -80.850, 35.515),
    "Matthews": (-80.745, 35.090, -80.690, 35.140),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concept", default="swimming_pool")
    ap.add_argument("--radius-m", type=float, default=300)
    ap.add_argument("--crop-m", type=float, default=60)
    ap.add_argument("--year", type=int, default=2022)
    a = ap.parse_args()
    snap = config.DATA / "labels" / "labels.geojson"
    feats = [f for f in json.loads(snap.read_text())["features"] if f["properties"]["concept"] == a.concept]
    print(f"{len(feats)} {a.concept} labels (read-only from {snap})")
    if not feats:
        return
    t = transformer(WGS84, MEASURE_CRS)
    xy = np.array([t.transform(*f["geometry"]["coordinates"][:2]) for f in feats])
    # single-linkage clusters at radius
    n = len(feats)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    D = np.sqrt(((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1))
    near = (D < a.radius_m) & ~np.eye(n, dtype=bool)
    for i, j in zip(*np.where(near)):
        parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    clusters = sorted([g for g in groups.values() if len(g) > 1], key=len, reverse=True)
    in_cluster = sum(len(g) for g in clusters)
    print(f"within {a.radius_m:.0f} m of another label: {in_cluster}/{n}; clusters: {len(clusters)}; "
          f"biggest: {len(clusters[0]) if clusters else 0}")
    for k, g in enumerate(clusters[:10], 1):
        ids = [feats[i]["properties"]["id"][:8] for i in g]
        span = D[np.ix_(g, g)].max()
        print(f"  cluster {k}: {len(g)} labels, span {span:.0f} m: {ids}")
    dup = [(feats[i]["properties"]["id"][:8], feats[j]["properties"]["id"][:8], round(float(D[i, j]), 1))
           for i, j in zip(*np.where(np.triu(D < 10, 1)))]
    if dup:
        print(f"  near-duplicates (< 10 m apart): {dup}")
    spread = Counter()
    for f in feats:
        p = Point(f["geometry"]["coordinates"][:2])
        spread[next((name for name, b in AREAS.items() if box(*b).contains(p)), "other")] += 1
    print("spread:", dict(spread))
    kinds = Counter(f["properties"]["kind"] for f in feats)
    print("kinds:", dict(kinds))

    # contact sheet (read imagery via the provider; downloads/caches scenes as needed)
    from groundtruth.providers.public import PublicDataProvider

    prov = PublicDataProvider()
    half = a.crop_m / 2
    tile, pad, head, cols = 200, 6, 26, 6
    rows = -(-n // cols)
    sheet = Image.new("RGB", (cols * (tile + pad) + pad, rows * (tile + pad + 30) + head + pad), (18, 20, 24))
    d = ImageDraw.Draw(sheet)
    d.text((pad, 6), f"{a.concept} labels: {a.crop_m:.0f} m NAIP {a.year} crops centred on each click (+ = click)", fill=(230, 230, 230))
    cl_of = {i: k for k, g in enumerate(clusters, 1) for i in g}
    for k, (f, (x, y)) in enumerate(zip(feats, xy)):
        g = config.GSD_M
        bx = (np.floor((x - half) / g) * g, np.floor((y - half) / g) * g, np.ceil((x + half) / g) * g, np.ceil((y + half) / g) * g)
        try:
            im = prov.read_grid(bx, a.year)
            img = Image.fromarray(im.rgb).resize((tile, tile), Image.NEAREST)
        except Exception as e:  # outside coverage etc.
            img = Image.new("RGB", (tile, tile), (60, 20, 20))
            ImageDraw.Draw(img).text((6, 90), str(e)[:30], fill=(255, 255, 255))
        dd = ImageDraw.Draw(img)
        c = tile / 2
        dd.line([(c - 8, c), (c + 8, c)], fill=(255, 60, 60), width=2)
        dd.line([(c, c - 8), (c, c + 8)], fill=(255, 60, 60), width=2)
        cx = pad + (k % cols) * (tile + pad)
        cy = head + pad + (k // cols) * (tile + pad + 30)
        sheet.paste(img, (cx, cy))
        p = f["properties"]
        kl = "T" if p["kind"] == "true_match" else "L"
        tag = f" C{cl_of[k]}" if k in cl_of else ""
        d.text((cx + 2, cy + tile + 2), f"{p['id'][:8]} {kl}{tag}", fill=(220, 220, 220))
        d.text((cx + 2, cy + tile + 15), p["created_at"][:19].replace("T", " "), fill=(150, 150, 150))
    out = config.DATA / "out" / f"label_check_{a.concept.split('_')[-1]}s.png"
    sheet.save(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
