"""F0.2 before/after evidence for the display mosaic, rendered directly with the provider (no API involved).

  python scripts/mosaic_evidence.py shots   # 3x3 tile blocks centred on real seams, z12/z14/16, raw vs mosaic
  python scripts/mosaic_evidence.py bench   # tile render latency, raw vs mosaic (same tiles), p50/p95

Seams are found from the scene footprints: the visible raw seam is the edge of a higher-priority scene where a
lower-priority scene continues (provider: first valid pixel wins). For each area the seam point nearest the area
centre between scenes of DIFFERENT acquisition dates is used (Lake Norman: the point given in the F0.2 ticket).
Images: docs/media/v3/f0/seam_<area>_z<z>_{before,after}.png. Numbers: docs/media/v3/f0/evidence.json.
"""
from __future__ import annotations

import io
import json
import random
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

import morecantile  # noqa: E402
from PIL import Image  # noqa: E402
from shapely.geometry import Point, box, shape  # noqa: E402
from shapely.ops import nearest_points, unary_union  # noqa: E402

from groundtruth import config  # noqa: E402
from groundtruth.geo import to_measure, to_wgs84  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402

OUT = ROOT / "docs" / "media" / "v3" / "f0"
TMS = morecantile.tms.get("WebMercatorQuad")
P = PublicDataProvider(download_scenes=False)
PLACES = json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"]
# (slug, place, year, explicit seam point or None). Mosaic v1 covers 2022 for these three places only.
# Lake Norman: the ticket's point, and the longest detected seamline (the 7 km flight-line seam in the main lake).
# Myers Park / Matthews have no open water: their seams are scene-footprint edges.
TARGETS = [("lake_norman", "Lake Norman", 2022, (-80.875, 35.50)),
           ("lake_norman_main", "Lake Norman", 2022, None),
           ("myers_park", "Myers Park", 2022, None),
           ("matthews", "Matthews", 2022, None)]
BENCH_YEARS = [2022]


def seam_lines(place: str, year: int):
    """Visible raw seams inside the place bbox: [(LineString in EPSG:32617, upper scene, lower scene)]."""
    bb = to_measure(box(*PLACES[place]["bbox"]))
    items = {it["scene_id"]: it for it in P._items(year)}
    scenes = [s.scene_id for s in P.list_scenes(tuple(PLACES[place]["bbox"]), year)]
    fps = {s: to_measure(shape(items[s]["geometry"])) for s in scenes}
    out = []
    for k, s in enumerate(scenes):  # s is drawn above every later scene
        above = unary_union([fps[t] for t in scenes[:k]]) if k else None
        edge = fps[s].boundary.intersection(bb)
        if above is not None:
            edge = edge.difference(above)
        for t in scenes[k + 1:]:
            seg = edge.intersection(fps[t].buffer(-1))
            if not seg.is_empty and seg.length > 50:
                out.append((seg, s, t, items[s]["datetime"][:10], items[t]["datetime"][:10]))
    return out


def water_seams(place: str, year: int):
    """Orthomosaic seamlines on water recorded by the mosaic build (meta.json "water.seams") inside the place bbox."""
    from shapely.geometry import LineString
    p = config.DATA / "tiles" / "mosaic" / config.data_version("mosaic") / str(year) / "meta.json"
    if not p.exists():
        return []
    bb = to_measure(box(*PLACES[place]["bbox"]))
    out = []
    for s in json.loads(p.read_text()).get("water", {}).get("seams", []):
        ln = LineString([s["xy0_m"], s["xy1_m"]]).intersection(bb)
        if not ln.is_empty and ln.length > 200:
            out.append((ln, s))
    return out


