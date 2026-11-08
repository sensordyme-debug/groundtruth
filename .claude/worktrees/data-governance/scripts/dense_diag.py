"""Phase 2 diagnosis: for each OSM pool in a place, why dense misses it (no blob / verifier / match rule)."""
import json
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Point, box, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.dense import DenseSet  # noqa: E402
from groundtruth.geo import to_measure  # noqa: E402

place = sys.argv[1] if len(sys.argv) > 1 else "Ballantyne"
bb = json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"][place]["bbox"]
ds = DenseSet(place, "swimming_pool", 2022)
osm = json.loads((config.DATA / "reference" / "osm_swimming_pool.geojson").read_text())["features"]
bbm = to_measure(box(*bb))
pools = [to_measure(shape(f["geometry"])) for f in osm if f.get("geometry")]
pools = [p for p in pools if p.intersects(bbm) and p.area > 5]
pts = np.column_stack([ds.x_m, ds.y_m])
rows = []
for p in pools:
    d = np.hypot(pts[:, 0] - p.centroid.x, pts[:, 1] - p.centroid.y)
    k = int(np.argmin(d))
    f = ds.feats[k]["properties"]
    rows.append({"osm_area_m2": round(p.area), "nearest_proposal_m": round(float(d[k]), 1), "passed": bool(ds.passed[k]),
                 "reason": f["object_check"]["reason"], "blob_m2": f["blob_m2"], "colour": round(float(ds.colour[k]), 2)})
for r in sorted(rows, key=lambda r: r["nearest_proposal_m"]):
    print(r)
print("no proposal within 10 m:", sum(r["nearest_proposal_m"] > 10 for r in rows), "of", len(rows))
print("proposal but verifier rejects:", sum(r["nearest_proposal_m"] <= 10 and not r["passed"] for r in rows))
