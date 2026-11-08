"""Build SYNTHETIC what-if scenarios (engine/groundtruth/scenario.py) into data/scenarios/<v>/<id>/.

  python scripts/build_scenarios.py                 # every scenario in conf/scenarios.json
  python scripts/build_scenarios.py ln_water_level  # one

Outputs per scenario: meta.json (definition, assumptions, real sources, per-level counts, FEMA consistency check),
extent_<level>.geojson and impacts_<level>.json. Every record is marked is_synthetic=true. Deterministic.
Refuses to start when Windows has less than 4 GB free (project rule) unless --force."""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from groundtruth import scenario as S  # noqa: E402


def windows_free_gb() -> float | None:
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-c",
                              "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return int(out) / 1024 / 1024
    except Exception:  # noqa: BLE001 (not on WSL: no check)
        return None


def main() -> int:
    ids = [a for a in sys.argv[1:] if not a.startswith("--")] or [d["id"] for d in S.conf()["scenarios"]]
    free = windows_free_gb()
    if free is not None and free < 4.0 and "--force" not in sys.argv:
        print(f"Windows has {free:.1f} GB free (< 4 GB): not starting. Free memory or pass --force.")
        return 2
    for sid in ids:
        t = time.time()
        m = S.build(sid)
        print(json.dumps({"scenario": sid, "build_s": round(time.time() - t, 1), "unmeasured_land_m2": m["unmeasured_land_m2"],
                          "levels": m["levels"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
