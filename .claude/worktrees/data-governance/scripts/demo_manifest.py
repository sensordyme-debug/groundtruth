"""data/DEMO_MANIFEST.json: every data file the demo reads (paths, sizes, mtimes, sha256), and the data versions.

  python scripts/demo_manifest.py write           # record the current demo data (run at the checkpoint)
  python scripts/demo_manifest.py verify          # quick: size + mtime for all, sha256 for files ≤ 64 MB
  python scripts/demo_manifest.py verify --full   # sha256 for everything (slow: ~30 GB incl. imagery)
Big files (index arrays, NAIP COGs) are hashed only in --full mode; quick mode trusts size + mtime for them.
"""
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from groundtruth import config  # noqa: E402

SMALL = 64 << 20
MAN = config.DATA / "DEMO_MANIFEST.json"


def demo_files() -> dict[str, list[Path]]:
    d = config.DATA
    groups: dict[str, list[Path]] = {}
    for year in (2022, 2020):
        p = config.precompute_dir(year, "county")
        groups[f"index_{year}"] = [p / n for n in ("index.json", "chips.parquet", "dino.npy", "clip.npy", "clip.faiss",
                                                  "thumbs.bin", "thumbs_offsets.npy") if (p / n).exists()]
    for kind in ("dense", "lidar", "tiles", "routing"):
        groups[kind] = sorted(q for q in config.data_dir(kind).rglob("*") if q.is_file())
    groups["openvocab_cache"] = sorted((d / "cache" / "openvocab" / config.data_version("openvocab_cache")).glob("*.json"))
    groups["layers"] = sorted(q for q in (d / "layers").rglob("*") if q.is_file())
    groups["naip_meta"] = sorted((d / "naip").glob("*.json"))
    groups["boundary"] = sorted((d / "boundary").glob("*"))
    cog = Path(__import__("os").environ.get("GROUNDTRUTH_COG_CACHE", Path.home() / ".cache" / "groundtruth" / "naip"))
    groups["naip_cogs"] = sorted(cog.rglob("*.tif"))
    return groups


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(8 << 20), b""):
            h.update(b)
    return h.hexdigest()


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def write() -> None:
    t0 = time.time()
    out = {"created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "git_commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
           "data_versions": json.loads((config.SENSES_PATH.parent / "data_versions.json").read_text()),
           "index_version": config.INDEX_VERSION, "files": {}}
    n = 0
    for g, files in demo_files().items():
        out["files"][g] = []
        for p in files:
            st = p.stat()
            out["files"][g].append({"path": rel(p), "size": st.st_size, "mtime": int(st.st_mtime),
                                    "sha256": sha(p) if st.st_size <= SMALL else None})
            n += 1
    MAN.write_text(json.dumps(out, indent=1))
    print(f"wrote {MAN} ({n} files, {time.time() - t0:.0f} s)")


def verify(full: bool) -> bool:
    m = json.loads(MAN.read_text())
    bad, n = [], 0
    for g, files in m["files"].items():
        for e in files:
            p = Path(e["path"]) if Path(e["path"]).is_absolute() else ROOT / e["path"]
            n += 1
            if not p.exists():
                bad.append(f"missing {e['path']}")
                continue
            st = p.stat()
            if st.st_size != e["size"]:
                bad.append(f"size changed {e['path']}")
            elif e["sha256"] and sha(p) != e["sha256"]:
                bad.append(f"sha256 changed {e['path']}")
            elif full and not e["sha256"] and int(st.st_mtime) != e["mtime"]:
                bad.append(f"mtime changed {e['path']}")
    cur = json.loads((config.SENSES_PATH.parent / "data_versions.json").read_text())
    if {k: v for k, v in cur.items() if not k.startswith("_")} != {k: v for k, v in m["data_versions"].items() if not k.startswith("_")}:
        bad.append(f"conf/data_versions.json differs from the manifest: {cur} vs {m['data_versions']}")
    print(f"manifest {'OK' if not bad else 'FAIL'}: {n} files checked" + ("" if not bad else "; " + "; ".join(bad[:8])))
    return not bad


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if cmd == "write":
        write()
    else:
        sys.exit(0 if verify("--full" in sys.argv) else 1)
