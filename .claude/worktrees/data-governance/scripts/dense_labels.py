"""Phase 2: recall on the human labels (READ-ONLY: data/labels/labels.geojson) in a place, standard vs dense.

  python scripts/dense_labels.py "Lake Norman"
Examples = the demo pick (planner default sense, unpicked senses as negatives) so the run matches what a user does.
A true-match pool label counts as found when a counted object lies within 15 m of it; look-alike labels found = errors.
"""
import json
import sys
from pathlib import Path

from shapely.geometry import Point, box, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.geo import to_measure  # noqa: E402
from groundtruth.planner import DEFAULT_SENSE  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.tools import Engine  # noqa: E402

place = sys.argv[1] if len(sys.argv) > 1 else "Lake Norman"
eng = Engine(PublicDataProvider(download_scenes=False), aoi="county")
bb = eng.place_bbox(place)
labels = json.loads((config.DATA / "labels" / "labels.geojson").read_text())["features"]  # read only
inside = [f for f in labels if f["properties"]["concept"] == "swimming_pool" and box(*bb).contains(Point(*f["geometry"]["coordinates"][:2]))]
tm = [to_measure(Point(*f["geometry"]["coordinates"][:2])) for f in inside if f["properties"]["kind"] == "true_match"]
la = [to_measure(Point(*f["geometry"]["coordinates"][:2])) for f in inside if f["properties"]["kind"] == "look_alike"]
pc = eng.pick_candidates("swimming pool")
tiles = [f for f in pc.result.geojson["features"] if not f["properties"].get("no_clear_example")]
ch = next(f for f in tiles if f["properties"]["sense_id"] == DEFAULT_SENSE["swimming_pool"])
ex, neg = [ch["properties"]["example"]], [f["properties"]["example"] for f in tiles if f is not ch]
out = {"place": place, "true_match_labels": len(tm), "look_alike_labels": len(la)}
for mode in (False, True):
    env = eng.find_all(ex, [], place=place, concept="swimming_pool", outline_top=500, dense=mode, soft_exclude=neg)
    gs = [to_measure(shape(f["geometry"])) for f in env.result.geojson["features"]]
    found = sum(1 for p in tm if any(g.distance(p) <= 15 for g in gs))
    looks = sum(1 for p in la if any(g.distance(p) <= 15 for g in gs))
    out["dense" if mode else "standard"] = {"count": len(gs), "true_found": found, "look_alikes_counted": looks}
print(json.dumps(out))
(config.DATA / "out" / f"dense_labels_{place.replace(' ', '_')}.json").write_text(json.dumps(out, indent=1))