def seam_point(place: str, year: int, given, kind: str = "auto"):
    """Seam to show: an orthomosaic seamline on water from the mosaic meta when the place has one (kind 'water' or
    'auto'), else the scene-footprint edge (kind 'footprint'). Returns ((lon, lat), info, LineString in EPSG:32617)."""
    ws = water_seams(place, year) if kind in ("auto", "water") else []
    if ws:
        if given is not None:
            pt = to_measure(Point(*given))
            ln, s = min(ws, key=lambda w: w[0].distance(pt))
        else:
            ln, s = max(ws, key=lambda w: w[0].length)
            pt = ln.interpolate(0.5, normalized=True)
        sp = nearest_points(ln, pt)[0]
        lon, lat = to_wgs84(sp).coords[0]
        return (lon, lat), {"seam": "orthomosaic seamline on water (detected)", "detected_step_dn": s["step_dn"],
                            "seam_length_m": s["length_m"]}, ln
    lines = seam_lines(place, year)
    if given is not None:
        pt = to_measure(Point(*given))
        cand = lines
    else:
        pt = to_measure(box(*PLACES[place]["bbox"])).centroid
        cand = [ln for ln in lines if ln[3] != ln[4]] or lines
    best = min(cand, key=lambda ln: ln[0].distance(pt))
    sp = nearest_points(best[0], pt)[0]
    lon, lat = to_wgs84(sp).coords[0]
    return (lon, lat), {"seam": "scene footprint edge", "upper": best[1], "lower": best[2], "upper_date": best[3],
                        "lower_date": best[4]}, best[0]


def decode(png: bytes | None, size: int = 256) -> np.ndarray:
    if not png:
        return np.zeros((size, size, 4), np.uint8)
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))


def stitch(fn, year, z, cx, cy) -> np.ndarray:
    rows = []
    for dy in (-1, 0, 1):
        rows.append(np.concatenate([decode(fn(year, z, cx + dx, cy + dy)) for dx in (-1, 0, 1)], axis=1))
    return np.concatenate(rows, axis=0)


def to_px(lon, lat, z, cx, cy):
    x, y = TMS.xy(lon, lat)
    ul = TMS.xy_bounds(morecantile.Tile(cx - 1, cy - 1, z))
    res = (ul.right - ul.left) / 256
    return (x - ul.left) / res, (ul.top - y) / res


def seam_step(img: np.ndarray, line_m, z, cx, cy, k: int = 3, control: int = 40) -> dict:
    """Median |left - right| colour step across the raw seam line (k px each side), and the same across parallel
    control lines `control` px away (natural texture). ratio = step / control."""
    rgb = img[..., :3].astype(np.float32)
    H, W = rgb.shape[:2]
    pts = []
    for d in np.arange(0, line_m.length, 2.0):
        a, b = line_m.interpolate(d), line_m.interpolate(min(d + 2.0, line_m.length))
        la, lb = to_wgs84(a).coords[0], to_wgs84(b).coords[0]
        pa, pb = np.array(to_px(*la, z, cx, cy)), np.array(to_px(*lb, z, cx, cy))
        t = pb - pa
        if np.linalg.norm(t) == 0:
            continue
        n = np.array([-t[1], t[0]]) / np.linalg.norm(t)
        pts.append((pa, n))

    def step(offset):
        vals = []
        for pa, n in pts:
            c = pa + n * offset
            l, r = c - n * k, c + n * k
            if all(0 <= q[0] < W and 0 <= q[1] < H for q in (l, r)):
                vl = rgb[int(l[1]), int(l[0])]; vr = rgb[int(r[1]), int(r[0])]
                if img[int(l[1]), int(l[0]), 3] and img[int(r[1]), int(r[0]), 3]:
                    vals.append(np.abs(vl - vr).mean())
        return float(np.median(vals)) if vals else float("nan"), len(vals)

    s, n = step(0)
    c1, _ = step(control)
    c2, _ = step(-control)
    ctrl = float(np.nanmean([c1, c2]))
    # the mosaic moves the transition to the medial line of the overlap (up to ~300 m from the raw seam): take the
    # worst median step over parallel lines across that band too
    offs = np.arange(-300.0, 300.1, 15.0) / _m_per_px(z, cy)
    band = [b for b in (step(o)[0] for o in offs) if np.isfinite(b)]
    bmax = float(max(band)) if band else float("nan")
    return {"step": round(s, 2), "control": round(ctrl, 2), "ratio": round(s / ctrl, 2) if ctrl else None, "n": n,
            "band_max_step": round(bmax, 2), "band_ratio": round(bmax / ctrl, 2) if ctrl else None}


