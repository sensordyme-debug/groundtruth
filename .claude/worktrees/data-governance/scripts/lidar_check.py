"""Phase 6 acceptance: lidar building heights vs published heights (Uptown towers) and vs Overture's height field."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
import numpy as np
from shapely.geometry import shape, Point
from groundtruth import config, lidar
from groundtruth.vector import store
# published roof/architectural heights (Wikipedia, "List of tallest buildings in Charlotte"), centre points from OSM
REF = {"Bank of America Corporate Center": (-80.8428, 35.2275, 265), "Duke Energy Center": (-80.8459, 35.2244, 240),
       "Truist Center (Hearst Tower)": (-80.8424, 35.2262, 201), "One Wells Fargo Center": (-80.8443, 35.2259, 179),
       "Bank of America Plaza": (-80.8407, 35.2270, 154)}
bb = json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"]["Uptown"]["bbox"]
fc, _ = store().buildings(bb)
H = lidar.building_heights(fc["features"], bb)
geoms = [(shape(f["geometry"]), f["properties"]) for f in fc["features"]]
print(f"{len(H)} of {len(fc['features'])} Uptown buildings have a lidar height")
for name, (lo, la, ref) in REF.items():
    p = Point(lo, la)
    cands = [(g, pr) for g, pr in geoms if g.distance(p) < 0.0006]
    if not cands:
        print(f"{name}: no footprint near"); continue
    g, pr = max(cands, key=lambda t: H.get(t[1]["id"], {}).get("height_m", -1))
    h = H.get(pr["id"], {}).get("height_m")
    print(f"{name}: lidar {h} m (p90 in footprint) · Overture {pr.get('height')} m · published {ref} m")
both = [(H[pr["id"]]["height_m"], pr["height"]) for g, pr in geoms if pr.get("height") and pr["id"] in H]
if both:
    a = np.array(both)
    print(f"lidar vs Overture height on {len(a)} buildings: median abs diff {np.median(np.abs(a[:,0]-a[:,1])):.1f} m, "
          f"corr {np.corrcoef(a[:,0], a[:,1])[0,1]:.3f}")
