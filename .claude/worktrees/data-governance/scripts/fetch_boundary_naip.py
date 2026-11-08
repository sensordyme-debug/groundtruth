"""Step 2 prerequisites: Mecklenburg County boundary (Census TIGERweb) + NAIP coverage check (Planetary Computer).

Writes:
  data/boundary/mecklenburg.geojson   one Feature, EPSG:4326
  data/naip/coverage.json             per-year coverage fraction, primary + secondary full-county years
  data/naip/items_<year>.json         scene list (id, bbox, href, gsd) per full-coverage year

Usage: python scripts/fetch_boundary_naip.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx
from pystac_client import Client
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth.geo import to_measure  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TIGERWEB = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query"
    "?where=GEOID%3D%2737119%27&outFields=NAME,GEOID,AREALAND,AREAWATER&outSR=4326&f=geojson"
)
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
FULL = 0.999  # fraction of county area that counts as "fully covered"


def fetch_boundary() -> dict:
    out = DATA / "boundary" / "mecklenburg.geojson"
    out.parent.mkdir(parents=True, exist_ok=True)
    r = httpx.get(TIGERWEB, headers={"User-Agent": "Groundtruth-hackathon/0.1"}, timeout=60)
    r.raise_for_status()
    fc = r.json()
    assert len(fc["features"]) == 1, "expected exactly one county feature"
    feat = fc["features"][0]
    geom = shape(feat["geometry"]).buffer(0)
    feature = {
        "type": "Feature",
        "properties": {
            "name": "Mecklenburg County, NC",
            "geoid": "37119",
            "source": "US Census Bureau TIGERweb State_County/MapServer/1",
            "retrieved": date.today().isoformat(),
            "area_km2": round(to_measure(geom).area / 1e6, 2),
        },
        "geometry": mapping(geom),
    }
    out.write_text(json.dumps(feature))
    print(f"boundary: {out} area_km2={feature['properties']['area_km2']} bbox={geom.bounds}")
    return feature


def naip_coverage(county: dict) -> dict:
    geom = shape(county["geometry"])
    county_m = to_measure(geom)
    client = Client.open(STAC)
    search = client.search(collections=["naip"], intersects=mapping(geom), max_items=5000)
    by_year: dict[int, list] = defaultdict(list)
    for item in search.items():
        year = int(item.properties.get("naip:year") or item.datetime.year)
        by_year[year].append(item)
    years = {}
    for year, items in sorted(by_year.items()):
        cover = unary_union([shape(i.geometry) for i in items])
        frac = to_measure(cover.intersection(geom)).area / county_m.area
        gsds = sorted({float(i.properties.get("gsd", 0)) for i in items})
        dates = sorted(i.datetime.date().isoformat() for i in items)
        years[year] = {
            "items": len(items),
            "coverage_fraction": round(frac, 5),
            "full": frac >= FULL,
            "gsd_m": gsds,
            "date_min": dates[0],
            "date_max": dates[-1],
        }
        print(f"NAIP {year}: {len(items):4d} items, coverage {frac:.4f}, gsd {gsds}, {dates[0]}..{dates[-1]}")
        if frac >= FULL:
            rows = [
                {
                    "scene_id": i.id,
                    "year": year,
                    "bbox": list(i.bbox),
                    "gsd_m": float(i.properties.get("gsd", 0.6)),
                    "href": i.assets["image"].href,
                    "datetime": i.datetime.isoformat(),
                    "geometry": i.geometry,
                }
                for i in sorted(items, key=lambda x: x.id)
            ]
            p = DATA / "naip" / f"items_{year}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(rows))
    full = sorted([y for y, v in years.items() if v["full"]], reverse=True)
    cov = {
        "collection": "naip",
        "stac": STAC,
        "checked": date.today().isoformat(),
        "full_threshold": FULL,
        "years": years,
        "full_years": full,
        "primary_year": full[0] if full else None,
        "secondary_year": full[1] if len(full) > 1 else None,
    }
    (DATA / "naip" / "coverage.json").write_text(json.dumps(cov, indent=2))
    print(f"full-county years: {full}; primary={cov['primary_year']} secondary={cov['secondary_year']}")
    return cov


if __name__ == "__main__":
    naip_coverage(fetch_boundary())
