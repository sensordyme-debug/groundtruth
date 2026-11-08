"""Phase 6: DTM / DSM / nDSM (height above ground) at 1 m for a demo place from USGS 3DEP lidar (public domain).

  ~/.venvs/groundtruth/bin/python scripts/build_lidar.py Uptown Ballantyne "Myers Park" [--max-depth 9]
Source: the Entwine Point Tile (EPT) copy of USGS_LPC_NC_Phase4_Mecklenburg_2016_LAS_2019 on the AWS public bucket
(usgs-lidar-public). PDAL is not packaged for Ubuntu 24.04, so this reads the EPT octree directly: hierarchy JSON →
every node intersecting the place down to --max-depth → LAZ via laspy+lazrs → per 1 m cell (EPSG:32617):
  DSM = max Z of all non-noise points; DTM = min Z of ground points (class 2), gaps filled from the nearest ground
  cells; nDSM = DSM − DTM (≥ 0). Written to data/lidar/<place>/{dtm,dsm,ndsm}.tif + meta.json.
"""
import argparse
import concurrent.futures as cf
import io
import json
import sys
import time
from pathlib import Path

import httpx
import laspy
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from groundtruth import config  # noqa: E402

EPT = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public/USGS_LPC_NC_Phase4_Mecklenburg_2016_LAS_2019/"
NOISE = {7, 18}
RES = 1.0
OUT_ROOT = config.next_data_dir("lidar")  # rebuilds go to a NEW version (data/lidar/vN/), never the one in use


def hierarchy(c: httpx.Client, root: list[float] | None = None, q: tuple | None = None) -> dict[str, int]:
    """EPT hierarchy; with root bounds + a query box (EPSG:3857) only sub-hierarchies that intersect it are fetched."""
    h, todo = {}, ["0-0-0-0"]
    while todo:
        k = todo.pop()
        part = c.get(EPT + f"ept-hierarchy/{k}.json").json()
        for key, n in part.items():
            if n == -1:
                if root is None or q is None:
                    todo.append(key)
                else:
                    b = node_bounds(key, root)
                    if b[0] < q[2] and b[2] > q[0] and b[1] < q[3] and b[3] > q[1]:
                        todo.append(key)
            else:
                h[key] = n
    return h


def node_bounds(key: str, root: list[float]) -> tuple[float, float, float, float]:
    d, x, y, _z = (int(v) for v in key.split("-"))
    w = (root[3] - root[0]) / (2 ** d)
    h = (root[4] - root[1]) / (2 ** d)
    return root[0] + x * w, root[1] + y * h, root[0] + (x + 1) * w, root[1] + (y + 1) * h


