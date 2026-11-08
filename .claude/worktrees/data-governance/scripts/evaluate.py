"""Evaluate against Monish's hand labels (read-only) + the OSM weak reference set. Writes docs/EVALUATION.md.

  python scripts/evaluate.py [--aoi county] [--year 2022] [--k 24]

Per concept with labels:
- search_text P@k: hits whose matching cell contains a true-match label OR touches an OSM-mapped object of the concept
  (known positives; unlabelled real matches count as misses → lower bound). Also P@k by labels only.
- find_all (re-ranked) leave-one-out from each true-match label (searched inside a 1.5 km box around it so local labels
  can be found), P@k with the same rule; look-alike rejection rate = 1 − (look-alike labels hit ÷ look-alikes nearby).
- pick_candidates: is a true-match chip (or an OSM-positive cell) among the 6 tiles?
OSM reference (swimming pools): recall of OSM pools inside each label cluster's box by find_all top 200; outline IoU vs
OSM pool polygons for outlined hits that overlap one.
Labels are never modified. Model versions are recorded in the report.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
from shapely.geometry import box, shape
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.geo import MEASURE_CRS, WGS84, to_measure, transformer  # noqa: E402
from groundtruth.labels import CONCEPTS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
QUERIES = {"swimming_pool": "swimming pool", "construction_site": "construction site", "warehouse": "warehouse",
           "parking_lot": "parking lot", "solar_installation": "solar panels"}


def label_cell(index, lon: float, lat: float) -> tuple[int, int] | None:
    """Chip containing the click where it is most central, and the 8×8 cell under the click."""
    x, y = transformer(WGS84, MEASURE_CRS).transform(lon, lat)
    m = index.meta
    cand = np.where((m["x0_m"] <= x) & (x < m["x0_m"] + config.CHIP_M) & (m["y0_m"] <= y) & (y < m["y0_m"] + config.CHIP_M))[0]
    if not len(cand):
        return None
    cx, cy = m["x0_m"][cand] + config.CHIP_M / 2, m["y0_m"][cand] + config.CHIP_M / 2
    i = int(cand[np.argmin((cx - x) ** 2 + (cy - y) ** 2)])
    size = config.CHIP_M / index.grid
    col = int((x - m["x0_m"][i]) // size)
    row = int((m["y0_m"][i] + config.CHIP_M - y) // size)
    return i, row * index.grid + col


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="county")
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--k", type=int, default=24)
    ap.add_argument("--max-loo", type=int, default=20, help="max leave-one-out queries per concept")
    a = ap.parse_args()
    from groundtruth.embed import CLIP_ID, DINO_ID
    from groundtruth.providers.public import PublicDataProvider
    from groundtruth.tools import Engine

    eng = Engine(PublicDataProvider(), aoi=a.aoi, year=a.year)
    k = a.k
    labels = json.loads((config.DATA / "labels" / "labels.geojson").read_text())["features"]
    by_c: dict[str, dict[str, list]] = defaultdict(lambda: {"true_match": [], "look_alike": []})
    for f in labels:
        p = f["properties"]
        lc = label_cell(eng.index, *f["geometry"]["coordinates"][:2])
        by_c[p["concept"]][p["kind"]].append({"id": p["id"], "lonlat": f["geometry"]["coordinates"][:2], "cell": lc})
    results: dict[str, dict] = {}
    for c in CONCEPTS:
        L = by_c.get(c, {"true_match": [], "look_alike": []})
        tm = [x for x in L["true_match"] if x["cell"]]
        la = [x for x in L["look_alike"] if x["cell"]]
        r: dict = {"labels": {"true_match": len(L["true_match"]), "look_alike": len(L["look_alike"]),
                              "true_in_index": len(tm), "lookalike_in_index": len(la)}}
        osm_p = config.DATA / "reference" / f"osm_{c}.geojson"
        osm = [shape(f["geometry"]) for f in json.loads(osm_p.read_text())["features"]] if osm_p.exists() else []
        osm_m = [to_measure(g if g.geom_type != "Point" else g.buffer(0.00004)) for g in osm]
        tree = STRtree(osm_m) if osm_m else None
        true_cells = {(x["cell"][0], x["cell"][1]) for x in tm}
        look_cells = {(x["cell"][0], x["cell"][1]) for x in la}

        def cell_box_m(i: int, cell: int):
            return box(*eng.index.cell_bounds_m(i, cell))

        def is_pos(i: int, cell: int, labels_only: bool = False) -> bool:
            b = cell_box_m(i, cell)
            for (li, lcell) in true_cells:
                if box(*eng.index.cell_bounds_m(li, lcell)).intersects(b):
                    return True
            return (not labels_only) and tree is not None and len(tree.query(b, predicate="intersects")) > 0

        def is_look(i: int, cell: int) -> bool:
            b = cell_box_m(i, cell)
            return any(box(*eng.index.cell_bounds_m(li, lcell)).intersects(b) for (li, lcell) in look_cells)

        if not tm and not osm:
            r["note"] = "no labels or reference for this concept"
            results[c] = r
            continue
        # search_text
        st = eng.search_text(QUERIES[c], k=k)
        hits = [(eng.index.pos[f["id"]], f["properties"]["cell"]) for f in st.result.geojson["features"]]
        r["search_text"] = {"P@k_labels_or_osm": round(sum(is_pos(i, cc) for i, cc in hits) / k, 3),
                            "P@k_labels_only": round(sum(is_pos(i, cc, True) for i, cc in hits) / k, 3)}
        # pick_candidates
        pc = eng.pick_candidates(QUERIES[c])
        reps = [(eng.index.pos[f["properties"]["chip_id"]], f["properties"]["cell"], f["properties"]["sense_id"])
                for f in pc.result.geojson["features"]]
        r["pick_candidates"] = {"intended_in_6": any(is_pos(i, cc) for i, cc, _ in reps),
                                "senses_with_positive": [s for i, cc, s in reps if is_pos(i, cc)]}
        # find_all leave-one-out, local box
        if tm:
            ps, ps_lab, look_hit, look_near, ious = [], [], 0, 0, []
            for x in tm[: a.max_loo]:
                lon, lat = x["lonlat"]
                bb = [lon - 0.009, lat - 0.007, lon + 0.009, lat + 0.007]  # ~1.6 × 1.5 km
                env = eng.find_all([f"{eng.index.ids[x['cell'][0]]}#{x['cell'][1]}"], k=k, bbox=bb, concept=c,
                                   rerank=True, outline_top=0)
                fh = [(eng.index.pos[f["properties"]["chip_id"]], f["properties"]["cell"]) for f in env.result.geojson["features"]]
                fh_all = [h for h in fh if h != tuple(x["cell"])]
                fh = fh_all[:k]  # v0.4: features = every counted object, ranked → P@k over the top k
                if not fh:
                    continue
                ps.append(sum(is_pos(i, cc) for i, cc in fh) / len(fh))
                ps_lab.append(sum(is_pos(i, cc, True) for i, cc in fh) / len(fh))
                bbm = to_measure(box(*bb))
                near = [lc for lc in look_cells if bbm.intersects(box(*eng.index.cell_bounds_m(*lc)))]
                look_near += len(near)
                look_hit += sum(1 for i, cc in fh_all if is_look(i, cc))
                for f in env.result.geojson["features"]:
                    if f["properties"]["geometry_kind"] == "outline" and tree is not None:
                        g = to_measure(shape(f["geometry"]))
                        for j in tree.query(g, predicate="intersects"):
                            o = osm_m[int(j)]
                            if o.geom_type in ("Polygon", "MultiPolygon"):
                                ious.append(g.intersection(o).area / max(g.union(o).area, 1e-9))
            r["find_all_loo"] = {"queries": len(ps), "P@k_labels_or_osm": round(float(np.mean(ps)), 3),
                                 "P@k_labels_only": round(float(np.mean(ps_lab)), 3),
                                 "lookalike_rejection": None if not look_near else round(1 - look_hit / look_near, 3),
                                 "lookalikes_nearby": look_near,
                                 "outline_iou_vs_osm_median": round(float(np.median(ious)), 3) if ious else None,
                                 "outline_iou_n": len(ious)}
        # look-alike rejection: for each look-alike label, search a box around it from up to 3 true-match examples;
        # rejected = the look-alike's cell is NOT among the top k
        if tm and la:
            exs = [f"{eng.index.ids[x['cell'][0]]}#{x['cell'][1]}" for x in tm[:3]]
            rej = 0
            for x in la:
                lon, lat = x["lonlat"]
                bb = [lon - 0.009, lat - 0.007, lon + 0.009, lat + 0.007]
                env = eng.find_all(exs, k=k, bbox=bb, concept=c, rerank=True, outline_top=0)
                lb = box(*eng.index.cell_bounds_m(*x["cell"]))
                hit = any(lb.intersects(box(*eng.index.cell_bounds_m(eng.index.pos[f["properties"]["chip_id"]],
                                                                      f["properties"]["cell"])))
                          for f in env.result.geojson["features"])
                rej += not hit
            r["lookalike_rejection_direct"] = {"lookalikes": len(la), "rejected": rej, "rate": round(rej / len(la), 3)}
        # OSM recall + outline IoU in a well-mapped place (pools: Ballantyne; parking: Uptown), OSM examples excluded
        place = {"swimming_pool": "Ballantyne", "parking_lot": "Uptown"}.get(c)
        if place and osm:
            bb = eng.place_bbox(place)
            bbm = to_measure(box(*bb))
            in_box = [o for o in osm_m if o.intersects(bbm) and o.area > 5]
            sizes = sorted(in_box, key=lambda o: o.area)
            mid = sizes[len(sizes) // 2: len(sizes) // 2 + 3]  # 3 typical-size mapped objects as examples
            ex_cells = []
            for o in mid:
                from groundtruth.geo import to_wgs84
                p = to_wgs84(o.centroid)
                lc = label_cell(eng.index, p.x, p.y)
                if lc:
                    ex_cells.append(f"{eng.index.ids[lc[0]]}#{lc[1]}")
            targets = [o for o in in_box if o not in mid]
            env = eng.find_all(ex_cells, k=200, bbox=bb, concept=c, rerank=True, outline_top=500)
            feats = env.result.geojson["features"]  # v0.4: the whole exhaustive, object-checked count
            cells = [to_measure(box(*f["properties"]["cell_bbox"])) for f in feats]
            found = sum(1 for o in targets if any(o.intersects(cb) for cb in cells))
            r["osm_recall_top200_in_place"] = {"place": place, "osm_objects": len(targets), "found": found,
                                               "counted_objects": len(feats),
                                               "recall": round(found / len(targets), 3) if targets else None,
                                               "note": "recall over OSM-mapped objects only; OSM is incomplete"}
            # outline IoU vs OSM polygons for the top hits that overlap one
            over = [f for f, cb in zip(feats, cells) if any(o.intersects(cb) for o in targets)][:30]
            if over:
                ol = eng.outline([f["properties"]["example"] for f in over], max=30)
                ious = []
                for f in ol.result.geojson["features"]:
                    if not f.get("geometry"):
                        continue
                    g = to_measure(shape(f["geometry"]))
                    best = max((g.intersection(o).area / max(g.union(o).area, 1e-9) for o in targets if o.intersects(g)), default=0)
                    ious.append(best)
                r["outline_iou_vs_osm"] = {"n": len(ious), "median": round(float(np.median(ious)), 3) if ious else None,
                                           "mean": round(float(np.mean(ious)), 3) if ious else None}
        results[c] = r
        print(c, json.dumps(r)[:400])
    out = {"generated": datetime.now().astimezone().isoformat(timespec="seconds"), "aoi": a.aoi, "year": eng.year,
           "index_version": config.INDEX_VERSION, "index_chips": len(eng.index), "k": k,
           "models": {"dino": DINO_ID, "clip": CLIP_ID, "sam": "sam2.1-hiera-tiny"}, "labels_total": len(labels),
           "results": results}
    (config.DATA / "out").mkdir(parents=True, exist_ok=True)
    (config.DATA / "out" / "evaluation.json").write_text(json.dumps(out, indent=1))
    write_md(out)


def write_md(o: dict) -> None:
    L = [f"# Evaluation — {o['generated'][:10]} (PARTIAL: labels are still being collected)", "",
         f"Index `{o['index_version']}` · AOI `{o['aoi']}` · NAIP {o['year']} · {o['index_chips']:,} chips · k = {o['k']} · "
         f"models: DINOv2 `{o['models']['dino']}`, OpenCLIP `{o['models']['clip']}`, SAM `{o['models']['sam']}` · "
         f"{o['labels_total']} human labels (read-only).", "",
         "**Bias warning:** 51 of the 59 swimming-pool labels sit in one 1.7 km stretch of the Lake Norman waterfront "
         "(see `scripts/label_qa.py`), so these scores mostly describe lakefront estates, not the whole county.", "",
         "**How to read:** a hit counts as correct if its matched 16.8 m cell contains a hand-labelled true match *or* touches an "
         "OSM-mapped object of the concept. Unlabelled, unmapped real matches count as misses, so all precision numbers are "
         "**lower bounds**. `labels only` ignores OSM. find_all is leave-one-out from each true-match label, searched in a "
         "~1.6 × 1.5 km box around it. Look-alike rejection = share of nearby look-alike labels that do *not* appear in the "
         "top k.", "",
         "| concept | labels T/L (in index) | search_text P@k (labels+OSM / labels) | find_all P@k, leave-one-out (labels+OSM / labels) | look-alike rejection (direct) | intended meaning in 6 picks | OSM recall, top 200 in place | outline IoU vs OSM (median, n) |",
         "|---|---|---|---|---|---|---|---|"]
    for c, r in o["results"].items():
        lab = r["labels"]
        if "search_text" not in r:
            L.append(f"| {c} | {lab['true_match']}/{lab['look_alike']} | – | – | – | – | – | – |")
            continue
        st, fa = r["search_text"], r.get("find_all_loo", {})
        rec = r.get("osm_recall_top200_in_place", {})
        lr = r.get("lookalike_rejection_direct", {})
        iou = r.get("outline_iou_vs_osm", {})
        L.append(f"| {c} | {lab['true_match']}/{lab['look_alike']} ({lab['true_in_index']}/{lab['lookalike_in_index']}) | "
                 f"{st['P@k_labels_or_osm']} / {st['P@k_labels_only']} | "
                 f"{fa.get('P@k_labels_or_osm', '–')} / {fa.get('P@k_labels_only', '–')} ({fa.get('queries', 0)} queries) | "
                 f"{(str(lr['rejected']) + '/' + str(lr['lookalikes']) + ' (' + str(lr['rate']) + ')') if lr else '–'} | "
                 f"{'yes' if r['pick_candidates']['intended_in_6'] else 'no'} | "
                 f"{(rec.get('place', '') + ': ' + str(rec.get('found')) + '/' + str(rec.get('osm_objects')) + ' (' + str(rec.get('recall')) + ')') if rec else '–'} | "
                 f"{iou.get('median', '–')} ({iou.get('n', 0)}) |")
    L += ["", "**Reading the numbers honestly**",
          "- *Intended meaning in 6 picks* uses the same lower-bound rule. A tile counts only if its cell is labelled or OSM-mapped, "
          "so a correct 'backyard pool' tile on an unlabelled, unmapped pool scores *no*. Check `data/out/*pick_candidates*.png` by eye.",
          "- *find_all leave-one-out* draws its queries from the Lake Norman cluster (the bias above).",
          "- *Look-alike rejection* tests each look-alike label directly: a box around it, searched from 3 true-match examples. "
          "The look-alikes are lake water, blue roofs and tennis courts.",
          "- *OSM recall* counts only OSM-mapped objects in the place; most backyard pools are unmapped, so it can't measure "
          "precision. The 3 typical-size mapped objects used as examples are excluded.",
          "- *Outline IoU* compares SAM 2.1 outlines prompted by one 16.8 m cell with OSM polygons. For pools the outline often "
          "includes the pool deck. For parking lots, one cell prompt outlines only part of a large lot, so IoU is low by design.",
          "- By-eye top-24 counts for the re-rank (county) are in `docs/STATUS_FOR_MONISH.md` and the build log.",
          "", "Reproduce: `bash scripts/gtg.sh python scripts/evaluate.py --aoi county` (raw numbers in `data/out/evaluation.json`)."]
    (ROOT / "docs" / "EVALUATION.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
