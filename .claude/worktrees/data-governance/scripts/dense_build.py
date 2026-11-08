"""Phase 2: build dense proposals for the demo neighbourhoods (GPU env; one heavy job; stop the API first if RAM is short).

  python scripts/dense_build.py Ballantyne "Lake Norman"     # or no args = all 8 places
Requires the place's NAIP scenes in the local COG cache (scripts/fetch_cogs.py); refuses otherwise so it never races the
background download.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.dense import build  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from types import SimpleNamespace  # noqa: E402

places = json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"]
want = sys.argv[1:] or list(places)
prov = PublicDataProvider(download_scenes=False)
# dense build needs only the provider, the year and place boxes: no chip index (saves ~5 GB RAM)
eng = SimpleNamespace(provider=prov, year=prov.primary_year, place_bbox=lambda p: places[p]["bbox"])
OUT = config.next_data_dir("dense")  # a NEW version; switch conf/data_versions.json only after A/B + demo_check
print(f"writing to {OUT}", flush=True)
for pl in want:
    bb = tuple(places[pl]["bbox"])
    missing = [s.scene_id for s in prov.list_scenes(bb, eng.year) if not prov.local_scene_path(s.scene_id, eng.year).exists()]
    if missing:
        print(f"SKIP {pl}: {len(missing)} scene(s) not local yet ({missing[0]} …)", flush=True)
        continue
    t0 = time.time()
    meta = build(eng, pl, "swimming_pool", log=lambda s: print(s, flush=True), out_root=OUT)
    print(f"DONE {pl} in {time.time() - t0:.0f} s: {meta['proposals']} proposals, {meta['passed_verifier']} verified",
          flush=True)
