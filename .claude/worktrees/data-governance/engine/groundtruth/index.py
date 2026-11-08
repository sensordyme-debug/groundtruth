"""Loads a precomputed v2 chip index: chips.parquet + dino.npy (N,16,768) + clip.npy (N,V,512) + FAISS fp16
indices over all cell/view rows + a JPEG thumbnail pack.

Row r of dino.faiss is (chip r // 16, cell r % 16); row r of clip.faiss is (chip r // V, view r % V).
Cells are numbered row-major from the north-west corner of the chip (8×8 grid of 28 px = 16.8 m in format v3).
CLIP views are [whole, NW, NE, SW, SE].
"""

from __future__ import annotations

import json
import os
from functools import cached_property
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pyarrow.parquet as pq

from groundtruth import config
from groundtruth.geo import MEASURE_CRS, WGS84, transformer

def view_cells(view: int, grid: int) -> tuple[int, ...]:
    """CLIP view (0 whole, 1 NW, 2 NE, 3 SW, 4 SE) → DINO cell ids it covers (grid×grid, row-major)."""
    if view == 0:
        return tuple(range(grid * grid))
    h = grid // 2
    r0, c0 = {1: (0, 0), 2: (0, h), 3: (h, 0), 4: (h, h)}[view]
    return tuple((r0 + r) * grid + (c0 + c) for r in range(h) for c in range(h))


