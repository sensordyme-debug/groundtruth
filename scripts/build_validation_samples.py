"""Build the Validation Studio sample set (data/validation/<version>/samples.jsonl) from the RUNNING API.

  bash scripts/gt.sh python scripts/build_validation_samples.py [--base http://127.0.0.1:8000] [--out DIR] [--dry-run]

Deterministic given the API's outputs (seeded per stratum, candidates sorted by id before drawing). The API calls are the
same ones the app makes (pick_candidates → find_all with the picked sense + soft negatives; find_buildings;
find_anything), made one at a time, with a memory guard between calls. Nothing here reviews anything: every sample
starts unreviewed. data/labels/ is only READ (to mark the Lake Norman label cluster) and never written.

Design (docs/VALIDATION_STUDIO.md):
- strata = (class, region); at most --cap samples per stratum (--cap-small for parking / dock / solar);
- regions are made disjoint: a prediction is kept only in the first listed region whose bbox holds its centroid;
- holdout_group by geography (north / south / central); a prediction within --buffer-m of a region of ANOTHER group is
  dropped, so the groups are spatially separated (no Lake Norman leakage into south / central);
- census tiles: per holdout group one small tile per class (pool, building) in which EVERY prediction is sampled, so a
  reviewer can draw every real object there and recall becomes measurable inside those tiles. Pool tiles are centred
  on a random Overture building (independent of the pool detector); building tiles on a random point.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from shapely.geometry import Point, box, mapping, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth import validation as V  # noqa: E402
from groundtruth.geo import to_measure, to_wgs84  # noqa: E402

SEED = 20261108
GROUPS = {  # holdout groups by geography
    "north": ["Lake Norman", "Cornelius", "Huntersville", "Davidson"],
    "south": ["Ballantyne", "Pineville", "Matthews"],
    "central": ["Myers Park", "University City", "UNC Charlotte", "NoDa", "South End", "Uptown", "Plaza Midwood",
                "Dilworth", "SouthPark"],
}
GROUP_OF = {p: g for g, ps in GROUPS.items() for p in ps}
POOL_REGIONS = ["Lake Norman", "Cornelius", "Huntersville", "Ballantyne", "Pineville", "Matthews", "Myers Park",
                "University City"]
PARKING_REGIONS = ["Huntersville", "Ballantyne", "Pineville", "Myers Park", "University City"]
SOLAR_REGIONS = ["Huntersville", "Ballantyne", "Matthews", "Myers Park", "University City"]
DOCK_REGIONS = ["Lake Norman"]
FIND_ALL_MODEL = ("DINOv2 ViT-B/14 8x8 cells PCA-192 + OpenCLIP ViT-B-16 (chip index), SAM 2.1 hiera-tiny outlines + "
                  "object check")
OPENVOCAB_MODEL = "OpenCLIP chip ranking → Grounding DINO tiny (zero-shot) → SAM 2.1 hiera-tiny → CLIP crop verifier"
SCORE_KIND = "heuristic confidence from the tool (uncalibrated; not a probability)"


def log(*a: Any) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def mem_available_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1e6
    except OSError:
        pass
    return 99.0


def guard(min_gb: float) -> None:
    gb = mem_available_gb()
    if gb < min_gb:
        sys.exit(f"stopping: only {gb:.1f} GB available (< {min_gb}); the PC is memory-tight")


def sid(cls: str, fid: str) -> str:  # fid = tool feature id @ centroid (some tools reuse ids per chip)
    return f"{cls}-{hashlib.sha1(f'{cls}|{fid}'.encode()).hexdigest()[:12]}"


def bbox_contains(b: list[float], x: float, y: float) -> bool:
    return b[0] <= x <= b[2] and b[1] <= y <= b[3]


class Builder:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.c = httpx.Client(base_url=a.base, timeout=900)
        self.places: dict[str, list[float]] = self.c.get("/api/v1/places").json()["places"]
        self.health = self.c.get("/api/v1/health").json()
        self.index_version = next((i["index_version"] for i in self.health.get("indexes", [])
                                   if i["aoi"] == "county" and i["year"] == 2022), config.INDEX_VERSION)
        self.data_versions = json.loads((config.SENSES_PATH.parent / "data_versions.json").read_text())
        self.items = json.loads((config.DATA / "naip" / "items_2022.json").read_text())
        for it in self.items:
            it["_g"] = shape(it["geometry"])
        self._load_label_cluster()
        self._group_boxes_m = {p: to_measure(box(*self.places[p])) for p in GROUP_OF if p in self.places}
        self.calls: list[dict[str, Any]] = []

    # ---- read-only: the Lake Norman label cluster (data/labels is never written) ----
    def _load_label_cluster(self) -> None:
        with open(config.DATA / "labels" / "labels.geojson", encoding="utf-8") as f:
            fc = json.load(f)
        pts = [shape(ft["geometry"]) for ft in fc["features"]]
        ln = self.places["Lake Norman"]
        inln = [p for p in pts if bbox_contains(ln, p.x, p.y)]
        self.label_points_m = [to_measure(p) for p in pts]
        if inln:
            xs, ys = [p.x for p in inln], [p.y for p in inln]
            bm = to_measure(box(min(xs), min(ys), max(xs), max(ys))).buffer(self.a.cluster_pad_m).envelope
            self.cluster_bbox = [round(v, 6) for v in to_wgs84(bm).bounds]
        else:
            self.cluster_bbox = None
        self.cluster_n = len(inln)
        log(f"labels read-only: {len(pts)} points, {len(inln)} in Lake Norman; cluster bbox {self.cluster_bbox}")

    # ---- API ----
    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        guard(self.a.min_mem_gb)
        t = time.time()
        r = self.c.post(path, json=body)
        r.raise_for_status()
        env = r.json()
        ms = round((time.time() - t) * 1000)
        self.calls.append({"path": path, "body": body, "ms": ms, "status": env.get("status")})
        n = len(((env.get("result") or {}).get("geojson") or {}).get("features", []))
        log(f"{path} {json.dumps(body)[:140]} → {env.get('status')} {n} features, {ms} ms")
        return env

    def find_all_final(self, body: dict[str, Any]) -> dict[str, Any]:
        """find_all, re-asked until no outline is pending (outlines finish in the background and can drop objects)."""
        env = self.post("/api/v1/tools/find_all", body)
        for _ in range(self.a.outline_polls):
            if env["status"] == "declined":
                break
            m = env["result"]["metrics"]
            pend = m.get("pending_outline") or 0
            if not pend or not m.get("outlines_queued"):  # nothing pending, or nothing more will be outlined
                break
            time.sleep(self.a.outline_wait_s)
            env = self.post("/api/v1/tools/find_all", body)
            if (env["result"]["metrics"].get("pending_outline") or 0) >= pend:  # no progress: stop waiting
                break
        m = env["result"]["metrics"] if env["status"] != "declined" else {}
        log(f"  final: pending_outline={m.get('pending_outline')} outlines_queued={m.get('outlines_queued')} "
            f"dense={m.get('dense')}")
        return env

    def pick(self, query: str, sense: str) -> tuple[str, list[str]]:
        pc = self.post("/api/v1/tools/pick_candidates", {"query": query})
        tiles = [f for f in pc["result"]["geojson"]["features"] if not f["properties"].get("no_clear_example")]
        ch = next(f for f in tiles if f["properties"]["sense_id"] == sense)
        return ch["properties"]["example"], [f["properties"]["example"] for f in tiles if f is not ch]

    # ---- per-feature provenance ----
    def scene(self, x: float, y: float, scene_id: str | None) -> tuple[str | None, str | None]:
        if scene_id:
            it = next((i for i in self.items if i["scene_id"] == scene_id), None)
            if it:
                return scene_id, it["datetime"][:10]
        p = Point(x, y)
        hits = sorted((i for i in self.items if i["_g"].contains(p)), key=lambda i: i["scene_id"])
        if len(hits) == 1:
            return hits[0]["scene_id"], hits[0]["datetime"][:10]
        if len({h["datetime"][:10] for h in hits}) == 1 and hits:
            return None, hits[0]["datetime"][:10]  # overlap of scenes flown the same day
        return None, None  # scene overlap with different dates: not knowable from the footprint alone

    def crop_bbox(self, g) -> list[float]:
        gm = to_measure(g)
        x0, y0, x1, y1 = gm.bounds
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        half = max(self.a.crop_min_m / 2, 0.75 * max(x1 - x0, y1 - y0))
        return [round(v, 6) for v in to_wgs84(box(cx - half, cy - half, cx + half, cy + half)).bounds]

    def near_other_group(self, gm, group: str) -> bool:
        return any(GROUP_OF[p] != group and gm.distance(bm) < self.a.buffer_m for p, bm in self._group_boxes_m.items())

    def record(self, cls: str, region: str, f: dict[str, Any], env: dict[str, Any], tool: str, request: dict[str, Any],
               model: str) -> dict[str, Any] | None:
        g = shape(f["geometry"])
        if g.is_empty:
            return None
        c = g.centroid
        p = f.get("properties") or {}
        fid = str(f.get("id") or p.get("id") or p.get("example"))
        sc, date = self.scene(c.x, c.y, p.get("scene_id"))
        cm = to_measure(c)
        dist = min((cm.distance(q) for q in self.label_points_m), default=None)
        cb = self.crop_bbox(g)
        src = env.get("sources") or []
        return {
            "id": sid(cls, f"{fid}@{c.x:.6f},{c.y:.6f}"), "class": cls, "region": region, "holdout_group": GROUP_OF[region],
            "geometry": mapping(g), "geometry_kind": p.get("geometry_kind") or g.geom_type.lower(),
            "centroid": [round(c.x, 7), round(c.y, 7)],
            "area_m2": round(float(to_measure(g).area), 1),
            "source_tool": tool, "source_tool_version": env.get("version"), "source_feature_id": fid,
            "source_request": request, "source_sources": src,
            "model": model, "index_version": self.index_version if tool != "find_buildings" else None,
            "data_versions": {k: v for k, v in self.data_versions.items() if not k.startswith("_")},
            "score": None if p.get("confidence") is None else round(float(p["confidence"]), 4),
            "score_kind": SCORE_KIND, "raw_similarity": p.get("score") if tool != "find_buildings" else None,
            "imagery_year": 2022, "scene_id": sc, "acquisition_date": date,
            "crop_bbox": cb, "crop_url": f"/api/v1/crop?bbox={','.join(str(v) for v in cb)}&year=2022&px=256",
            "in_label_cluster": bool(self.cluster_bbox and bbox_contains(self.cluster_bbox, c.x, c.y)),
            "dist_to_nearest_label_m": None if dist is None else round(dist, 1),
            "census_tile_id": None,
        }

    # ---- candidates per (class, region): disjoint regions, holdout buffer ----
    def candidates(self, cls: str, regions: list[str], fetch) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        seen: set[str] = set()
        for k, region in enumerate(regions):
            feats, env, tool, req, model = fetch(region)
            rb = self.places[region]
            earlier = [self.places[r] for r in regions[:k]]
            keep, drop = [], {"outside_region": 0, "earlier_region": 0, "near_other_group": 0, "duplicate": 0}
            for f in feats:
                if not f.get("geometry"):
                    continue
                g = shape(f["geometry"])
                c = g.centroid
                if not bbox_contains(rb, c.x, c.y):
                    drop["outside_region"] += 1
                    continue
                if any(bbox_contains(b, c.x, c.y) for b in earlier):
                    drop["earlier_region"] += 1
                    continue
                if self.near_other_group(to_measure(g), GROUP_OF[region]):
                    drop["near_other_group"] += 1
                    continue
                r = self.record(cls, region, f, env, tool, req, model)
                if r is None or r["id"] in seen:
                    drop["duplicate"] += 1
                    continue
                seen.add(r["id"])
                keep.append(r)
            keep.sort(key=lambda r: r["id"])
            out[region] = keep
            self.population[(cls, region)] = {"tool_features": len(feats), "eligible": len(keep), "dropped": drop,
                                              "tool_metrics": {k2: v for k2, v in (env.get("result") or {}).get("metrics", {}).items()
                                                               if isinstance(v, (int, float, str, bool)) or v is None}}
            log(f"{cls}/{region}: {len(feats)} → {len(keep)} eligible; dropped {drop}")
        return out

    def run(self) -> None:
        a = self.a
        self.population: dict[tuple[str, str], dict[str, Any]] = {}
        log(f"memory available {mem_available_gb():.1f} GB; index {self.index_version}")
        pool_cands: dict[str, list[dict[str, Any]]] = {}
        cands: dict[str, dict[str, list[dict[str, Any]]]] = {}
        classes = a.classes.split(",")

        if "pool" in classes:
            ex, neg = self.pick("swimming pool", "backyard_pool")

            def fetch_pool(region: str):
                req = {"examples": [ex], "soft_exclude": neg, "place": region, "concept": "swimming_pool"}
                env = self.find_all_final(req)
                m = env["result"]["metrics"]
                model = FIND_ALL_MODEL + (f"; dense {self.data_versions.get('dense')} proposals" if m.get("dense") else "")
                return env["result"]["geojson"]["features"], env, "find_all", req, model
            cands["pool"] = pool_cands = self.candidates("pool", POOL_REGIONS, fetch_pool)

        if "parking" in classes:
            ex, neg = self.pick("parking lot", "surface_lot_full")

            def fetch_parking(region: str):
                req = {"examples": [ex], "soft_exclude": neg, "place": region, "concept": "parking_lot"}
                env = self.find_all_final(req)
                return env["result"]["geojson"]["features"], env, "find_all", req, FIND_ALL_MODEL
            cands["parking"] = self.candidates("parking", PARKING_REGIONS, fetch_parking)

        if "solar" in classes:
            ex, neg = self.pick("solar panels", "rooftop_solar_res")

            def fetch_solar(region: str):
                req = {"examples": [ex], "soft_exclude": neg, "place": region, "concept": "solar_installation"}
                env = self.find_all_final(req)
                return env["result"]["geojson"]["features"], env, "find_all", req, FIND_ALL_MODEL
            cands["solar"] = self.candidates("solar", SOLAR_REGIONS, fetch_solar)

        if "dock" in classes:
            def fetch_dock(region: str):
                req = {"text": "boat docks", "place": region}
                env = self.post("/api/v1/tools/find_anything", req)
                return env["result"]["geojson"]["features"], env, "find_anything", req, OPENVOCAB_MODEL
            cands["dock"] = self.candidates("dock", DOCK_REGIONS, fetch_dock)

        bld_feats: dict[str, list[dict[str, Any]]] = {}
        if "building" in classes:
            def fetch_bld(region: str):
                rng = random.Random(f"{SEED}|building-boxes|{region}")
                b = self.places[region]
                earlier = [self.places[r] for r in POOL_REGIONS[:POOL_REGIONS.index(region)]]
                feats, env, reqs = [], {}, []
                tries = 0
                while len(reqs) < a.building_boxes and tries < 200:
                    tries += 1
                    x, y = rng.uniform(b[0], b[2]), rng.uniform(b[1], b[3])
                    if any(bbox_contains(e, x, y) for e in earlier):
                        continue
                    cm = to_measure(Point(x, y))
                    if self.near_other_group(cm, GROUP_OF[region]):
                        continue
                    h = a.building_box_m / 2
                    bb = [round(v, 6) for v in to_wgs84(box(cm.x - h, cm.y - h, cm.x + h, cm.y + h)).bounds]
                    req = {"bbox": bb, "limit": 2000}
                    env = self.post("/api/v1/tools/find_buildings", req)
                    feats += env["result"]["geojson"]["features"]
                    reqs.append(req)
                bld_feats[region] = feats
                return feats, env, "find_buildings", {"boxes": reqs}, "Overture Maps buildings (OSM + ML-derived footprints)"
            cands["building"] = self.candidates("building", POOL_REGIONS, fetch_bld)

        # ---- stratified draw ----
        samples: dict[str, dict[str, Any]] = {}
        for cls in sorted(cands):
            cap = a.cap if cls in ("pool", "building") else a.cap_small
            for region, rows in sorted(cands[cls].items()):
                rng = random.Random(f"{SEED}|{cls}|{region}")
                pick = sorted(rng.sample(rows, min(cap, len(rows))), key=lambda r: r["id"])
                for r in pick:
                    r = dict(r)
                    r["sampling"] = {"design": "stratified_random", "stratum": f"{cls}|{region}",
                                     "stratum_eligible": len(rows), "stratum_sampled": len(pick),
                                     "weight": round(len(rows) / len(pick), 4), "seed": SEED}
                    samples[r["id"]] = r

        # ---- census tiles ----
        tiles = []
        if not a.no_census:
            tiles = self.census(cands, pool_cands, samples)

        order = sorted(samples.values(), key=lambda r: (V.CLASSES.index(r["class"]), r["region"], r["id"]))
        for r in order:
            r["built_at"] = self.built_at
        man = {"version": V.VERSION, "builder": "scripts/build_validation_samples.py", "seed": SEED,
               "api_base": a.base, "engine_version": self.health.get("engine_version"),
               "contract": self.health.get("contract"), "index_version": self.index_version,
               "data_versions": {k: v for k, v in self.data_versions.items() if not k.startswith("_")},
               "caps": {"pool": a.cap, "building": a.cap, "parking": a.cap_small, "dock": a.cap_small, "solar": a.cap_small},
               "regions": {"pool": POOL_REGIONS, "building": POOL_REGIONS, "parking": PARKING_REGIONS,
                           "solar": SOLAR_REGIONS, "dock": DOCK_REGIONS},
               "holdout_groups": GROUPS, "holdout_buffer_m": a.buffer_m,
               "lake_norman_label_cluster": {"bbox": self.cluster_bbox, "labels_in_lake_norman": self.cluster_n,
                                              "pad_m": a.cluster_pad_m, "source": "data/labels/labels.geojson (read-only)"},
               "population": {f"{k[0]}|{k[1]}": v for k, v in sorted(self.population.items())},
               "calls": self.calls, "built_at": self.built_at,
               "imagery": "NAIP 2022 0.6 m (scenes flown 2022-06-02, 2022-06-22, 2022-09-27)"}
        counts: dict[str, dict[str, int]] = {}
        for r in order:
            counts.setdefault(r["class"], {}).setdefault(r["region"], 0)
            counts[r["class"]][r["region"]] += 1
        man["counts"] = counts
        log("counts", json.dumps(counts))
        log(f"total {len(order)} samples, {len(tiles)} census tiles")
        # leakage checks
        for r in order:
            if r["holdout_group"] != "north":
                assert not r["in_label_cluster"], r["id"]
                assert not bbox_contains(self.places["Lake Norman"], *r["centroid"]), r["id"]
        if a.dry_run:
            log("dry run: nothing written")
            return
        written = V.write_samples(Path(a.out), order, tiles, man)
        log(f"wrote {Path(a.out)}: {written['samples']} samples, sha256 {written['samples_sha256'][:12]}")

    def _tile_ok(self, region: str, regions: list[str], tb: list[float]) -> bool:
        """The whole tile lies in its region, outside every earlier region, and away from other holdout groups, so
        every prediction inside it is in this region's candidate list (no prediction silently missing)."""
        g = box(*tb)
        return (box(*self.places[region]).contains(g)
                and not any(box(*self.places[e]).intersects(g) for e in regions[:regions.index(region)])
                and not self.near_other_group(to_measure(g), GROUP_OF[region]))

    def census(self, cands, pool_cands, samples) -> list[dict[str, Any]]:
        a = self.a
        tiles = []
        for group in ("north", "south", "central"):
            regions = [r for r in POOL_REGIONS if GROUP_OF[r] == group]
            # pools: a tile centred on a random Overture building (from the building draw boxes: independent of the
            # pool detector) in a random pool region of this group
            rng = random.Random(f"{SEED}|census|pool|{group}")
            if "pool" in cands and "building" in cands and regions:
                region = rng.choice(regions)
                blds = list(cands["building"].get(region, []))
                rng.shuffle(blds)
                for bl in blds:
                    c = Point(*bl["centroid"])
                    if self._tile_ok(region, POOL_REGIONS, self._tile_box(c, a.census_pool_m)):
                        tiles.append(self._tile("pool", region, c, a.census_pool_m, pool_cands.get(region, []), samples))
                        break
            # buildings: a tile centred on a random point in a random building region of this group
            rng = random.Random(f"{SEED}|census|building|{group}")
            if "building" in cands and regions:
                region = rng.choice(regions)
                b = self.places[region]
                c = None
                for _ in range(200):
                    p = Point(rng.uniform(b[0], b[2]), rng.uniform(b[1], b[3]))
                    if self._tile_ok(region, POOL_REGIONS, self._tile_box(p, a.census_building_m)):
                        c = p
                        break
                if c is None:
                    continue
                tb = self._tile_box(c, a.census_building_m)
                env = self.post("/api/v1/tools/find_buildings", {"bbox": tb, "limit": 2000})
                feats = env["result"]["geojson"]["features"]
                req = {"bbox": tb, "limit": 2000}
                rows = [r for r in (self.record("building", region, f, env, "find_buildings", req,
                                                 "Overture Maps buildings (OSM + ML-derived footprints)") for f in feats) if r]
                tiles.append(self._tile("building", region, c, a.census_building_m, rows, samples, box_=tb))
        return tiles

    def _tile_box(self, c, size_m: float) -> list[float]:
        cm = to_measure(c)
        h = size_m / 2
        return [round(v, 6) for v in to_wgs84(box(cm.x - h, cm.y - h, cm.x + h, cm.y + h)).bounds]

    def _tile(self, cls, region, c, size_m, rows, samples, box_=None) -> dict[str, Any]:
        tb = box_ or self._tile_box(c, size_m)
        tid = f"census-{cls}-{GROUP_OF[region]}-{hashlib.sha1(json.dumps(tb).encode()).hexdigest()[:8]}"
        tg = box(*tb)
        inside = sorted((r for r in rows if shape(r["geometry"]).intersects(tg)), key=lambda r: r["id"])
        ids = []
        for r in inside:
            s = samples.get(r["id"])
            if s is None:
                s = dict(r)
                s["sampling"] = {"design": "census_tile", "stratum": f"{cls}|{region}|census", "weight": None,
                                 "seed": SEED}
                samples[s["id"]] = s
            s["census_tile_id"] = tid
            ids.append(s["id"])
        log(f"census tile {tid}: {cls} in {region}, {len(ids)} predictions")
        return {"id": tid, "class": cls, "region": region, "holdout_group": GROUP_OF[region], "bbox": tb,
                "size_m": size_m, "centre": [round(c.x, 7), round(c.y, 7)], "prediction_sample_ids": ids,
                "crop_url": f"/api/v1/crop?bbox={','.join(str(v) for v in tb)}&year=2022&px=512",
                "centre_rule": "random Overture building (detector-independent)" if cls == "pool" else "random point",
                "instructions": f"Draw EVERY real {cls} whose outline lies (mostly) inside this tile, then submit with "
                                "complete=true. Leave out nothing; draw objects the AI missed too."}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default=str(V.default_root()))
    ap.add_argument("--classes", default="pool,building,parking,dock,solar")
    ap.add_argument("--cap", type=int, default=20, help="max samples per (class, region) for pool and building")
    ap.add_argument("--cap-small", type=int, default=12, help="max samples per (class, region) for parking/dock/solar")
    ap.add_argument("--buffer-m", type=float, default=1000.0)
    ap.add_argument("--cluster-pad-m", type=float, default=200.0)
    ap.add_argument("--crop-min-m", type=float, default=60.0)
    ap.add_argument("--building-boxes", type=int, default=4)
    ap.add_argument("--building-box-m", type=float, default=400.0)
    ap.add_argument("--census-pool-m", type=float, default=400.0)
    ap.add_argument("--census-building-m", type=float, default=200.0)
    ap.add_argument("--no-census", action="store_true")
    ap.add_argument("--min-mem-gb", type=float, default=3.0)
    ap.add_argument("--outline-polls", type=int, default=8)
    ap.add_argument("--outline-wait-s", type=float, default=15.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.dry_run and (Path(a.out) / "samples.jsonl").exists():
        sys.exit(f"{a.out}/samples.jsonl exists: samples are immutable; use --out data/validation/v2")
    b = Builder(a)
    b.built_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    b.run()


if __name__ == "__main__":
    main()
