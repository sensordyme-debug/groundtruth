"""Experiment: does a finer DINOv2 cell grid make find_similar find pools? (query-time recompute from thumbnails)

  python scripts/exp_cell_grid.py --aoi south_charlotte --example 2022_e4472_n33675 --grid 8
Recomputes DINOv2 cells at the given grid for every chip in the slice (from the JPEG thumbnails), localises the
example's object cell with OpenCLIP ("swimming pool"), runs cell search + NMS, and writes a contact sheet.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.embed import ClipEmbedder, DinoEmbedder  # noqa: E402
from groundtruth.index import ChipIndex  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="south_charlotte")
    ap.add_argument("--example", required=True)
    ap.add_argument("--grid", type=int, default=8)
    ap.add_argument("--query", default="swimming pool")
    ap.add_argument("-k", type=int, default=24)
    ap.add_argument("--pca", type=int, default=0)
    ap.add_argument("--fit-aois", nargs="*", default=["unc_charlotte", "south_charlotte"])
    a = ap.parse_args()
    idx = ChipIndex(config.precompute_dir(2022, a.aoi))
    imgs = np.stack([np.asarray(Image.open(io.BytesIO(idx.thumb_jpeg(i))).convert("RGB")) for i in range(len(idx))])
    dino = DinoEmbedder()
    cells = dino.embed_cells(imgs, grid=a.grid)  # (N, g*g, 768)
    g = a.grid
    if a.pca:
        # fit PCA on cells from the other slice(s) too (as a county-wide fit would), then project + renormalise
        fit = [cells.reshape(-1, cells.shape[-1])]
        for other in a.fit_aois:
            if other == a.aoi:
                continue
            oi = ChipIndex(config.precompute_dir(2022, other))
            oimg = np.stack([np.asarray(Image.open(io.BytesIO(oi.thumb_jpeg(i))).convert("RGB")) for i in range(len(oi))])
            fit.append(dino.embed_cells(oimg, grid=g).reshape(-1, cells.shape[-1]))
        F = np.concatenate(fit)
        mu = F.mean(0)
        _, _, Vt = np.linalg.svd(F[np.random.default_rng(0).choice(len(F), min(len(F), 60000), replace=False)] - mu,
                                 full_matrices=False)
        P = Vt[: a.pca].T
        cells = (cells - mu) @ P
        cells /= np.linalg.norm(cells, axis=-1, keepdims=True)
        print(f"PCA {a.pca}: fitted on {len(F)} cells")
    cp = config.CHIP_PX // g
    # localise example with CLIP on context crops (3 cells wide)
    clip = ClipEmbedder()
    q = clip.embed_query(a.query)
    ex = idx.pos[a.example]
    ctx = min(config.CHIP_PX, cp * 3)
    crops = []
    for c in range(g * g):
        r, cc = divmod(c, g)
        cx, cy = cc * cp + cp // 2, r * cp + cp // 2
        x0 = min(max(cx - ctx // 2, 0), config.CHIP_PX - ctx)
        y0 = min(max(cy - ctx // 2, 0), config.CHIP_PX - ctx)
        crops.append(np.asarray(Image.fromarray(imgs[ex]).crop((x0, y0, x0 + ctx, y0 + ctx)).resize((224, 224))))
    best = int(np.argmax(clip.embed_images(np.stack(crops)) @ q))
    v = cells[ex, best]
    S = cells @ v  # (N, g*g)
    S[ex] = -1
    order = np.argsort(-S.max(1))
    # NMS in metres on cell boxes
    size = config.CHIP_M / g

    def cbox(i, c):
        r, cc = divmod(c, g)
        x0 = idx.meta["x0_m"][i] + cc * size
        top = idx.meta["y0_m"][i] + config.CHIP_M
        return (x0, top - (r + 1) * size, x0 + size, top - r * size)

    def overlap(b1, b2):
        return max(0, min(b1[2], b2[2]) - max(b1[0], b2[0])) * max(0, min(b1[3], b2[3]) - max(b1[1], b2[1])) > 0.3 * size * size

    kept, boxes = [], [cbox(ex, best)]
    for i in order:
        c = int(S[i].argmax())
        b = cbox(i, c)
        if any(overlap(b, o) for o in boxes):
            continue
        kept.append((int(i), c, float(S[i, c]))); boxes.append(b)
        if len(kept) >= a.k:
            break
    tile, pad, head, cols = 224, 4, 28, 6
    rows = -(-(len(kept) + 1) // cols)
    out = Image.new("RGB", (cols * (tile + pad) + pad, rows * (tile + pad + 16) + pad + head), (18, 20, 24))
    d = ImageDraw.Draw(out)
    d.text((pad, 8), f"grid {g}x{g} find_similar from {a.example}#{best} (first tile = example)", fill=(230, 230, 230))
    for n, (i, c, s) in enumerate([(ex, best, 1.0)] + kept):
        x = pad + (n % cols) * (tile + pad); y = head + pad + (n // cols) * (tile + pad + 16)
        out.paste(Image.fromarray(imgs[i]), (x, y))
        r, cc = divmod(c, g)
        d.rectangle([x + cc * cp, y + r * cp, x + (cc + 1) * cp - 1, y + (r + 1) * cp - 1], outline=(255, 70, 70), width=2)
        d.text((x + 2, y + tile + 2), f"#{n} {s:.3f}", fill=(200, 200, 200))
    p = config.DATA / "out" / f"exp_grid{g}{'_pca' + str(a.pca) if a.pca else ''}_{a.aoi}_{a.example}.png"
    out.save(p)
    print("wrote", p)


if __name__ == "__main__":
    main()
