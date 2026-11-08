"""Paths and fixed engine parameters (versioned: change => bump INDEX_VERSION)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("GROUNDTRUTH_DATA", ROOT / "data"))

# Chip grid (EPSG:32617). 224 px at 0.6 m = 134.4 m; stride 192 px = 115.2 m (32 px ≈ 19 m overlap).
GSD_M = 0.6
CHIP_PX = 224
STRIDE_PX = 192
CHIP_M = CHIP_PX * GSD_M
STRIDE_M = STRIDE_PX * GSD_M
INDEX_VERSION = "chips224s192-g0.6-v3c8p192"

# Locked storage format v3 (docs/PRECOMPUTE.md): DINOv2 patch tokens pooled to an 8x8 grid (64 cells of 28 px =
# 16.8 m), projected with a fixed PCA basis to 192 dims, L2-normalised, fp16 + whole-chip OpenCLIP (fp16).
# CLIP 2x2 sub-crops: A/B showed no gain (6 vs 6 pools / 24), so they are OFF everywhere.
CELL_GRID = 8
CELL_PX = CHIP_PX // CELL_GRID
DINO_PCA_DIM = 192
DINO_PCA_PATH = Path(__file__).resolve().parent / "conf" / f"dino_cells{CELL_GRID}_pca{DINO_PCA_DIM}.npz"
CLIP_SUBCROPS = os.environ.get("GROUNDTRUTH_CLIP_SUBCROPS", "0") == "1"

SENSES_PATH = Path(__file__).resolve().parent / "conf" / "senses.json"

# AOIs (EPSG:4326)
AOIS: dict[str, tuple[float, float, float, float]] = {
    "unc_charlotte": (-80.745, 35.297, -80.722, 35.315),
    # residential area with many backyard pools, used to judge find_similar fairly
    "south_charlotte": (-80.835, 35.050, -80.815, 35.066),
    # small extra areas used only to fit the DINOv2 cell PCA basis (land-cover variety)
    "pca_airport_industrial": (-80.968, 35.200, -80.956, 35.210),
    "pca_rural_north": (-80.930, 35.447, -80.918, 35.457),
}


def precompute_dir(year: int, aoi: str) -> Path:
    return DATA / "precompute" / INDEX_VERSION / aoi / str(year)


def offline() -> bool:
    """GROUNDTRUTH_OFFLINE=1 blocks every outbound fetch to Planetary Computer (tiles + imagery): local caches only.
    Used to prove the hero flow works with the network unplugged (without touching the system network)."""
    return os.environ.get("GROUNDTRUTH_OFFLINE", "0") == "1"


def data_version(kind: str) -> str:
    """Current version of a rebuildable data product (conf/data_versions.json; env GROUNDTRUTH_DATA_<KIND> overrides)."""
    env = os.environ.get(f"GROUNDTRUTH_DATA_{kind.upper()}")
    if env:
        return env
    import json as _json

    return _json.loads((SENSES_PATH.parent / "data_versions.json").read_text()).get(kind, "v1")


def data_dir(kind: str, version: str | None = None) -> Path:
    """data/<kind>/<version>/ — read paths for the demo; rebuild scripts pass an explicit new version."""
    return DATA / kind / (version or data_version(kind))


def next_data_dir(kind: str) -> Path:
    """A NEW version folder for a rebuild: data/<kind>/v<N+1>/ (never the version the demo reads)."""
    root = DATA / kind
    ns = [int(p.name[1:]) for p in root.glob("v*") if p.name[1:].isdigit()] if root.exists() else []
    return root / f"v{(max(ns) if ns else 0) + 1}"
