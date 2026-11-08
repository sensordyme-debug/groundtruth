"""Build the slice bundle Donovan (or anyone) needs to run the API locally, without the full county data.

  python scripts/make_slice_bundle.py [--out dist/groundtruth-slices-<ver>.tar.gz]
Contents (public / open data only; no ORYN data, no secrets, no people data):
  data/precompute/<INDEX_VERSION>/{unc_charlotte,south_charlotte}/2022/   index files (no shards)
  data/boundary/mecklenburg.geojson                                         US Census TIGERweb (public domain)
  data/naip/coverage.json                                                   NAIP coverage summary (public domain imagery)
  data/layers/{fema_flood_zones,osm_*}.geojson                              clipped to the two slice boxes
  BUNDLE_SOURCES.md, MANIFEST.sha256
OSM layers are fetched fresh from the public Overpass API for the slice boxes (ODbL, attribution required).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
import time
from datetime import date
from pathlib import Path

import httpx
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402

SLICES = ["unc_charlotte", "south_charlotte"]
YEAR = 2022
OVERPASS = "https://overpass-api.de/api/interpreter"
UA = "Groundtruth/0.1 (CrownHacks hackathon; slice bundle)"
INDEX_FILES = ["index.json", "chips.parquet", "dino.npy", "clip.npy", "dino.faiss", "clip.faiss", "thumbs.bin",
               "thumbs_offsets.npy"]


def overpass_layers(boxes: list[tuple[float, float, float, float]]) -> dict[str, list[dict]]:
    out = {"osm_motorways": [], "osm_i485": [], "osm_light_rail": [], "osm_light_rail_stations": [], "osm_water": []}
    for w, s, e, n in boxes:
        bb = f"{s},{w},{n},{e}"
        q = f"""[out:json][timeout:90];
        ( way["highway"~"^(motorway|motorway_link)$"]({bb});
          way["railway"="light_rail"]({bb});
          node["railway"~"^(station|halt|stop)$"]["station"="light_rail"]({bb});
          way["natural"="water"]({bb}); way["waterway"~"^(river|stream|canal)$"]({bb}); );
        out geom tags;"""
        for attempt in range(3):
            r = httpx.post(OVERPASS, data={"data": q}, headers={"User-Agent": UA}, timeout=180)
            if r.status_code == 200:
                break
            time.sleep(10 * (attempt + 1))
        r.raise_for_status()
        clip = box(w, s, e, n)
        for el in r.json()["elements"]:
            t = el.get("tags", {})
            if el["type"] == "node":
                out["osm_light_rail_stations"].append({"type": "Feature", "properties": {
                    "osm_id": f"node/{el['id']}", "name": t.get("name", "")},
                    "geometry": {"type": "Point", "coordinates": [el["lon"], el["lat"]]}})
                continue
            coords = [[p["lon"], p["lat"]] for p in el.get("geometry", [])]
            if len(coords) < 2:
                continue
            closed = coords[0] == coords[-1] and len(coords) >= 4
            g = shape({"type": "Polygon", "coordinates": [coords]} if (closed and t.get("natural") == "water")
                      else {"type": "LineString", "coordinates": coords}).intersection(clip)
            if g.is_empty:
                continue
            p = {"osm_id": f"way/{el['id']}", "name": t.get("name", "")}
            if t.get("highway"):
                refs = {x.strip().upper().replace(" ", "-") for x in t.get("ref", "").split(";")}
                p.update(highway=t["highway"], ref=t.get("ref", ""), is_i485="I-485" in refs, is_i77="I-77" in refs,
                         is_i85="I-85" in refs)
                f = {"type": "Feature", "properties": p, "geometry": mapping(g)}
                out["osm_motorways"].append(f)
                if p["is_i485"]:
                    out["osm_i485"].append(f)
            elif t.get("railway") == "light_rail":
                out["osm_light_rail"].append({"type": "Feature", "properties": p, "geometry": mapping(g)})
            else:
                p["kind"] = t.get("waterway") or t.get("water") or t.get("natural")
                out["osm_water"].append({"type": "Feature", "properties": p, "geometry": mapping(g)})
        time.sleep(2)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=config.ROOT / "dist" / f"groundtruth-slices-{config.INDEX_VERSION}.tar.gz")
    a = ap.parse_args()
    boxes = [config.AOIS[s] for s in SLICES]
    area = unary_union([box(*b) for b in boxes])
    files: dict[str, bytes] = {}
    for s in SLICES:
        d = config.precompute_dir(YEAR, s)
        for f in INDEX_FILES:
            files[f"data/precompute/{config.INDEX_VERSION}/{s}/{YEAR}/{f}"] = (d / f).read_bytes()
    files["data/boundary/mecklenburg.geojson"] = (config.DATA / "boundary" / "mecklenburg.geojson").read_bytes()
    cov = json.loads((config.DATA / "naip" / "coverage.json").read_text())
    files["data/naip/coverage.json"] = json.dumps(cov, indent=1).encode()
    # FEMA (web build) clipped to the slice boxes
    fema = json.loads((config.DATA / "layers" / "web" / "fema_flood_zones.geojson").read_text())
    ff = []
    for f in fema["features"]:
        g = shape(f["geometry"])
        if g.intersects(area):
            ff.append({**f, "geometry": mapping(g.intersection(area))})
    files["data/layers/fema_flood_zones.geojson"] = json.dumps({"type": "FeatureCollection", "features": ff}).encode()
    counts = {"fema_flood_zones": len(ff)}
    for name, feats in overpass_layers(boxes).items():
        files[f"data/layers/{name}.geojson"] = json.dumps({"type": "FeatureCollection", "features": feats}).encode()
        counts[name] = len(feats)
    today = date.today().isoformat()
    srcs = [
        {"name": "US Census TIGERweb county boundary (Mecklenburg, GEOID 37119)",
         "url": "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1",
         "date": cov.get("checked", today), "licence": "Public domain (US Government work)"},
        {"name": "FEMA NFHL flood hazard zones (clipped to slices)",
         "url": "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28", "date": today,
         "licence": "Public domain (US Government work, FEMA)"},
        {"name": "OpenStreetMap motorways / light rail / stations / water (Overpass, slices only)",
         "url": "https://www.openstreetmap.org/copyright", "date": today,
         "licence": "ODbL 1.0, © OpenStreetMap contributors"},
    ]
    files["data/layers/_sources.json"] = json.dumps(srcs, indent=1).encode()
    files["BUNDLE_SOURCES.md"] = (
        f"# Groundtruth slice bundle ({config.INDEX_VERSION}, built {today})\n\n"
        "Public/open data only. No ORYN data, no secrets, no data about people.\n\n"
        "| content | source | licence |\n|---|---|---|\n"
        "| chip indexes (`data/precompute/…`): thumbnails + embeddings of USDA NAIP 2022, 0.6 m | USDA NAIP via Microsoft Planetary Computer | Public domain (USDA FSA); embeddings by DINOv2 (Apache-2.0) and OpenCLIP ViT-B-16 laion2b_s34b_b88k (MIT) |\n"
        "| `data/boundary/mecklenburg.geojson` | US Census TIGERweb | Public domain |\n"
        "| `data/layers/fema_flood_zones.geojson` | FEMA NFHL (regulatory zones, simplified ~3 m) | Public domain |\n"
        "| `data/layers/osm_*.geojson` | OpenStreetMap via Overpass API | ODbL 1.0, attribution \"© OpenStreetMap contributors\" required |\n\n"
        f"Feature counts: {json.dumps(counts)}\n").encode()
    man = "\n".join(f"{hashlib.sha256(b).hexdigest()}  {p}" for p, b in sorted(files.items())) + "\n"
    files["MANIFEST.sha256"] = man.encode()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(a.out, "w:gz", compresslevel=6) as tar:
        for p, b in sorted(files.items()):
            ti = tarfile.TarInfo(p); ti.size = len(b); ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(b))
    print(f"wrote {a.out} ({a.out.stat().st_size/1e6:.1f} MB, {len(files)} files) counts={counts}")
    print("sha256", hashlib.sha256(a.out.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
