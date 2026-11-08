"""Phase 1.3: Overture query times, naive (pyarrow read + shapely filter per query) vs DuckDB spatial store."""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from shapely import wkb
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.vector import VectorStore  # noqa: E402

places = json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"]
Q = [("buildings in Ballantyne", "buildings", places["Ballantyne"]["bbox"], {}),
     ("buildings in Uptown", "buildings", places["Uptown"]["bbox"], {}),
     ("restaurants in Uptown", "places", places["Uptown"]["bbox"], {"categories": ["restaurant"]}),
     ("schools county-wide", "places", None, {"categories": ["school", "elementary_school", "high_school", "middle_school"]}),
     ("name search 'Myers Park'", "places", None, {"name": "myers park"})]


def naive(table, bb, kw):
    t0 = time.perf_counter()
    t = pq.read_table(config.DATA / "layers" / f"overture_{table}.parquet").to_pydict()
    q = box(*bb) if bb else None
    n = 0
    for i, g in enumerate(t["geometry_wkb"]):
        if kw.get("categories") and t["category"][i] not in kw["categories"]:
            continue
        if kw.get("name") and kw["name"] not in (t["name"][i] or "").lower():
            continue
        if q is not None and not wkb.loads(g).intersects(q):
            continue
        n += 1
    return n, (time.perf_counter() - t0) * 1000


vs = VectorStore()
_ = vs.con
print(f"duckdb load: {vs.load_ms} ms (one-off, at API start)")
for name, table, bb, kw in Q:
    n0, ms0 = naive(table, bb, kw)
    runs = []
    for _ in range(5):
        fc, ms = (vs.places(bb, **kw) if table == "places" else vs.buildings(bb))
        runs.append(ms)
    print(f"{name:28s} naive {ms0:8.0f} ms ({n0}) | duckdb median {np.median(runs):6.1f} ms ({len(fc['features'])})")