def _m_per_px(z: int, cy: int) -> float:
    b = TMS.xy_bounds(morecantile.Tile(0, cy, z))
    lat = TMS.lnglat((b.left + b.right) / 2, (b.top + b.bottom) / 2)[1]
    return (b.right - b.left) / 256 * float(np.cos(np.radians(lat)))


def shots() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ev = {"created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "mosaic_version": config.data_version("mosaic"), "shots": []}
    for slug, place, year, given in TARGETS:
        (lon, lat), info, line_m = seam_point(place, year, given)
        for z in (12, 14, 16):
            t = TMS.tile(lon, lat, z)
            before = stitch(P.render_tile_raw, year, z, t.x, t.y)
            covered = P.render_tile_mosaic(year, z, t.x, t.y) is not None
            after = stitch(P.render_tile, year, z, t.x, t.y)
            fb = OUT / f"seam_{slug}_z{z}_before.png"
            fa = OUT / f"seam_{slug}_z{z}_after.png"
            Image.fromarray(before).save(fb, optimize=True)
            Image.fromarray(after).save(fa, optimize=True)
            row = {"area": place, "year": year, "z": z, "centre_tile": [t.x, t.y], "seam_lonlat": [round(lon, 5), round(lat, 5)],
                   **info, "mosaic_covers_centre": covered,
                   "before": seam_step(before, line_m, z, t.x, t.y), "after": seam_step(after, line_m, z, t.x, t.y),
                   "files": [str(fb.relative_to(ROOT)), str(fa.relative_to(ROOT))]}
            ev["shots"].append(row)
            print(json.dumps(row), flush=True)
    (OUT / "evidence.json").write_text(json.dumps(ev, indent=1))


def bench(n: int = 50, seed: int = 7) -> None:
    rng = random.Random(seed)
    tiles = []
    names = sorted(PLACES)
    while len(tiles) < n:
        pl = PLACES[names[rng.randrange(len(names))]]["bbox"]
        z = rng.choice([12, 13, 14, 15, 16, 17])
        lon, lat = rng.uniform(pl[0], pl[2]), rng.uniform(pl[1], pl[3])
        year = rng.choice(BENCH_YEARS)
        t = TMS.tile(lon, lat, z)
        if P.render_tile_mosaic(year, z, t.x, t.y) is None:
            continue
        tiles.append((year, z, t.x, t.y))
    res = {"raw": [], "mosaic": []}
    for k, tl in enumerate(tiles):  # alternate which path goes first (page-cache fairness)
        order = ("raw", "mosaic") if k % 2 == 0 else ("mosaic", "raw")
        for which in order:
            fn = P.render_tile_raw if which == "raw" else P.render_tile_mosaic
            t0 = time.perf_counter(); png = fn(*tl); dt = (time.perf_counter() - t0) * 1000
            if png is not None:
                res[which].append(dt)
    out = {"tiles": len(tiles), "zooms": "12-17", "seed": seed}
    for k, v in res.items():
        out[k] = {"n": len(v), "p50_ms": round(float(np.percentile(v, 50)), 1), "p95_ms": round(float(np.percentile(v, 95)), 1),
                  "mean_ms": round(float(np.mean(v)), 1)}
    print(json.dumps(out))
    p = OUT / "evidence.json"
    ev = json.loads(p.read_text()) if p.exists() else {}
    ev["latency"] = out
    p.write_text(json.dumps(ev, indent=1))


if __name__ == "__main__":
    {"shots": shots, "bench": bench}[sys.argv[1]]()