def build(place: str, bbox: list[float], max_depth: int, log=print) -> dict:
    t0 = time.time()
    to3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    to_utm_from3857 = Transformer.from_crs("EPSG:3857", "EPSG:32617", always_xy=True)
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32617", always_xy=True)
    qx0, qy0 = to3857.transform(bbox[0], bbox[1])
    qx1, qy1 = to3857.transform(bbox[2], bbox[3])
    ux = to_utm.transform([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
    gx0, gy0 = np.floor(min(ux[0])), np.floor(min(ux[1]))
    gx1, gy1 = np.ceil(max(ux[0])), np.ceil(max(ux[1]))
    W, H = int((gx1 - gx0) / RES), int((gy1 - gy0) / RES)
    dsm = np.full((H, W), -np.inf, np.float32)
    dtm = np.full((H, W), np.inf, np.float32)
    with httpx.Client(timeout=120, follow_redirects=True) as c:
        meta = c.get(EPT + "ept.json").json()
        root = meta["bounds"]
        h = hierarchy(c, root, (qx0, qy0, qx1, qy1))
    keys = sorted(k for k, n in h.items() if n > 0 and int(k.split("-")[0]) <= max_depth
                  and (lambda b: b[0] < qx1 and b[2] > qx0 and b[1] < qy1 and b[3] > qy0)(node_bounds(k, root)))
    log(f"{place}: {len(keys)} EPT nodes (depth ≤ {max_depth}), {sum(h[k] for k in keys):,} points; grid {W}×{H} m")
    n_pts = [0, 0]

    def fetch(k: str):
        for attempt in range(8):  # survives DNS / network blips (laptop sleep, Wi-Fi)
            try:
                with httpx.Client(timeout=180) as c:
                    r = c.get(EPT + f"ept-data/{k}.laz")
                    r.raise_for_status()
                    return laspy.read(io.BytesIO(r.content))
            except Exception:
                if attempt == 7:
                    raise
                time.sleep(min(60, 2 ** (attempt + 1)))

    done = 0
    with cf.ThreadPoolExecutor(8) as ex:
        for las in ex.map(fetch, keys):
            done += 1
            x, y, z = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z, np.float32)
            cls = np.asarray(las.classification)
            m = (x >= qx0) & (x <= qx1) & (y >= qy0) & (y <= qy1) & ~np.isin(cls, list(NOISE))
            if not m.any():
                continue
            ex_, ey_ = to_utm_from3857.transform(x[m], y[m])
            col = ((np.asarray(ex_) - gx0) / RES).astype(np.int64)
            row = ((gy1 - np.asarray(ey_)) / RES).astype(np.int64)
            ok = (col >= 0) & (col < W) & (row >= 0) & (row < H)
            col, row, zz, cc = col[ok], row[ok], z[m][ok], cls[m][ok]
            np.maximum.at(dsm, (row, col), zz)
            g = cc == 2
            np.minimum.at(dtm, (row[g], col[g]), zz[g])
            n_pts[0] += int(ok.sum())
            n_pts[1] += int(g.sum())
            if done % 200 == 0:
                log(f"  {done}/{len(keys)} nodes, {n_pts[0]:,} points ({time.time() - t0:.0f} s)")
    # fill gaps: DTM from the nearest ground cell (buildings/canopy have no ground returns); DSM from the DTM
    have_g = np.isfinite(dtm)
    if not have_g.any():
        raise RuntimeError("no ground points")
    idx = ndimage.distance_transform_edt(~have_g, return_distances=False, return_indices=True)
    dtm_f = dtm[tuple(idx)]
    dtm_f = ndimage.median_filter(dtm_f, size=5)  # smooth the nearest-neighbour fill under buildings
    dtm_f[have_g] = dtm[have_g]
    have_s = np.isfinite(dsm)
    dsm_f = np.where(have_s, dsm, dtm_f)
    ndsm = np.clip(dsm_f - dtm_f, 0, None).astype(np.float32)
    out = OUT_ROOT / place.replace(" ", "_")
    out.mkdir(parents=True, exist_ok=True)
    prof = {"driver": "GTiff", "height": H, "width": W, "count": 1, "dtype": "float32", "crs": "EPSG:32617",
            "transform": from_origin(gx0, gy1, RES, RES), "compress": "deflate", "tiled": True, "nodata": -9999.0}
    for name, arr in (("dtm", dtm_f), ("dsm", dsm_f), ("ndsm", ndsm)):
        with rasterio.open(out / f"{name}.tif", "w", **prof) as dst:
            dst.write(arr.astype(np.float32), 1)
    info = {"place": place, "bbox": bbox, "source": EPT + "ept.json", "project": "USGS_LPC_NC_Phase4_Mecklenburg_2016_LAS_2019",
            "acquired": "2016 (published 2019)", "licence": "Public domain (USGS 3DEP)", "max_depth": max_depth,
            "nodes": len(keys), "points_used": n_pts[0], "ground_points": n_pts[1], "res_m": RES, "grid": [W, H],
            "ground_cover_frac": round(float(have_g.mean()), 3), "surface_cover_frac": round(float(have_s.mean()), 3),
            "ms_total": round((time.time() - t0) * 1000), "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "method": "EPT read with laspy+lazrs; DSM = max Z (non-noise), DTM = min ground Z with nearest-fill + 5 m median "
                      "under gaps; nDSM = DSM − DTM ≥ 0"}
    (out / "meta.json").write_text(json.dumps(info, indent=1))
    log(f"{place}: done in {time.time() - t0:.0f} s · {n_pts[0]:,} points · ground cover {info['ground_cover_frac']}")
    return info


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("places", nargs="*")
    ap.add_argument("--max-depth", type=int, default=9)
    ap.add_argument("--area", nargs=5, metavar=("NAME", "W", "S", "E", "N"), help="extra named area (not a demo place)")
    a = ap.parse_args()
    places = json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"]
    if a.area:
        build(a.area[0], [float(v) for v in a.area[1:]], a.max_depth, log=lambda s: print(s, flush=True))
    for pl in a.places or ([] if a.area else list(places)):
        build(pl, places[pl]["bbox"], a.max_depth, log=lambda s: print(s, flush=True))
