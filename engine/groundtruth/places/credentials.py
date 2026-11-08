"""Tiny secret loader: process environment first, then a git-ignored ``.env`` at the repo root.

Values are returned to the caller only; this module never prints, logs or stores them. ``.env`` syntax: ``NAME=value``
lines, optional ``export`` prefix, ``#`` comments, optional single/double quotes. No variable interpolation.
"""
from __future__ import annotations

import os
from pathlib import Path

from groundtruth import config


def env_file() -> Path:
    return Path(os.environ.get("GROUNDTRUTH_ENV_FILE", config.ROOT / ".env"))


def _parse(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, PermissionError, UnicodeDecodeError):
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if name:
            out[name] = value
    return out


def get_secret(name: str) -> str | None:
    """The secret's value, or None when unset/empty. Never log the return value."""
    v = os.environ.get(name)
    if v is None:
        v = _parse(env_file()).get(name)
    v = (v or "").strip()
    return v or None


def has_secret(name: str) -> bool:
    return get_secret(name) is not None
