"""Generate docs/fixtures/<tool>.json: one real request/response per tool, through the HTTP routes (TestClient).

  python scripts/make_fixtures.py [--aoi south_charlotte]
Large GeoJSON payloads are truncated to the first 3 features (metrics/steps kept) so fixtures stay readable;
`_fixture.truncated_features` records how many were dropped.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402

OUT = ROOT / "docs" / "fixtures"


def trim(env: dict, keep: int = 3, max_geom_chars: int = 4000) -> dict:
    from shapely.geometry import mapping, shape

    feats = env.get("result", {}).get("geojson", {}).get("features", [])
    if len(feats) > keep:
        dropped = len(feats) - keep
        env["result"]["geojson"]["features"] = feats = feats[:keep]
        env["_fixture"] = {"truncated_features": dropped}
    for f in feats:  # fixtures show shape, not survey-grade geometry
        g = f.get("geometry")
        if g and len(json.dumps(g)) > max_geom_chars:
            s = shape(g)
            tol = 0.0001
            while len(json.dumps(mapping(s.simplify(tol)))) > max_geom_chars and tol < 0.05:
                tol *= 2
            f["geometry"] = json.loads(json.dumps(mapping(s.simplify(tol))))
            env.setdefault("_fixture", {})["geometry_simplified_deg"] = tol
    c = env.get("result", {}).get("metrics", {}).get("content")
    if isinstance(c, str) and len(c) > 600:
        env["result"]["metrics"]["content"] = c[:600] + "…"
    return env


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="south_charlotte")
    a = ap.parse_args()
    c = TestClient(app)
    OUT.mkdir(parents=True, exist_ok=True)
    common = {"aoi": a.aoi, "year": 2022}

    def call(tool: str, body: dict) -> dict:
        r = c.post(f"/api/v1/tools/{tool}", json=body)
        assert r.status_code == 200, (tool, r.status_code, r.text[:300])
        env = r.json()
        (OUT / f"{tool}.json").write_text(json.dumps({"request": {"method": "POST", "path": f"/api/v1/tools/{tool}",
                                                                  "body": body}, "response": trim(json.loads(json.dumps(env)))}, indent=1))
        print(f"{tool}: {env['status']} {env['result']['metrics'] if env['status'] != 'declined' else env['declined_reason']}")
        return env

    st = call("search_text", {**common, "query": "swimming pool", "k": 12})
    pc = call("pick_candidates", {**common, "query": "swimming pool"})
    ex = next(f["properties"]["example"] for f in pc["result"]["geojson"]["features"]
              if f["properties"]["sense_id"].endswith("_pool"))
    neg = next(f["properties"]["example"] for f in pc["result"]["geojson"]["features"]
               if f["properties"]["sense_id"] == "stormwater_pond")
    fs = call("find_similar", {**common, "examples": [ex], "exclude": [neg], "k": 12})
    hits = fs["result"]["geojson"]["features"]
    rf = call("refine", {**common, "accept": [ex, f"{hits[0]['id']}#{hits[0]['properties']['cell']}"],
                         "reject": [neg, f"{hits[-1]['id']}#{hits[-1]['properties']['cell']}"], "k": 12})
    call("outline", {**common, "examples": [ex]})
    from groundtruth import config as _cfg

    fa = call("find_all", {**common, "examples": [ex], "exclude": [neg], "concept": "swimming_pool",
                           "bbox": list(_cfg.AOIS[a.aoi]), "outline_top": 50})
    fa_ids = [f["properties"]["example"] for f in fa["result"]["geojson"]["features"]]
    if len(fa_ids) >= 3:
        call("refine", {**common, "accept": [ex, fa_ids[0]], "reject": [fa_ids[-1]], "concept": "swimming_pool",
                        "candidates": fa_ids})
    call("compare_years", {**common, "examples": [f["id"] for f in fa["result"]["geojson"]["features"][:12]],
                           "from_year": 2020, "concept": "swimming_pool"})
    cells = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": h["id"], "properties": {"score": h["properties"]["score"]},
         "geometry": {"type": "Polygon", "coordinates": [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]]}}
        for h in hits[:5] for b in [h["properties"]["cell_bbox"]]]}
    call("measure", {**common, "geojson": cells})
    call("filter", {**common, "geojson": cells, "where": {"within_m_of": {"layer": "osm_i485", "m": 1500},
                                                           "max_area_acres": 1.0}})
    call("export", {**common, "geojson": cells, "format": "csv"})
    layers = c.get("/api/v1/layers").json()
    name = "fema_flood_zones" if "fema_flood_zones" in layers else (layers[0] if layers else "none")
    call("add_layer", {**common, "name": name, "bbox": [-80.745, 35.297, -80.722, 35.315]})  # UNC Charlotte / Toby Creek
    # one declined example (policy: no information about people)
    r = c.post("/api/v1/tools/search_text", json={**common, "query": "who lives at 123 Main St"})
    (OUT / "declined.json").write_text(json.dumps({"request": {"method": "POST", "path": "/api/v1/tools/search_text",
                                                                "body": {**common, "query": "who lives at 123 Main St"}},
                                                    "response": r.json()}, indent=1))
    print("declined:", r.json()["status"], r.json()["declined_reason"])
    for path in ("/api/v1/health", "/api/v1/tools"):
        (OUT / f"{path.rsplit('/', 1)[1]}.json").write_text(json.dumps({"request": {"method": "GET", "path": path},
                                                                       "response": c.get(path).json()}, indent=1))
    print("fixtures →", OUT)


if __name__ == "__main__":
    main()
