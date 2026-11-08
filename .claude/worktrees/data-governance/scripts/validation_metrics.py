"""Validation Studio metrics from HUMAN reviews only → data/out/validation_metrics.json.

  bash scripts/gt.sh python scripts/validation_metrics.py [--root data/validation/v1] [--out data/out/validation_metrics.json]

Writes two views: all samples, and samples outside the Lake Norman label cluster (``exclude_label_cluster``).
Read-only on the validation store (it never adds reviews).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth import validation as V  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(V.default_root()))
    ap.add_argument("--out", default=str(config.DATA / "out" / "validation_metrics.json"))
    a = ap.parse_args()
    s = V.ValidationStore(Path(a.root))
    doc = {"progress": s.progress(), "all": s.metrics(), "outside_label_cluster": s.metrics(exclude_label_cluster=True)}
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    V._fsync_write(out, json.dumps(doc, indent=1, sort_keys=True))
    p = doc["progress"]
    print(f"{p['samples']} samples, {p['reviewed']} reviewed, census complete {p['census_complete']}/{p['census_tiles']}")
    for cls, b in doc["all"]["classes"].items():
        print(f"  {cls:9s} n={b['n_samples']:4d} judged={b['n_judged']:3d} precision={b['precision']} "
              f"ci95={b['precision_ci95']} uncertain={b['uncertain']} recall={b['recall'].get('recall', b['recall']['status'])}")
    print("→", out)


if __name__ == "__main__":
    main()
