"""Pack finished shards into download-sized zip parts + a SHA-256 manifest (for moving shards between machines).

  python scripts/pack_shards.py <shards_dir> <out_dir> [--part-mb 900]
Writes <out_dir>/part_NNN.zip (stored, no recompression) and <out_dir>/MANIFEST.sha256 listing every shard and
every part. Idempotent: shards already packed (listed in MANIFEST) are not packed again, so it can be re-run as
more scenes finish.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("shards_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--part-mb", type=int, default=900)
    ap.add_argument("--meta", type=Path, nargs="*", default=[], help="extra files to include (plan.json, log)")
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)
    man_path = a.out_dir / "MANIFEST.json"
    man = json.loads(man_path.read_text()) if man_path.exists() else {"shards": {}, "parts": {}}
    todo = [p for p in sorted(a.shards_dir.glob("*.parquet")) if p.name not in man["shards"]]
    part_no = len(man["parts"])
    batch, size = [], 0
    limit = a.part_mb * 1024 * 1024

    def flush() -> None:
        nonlocal batch, size, part_no
        if not batch:
            return
        part = a.out_dir / f"part_{part_no:03d}.zip"
        with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_STORED) as z:
            for p in batch:
                z.write(p, f"shards/{p.name}")
        man["parts"][part.name] = {"sha256": sha256(part), "bytes": part.stat().st_size, "shards": [p.name for p in batch]}
        print(f"{part.name}: {len(batch)} shards, {part.stat().st_size/1e6:.0f} MB")
        part_no += 1
        batch, size = [], 0

    for p in todo:
        man["shards"][p.name] = {"sha256": sha256(p), "bytes": p.stat().st_size}
        batch.append(p); size += p.stat().st_size
        if size >= limit:
            flush()
    flush()
    for m in a.meta:
        if m.exists():
            man.setdefault("meta", {})[m.name] = {"sha256": sha256(m), "bytes": m.stat().st_size}
            (a.out_dir / m.name).write_bytes(m.read_bytes())
    man_path.write_text(json.dumps(man, indent=1))
    lines = [f"{v['sha256']}  {k}" for k, v in man["parts"].items()]
    (a.out_dir / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")
    print(f"manifest: {len(man['shards'])} shards in {len(man['parts'])} parts → {man_path}")


if __name__ == "__main__":
    main()
