#!/usr/bin/env bash
# Phase 5: routing env (separate, pinned): pyvalhalla (MIT; ships the Valhalla tile builder) + pyosmium (BSD-2) for the clip.
set -e
[ -d ~/.venvs/gt-routing ] || uv venv -q --python 3.12 ~/.venvs/gt-routing
uv pip install -q --python ~/.venvs/gt-routing/bin/python "pyvalhalla>=3.6,<4" "osmium>=4,<5" "shapely>=2" 2>&1 | tail -3
~/.venvs/gt-routing/bin/python -c "import valhalla, osmium; print('valhalla', getattr(valhalla, '__version__', '?'), 'osmium', osmium.version.pyosmium_release)"
ls ~/.venvs/gt-routing/bin | grep -i valhalla | head
# the API envs call the Valhalla Actor in-process (provider.isochrone)
for e in groundtruth groundtruth-gpu; do uv pip install -q --python ~/.venvs/$e/bin/python "pyvalhalla>=3.6,<4" 2>&1 | tail -2; done
