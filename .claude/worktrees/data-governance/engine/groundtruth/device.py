"""Compute device auto-selection: cuda, then mps, then cpu.

``GROUNDTRUTH_DEVICE`` overrides (e.g. ``cpu`` on the production PC, whose GPU must not be used).
"""

from __future__ import annotations

import os


def torch_device(name: str):
    """Turn a device name into something ``tensor.to()`` accepts. ``dml`` = DirectML (AMD/Intel GPUs on
    Windows via torch-directml, MIT)."""
    if name == "dml":
        import torch_directml

        return torch_directml.device()
    if name.startswith("cuda"):
        cap = float(os.environ.get("GROUNDTRUTH_VRAM_GB", "0") or 0)
        if cap > 0:  # hard per-process cap (allocations beyond it raise OOM instead of growing)
            import torch

            total = torch.cuda.get_device_properties(0).total_memory / 1024**3
            torch.cuda.set_per_process_memory_fraction(min(1.0, cap / total), 0)
    return name


def gpu_has_room() -> bool:
    """Back-off guard: use the GPU only while at least GROUNDTRUTH_GPU_MIN_FREE_GB (default 1.5) is free, so anything
    else that loads onto the card (e.g. ORYN) wins and Groundtruth falls back to CPU."""
    try:
        import torch

        free, _ = torch.cuda.mem_get_info()
        return free / 1024**3 >= float(os.environ.get("GROUNDTRUTH_GPU_MIN_FREE_GB", "1.5"))
    except Exception:
        return False


def pick_device(override: str | None = None) -> str:
    forced = override or os.environ.get("GROUNDTRUTH_DEVICE")
    if forced == "cuda" and not gpu_has_room():
        return "cpu"
    if forced:
        return forced
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available() and gpu_has_room():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    try:
        import torch_directml

        if torch_directml.is_available():
            return "dml"
    except ImportError:
        pass
    return "cpu"
