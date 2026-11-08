"""Fit the fixed PCA basis for DINOv2 8x8 cell vectors (768 → 192) and save it to engine/groundtruth/conf/.

  python scripts/fit_dino_pca.py
Samples chips from varied small AOIs (campus, suburb, airport/industrial, rural) in NAIP 2022 plus one 2020 area,
so both years project through the same basis. Deterministic (seeded). Re-fitting changes INDEX_VERSION semantics:
never refit after a county precompute has started.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.embed import DINO_ID, DinoEmbedder  # noqa: E402
from groundtruth.geo import to_measure  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.tiler import assign_scenes, chips_for_area  # noqa: E402

SAMPLES = [("unc_charlotte", 2022), ("south_charlotte", 2022), ("pca_airport_industrial", 2022),
           ("pca_rural_north", 2022), ("south_charlotte", 2020)]
MAX_CHIPS = 160  # per sample


def chips_pixels(prov: PublicDataProvider, aoi: str, year: int, rng: np.random.Generator) -> np.ndarray:
    g = box(*config.AOIS[aoi])
    chips = assign_scenes(chips_for_area(to_measure(g), year), prov.scene_footprints_m(g.bounds, year))
    if len(chips) > MAX_CHIPS:
        chips = [chips[i] for i in sorted(rng.choice(len(chips), MAX_CHIPS, replace=False))]
    x0 = min(c.x0_m for c in chips); y0 = min(c.y0_m for c in chips)
    x1 = max(c.x1_m for c in chips); y1 = max(c.y1_m for c in chips)
    im = prov.read_grid((x0, y0, x1, y1), year)
    out = []
    for c in chips:
        ci, ri = int(round((c.x0_m - x0) / config.GSD_M)), int(round((y1 - c.y1_m) / config.GSD_M))
        a = im.rgb[ri: ri + config.CHIP_PX, ci: ci + config.CHIP_PX]
        if a.shape[:2] == (config.CHIP_PX, config.CHIP_PX) and im.meta["valid"][ri: ri + 224, ci: ci + 224].mean() > 0.9:
            out.append(a)
    return np.stack(out)


def main() -> None:
    rng = np.random.default_rng(0)
    prov = PublicDataProvider()
    dino = DinoEmbedder()
    feats = []
    for aoi, year in SAMPLES:
        X = chips_pixels(prov, aoi, year, rng)
        C = dino.embed_cells(X, grid=config.CELL_GRID).reshape(-1, 768)
        print(f"{aoi} {year}: {len(X)} chips → {len(C)} cells")
        feats.append(C)
    F = np.concatenate(feats).astype(np.float64)
    mu = F.mean(0)
    sub = F[rng.choice(len(F), min(len(F), 80000), replace=False)] - mu
    _, S, Vt = np.linalg.svd(sub, full_matrices=False)
    P = Vt[: config.DINO_PCA_DIM].T
    var = float((S[: config.DINO_PCA_DIM] ** 2).sum() / (S ** 2).sum())
    config.DINO_PCA_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez(config.DINO_PCA_PATH, mean=mu.astype(np.float32), components=P.astype(np.float32),
             explained_variance=np.float32(var), n_cells=np.int64(len(F)), dino_id=DINO_ID,
             samples=np.array([f"{a}:{y}" for a, y in SAMPLES]))
    print(f"saved {config.DINO_PCA_PATH.name}: {len(F)} cells, {config.DINO_PCA_DIM} dims, explained variance {var:.3f}")


if __name__ == "__main__":
    main()
