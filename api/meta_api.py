"""GET /api/v1/sources: the data-source registry (engine/groundtruth/conf/sources.json) for per-layer freshness in the
UI: acquisition date, resolution, coverage, licence, attribution, freshness status. Read on each call (small file)."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")


@router.get("/runtime")
def runtime() -> dict[str, Any]:
    """Memory monitoring for the running API: process RSS, system memory available (Linux /proc), GPU memory in use by
    this process (only if torch already loaded CUDA; never initialises it)."""
    import os
    import sys
    out: dict[str, Any] = {"pid": os.getpid()}
    try:
        with open(f"/proc/{os.getpid()}/status") as fh:
            for line in fh:
                if line.startswith(("VmRSS:", "RssAnon:", "RssFile:")):
                    k, v = line.split(":", 1)
                    out[k.lower() + "_mb"] = round(int(v.split()[0]) / 1024, 1)
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith(("MemAvailable:", "MemTotal:")):
                    k, v = line.split(":", 1)
                    out[k.lower() + "_mb"] = round(int(v.split()[0]) / 1024, 1)
    except OSError:
        out["note"] = "memory stats unavailable on this OS"
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available() and torch.cuda.is_initialized():
        out["gpu_allocated_mb"] = round(torch.cuda.memory_allocated() / 2**20, 1)
        out["gpu_reserved_mb"] = round(torch.cuda.memory_reserved() / 2**20, 1)
    return out


@router.get("/sources")
def sources() -> dict[str, Any]:
    from groundtruth import config
    p = config.SENSES_PATH.parent / "sources.json"
    if not p.exists():
        return {"sources": [], "note": "source registry not built yet"}
    d = json.loads(p.read_text())
    rows = d.get("sources", d) if isinstance(d, dict) else d
    return {"sources": rows}
