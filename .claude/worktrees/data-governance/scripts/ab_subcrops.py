"""A/B: search_text top-k with vs without CLIP 2x2 sub-crops, on an index that stores all 5 views.

  GROUNDTRUTH_CLIP_SUBCROPS=1 python scripts/precompute_county.py --aoi unc_charlotte --index-only
  python scripts/ab_subcrops.py --aoi unc_charlotte --query "swimming pool"
Writes two contact sheets to data/out/ab_<aoi>_{whole,subcrops}.png for counting by eye.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from groundtruth import config  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.tools import Engine  # noqa: E402
from smoke_slice import sheet  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="unc_charlotte")
    ap.add_argument("--query", default="swimming pool")
    ap.add_argument("-k", type=int, default=24)
    a = ap.parse_args()
    eng = Engine(PublicDataProvider(), aoi=a.aoi)
    assert eng.index.n_views == 5, f"index has {eng.index.n_views} CLIP views; rebuild with GROUNDTRUTH_CLIP_SUBCROPS=1"
    full = np.asarray(eng.index.clip, dtype=np.float32)  # (N, 5, 512)

    def rank_with(views: slice):
        def _clip_rank(q, k_chips):
            S = full[:, views] @ q  # (N, v)
            best_v = S.argmax(1) + views.start
            s = S.max(1)
            order = sorted(range(len(s)), key=lambda i: (-round(float(s[i]), 6), eng.index.ids[i]))[:k_chips]
            return [(i, float(s[i]), int(best_v[i])) for i in order]
        return _clip_rank

    for name, views in (("whole", slice(0, 1)), ("subcrops", slice(0, 5))):
        eng._clip_rank = rank_with(views)
        env = eng.search_text(a.query, k=a.k)
        out = config.DATA / "out" / f"ab_{a.aoi}_{name}.png"
        sheet(eng, env, out, title=f"A/B {name}: search_text('{a.query}') top {a.k} · {a.aoi}")
        print(name, [f["id"] for f in env.result.geojson["features"]][:a.k])


if __name__ == "__main__":
    main()
