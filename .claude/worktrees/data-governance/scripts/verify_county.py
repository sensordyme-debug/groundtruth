"""Verify a county precompute: shards vs plan, schema widths, index row counts. Read-only.

  python scripts/verify_county.py 2022
"""
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402

year = int(sys.argv[1]) if len(sys.argv) > 1 else 2022
d = config.precompute_dir(year, "county")
plan = json.loads((d / "plan.json").read_text())
shards = {p.stem: p for p in (d / "shards").glob("*.parquet")}
missing = sorted(set(plan) - set(shards))
want_d = config.CELL_GRID ** 2 * config.DINO_PCA_DIM
bad, rows = [], 0
for sid, p in sorted(shards.items()):
    s = pq.read_schema(p)
    if s.field("dino").type.list_size != want_d or s.field("clip").type.list_size not in (512, 2560):
        bad.append(sid)
    rows += pq.read_metadata(p).num_rows
planned = sum(plan.values())
print(f"year {year}: scenes {len(shards)}/{len(plan)} (missing {missing or 'none'}); schema bad {bad or 'none'}")
print(f"chips in shards {rows:,} of {planned:,} planned (rest skipped: <50% valid pixels, e.g. outside NC coverage)")
if (d / "index.json").exists():
    info = json.loads((d / "index.json").read_text())
    dino = np.load(d / "dino.npy", mmap_mode="r"); clip = np.load(d / "clip.npy", mmap_mode="r")
    off = np.load(d / "thumbs_offsets.npy")
    sizes = {f.name: round(f.stat().st_size / 1e9, 2) for f in d.iterdir() if f.is_file()}
    ok = info["chips"] == rows == dino.shape[0] == clip.shape[0] == len(off) - 1
    print(f"index: {info['chips']:,} chips, dino {dino.shape} clip {clip.shape}, thumbs {len(off)-1:,} → {'OK' if ok else 'MISMATCH'}")
    print("files (GB):", {k: v for k, v in sizes.items() if v >= 0.01})
    print("total GB:", round(sum(sizes.values()) + sum(p.stat().st_size for p in shards.values()) / 1e9, 2))
