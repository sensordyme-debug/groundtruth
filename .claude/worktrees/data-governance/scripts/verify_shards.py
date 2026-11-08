"""Verify downloaded shard parts (SHA-256 + schema), unpack them into the precompute dir, and build the index.

  python scripts/verify_shards.py data/incoming/2022 --year 2022 [--aoi county] [--no-index]
Expects MANIFEST.json + part_NNN.zip in the folder (from scripts/pack_shards.py / the Colab notebook).
Refuses to unpack anything whose hash does not match. Checks every shard's vector widths against the locked format.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from groundtruth import config  # noqa: E402
from pack_shards import sha256  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("incoming", type=Path)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--aoi", default="county")
    ap.add_argument("--no-index", action="store_true")
    a = ap.parse_args()
    man = json.loads((a.incoming / "MANIFEST.json").read_text())
    out = config.precompute_dir(a.year, a.aoi)
    (out / "shards").mkdir(parents=True, exist_ok=True)
    bad = []
    for name, info in man["parts"].items():
        p = a.incoming / name
        if not p.exists():
            print(f"MISSING {name}"); bad.append(name); continue
        h = sha256(p)
        if h != info["sha256"]:
            print(f"HASH MISMATCH {name}"); bad.append(name); continue
        with zipfile.ZipFile(p) as z:
            for member in z.namelist():
                shard = Path(member).name
                data = z.read(member)
                import hashlib

                if hashlib.sha256(data).hexdigest() != man["shards"][shard]["sha256"]:
                    print(f"SHARD HASH MISMATCH {shard} in {name}"); bad.append(shard); continue
                dst = out / "shards" / shard
                dst.write_bytes(data)
                sch = pq.read_schema(dst)
                dw, cw = sch.field("dino").type.list_size, sch.field("clip").type.list_size
                want_d = config.CELL_GRID * config.CELL_GRID * config.DINO_PCA_DIM
                if dw != want_d or cw != 512:
                    print(f"SCHEMA MISMATCH {shard}: dino {dw} (want {want_d}), clip {cw} (want 512)")
                    dst.unlink(); bad.append(shard)
        print(f"ok {name}: {len(info['shards'])} shards")
    plan = out / "plan.json"
    if plan.exists():
        expected = set(json.loads(plan.read_text()))
        have = {p.stem for p in (out / "shards").glob("*.parquet")}
        print(f"scenes: {len(have & expected)}/{len(expected)} present" +
              (f"; missing {sorted(expected - have)[:5]}…" if expected - have else ""))
    if bad:
        print(f"FAILED: {len(bad)} problems; nothing indexed. Re-download those parts.")
        sys.exit(1)
    if not a.no_index:
        from precompute_county import build_index

        build_index(out)


if __name__ == "__main__":
    main()
