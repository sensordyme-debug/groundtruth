"""Chip + embed + index an AOI (or the whole county) for one NAIP year. Resumable, retrying, logs throughput.

  python scripts/precompute_county.py --aoi unc_charlotte --year 2022
  python scripts/precompute_county.py --aoi county --year 2022            # full Mecklenburg
  GROUNDTRUTH_DEVICE=cuda python scripts/precompute_county.py --aoi county --year 2020   # on a GPU box

Work unit = one NAIP scene: download COG (prefetched in a thread), warp chips onto the EPSG:32617 grid, embed
with DINOv2 + OpenCLIP, write shards/<scene>.parquet atomically. Finished scenes are skipped on restart.
Then `--index` (automatic at the end) concatenates shards → chips.parquet, dino.npy, clip.npy, *.faiss.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import io
import json
import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image
from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.geo import to_measure  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.tiler import assign_scenes, chips_for_area  # noqa: E402

log = logging.getLogger("precompute")
MIN_VALID = 0.5


def aoi_geometry(provider: PublicDataProvider, aoi: str):
    if aoi == "county":
        return shape(provider.get_boundary()["geometry"])
    return box(*config.AOIS[aoi])


def plan(provider: PublicDataProvider, aoi: str, year: int):
    g = aoi_geometry(provider, aoi)
    chips = chips_for_area(to_measure(g), year)
    chips = assign_scenes(chips, provider.scene_footprints_m(g.bounds, year))
    by_scene = defaultdict(list)
    for c in chips:
        by_scene[c.scene_id].append(c)
    return dict(sorted(by_scene.items()))


def process_scene(provider, sid, chips, year, dino, clip, batch):
    x0 = min(c.x0_m for c in chips); y0 = min(c.y0_m for c in chips)
    x1 = max(c.x1_m for c in chips); y1 = max(c.y1_m for c in chips)
    t0 = time.perf_counter()
    im = provider.read_grid((x0, y0, x1, y1), year, scene_ids=[sid])
    t_read = time.perf_counter() - t0
    g = config.GSD_M
    arrs, keep, valid_fracs = [], [], []
    for c in chips:
        ci, ri = int(round((c.x0_m - x0) / g)), int(round((y1 - c.y1_m) / g))
        a = im.rgb[ri : ri + config.CHIP_PX, ci : ci + config.CHIP_PX]
        v = float(im.meta["valid"][ri : ri + config.CHIP_PX, ci : ci + config.CHIP_PX].mean())
        if a.shape[:2] != (config.CHIP_PX, config.CHIP_PX) or v < MIN_VALID:
            continue
        arrs.append(a); keep.append(c); valid_fracs.append(v)
    if not keep:
        return None, {"read_s": t_read, "embed_s": 0.0, "n": 0}
    X = np.stack(arrs)
    t1 = time.perf_counter()
    from groundtruth.embed import project_cells

    d = project_cells(dino.embed_cells(X, batch=batch, grid=config.CELL_GRID)).astype(np.float16)  # (n, 64, 192)
    t_dino = time.perf_counter() - t1
    k = clip.embed_multi(X, batch=batch, subcrops=config.CLIP_SUBCROPS).astype(np.float16)  # (n, 5, 512)
    t_emb = time.perf_counter() - t1
    jpegs = []
    for a in arrs:
        b = io.BytesIO(); Image.fromarray(a).save(b, format="JPEG", quality=88); jpegs.append(b.getvalue())
    bbs = [c.bbox_wgs84() for c in keep]
    table = pa.table({
        "chip_id": [c.chip_id for c in keep], "year": [year] * len(keep), "scene_id": [sid] * len(keep),
        "col": [c.col for c in keep], "row": [c.row for c in keep],
        "x0_m": [c.x0_m for c in keep], "y0_m": [c.y0_m for c in keep],
        "west": [b[0] for b in bbs], "south": [b[1] for b in bbs], "east": [b[2] for b in bbs],
        "north": [b[3] for b in bbs], "valid_frac": valid_fracs, "jpeg": jpegs,
        "dino": pa.FixedSizeListArray.from_arrays(pa.array(d.ravel()), d.shape[1] * d.shape[2]),
        "clip": pa.FixedSizeListArray.from_arrays(pa.array(k.ravel()), k.shape[1] * k.shape[2]),
    })
    return table, {"read_s": t_read, "embed_s": t_emb, "dino_s": t_dino, "clip_s": t_emb - t_dino, "n": len(keep)}


def build_index(out: Path) -> None:
    """Stream shards (sorted by name; rows in plan order) into memory-mapped fp16 arrays + FAISS, one shard at a time,
    so peak RAM stays ~one shard regardless of county size. Row order = chips.parquet order (deterministic)."""
    import faiss

    shards = sorted((out / "shards").glob("*.parquet"))
    metas, n = [], 0
    for s in shards:
        m = pq.read_table(s, columns=[c for c in pq.read_schema(s).names if c not in ("jpeg", "dino", "clip")])
        metas.append(m); n += m.num_rows
    pq.write_table(pa.concat_tables(metas), out / "chips.parquet")
    views = 1 if not config.CLIP_SUBCROPS else 5
    dims = {"dino": (config.CELL_GRID * config.CELL_GRID, config.DINO_PCA_DIM), "clip": (views, 512)}
    mm = {c: np.lib.format.open_memmap(out / f"{c}.npy.tmp", mode="w+", dtype=np.float16, shape=(n, *d))
          for c, d in dims.items()}
    idx = {c: faiss.IndexScalarQuantizer(d[1], faiss.ScalarQuantizer.QT_fp16, faiss.METRIC_INNER_PRODUCT)
           for c, d in dims.items()}
    offs = [0]
    row = 0
    with open(out / "thumbs.bin.tmp", "wb") as tf:
        for s in shards:
            t = pq.read_table(s, columns=["jpeg", "dino", "clip"])
            k = t.num_rows
            for c, (v, d) in dims.items():
                X = np.asarray(t.column(c).combine_chunks().flatten(), dtype=np.float32).reshape(k, -1, d)[:, :v]
                X /= np.clip(np.linalg.norm(X, axis=-1, keepdims=True), 1e-12, None)
                mm[c][row:row + k] = X.astype(np.float16)
                idx[c].add(np.ascontiguousarray(X.reshape(-1, d)))
            for b in t.column("jpeg").to_pylist():
                tf.write(b); offs.append(offs[-1] + len(b))
            row += k
    for c in dims:
        mm[c].flush(); del mm[c]
        os.replace(out / f"{c}.npy.tmp", out / f"{c}.npy")
        faiss.write_index(idx[c], str(out / f"{c}.faiss"))
    os.replace(out / "thumbs.bin.tmp", out / "thumbs.bin")
    np.save(out / "thumbs_offsets.npy", np.asarray(offs, dtype=np.int64))
    (out / "index.json").write_text(json.dumps({
        "index_version": config.INDEX_VERSION, "chips": n, "shards": len(shards),
        "cell_grid": config.CELL_GRID, "dino_dim": config.DINO_PCA_DIM, "dino_pca": config.DINO_PCA_PATH.name,
        "clip_views": ["whole", "NW", "NE", "SW", "SE"] if config.CLIP_SUBCROPS else ["whole"],
        "built": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, indent=2))
    log.info("index built: %d chips from %d shards → %s", n, len(shards), out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="unc_charlotte")
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--index-only", action="store_true")
    ap.add_argument("--keep-cogs", action="store_true", help="keep downloaded COGs (default: delete after use)")
    ap.add_argument("--no-index", action="store_true", help="only write shards (index is built after transfer)")
    a = ap.parse_args()
    provider = PublicDataProvider()
    year = a.year or provider.primary_year
    out = config.precompute_dir(year, a.aoi)
    (out / "shards").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(out / "precompute.log")])
    for noisy in ("httpx", "httpcore", "urllib3"):  # their INFO lines contain signed (SAS) URLs: never log tokens
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if a.index_only:
        return build_index(out)
    scenes = plan(provider, a.aoi, year)
    total = sum(len(v) for v in scenes.values())
    log.info("plan: aoi=%s year=%d scenes=%d chips=%d", a.aoi, year, len(scenes), total)
    (out / "plan.json").write_text(json.dumps({s: len(c) for s, c in scenes.items()}, indent=1))
    if a.plan_only:
        return
    from groundtruth.embed import ClipEmbedder, DinoEmbedder

    dino, clip = DinoEmbedder(), ClipEmbedder()
    log.info("device=%s threads=%s", dino.device, os.environ.get("GROUNDTRUTH_THREADS", "default"))
    todo = [s for s in scenes if not (out / "shards" / f"{s}.parquet").exists()]
    pool = cf.ThreadPoolExecutor(1)
    fut = {s: pool.submit(provider.ensure_local, s, year) for s in todo[:1]}
    done_n, t_start = 0, time.perf_counter()
    for i, sid in enumerate(todo):
        if i + 1 < len(todo):  # prefetch next scene while embedding this one
            fut[todo[i + 1]] = pool.submit(provider.ensure_local, todo[i + 1], year)
        for attempt in range(3):
            try:
                fut[sid].result()
                table, st = process_scene(provider, sid, scenes[sid], year, dino, clip, a.batch)
                break
            except Exception as e:
                log.warning("scene %s attempt %d failed: %s", sid, attempt + 1, e)
                provider.local_scene_path(sid, year).unlink(missing_ok=True)
                fut[sid] = pool.submit(provider.ensure_local, sid, year)
        else:
            log.error("scene %s skipped after retries", sid); continue
        if table is not None:
            tmp = out / "shards" / f"{sid}.parquet.tmp"
            pq.write_table(table, tmp); os.replace(tmp, out / "shards" / f"{sid}.parquet")
        if not a.keep_cogs:
            provider.local_scene_path(sid, year).unlink(missing_ok=True)
        done_n += st["n"]
        el = time.perf_counter() - t_start
        log.info("scene %d/%d %s: %d chips read %.1fs embed %.1fs (dino %.1fs clip %.1fs; %.1f chips/s) | "
                 "overall %.2f chips/s, eta %.0f min", i + 1, len(todo), sid, st["n"], st["read_s"], st["embed_s"],
                 st.get("dino_s", 0), st.get("clip_s", 0), st["n"] / max(st["embed_s"], 1e-6), done_n / el,
                 (total - done_n) / max(done_n / el, 1e-6) / 60)
    missing = [s for s in scenes if not (out / "shards" / f"{s}.parquet").exists()]
    log.info("done: %d/%d scenes have shards%s", len(scenes) - len(missing), len(scenes),
             f"; missing {missing} (re-run to retry)" if missing else "")
    if not a.no_index:
        build_index(out)


if __name__ == "__main__":
    main()
