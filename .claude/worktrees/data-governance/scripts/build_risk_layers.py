"""T4 risk layers → data/risk/<version>/ (a NEW versioned folder; the demo's data is never touched).

  python scripts/build_risk_layers.py flood   --out data/risk/v1 [--fallback data/layers/fema_flood_zones.geojson]
  python scripts/build_risk_layers.py shore   --out data/risk/v1
  python scripts/build_risk_layers.py rasters --out data/risk/v1 --lidar-dir data/lidar/v2 [--places "Myers Park" ...]

flood    FEMA NFHL flood hazard zones (MapServer layer 28, public domain), paged by DFIRM (37119C = Mecklenburg) with
         the zone attributes kept: FLD_ZONE, ZONE_SUBTY, SFHA_TF, STATIC_BFE, V_DATUM, LEN_UNIT, DEPTH.
           flood/nfhl_raw.parquet      every polygon as FEMA returns it (incl. unshaded X), unsimplified
           flood/flood_zones.parquet   regulatory zones only, accurate (unsimplified), with kind = 100yr (SFHA: A, AE,
                                       AO, AH, VE ...) | 500yr (shaded X, 0.2 % annual chance), bfe_m where FEMA
                                       publishes a STATIC_BFE (else null), bbox columns for DuckDB range queries
           flood/flood_zones_web.geojson  web copy for low zoom: simplified with tolerance conf/risk.json
                                       flood.web_simplify_tolerance_deg (~3 m), 6-decimal coordinates
shore    OSM water (ODbL) → the main water bodies (conf/risk.json shore.water_bodies): their union and the SHORELINE
         (union boundary minus the county line, because data/layers/osm_water was clipped to the county)
           shore/water_bodies.geojson, shore/shoreline.geojson
rasters  per lidar place, aligned to its 1 m nDSM grid (EPSG:32617):
           lidar/<place>/slope.tif   uint8 degrees from the bare-earth DTM (np.gradient, 1 m)
           lidar/<place>/canopy.tif  uint8 1 = canopy (nDSM ≥ canopy.min_height_m AND the NAIP pixel passes the colour
                                     rule: green chromaticity ≥ g_chroma_min, G > R, G ≥ B; no NDVI), 0 = not, 255 = no imagery
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from groundtruth import config  # noqa: E402

CONF = json.loads((config.SENSES_PATH.parent / "risk.json").read_text())
FIELDS = "DFIRM_ID,FLD_AR_ID,FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE,V_DATUM,LEN_UNIT,DEPTH,SOURCE_CIT"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def kind_of(zone: str | None, subty: str | None, sfha: bool) -> str | None:
    fc = CONF["flood"]
    z = (zone or "").upper().strip()
    if sfha or z in fc["zones_100yr"]:
        return "100yr"
    if any(s in (subty or "").upper() for s in fc["subtype_500yr_contains"]):
        return "500yr"
    return None


def fetch_nfhl(dfirm: str):
    """Yields pages (lists of GeoJSON features) so the build streams (low RAM)."""
    import httpx

    url = CONF["flood"]["nfhl_url"] + "/query"
    n, offset, page = 0, 0, 200
    with httpx.Client(timeout=180, headers={"User-Agent": "Groundtruth/0.4 (CrownHacks; T4 risk layers)"}) as c:
        while True:
            params = {"where": f"DFIRM_ID='{dfirm}'", "outFields": FIELDS, "outSR": 4326, "f": "geojson",
                      "resultOffset": offset, "resultRecordCount": page, "orderByFields": "OBJECTID"}
            for attempt in range(6):
                try:
                    r = c.get(url, params=params)
                    r.raise_for_status()
                    js = r.json()
                    if "error" in js:
                        raise RuntimeError(str(js["error"])[:200])
                    break
                except Exception as e:  # noqa: BLE001
                    log("  NFHL retry", attempt + 1, type(e).__name__, str(e)[:120])
                    time.sleep(4 * (attempt + 1))
            else:
                raise RuntimeError("NFHL fetch failed")
            fs = js.get("features", [])
            got = [f for f in fs if f.get("geometry")]
            n += len(got)
            if offset % 2000 < page:
                log(f"  NFHL {dfirm} offset {offset}: {n} polygons")
            if not fs:
                break
            yield got
            offset += len(fs)


class _PQ:
    """Streaming parquet writer (row groups of `batch` rows)."""

    def __init__(self, path: Path, batch: int = 512) -> None:
        self.path, self.batch, self.rows, self.w, self.n = path, batch, [], None, 0

    def add(self, row: dict) -> None:
        self.rows.append(row)
        if len(self.rows) >= self.batch:
            self.flush()

    def flush(self) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        if not self.rows:
            return
        t = pa.Table.from_pylist(self.rows, schema=self.w.schema if self.w else None)
        if self.w is None:
            self.w = pq.ParquetWriter(self.path, t.schema, compression="zstd")
        self.w.write_table(t)
        self.n += len(self.rows)
        self.rows = []

    def close(self) -> None:
        self.flush()
        if self.w:
            self.w.close()


def _schema(kind: bool):
    import pyarrow as pa

    f = [("fld_ar_id", pa.string()), ("fld_zone", pa.string()), ("zone_subty", pa.string()), ("sfha", pa.bool_()),
         ("static_bfe", pa.float64()), ("len_unit", pa.string()), ("v_datum", pa.string()), ("depth", pa.float64()),
         ("dfirm_id", pa.string()), ("xmin", pa.float64()), ("ymin", pa.float64()), ("xmax", pa.float64()),
         ("ymax", pa.float64()), ("geometry_wkb", pa.binary())]
    if kind:
        f += [("kind", pa.string()), ("floodway", pa.bool_()), ("bfe_m", pa.float64())]
    return pa.schema(f)


def flood(out: Path, fallback: Path | None) -> None:
    """Streams NFHL pages → nfhl_raw.parquet + flood_zones.parquet + flood_zones_web.geojson (low RAM)."""
    from shapely import make_valid
    from shapely.geometry import mapping, shape

    d = out / "flood"
    d.mkdir(parents=True, exist_ok=True)
    ft = CONF["flood"]["ft_to_m"]
    tol = CONF["flood"]["web_simplify_tolerance_deg"]
    upper = {k: k for k in FIELDS.split(",")}
    lower = {"DFIRM_ID": "dfirm_id", "FLD_AR_ID": "fld_ar_id", "FLD_ZONE": "fld_zone", "ZONE_SUBTY": "zone_subty",
             "SFHA_TF": "sfha", "STATIC_BFE": "static_bfe", "V_DATUM": "v_datum", "LEN_UNIT": "len_unit",
             "DEPTH": "depth", "SOURCE_CIT": "source_cit"}

    def pages():
        try:
            for dfirm in CONF["flood"]["dfirm_ids"]:
                for pg in fetch_nfhl(dfirm):
                    yield pg, upper
            pages.src = (f"FEMA NFHL MapServer layer 28, fetched {time.strftime('%Y-%m-%d')} "
                         f"(DFIRM {', '.join(CONF['flood']['dfirm_ids'])})")
        except Exception as e:  # noqa: BLE001
            if fallback is None or not fallback.exists() or pages.n:
                raise
            log(f"NFHL fetch failed ({e}); using the existing extract {fallback}")
            yield json.loads(fallback.read_text())["features"], lower
            pages.src = (f"existing extract {fallback.name} (FEMA NFHL layer 28 fetched 2026-11-07; V_DATUM/LEN_UNIT "
                         "were not kept there)")

    pages.n, pages.src = 0, None
    raw_w = _PQ(d / "nfhl_raw.parquet")
    reg_w = _PQ(d / "flood_zones.parquet")
    raw_w.w = None
    import pyarrow.parquet as pq

    raw_w.w = pq.ParquetWriter(d / "nfhl_raw.parquet", _schema(False), compression="zstd")
    reg_w.w = pq.ParquetWriter(d / "flood_zones.parquet", _schema(True), compression="zstd")
    web_tmp = d / "flood_zones_web.geojson.tmp"
    wf = web_tmp.open("w")
    wf.write('{"type":"FeatureCollection","features":[')
    n_web, n_bfe, zones, datums, units = 0, 0, {}, set(), set()
    for feats, key in pages():
        for f in feats:
            pages.n += 1
            p = f["properties"]
            g = make_valid(shape(f["geometry"]))
            if g.is_empty:
                continue
            get = lambda k: p.get(key[k])  # noqa: E731
            sf = get("SFHA_TF")
            sfha = bool(sf is True or str(sf).upper() == "T")
            zone, sub = get("FLD_ZONE"), get("ZONE_SUBTY")
            bfe = get("STATIC_BFE")
            unit = get("LEN_UNIT")
            bfe_m = None
            if bfe is not None and float(bfe) > -9000:
                bfe_m = round(float(bfe) * (1.0 if str(unit or "Feet").lower().startswith("m") else ft), 3)
            b = g.bounds
            base = {"fld_ar_id": get("FLD_AR_ID"), "fld_zone": zone, "zone_subty": sub, "sfha": sfha,
                    "static_bfe": None if bfe is None else float(bfe), "len_unit": unit, "v_datum": get("V_DATUM"),
                    "depth": None if get("DEPTH") is None else float(get("DEPTH")), "dfirm_id": get("DFIRM_ID"),
                    "xmin": b[0], "ymin": b[1], "xmax": b[2], "ymax": b[3], "geometry_wkb": g.wkb}
            raw_w.add(base)
            k = kind_of(zone, sub, sfha)
            if k is None:
                continue
            if bfe_m is not None:
                n_bfe += 1
                datums.add(str(base["v_datum"]))
                units.add(str(unit))
            reg = {**base, "kind": k, "floodway": "FLOODWAY" in (sub or "").upper(), "bfe_m": bfe_m}
            reg_w.add(reg)
            zones[f"{k}:{zone}"] = zones.get(f"{k}:{zone}", 0) + 1
            s = g.simplify(tol, preserve_topology=True)
            if not s.is_empty:
                wf.write(("," if n_web else "") + json.dumps(
                    {"type": "Feature", "properties": {"fld_zone": zone, "flood_kind": k, "floodway": reg["floodway"],
                                                       "bfe_m": bfe_m}, "geometry": _round(mapping(s))},
                    separators=(",", ":")))
                n_web += 1
    wf.write("]}")
    wf.close()
    raw_w.close()
    reg_w.close()
    web_tmp.replace(d / "flood_zones_web.geojson")
    meta = {"source": pages.src, "url": CONF["flood"]["nfhl_url"], "licence": "Public domain (US Government work, FEMA)",
            "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "raw_polygons": raw_w.n, "regulatory_polygons": reg_w.n,
            "web_features": n_web, "zones": dict(sorted(zones.items())), "polygons_with_static_bfe": n_bfe,
            "bfe_v_datums": sorted(datums), "bfe_len_units": sorted(units),
            "web_simplify_tolerance_deg": tol, "web_mb": round((d / "flood_zones_web.geojson").stat().st_size / 1e6, 1),
            "web_note": CONF["flood"]["web_simplify_note"],
            "kind_rule": "100yr = SFHA_TF true or FLD_ZONE in " + str(CONF["flood"]["zones_100yr"])
                         + "; 500yr = ZONE_SUBTY contains '0.2 PCT' (shaded X)"}
    (d / "meta.json").write_text(json.dumps(meta, indent=1))
    log("flood done", json.dumps(meta)[:800])


def _round(obj, nd=6):
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(v, nd) for v in obj]
        return [_round(o, nd) for o in obj]
    return obj


def shore(out: Path) -> None:
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    from groundtruth.geo import to_measure, to_wgs84

    c = CONF["shore"]
    d = out / "shore"
    d.mkdir(parents=True, exist_ok=True)
    fc = json.loads((config.DATA / "layers" / "osm_water.geojson").read_text())
    county = to_measure(shape(json.loads((config.DATA / "boundary" / "mecklenburg.geojson").read_text())["geometry"]))
    edge = county.boundary.buffer(c["county_edge_buffer_m"])
    bodies, lines = [], []
    by_name: dict[str, list] = {}
    for f in fc["features"]:
        p = f["properties"]
        g = shape(f["geometry"])
        if g.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        nm = p.get("name") or ""
        if nm in c["water_bodies"]:
            by_name.setdefault(nm, []).append(to_measure(g).buffer(0))
        elif p.get("osm_id") in c["extra_osm_ids"]:
            by_name.setdefault("Catawba River", []).append(to_measure(g).buffer(0))
    for nm, gs in sorted(by_name.items()):
        u = unary_union(gs)
        sl = u.boundary.difference(edge)
        bodies.append({"type": "Feature", "properties": {"name": nm, "area_km2": round(u.area / 1e6, 3)},
                       "geometry": _round(mapping(to_wgs84(u)), 7)})
        lines.append({"type": "Feature", "properties": {"name": nm, "length_km": round(sl.length / 1e3, 2)},
                      "geometry": _round(mapping(to_wgs84(sl)), 7)})
        log(f"shore: {nm}: {u.area / 1e6:.2f} km², shoreline {sl.length / 1e3:.1f} km (county-line edges removed)")
    (d / "water_bodies.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": bodies}))
    (d / "shoreline.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": lines}))
    (d / "meta.json").write_text(json.dumps({
        "source": "OpenStreetMap water (data/layers/osm_water.geojson, Geofabrik NC 2026-11-06)",
        "licence": "ODbL 1.0 — © OpenStreetMap contributors", "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "water_bodies": {b["properties"]["name"]: b["properties"]["area_km2"] for b in bodies},
        "shoreline_km": {l["properties"]["name"]: l["properties"]["length_km"] for l in lines},
        "method": f"union of the named OSM water polygons; shoreline = boundary minus the county line buffered "
                  f"{c['county_edge_buffer_m']} m (osm_water is clipped to the county)"}, indent=1))


def rasters(out: Path, lidar_dir: Path, places: list[str], strip: int = 1024) -> None:
    """Strip-wise (``strip`` rows at a time, 1-row overlap for the slope), so even Lake Norman stays < ~400 MB."""
    import rasterio
    from rasterio.windows import Window
    from shapely.geometry import box

    from groundtruth.geo import to_wgs84
    from groundtruth.providers.public import PublicDataProvider

    cc = CONF["canopy"]
    rule = cc["colour_rule"]
    prov = PublicDataProvider()
    for pl in places:
        src_dir = lidar_dir / pl.replace(" ", "_")
        if not (src_dir / "ndsm.tif").exists():
            log(f"rasters: no lidar for {pl} in {lidar_dir}, skipped")
            continue
        t0 = time.time()
        dst = out / "lidar" / pl.replace(" ", "_")
        dst.mkdir(parents=True, exist_ok=True)
        sd, sn = rasterio.open(src_dir / "dtm.tif"), rasterio.open(src_dir / "ndsm.tif")
        prof, tr = sd.profile.copy(), sd.transform
        H, W = sd.height, sd.width
        res = abs(tr.a)
        x0, y1 = tr.c, tr.f
        bounds_m = (x0, y1 - H * res, x0 + W * res, y1)
        p8 = {**prof, "dtype": "uint8", "nodata": 255, "compress": "deflate", "tiled": True, "blockxsize": 256,
              "blockysize": 256}
        log(f"rasters {pl}: {W}×{H} grid at {res} m, NAIP {cc['naip_year']}, strips of {strip} rows")
        n_can = n_valid = n_tall = 0
        scenes: set[str] = set()
        with rasterio.open(dst / "slope.tif", "w", **p8) as os_, rasterio.open(dst / "canopy.tif", "w", **p8) as oc:
            for r0 in range(0, H, strip):
                r1 = min(H, r0 + strip)
                a0, a1 = max(0, r0 - 1), min(H, r1 + 1)  # 1-row overlap for the gradient
                dtm = sd.read(1, window=Window(0, a0, W, a1 - a0)).astype(np.float32)
                gy, gx = np.gradient(dtm, res)
                sl = np.degrees(np.arctan(np.hypot(gx, gy)))[r0 - a0: r0 - a0 + (r1 - r0)]
                os_.write(np.clip(np.round(sl), 0, 90).astype(np.uint8), 1, window=Window(0, r0, W, r1 - r0))
                del dtm, gx, gy, sl
                ndsm = sn.read(1, window=Window(0, r0, W, r1 - r0)).astype(np.float32)
                sb = (x0, y1 - r1 * res, x0 + W * res, y1 - r0 * res)
                img = prov.read_grid(sb, int(cc["naip_year"]), gsd_m=res)
                scenes |= set(img.scene_ids)
                rgb = img.rgb.astype(np.float32)
                r_, g_, b_ = rgb[..., 0], rgb[..., 1], rgb[..., 2]
                green = (g_ / (r_ + g_ + b_ + 1e-6) >= rule["g_chroma_min"]) & (g_ - b_ >= rule["green_over_blue_min"])
                if rule.get("green_over_red", True):
                    green &= g_ > r_
                tall = np.isfinite(ndsm) & (ndsm >= cc["min_height_m"])
                can = (tall & green).astype(np.uint8)
                can[~img.meta["valid"]] = 255
                oc.write(can, 1, window=Window(0, r0, W, r1 - r0))
                n_can += int((can == 1).sum())
                n_valid += int((can != 255).sum())
                n_tall += int(tall.sum())
                del ndsm, rgb, img, can, tall, green
        sd.close()
        sn.close()
        meta = {"place": pl, "lidar": str(src_dir), "naip_year": cc["naip_year"], "naip_scenes": sorted(scenes),
                "canopy_rule": cc, "res_m": res, "canopy_pct_of_valid": round(100 * n_can / max(n_valid, 1), 1),
                "tall_pct": round(100 * n_tall / (W * H), 1), "grid": [W, H],
                "bbox_wgs84": list(to_wgs84(box(*bounds_m)).bounds), "bounds_m": list(bounds_m),
                "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "seconds": round(time.time() - t0),
                "slope": "degrees from the DTM, np.gradient at the grid spacing, uint8 rounded"}
        (dst / "meta.json").write_text(json.dumps(meta, indent=1))
        log(f"rasters {pl}: done in {meta['seconds']} s, canopy {meta['canopy_pct_of_valid']}% (tall {meta['tall_pct']}%)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["flood", "shore", "rasters"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--fallback")
    ap.add_argument("--lidar-dir")
    ap.add_argument("--places", nargs="*")
    a = ap.parse_args()
    def data_path(s: str) -> Path:  # "data/..." → under GROUNDTRUTH_DATA
        p = Path(s)
        return p if p.is_absolute() or p.parts[0] != "data" else config.DATA / Path(*p.parts[1:])

    o = data_path(a.out)
    if a.what == "flood":
        flood(o, data_path(a.fallback) if a.fallback else None)
    elif a.what == "shore":
        shore(o)
    else:
        ld = data_path(a.lidar_dir)
        pls = a.places or sorted(p.name.replace("_", " ") for p in ld.iterdir() if (p / "ndsm.tif").exists())
        rasters(o, ld, pls)
