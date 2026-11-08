"""Phase 2 A/B: standard vs dense find_all in a place (in-process, GPU env; stop the API first).

  python scripts/dense_ab.py Ballantyne
Two example sets: (a) the demo pick (planner default sense + unpicked senses as negatives), (b) the evaluate.py set
(3 typical-size OSM pools as examples). Reports OSM recall over the OSM pools in the place (minus the examples), counts,
and writes contact sheets for eye precision: data/out/dense_<place>_top50_{standard,dense}.png and
data/out/dense_<place>_added.png (the objects dense adds; random 50).
"""
import io
import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.geo import to_measure, to_wgs84  # noqa: E402
from groundtruth.planner import DEFAULT_SENSE  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.tools import Engine  # noqa: E402

place = sys.argv[1] if len(sys.argv) > 1 else "Ballantyne"
eng = Engine(PublicDataProvider(download_scenes=False), aoi="county")
bb = eng.place_bbox(place)
osm = json.loads((config.DATA / "reference" / "osm_swimming_pool.geojson").read_text())["features"]
bbm = to_measure(box(*bb))
pools = [to_measure(shape(f["geometry"])) for f in osm if f.get("geometry")]
pools = [p for p in pools if p.intersects(bbm) and p.area > 5]


def recall(feats, targets):
    gs = [to_measure(shape(f["geometry"])).buffer(3) for f in feats]
    return sum(1 for t in targets if any(t.intersects(g) for g in gs))


def sheet(feats, path, title):
    T, G = 150, 10
    rows = (len(feats) + G - 1) // G
    im = Image.new("RGB", (G * T, rows * (T + 12) + 18), (17, 19, 24))
    d = ImageDraw.Draw(im)
    d.text((4, 3), title, fill=(230, 230, 230))
    for k, f in enumerate(feats):
        p = f["properties"]
        i = eng.index.pos[p["chip_id"]] if p.get("chip_id") in eng.index.pos else eng.index.pos[p["example"].split("#")[0]]
        th = Image.open(io.BytesIO(eng.index.thumb_jpeg(i))).convert("RGB")
        x0, y0 = float(eng.index.meta["x0_m"][i]), float(eng.index.meta["y0_m"][i])
        g = to_measure(shape(f["geometry"]))
        cx, cy = g.centroid.x, g.centroid.y
        px, py = (cx - x0) / config.GSD_M, (y0 + config.CHIP_M - cy) / config.GSD_M
        dd = ImageDraw.Draw(th)
        for pg in ([g] if g.geom_type == "Polygon" else list(g.geoms)):
            dd.line([((x - x0) / config.GSD_M, (y0 + config.CHIP_M - y) / config.GSD_M) for x, y in pg.exterior.coords],
                    fill=(255, 50, 50), width=1)
        c = th.crop((int(px - 40), int(py - 40), int(px + 40), int(py + 40))).resize((T, T))
        X, Y = (k % G) * T, 18 + (k // G) * (T + 12)
        im.paste(c, (X, Y))
        d.text((X + 2, Y + T), f"{k + 1}{' D' if p.get('dense_id') else ''} {p.get('confidence')}", fill=(200, 200, 200))
    im.save(path)


out = {}
# (a) demo pick
pc = eng.pick_candidates("swimming pool")
tiles = [f for f in pc.result.geojson["features"] if not f["properties"].get("no_clear_example")]
ch = next(f for f in tiles if f["properties"]["sense_id"] == DEFAULT_SENSE["swimming_pool"])
ex, neg = [ch["properties"]["example"]], [f["properties"]["example"] for f in tiles if f is not ch]
# (b) evaluate.py examples: 3 typical-size OSM pools
srt = sorted(pools, key=lambda p: p.area)
mid = srt[len(srt) // 2: len(srt) // 2 + 3]
ex_b = []
for p in mid:
    c = to_wgs84(p.centroid)
    rows = eng._bbox_rows([c.x - 1e-4, c.y - 1e-4, c.x + 1e-4, c.y + 1e-4])
    i = int(rows[0])
    x0, y0 = float(eng.index.meta["x0_m"][i]), float(eng.index.meta["y0_m"][i])
    pr, pc_ = int((y0 + config.CHIP_M - p.centroid.y) / (config.CHIP_M / 16)), int((p.centroid.x - x0) / (config.CHIP_M / 16))
    pr, pc_ = min(max(pr, 0), 15), min(max(pc_, 0), 15)
    cell = (pr * eng.index.grid // 16) * eng.index.grid + (pc_ * eng.index.grid // 16)
    ex_b.append(f"{eng.index.ids[i]}#{cell}:{pr * 16 + pc_}")
targets_b = [p for p in pools if p not in mid]
BACKYARD_MAX_M2 = 150.0  # "backyard-only recall": OSM pools up to this size (community pools are bigger)
for name, (E, N, S, targets) in {"demo_pick_hard": (ex, neg, [], pools), "demo_pick_soft": (ex, [], neg, pools),
                                  "osm_examples": (ex_b, [], [], targets_b)}.items():
    back = [t for t in targets if t.area <= BACKYARD_MAX_M2]
    res = {}
    for mode in (False, True):
        env = eng.find_all(E, N, place=place, concept="swimming_pool", outline_top=500, dense=mode, soft_exclude=S)
        feats = env.result.geojson["features"]
        m = env.result.metrics
        res["dense" if mode else "standard"] = {"count": len(feats), "osm_found": recall(feats, targets),
                                                "osm_total": len(targets), "backyard_found": recall(feats, back),
                                                "backyard_total": len(back), "dense_added": m.get("dense_added"),
                                                "dense_matched": m.get("dense_matched"),
                                                "ms": round(sum(s.ms for s in env.steps))}
        if name == "demo_pick_soft":
            tag = "dense" if mode else "standard"
            sheet(feats[:50], config.DATA / "out" / f"dense_{place}_top50_{tag}.png", f"{place} {tag}: top 50 of {len(feats)}")
            if mode:
                added = [f for f in feats if f["properties"].get("dense_id") and "chip_id" not in f["properties"]
                         or f["properties"].get("dense_id")]
                random.Random(0).shuffle(added)
                sheet(added[:50], config.DATA / "out" / f"dense_{place}_added.png",
                      f"{place} dense: random {min(50, len(added))} of {len(added)} dense objects (D = from dense)")
    out[name] = res
    print(name, json.dumps(res), flush=True)
(config.DATA / "out" / f"dense_ab_{place}.json").write_text(json.dumps(out, indent=1))