class ChipIndex:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        if not (self.root / "index.json").exists():
            raise FileNotFoundError(f"no index at {self.root}; run scripts/precompute_county.py")
        self.info: dict[str, Any] = json.loads((self.root / "index.json").read_text())
        t = pq.read_table(self.root / "chips.parquet")
        self.meta = {c: t.column(c).to_numpy(zero_copy_only=False) for c in t.column_names}
        self.ids: list[str] = [str(x) for x in self.meta["chip_id"]]
        self.pos = {cid: i for i, cid in enumerate(self.ids)}
        self.grid = int(self.info.get("cell_grid", config.CELL_GRID))
        self.n_cells = self.grid * self.grid

    def __len__(self) -> int:
        return len(self.ids)

    # ---------- vectors ----------
    @cached_property
    def dino(self) -> np.ndarray:  # (N, 16, 768) float16, memory-mapped
        return np.load(self.root / "dino.npy", mmap_mode="r")

    @cached_property
    def clip(self) -> np.ndarray:  # (N, V, 512) float16, memory-mapped
        return np.load(self.root / "clip.npy", mmap_mode="r")

    @property
    def n_views(self) -> int:
        return int(self.clip.shape[1])

    def cells(self, i: int) -> np.ndarray:
        return np.asarray(self.dino[i], dtype=np.float32)

    def clip_views(self, idx: Iterable[int]) -> np.ndarray:
        return np.asarray(self.clip[np.asarray(list(idx))], dtype=np.float32)

    @cached_property
    def background(self) -> np.ndarray:
        """Mean cell vector over (a sample of) the index: what a 'typical' 33 m patch looks like here."""
        n = len(self)
        take = np.linspace(0, n - 1, min(n, 4000)).astype(int)
        m = np.asarray(self.dino[take], dtype=np.float32).reshape(-1, self.dino.shape[-1]).mean(0)
        return m / max(np.linalg.norm(m), 1e-12)

    def salient_cell(self, i: int, among: Iterable[int] | None = None) -> int:
        """The cell least like the county's typical patch (where 'the object' most likely is)."""
        cand = list(among) if among is not None else list(range(self.n_cells))
        sims = self.cells(i)[cand] @ self.background
        return int(cand[int(np.argmin(sims))])

    def _faiss(self, name: str):
        """Memory-mapped when the FAISS build supports it (codes stay in the reclaimable page cache, not process RAM)."""
        import faiss

        p = str(self.root / f"{name}.faiss")
        flag = getattr(faiss, "IO_FLAG_MMAP", 0) | getattr(faiss, "IO_FLAG_READ_ONLY", 0)
        if flag:
            try:
                return faiss.read_index(p, flag)
            except Exception:
                pass
        return faiss.read_index(p)

    @cached_property
    def dino_index(self):
        return self._faiss("dino")

    @cached_property
    def clip_index(self):
        return self._faiss("clip")

    def search_rows(self, kind: str, q: np.ndarray, k_rows: int) -> tuple[np.ndarray, np.ndarray]:
        if kind == "dino" and os.environ.get("GROUNDTRUTH_DINO_SEARCH", "numpy") == "numpy":
            return self._scan_dino(q, k_rows)
        idx = self.dino_index if kind == "dino" else self.clip_index
        k_rows = int(min(k_rows, idx.ntotal))
        D, I = idx.search(np.ascontiguousarray(q, dtype=np.float32).reshape(1, -1), k_rows)
        return D[0], I[0]

    def _scan_dino(self, q: np.ndarray, k_rows: int, chunk: int = 2048) -> tuple[np.ndarray, np.ndarray]:
        """Exact inner-product top-k over the memory-mapped cell vectors (rows = chip × cell), chunked. Replaces the
        in-RAM FAISS copy of the same 2.6 GB (the flat FAISS index scanned everything too); pages stay in the
        reclaimable page cache and are released after use (``release_pages``)."""
        q = np.ascontiguousarray(q, dtype=np.float32).reshape(-1)
        n, c = self.dino.shape[0], self.dino.shape[1]
        k_rows = int(min(k_rows, n * c))
        best_s = np.empty(0, np.float32)
        best_i = np.empty(0, np.int64)
        for s in range(0, n, chunk):
            x = np.asarray(self.dino[s:s + chunk], dtype=np.float32).reshape(-1, q.shape[0])
            sc = x @ q
            kk = min(k_rows, len(sc))
            top = np.argpartition(-sc, kk - 1)[:kk]
            best_s = np.concatenate([best_s, sc[top]])
            best_i = np.concatenate([best_i, top.astype(np.int64) + s * c])
            if len(best_s) > 4 * k_rows:
                keep = np.argpartition(-best_s, k_rows - 1)[:k_rows]
                best_s, best_i = best_s[keep], best_i[keep]
        order = np.lexsort((best_i, -best_s))[:k_rows]  # deterministic: score desc, then row id
        self.release_pages()
        return best_s[order], best_i[order]

    def release_pages(self) -> None:
        """Drop this index's file pages from the page cache (posix_fadvise DONTNEED: no root needed), so a big scan
        doesn't keep ~2.6 GB of the WSL VM's memory away from Windows."""
        for name in ("dino.npy", "clip.npy", "thumbs.bin"):
            p = self.root / name
            if not p.exists():
                continue
            try:
                fd = os.open(p, os.O_RDONLY)
                try:
                    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                finally:
                    os.close(fd)
            except (OSError, AttributeError):
                pass

    # ---------- geometry ----------
    def cell_bounds_m(self, i: int, cell: int) -> tuple[float, float, float, float]:
        x0, y0 = float(self.meta["x0_m"][i]), float(self.meta["y0_m"][i])
        size = config.CHIP_M / self.grid
        r, c = divmod(cell, self.grid)
        top = y0 + config.CHIP_M
        return (x0 + c * size, top - (r + 1) * size, x0 + (c + 1) * size, top - r * size)

    def view_bounds_m(self, i: int, view: int) -> tuple[float, float, float, float]:
        cells = view_cells(view, self.grid)
        bs = [self.cell_bounds_m(i, c) for c in cells]
        return (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))

    @staticmethod
    def bounds_wgs84(b: tuple[float, float, float, float]) -> list[float]:
        t = transformer(MEASURE_CRS, WGS84)
        xs, ys = t.transform([b[0], b[2], b[2], b[0]], [b[1], b[1], b[3], b[3]])
        return [round(min(xs), 7), round(min(ys), 7), round(max(xs), 7), round(max(ys), 7)]

    # ---------- thumbnails ----------
    @cached_property
    def _thumb_offsets(self) -> np.ndarray:
        return np.load(self.root / "thumbs_offsets.npy")

    def thumb_jpeg(self, i: int) -> bytes:
        o = self._thumb_offsets
        with open(self.root / "thumbs.bin", "rb") as f:
            f.seek(int(o[i]))
            return f.read(int(o[i + 1] - o[i]))

    # ---------- features ----------
    def feature(self, i: int, *, cell: int | None = None, **props: Any) -> dict[str, Any]:
        m = self.meta
        w, s, e, n = (float(m[k][i]) for k in ("west", "south", "east", "north"))
        p: dict[str, Any] = {"chip_id": self.ids[i], "year": int(m["year"][i]), "scene_id": str(m["scene_id"][i]),
                             "thumb_url": f"/api/v1/chips/{self.ids[i]}.jpg?aoi={self.root.parent.name}",
                             "chip_bbox": [w, s, e, n]}
        if cell is not None:
            p["cell"] = int(cell)
            p["cell_bbox"] = self.bounds_wgs84(self.cell_bounds_m(i, cell))
        p.update(props)
        return {"type": "Feature", "id": self.ids[i],
                "geometry": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]},
                "properties": p}
