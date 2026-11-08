"""find_similar from clearly visible backyard pools: 1 positive vs 3 positives (sheets in data/out/)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from groundtruth import config  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.tools import Engine  # noqa: E402
from smoke_slice import sheet  # noqa: E402

eng = Engine(PublicDataProvider(), aoi="south_charlotte")
seed = eng.find_similar(["2022_e4472_n33675#31"], k=40)  # community pool → locate the backyard pool cells
cells = {f["id"]: f["properties"]["cell"] for f in seed.result.geojson["features"]}
want = ["2022_e4474_n33670", "2022_e4475_n33673", "2022_e4474_n33669"]
pos = [f"{c}#{cells[c]}" for c in want if c in cells]
print("positives:", pos)
for name, ex in (("1pos", pos[:1]), ("3pos", pos)):
    env = eng.find_similar(ex, k=24)
    sheet(eng, env, config.DATA / "out" / f"south_charlotte_backyard_{name}.png",
          title=f"find_similar from clear backyard pool(s) {ex} top 24 (red = matching cell)")
