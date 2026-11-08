"""OSM major roads (highway ≥ secondary) for the lakefront check → data/layers/osm_major_roads.geojson (new file).
Source: data/routing/v1/meck_roads.osm.pbf (the Valhalla clip of the Geofabrik NC extract, ODbL). gt-routing env:
  ~/.venvs/gt-routing/bin/python scripts/build_major_roads.py
"""
import json
import os
import sys
from pathlib import Path

import osmium

DATA = Path(os.environ.get("GROUNDTRUTH_DATA", Path(__file__).resolve().parents[1] / "data"))
KEEP = {"motorway", "trunk", "primary", "secondary", "motorway_link", "trunk_link", "primary_link", "secondary_link"}


class H(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.f = []

    def way(self, w):
        hw = w.tags.get("highway")
        if hw in KEEP:
            try:
                coords = [[round(n.lon, 7), round(n.lat, 7)] for n in w.nodes]
            except osmium.InvalidLocationError:
                return
            if len(coords) >= 2:
                self.f.append({"type": "Feature", "properties": {"highway": hw, "name": w.tags.get("name"),
                                                                 "ref": w.tags.get("ref")},
                               "geometry": {"type": "LineString", "coordinates": coords}})


h = H()
h.apply_file(str(DATA / "routing" / "v1" / "meck_roads.osm.pbf"), locations=True)
out = DATA / "layers" / "osm_major_roads.geojson"
out.write_text(json.dumps({"type": "FeatureCollection", "features": h.f}))
print(f"{len(h.f)} major road ways → {out}")
