"""Phase 1.2: vector tiles (PMTiles) for the context layers, built with tippecanoe (BSD-2).

  python scripts/build_pmtiles.py            # all layers → data/tiles/<layer>.pmtiles (source-layer = <layer>)

Zoom ranges + tippecanoe options per layer live in conf/vector_tiles.json. Overture layers are exported from the DuckDB
store (GeoJSONSeq), the rest from data/layers/<layer>.geojson (full resolution, not the simplified web build).
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.vector import store  # noqa: E402

conf = json.loads((config.SENSES_PATH.parent / "vector_tiles.json").read_text())
out_dir = config.next_data_dir("tiles")  # a NEW version; switch conf/data_versions.json after checking
out_dir.mkdir(parents=True, exist_ok=True)
only = set(sys.argv[1:])
for name, c in conf["layers"].items():
    if only and name not in only:
        continue
    t0 = time.time()
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"{name}.geojsonl"
        if name.startswith("overture_"):
            fc, _ = store().query(name.removeprefix("overture_"), None)
            feats = fc["features"]
            keep = c.get("properties")
        else:
            p = config.DATA / "layers" / ("web/" if c.get("source") == "web" else "") / f"{name}.geojson"
            if not p.exists():
                print(f"skip {name}: no {p.name}")
                continue
            feats = json.loads(p.read_text())["features"]
            keep = c.get("properties")
        with open(src, "w") as f:
            for ft in feats:
                if not ft.get("geometry"):
                    continue
                props = {k: v for k, v in (ft.get("properties") or {}).items()
                         if (keep is None or k in keep) and v is not None and not isinstance(v, (dict, list))}
                f.write(json.dumps({"type": "Feature", "properties": props, "geometry": ft["geometry"]}) + "\n")
        dst = out_dir / f"{name}.pmtiles"
        cmd = ["tippecanoe", "-q", "-f", "-o", str(dst), "-l", name, "-Z", str(c["minzoom"]), "-z", str(c["maxzoom"]),
               *c.get("options", []), "-P", str(src)]
        subprocess.run(cmd, check=True)
    print(f"{name}: {len(feats):,} features → {dst.name} {dst.stat().st_size / 2**20:.1f} MB ({time.time() - t0:.0f} s)")
