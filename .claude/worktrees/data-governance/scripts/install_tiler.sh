#!/usr/bin/env bash
# Phase 1.1 deps (MIT / BSD, see docs/LICENCE_GATE.md): titiler.core + rio-tiler in both envs.
for e in groundtruth groundtruth-gpu; do
  echo "== $e"
  uv pip install --python ~/.venvs/$e/bin/python "titiler.core>=0.19,<0.25" "rio-tiler>=7,<8" 2>&1 | tail -6
done
