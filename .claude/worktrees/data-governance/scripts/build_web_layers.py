"""Build small web versions of heavy layers (for the map UI). Raw files stay local/gitignored.

  python scripts/build_web_layers.py [--max-mb 30]
FEMA NFHL → data/layers/web/fema_flood_zones.geojson:
  keep regulatory hazard areas only (SFHA = 1%-annual-chance zones A/AE/AO/AH/VE…, plus the 0.2%-annual-chance
  "shaded X" band); drop "area of minimal flood hazard" (unshaded X), which is most of the county and most bytes.
  Simplify (topology-preserving), round coordinates to 6 decimals (~0.1 m), keep 3 properties. If the output is
  over --max-mb, the simplification tolerance is doubled until it fits.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shapely.geometry import mapping, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402


def _round(obj, nd=6):
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(v, nd) for v in obj]
        return [_round(o, nd) for o in obj]
    return obj


def fema(max_mb: float) -> None:
    src = config.DATA / "layers" / "fema_flood_zones.geojson"
    out = config.DATA / "layers" / "web" / "fema_flood_zones.geojson"
    out.parent.mkdir(parents=True, exist_ok=True)
    fc = json.loads(src.read_text())
    keep = []
    for f in fc["features"]:
        p = f["properties"]
        sub = (p.get("zone_subty") or "").upper()
        shaded_x = "0.2 PCT" in sub
        if not (p.get("sfha") or shaded_x):
            continue
        cls = "1pct" if p.get("sfha") else "0.2pct"
        keep.append((shape(f["geometry"]), {"fld_zone": p.get("fld_zone"), "flood_class": cls,
                                            "floodway": "FLOODWAY" in sub}))
    print(f"FEMA: {len(fc['features'])} raw → {len(keep)} regulatory (1% + 0.2%) polygons")
    del fc
    tol = 0.00003  # ~3 m
    while True:
        feats = []
        for g, p in keep:
            s = g.simplify(tol, preserve_topology=True)
            if s.is_empty:
                continue
            feats.append({"type": "Feature", "properties": p, "geometry": _round(mapping(s))})
        text = json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":"))
        mb = len(text) / 1e6
        print(f"  tolerance {tol:.5f}° → {len(feats)} features, {mb:.1f} MB")
        if mb <= max_mb or tol > 0.0005:
            break
        tol *= 2
    out.write_text(text)
    meta = {"source": "FEMA NFHL layer 28 (DFIRM 37119C)", "filter": "SFHA (1%) + 0.2% annual chance",
            "simplify_tolerance_deg": tol, "features": len(feats), "mb": round(mb, 1)}
    (out.parent / "fema_flood_zones.meta.json").write_text(json.dumps(meta, indent=1))
    print(f"wrote {out} ({mb:.1f} MB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-mb", type=float, default=30)
    a = ap.parse_args()
    fema(a.max_mb)
