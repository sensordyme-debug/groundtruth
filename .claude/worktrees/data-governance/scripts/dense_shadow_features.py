"""Item 3 (shadow false positives): per dense proposal, the mean brightness (HSV V, 0–255) inside its outline and the
tallest lidar height within radius_m → data/dense/<place>/swimming_pool_2022/shadow_v1.npz (new file; nothing the
demo uses is overwritten). CPU, reads the local COGs.

  python scripts/dense_shadow_features.py Ballantyne Uptown
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config, lidar  # noqa: E402
from groundtruth.dense import dense_dir  # noqa: E402
from groundtruth.geo import to_measure  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.rerank import _hsv, concept_conf  # noqa: E402

RADIUS = float(concept_conf("swimming_pool").get("dense", {}).get("shadow", {}).get("radius_m", 30))
prov = PublicDataProvider(download_scenes=False)
for place in sys.argv[1:]:
    t0 = time.time()
    d = dense_dir(place, "swimming_pool", prov.primary_year)
    feats = json.loads((d / "outlines.json").read_text())
    meta = json.loads((d / "meta.json").read_text())
    lp = lidar.place_for(meta["bbox"])
    v_mean = np.full(len(feats), np.nan, np.float32)
    tall = np.full(len(feats), np.nan, np.float32)
    for k, f in enumerate(feats):
        if not f.get("geometry"):
            continue
        gm = to_measure(shape(f["geometry"]))
        b = gm.bounds
        g = config.GSD_M
        bx = (np.floor(b[0] / g) * g, np.floor(b[1] / g) * g, np.ceil(b[2] / g) * g + g, np.ceil(b[3] / g) * g + g)
        im = prov.read_grid(bx, prov.primary_year)
        from rasterio.features import rasterize
        from rasterio.transform import from_origin

        m = rasterize([gm], out_shape=im.rgb.shape[:2], transform=from_origin(bx[0], bx[3], g, g)) > 0
        if m.any():
            v_mean[k] = float(_hsv(im.rgb[m][None])[0][:, 2].mean())
        if lp:
            v = lidar.values(gm.buffer(RADIUS), lp)
            if len(v):
                tall[k] = float(np.max(v))
    np.savez_compressed(d / "shadow_v1.npz", v_mean=v_mean, tall_within_m=tall, radius_m=RADIUS)
    print(f"{place}: {len(feats)} proposals, lidar={lp}, median V {np.nanmedian(v_mean):.0f}, {time.time() - t0:.0f} s",
          flush=True)
