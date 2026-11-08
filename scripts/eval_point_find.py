"""Evaluate "Find like this" (POST /api/v1/point/find) per object type: one click on an OSM-mapped example, then 1 and 3
"not this" clicks chosen by an oracle (the top-ranked results that match NO OSM polygon of that type: a simulated user
rejecting false positives). Precision@24 is measured against OSM (a LOWER bound: OSM misses real objects) for the raw
scan (no calibration) and the calibrated result; plus counts and latency. Contact sheets (top 24, green = OSM match)
go to data/out/t2_<type>_<k>.png for an eye-check. Results → data/out/eval_point_find.json.

  python scripts/eval_point_find.py [--base http://127.0.0.1:8000]
Run in the CPU env (needs shapely + PIL); the API does the work.
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw
from shapely.geometry import box, shape
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from groundtruth import config  # noqa: E402

TYPES = [  # (name, OSM reference file, place) — places chosen away from the Lake Norman label cluster
    ("swimming_pool", "osm_swimming_pool", "Ballantyne"),
    ("parking_lot", "osm_parking_lot", "Ballantyne"),
    ("solar_installation", "osm_solar_installation", None),
    ("warehouse", "osm_warehouse", "Steele Creek"),
]
K = 24


def post(base, path, body):
    r = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    d = json.load(urllib.request.urlopen(r, timeout=600))
    return d, (time.perf_counter() - t) * 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000/api/v1")
    a = ap.parse_args()
    places = json.loads((ROOT / "engine/groundtruth/conf/places.json").read_text())["places"]
    out = {"_doc": __doc__.split("\n")[0], "k": K, "types": {}}
    for name, ref, place in TYPES:
        osm = [f for f in json.loads((config.DATA / "reference" / f"{ref}.geojson").read_text())["features"] if f.get("geometry")]
        geoms = [shape(f["geometry"]) for f in osm]
        if place is None:  # the place holding the most OSM objects of this type
            place = max(places, key=lambda p: sum(box(*places[p]["bbox"]).contains(g.centroid) for g in geoms))
        bb = box(*places[place]["bbox"])
        inside = sorted([g for g in geoms if bb.contains(g.centroid) and g.area > 0], key=lambda g: (-g.area, g.centroid.x))
        if not inside:
            print(name, "no OSM example in", place)
            continue
        ex = inside[len(inside) // 3]  # a typical-size example, deterministic
        c = ex.representative_point()
        tree = STRtree(geoms)

        def is_ref(f):
            g = shape(f["geometry"])
            return any(geoms[i].distance(g) < 1e-4 for i in tree.query(g.buffer(1e-4)))

        rec = {"place": place, "example_lonlat": [round(c.x, 6), round(c.y, 6)], "osm_in_place": len(inside), "runs": []}
        negs: list[dict] = []
        for k_fb in (0, 1, 3):
            for cal in (False, True):
                body = {"positives": [{"lon": c.x, "lat": c.y}], "negatives": negs, "area": {"place": place}, "calibrate": cal}
                d, ms = post(a.base, "/point/find", body)
                fs = (d.get("result") or {}).get("geojson", {}).get("features", [])
                top = fs[:K]
                hits = [is_ref(f) for f in top]
                prec = sum(hits) / len(top) if top else None
                m = (d.get("result") or {}).get("metrics", {})
                run = {"feedback": k_fb, "calibrated": cal, "count": len(fs), "precision_at_24_vs_osm": None if prec is None else round(prec, 3),
                       "n": len(top), "ms": round(ms), "threshold": (m.get("calibration") or {}).get("threshold"), "status": d.get("status")}
                rec["runs"].append(run)
                print(name, run, flush=True)
                if cal:
                    sheet(top, hits, config.DATA / "out" / f"t2_{name}_{k_fb}.png")
                    calib_top = (fs, hits)
            # the oracle rejects the highest-ranked non-OSM results of the calibrated run (up to the next feedback level)
            fs, hits = calib_top
            want = {0: 1, 1: 3}.get(k_fb)
            if want:
                for f, h in zip(fs[:K], hits + [True] * K):
                    if len(negs) >= want:
                        break
                    if not h and f["properties"].get("example"):
                        negs.append({"id": f["properties"]["example"]})
        out["types"][name] = rec
    (config.DATA / "out" / "eval_point_find.json").write_text(json.dumps(out, indent=1))
    print("→", config.DATA / "out" / "eval_point_find.json")


def sheet(feats, hits, path):
    import io
    S, cols = 128, 6
    img = Image.new("RGB", (cols * (S + 4), ((len(feats) + cols - 1) // cols or 1) * (S + 4)), "white")
    for i, (f, h) in enumerate(zip(feats, hits)):
        g = shape(f["geometry"]).centroid
        d = 0.00035
        url = f"http://127.0.0.1:8000/api/v1/crop?bbox={g.x - d:.6f},{g.y - d * 0.82:.6f},{g.x + d:.6f},{g.y + d * 0.82:.6f}&px={S}"
        try:
            im = Image.open(io.BytesIO(urllib.request.urlopen(url, timeout=60).read())).convert("RGB").resize((S, S))
        except Exception:  # noqa: BLE001
            im = Image.new("RGB", (S, S), "grey")
        x, y = (i % cols) * (S + 4), (i // cols) * (S + 4)
        img.paste(im, (x + 2, y + 2))
        dr = ImageDraw.Draw(img)
        dr.rectangle([x + 1, y + 1, x + S + 2, y + S + 2], outline=(46, 139, 87) if h else (200, 60, 60), width=2)
        dr.rectangle([x + S // 2 - 6, y + S // 2 - 6, x + S // 2 + 8, y + S // 2 + 8], outline="white", width=1)
        dr.text((x + 5, y + 4), str(i + 1), fill="white")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


if __name__ == "__main__":
    main()
