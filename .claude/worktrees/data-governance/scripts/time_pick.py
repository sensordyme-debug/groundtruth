"""Time pick_candidates end to end (warm) and print where the community-pool tile's cell landed."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.tools import Engine  # noqa: E402

eng = Engine(PublicDataProvider(), aoi=sys.argv[1] if len(sys.argv) > 1 else "south_charlotte")
eng.pick_candidates("swimming pool")  # warm (model load)
t = time.perf_counter()
env = eng.pick_candidates("swimming pool")
print(f"warm pick_candidates: {(time.perf_counter() - t) * 1000:.0f} ms")
for s in env.steps:
    print(f"  {s.phase:15s} {s.ms:8.0f} ms")
print([f["properties"]["example"] + " " + f["properties"]["sense_id"] for f in env.result.geojson["features"]])
