"""Check FAISS mmap loading on the county index: RSS before/after and one search's latency."""
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.index import ChipIndex  # noqa: E402


def rss_mb() -> float:
    return int(open(f"/proc/{os.getpid()}/statm").read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6


r0 = rss_mb()
idx = ChipIndex(config.precompute_dir(2022, "county"))
t = time.perf_counter(); fi = idx.dino_index; tl = time.perf_counter() - t
r1 = rss_mb()
q = np.asarray(idx.dino[1234, 10], dtype=np.float32)
t = time.perf_counter(); D, I = idx.search_rows("dino", q, 3000); ts = time.perf_counter() - t
r2 = rss_mb()
print(f"rss: start {r0:.0f} MB, after load {r1:.0f} MB (load {tl:.2f}s), after search {r2:.0f} MB; search {ts*1000:.0f} ms; ntotal {fi.ntotal:,}")
