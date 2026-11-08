"""Validation Studio store: human review of AI detections, kept apart from the training/test labels.

Layout (``data/validation/<VERSION>/``, default ``v1``):
  samples.jsonl          the sampled predictions to review. IMMUTABLE once built (``write_samples`` refuses to
                         overwrite); its sha256 is recorded in manifest.json and checked on load.
  census_tiles.json      small tiles in which EVERY prediction of a class was sampled, so a reviewer can draw every
                         real object in the tile (a complete census) and recall becomes measurable there. Immutable.
  manifest.json          build parameters (seed, caps, regions, holdout groups, API/index versions, checksums).
  reviews.jsonl          append-only journal of human reviews and census submissions (the source of truth).
  reviews_latest.json    snapshot (latest review per sample + census per tile), rewritten atomically on every change.
  backups/               a copy of every snapshot (most recent ``keep_backups`` kept; the journal is never pruned).

Write path for every review (under a lock), the same pattern as ``groundtruth.labels``:
  1. append one JSON line to the journal, flush + fsync;  2. rewrite the snapshot atomically (tmp + fsync +
  os.replace);  3. copy the snapshot to backups/.  On start, state is rebuilt from the journal; a torn final line is
  skipped. If the snapshot write fails the journal still holds the review and the previous snapshot is intact.

This module never reads from or writes to ``data/labels/`` (the human labelling test set), and it never creates a
review on its own: reviews only come from a person through the API. Reviewer ids are anonymous browser ids, never
names. Metrics (``metrics``) are computed from human reviews only.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from groundtruth import config
from groundtruth.geo import to_measure

VERSION = "v1"
TOOL_VERSION = "0.1.0"
CLASSES: tuple[str, ...] = ("pool", "building", "parking", "dock", "solar")
DECISIONS: tuple[str, ...] = ("accept", "reject", "uncertain", "needs_review", "corrected")
POSITIVE = ("accept", "corrected")  # the object is real (corrected = real, outline fixed by the reviewer)
NEGATIVE = ("reject",)
NOT_COUNTED = ("uncertain", "needs_review")  # never positive, never negative: reported separately
REVIEWER_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
MAX_POLY_AREA_M2 = 1_000_000.0  # 1 km²
MAX_POLY_DIST_M = 1_000.0  # a drawn polygon must lie within 1 km of the sample it corrects
MAX_NOTE = 2000
SCORE_BUCKETS: tuple[float, ...] = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0000001)
MATCH_IOU = 0.1  # census matching: a human object is "found" by a prediction overlapping it with IoU ≥ this
NOT_MEASURABLE = "not measurable"


def default_root() -> Path:
    return config.DATA / "validation" / VERSION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _fsync_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ validation helpers
class ValidationError(ValueError):
    """Bad input (maps to HTTP 400/422)."""


def check_reviewer(reviewer: str | None) -> str:
    if not reviewer or not REVIEWER_RE.match(reviewer):
        raise ValidationError("reviewer must be an anonymous id (8–64 letters, digits, dashes), never a name")
    return reviewer


def check_polygon(geom: Mapping[str, Any], near: BaseGeometry | None = None) -> dict[str, Any]:
    """A valid GeoJSON Polygon in EPSG:4326, 0 < area < 1 km², (optionally) within 1 km of ``near``."""
    if not isinstance(geom, Mapping) or geom.get("type") != "Polygon":
        raise ValidationError("polygon must be a GeoJSON Polygon")
    try:
        coords = geom["coordinates"]
        for ring in coords:
            for pt in ring:
                lon, lat = float(pt[0]), float(pt[1])
                if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                    raise ValidationError("polygon coordinates must be lon/lat (EPSG:4326)")
        g = shape({"type": "Polygon", "coordinates": coords})
    except ValidationError:
        raise
    except Exception as e:  # malformed rings etc.
        raise ValidationError(f"polygon is not readable: {e}") from None
    if g.is_empty or not g.is_valid:
        raise ValidationError("polygon is invalid (self-intersecting, too few points or empty)")
    gm = to_measure(g)
    if not (0 < gm.area < MAX_POLY_AREA_M2):
        raise ValidationError(f"polygon area must be > 0 and < 1 km² (got {gm.area:.1f} m²)")
    if near is not None and gm.distance(to_measure(near)) > MAX_POLY_DIST_M:
        raise ValidationError("polygon is more than 1 km from the sample")
    return mapping(g)


def iou_m(a: Mapping[str, Any] | BaseGeometry, b: Mapping[str, Any] | BaseGeometry) -> float:
    """Intersection over union of two EPSG:4326 geometries, measured in EPSG:32617."""
    ga = to_measure(a if isinstance(a, BaseGeometry) else shape(a))
    gb = to_measure(b if isinstance(b, BaseGeometry) else shape(b))
    u = ga.union(gb).area
    return float(ga.intersection(gb).area / u) if u > 0 else 0.0


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float] | None:
    """Wilson score 95 % interval for k successes in n trials (None when n = 0)."""
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0.0, c - h), 4), round(min(1.0, c + h), 4))


# ------------------------------------------------------------------ samples (immutable)
def write_samples(root: Path, samples: Sequence[Mapping[str, Any]], census_tiles: Sequence[Mapping[str, Any]],
                  manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Write a NEW sample set. Refuses if one already exists (samples are immutable; build a new version instead)."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    sp, cp, mp = root / "samples.jsonl", root / "census_tiles.json", root / "manifest.json"
    if sp.exists() or cp.exists():
        raise FileExistsError(f"{sp} already exists: samples are immutable; write a new version folder instead")
    ids = [s["id"] for s in samples]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate sample ids")
    for s in samples:
        if s["class"] not in CLASSES:
            raise ValueError(f"unknown class {s['class']!r}")
    _fsync_write(sp, "".join(json.dumps(s, separators=(",", ":"), sort_keys=True) + "\n" for s in samples))
    _fsync_write(cp, json.dumps(list(census_tiles), indent=1, sort_keys=True))
    man = {**manifest, "samples": len(samples), "census_tiles": len(census_tiles),
           "samples_sha256": sha256_file(sp), "census_tiles_sha256": sha256_file(cp), "written_at": _now()}
    _fsync_write(mp, json.dumps(man, indent=1, sort_keys=True))
    for p in (sp, cp, mp):  # read-only on disk as a guard (best effort; Windows/WSL mounts may ignore it)
        try:
            os.chmod(p, 0o444)
        except OSError:
            pass
    return man


# ------------------------------------------------------------------ the store
class ValidationStore:
    def __init__(self, root: Path | None = None, *, keep_backups: int = 200) -> None:
        self.root = Path(root) if root is not None else default_root()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "backups").mkdir(exist_ok=True)
        self.journal = self.root / "reviews.jsonl"
        self.snapshot = self.root / "reviews_latest.json"
        self.keep_backups = keep_backups
        self._lock = threading.RLock()
        self.samples: dict[str, dict[str, Any]] = {}
        self.sample_order: list[str] = []
        self.census_tiles: dict[str, dict[str, Any]] = {}
        self.manifest: dict[str, Any] = {}
        self.integrity: dict[str, Any] = {}
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._census: dict[str, list[dict[str, Any]]] = {}
        self._events = 0
        self._load_samples()
        self._load_journal()

    # ---------- loading ----------
    def _load_samples(self) -> None:
        sp, cp, mp = self.root / "samples.jsonl", self.root / "census_tiles.json", self.root / "manifest.json"
        if mp.exists():
            self.manifest = json.loads(mp.read_text(encoding="utf-8"))
        if sp.exists():
            with open(sp, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        s = json.loads(line)
                        self.samples[s["id"]] = s
                        self.sample_order.append(s["id"])
        if cp.exists():
            self.census_tiles = {t["id"]: t for t in json.loads(cp.read_text(encoding="utf-8"))}
        self.integrity = {
            "samples_sha256_ok": (sha256_file(sp) == self.manifest.get("samples_sha256")) if sp.exists() and self.manifest else None,
            "census_tiles_sha256_ok": (sha256_file(cp) == self.manifest.get("census_tiles_sha256")) if cp.exists() and self.manifest else None,
        }

    def _load_journal(self) -> None:
        if not self.journal.exists():
            return
        with open(self.journal, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:  # a torn final line (power loss mid-write) is skipped, never fatal
                    continue
                self._apply(ev)

    def _apply(self, ev: dict[str, Any]) -> None:
        self._events += 1
        if ev.get("op") == "review":
            r = ev["review"]
            self._history.setdefault(r["sample_id"], []).append(r)
        elif ev.get("op") == "census":
            c = ev["census"]
            self._census.setdefault(c["tile_id"], []).append(c)

    # ---------- writing ----------
    def _journal_append(self, ev: dict[str, Any]) -> None:
        with open(self.journal, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(ev, separators=(",", ":"), sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def snapshot_doc(self) -> dict[str, Any]:
        return {"version": VERSION, "events": self._events, "updated_at": _now(),
                "latest": {sid: h[-1] for sid, h in sorted(self._history.items())},
                "census": {tid: c[-1] for tid, c in sorted(self._census.items())},
                "counts": self.progress()["by_decision"]}

    def _persist(self) -> None:
        _fsync_write(self.snapshot, json.dumps(self.snapshot_doc(), indent=1, sort_keys=True))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        shutil.copyfile(self.snapshot, self.root / "backups" / f"reviews_{stamp}.json")
        backups = sorted((self.root / "backups").glob("reviews_*.json"))
        for old in backups[: max(0, len(backups) - self.keep_backups)]:
            old.unlink(missing_ok=True)

    def _commit(self, ev: dict[str, Any]) -> None:
        self._journal_append(ev)  # source of truth first: a failed snapshot never loses the review
        self._apply(ev)
        self._persist()

    # ---------- public API ----------
    def add_review(self, *, sample_id: str, reviewer: str, decision: str, polygon: Mapping[str, Any] | None = None,
                   note: str = "", review_id: str | None = None) -> dict[str, Any]:
        s = self.samples.get(sample_id)
        if s is None:
            raise KeyError(sample_id)
        check_reviewer(reviewer)
        if decision not in DECISIONS:
            raise ValidationError(f"decision must be one of {DECISIONS}")
        if decision == "corrected" and polygon is None:
            raise ValidationError("a 'corrected' review needs the corrected polygon")
        poly = check_polygon(polygon, near=shape(s["geometry"])) if polygon is not None else None
        note = (note or "")[:MAX_NOTE]
        with self._lock:
            rid = review_id or uuid.uuid4().hex
            for r in self._history.get(sample_id, []):
                if r["id"] == rid:  # idempotent client retry
                    return r
            r = {"id": rid, "sample_id": sample_id, "reviewer": reviewer, "decision": decision, "polygon": poly,
                 "note": note, "t": _now()}
            self._commit({"op": "review", "at": r["t"], "review": r})
            return r

    def add_census(self, *, tile_id: str, reviewer: str, polygons: Sequence[Mapping[str, Any]], complete: bool,
                   note: str = "") -> dict[str, Any]:
        """A reviewer's drawing of EVERY real object of the tile's class inside the tile (``complete`` = the
        reviewer asserts nothing was left out). Only complete censuses are used for recall."""
        t = self.census_tiles.get(tile_id)
        if t is None:
            raise KeyError(tile_id)
        check_reviewer(reviewer)
        from shapely.geometry import box as sbox

        tb = sbox(*t["bbox"])
        polys = [check_polygon(p, near=tb) for p in polygons]
        with self._lock:
            c = {"id": uuid.uuid4().hex, "tile_id": tile_id, "reviewer": reviewer, "polygons": polys,
                 "complete": bool(complete), "note": (note or "")[:MAX_NOTE], "t": _now()}
            self._commit({"op": "census", "at": c["t"], "census": c})
            return c

    def history(self, sample_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history.get(sample_id, []))

    def latest(self) -> dict[str, dict[str, Any]]:
        """Latest review per sample (journal order = server time order; the latest wins)."""
        with self._lock:
            return {sid: h[-1] for sid, h in self._history.items() if h}

    def latest_census(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {tid: c[-1] for tid, c in self._census.items() if c}

    def sample_view(self, sample_id: str, *, with_history: bool = False) -> dict[str, Any]:
        s = self.samples[sample_id]
        h = self.history(sample_id)
        v = {**s, "review": h[-1] if h else None, "n_reviews": len(h),
             "status": (h[-1]["decision"] if h else "unreviewed")}
        if with_history:
            v["history"] = h
        return v

    def list_samples(self, *, cls: str | None = None, region: str | None = None, status: str = "all",
                     limit: int = 50, offset: int = 0) -> dict[str, Any]:
        if status not in ("all", "unreviewed", "reviewed", *DECISIONS):
            raise ValidationError("status must be unreviewed | reviewed | all | <a decision>")
        latest = self.latest()
        rows = []
        for sid in self.sample_order:
            s = self.samples[sid]
            if cls and s["class"] != cls:
                continue
            if region and s["region"] != region:
                continue
            r = latest.get(sid)
            if status == "unreviewed" and r is not None:
                continue
            if status == "reviewed" and r is None:
                continue
            if status in DECISIONS and (r is None or r["decision"] != status):
                continue
            rows.append(sid)
        page = rows[offset: offset + max(0, limit)]
        return {"total": len(rows), "offset": offset, "limit": limit,
                "samples": [self.sample_view(sid) for sid in page]}

    def progress(self) -> dict[str, Any]:
        latest = self.latest()
        by: dict[str, dict[str, dict[str, int]]] = {}
        dec = {d: 0 for d in DECISIONS}
        for sid in self.sample_order:
            s = self.samples[sid]
            cell = by.setdefault(s["class"], {}).setdefault(s["region"], {"samples": 0, "reviewed": 0, **{d: 0 for d in DECISIONS}})
            cell["samples"] += 1
            r = latest.get(sid)
            if r:
                cell["reviewed"] += 1
                cell[r["decision"]] += 1
                dec[r["decision"]] += 1
        lc = self.latest_census()
        return {"samples": len(self.samples), "reviewed": len([s for s in self.sample_order if s in latest]),
                "by_decision": dec, "by_class_region": by,
                "census_tiles": len(self.census_tiles),
                "census_complete": sum(1 for c in lc.values() if c["complete"]),
                "journal_events": self._events, "integrity": self.integrity,
                "dataset": f"validation/{VERSION}"}

    def metrics(self, **kw: Any) -> dict[str, Any]:
        return metrics(list(self.samples.values()), self.latest(), self.census_tiles, self.latest_census(), **kw)

    # ---------- export (the evaluation labels) ----------
    def export_features(self) -> list[dict[str, Any]]:
        latest = self.latest()
        out = []
        for sid in self.sample_order:
            r = latest.get(sid)
            if r is None:
                continue
            s = self.samples[sid]
            human = r["decision"] == "corrected" and r.get("polygon")
            props = {"sample_id": sid, "class": s["class"], "region": s["region"],
                     "holdout_group": s["holdout_group"], "decision": r["decision"], "reviewer": r["reviewer"],
                     "reviewed_at": r["t"], "note": r["note"], "n_reviews": len(self._history.get(sid, [])),
                     "geometry_source": "human" if human else "prediction",
                     "prediction_geometry": s["geometry"] if human else None,
                     "source_tool": s["source_tool"], "source_tool_version": s.get("source_tool_version"),
                     "model": s.get("model"), "index_version": s.get("index_version"),
                     "score": s.get("score"), "score_kind": s.get("score_kind"),
                     "imagery_year": s.get("imagery_year"), "acquisition_date": s.get("acquisition_date"),
                     "in_label_cluster": s.get("in_label_cluster"), "census_tile_id": s.get("census_tile_id"),
                     "dataset": f"validation/{VERSION}"}
            out.append({"type": "Feature", "id": sid, "geometry": r["polygon"] if human else s["geometry"],
                        "properties": props})
        for tid, c in sorted(self.latest_census().items()):
            t = self.census_tiles[tid]
            for k, p in enumerate(c["polygons"]):
                out.append({"type": "Feature", "id": f"{tid}/obj{k}", "geometry": p,
                            "properties": {"census_tile_id": tid, "class": t["class"], "region": t["region"],
                                           "holdout_group": t["holdout_group"], "decision": "census_object",
                                           "census_complete": c["complete"], "reviewer": c["reviewer"],
                                           "reviewed_at": c["t"], "geometry_source": "human",
                                           "dataset": f"validation/{VERSION}"}})
        return out

    def export_geojson(self) -> dict[str, Any]:
        return {"type": "FeatureCollection", "name": f"groundtruth_validation_{VERSION}", "crs_note": "EPSG:4326",
                "features": self.export_features()}

    def export_csv(self) -> str:
        fields = ["sample_id", "census_tile_id", "class", "region", "holdout_group", "decision", "reviewer",
                  "reviewed_at", "geometry_source", "centroid_lon", "centroid_lat", "geometry_wkt", "source_tool",
                  "source_tool_version", "model", "index_version", "score", "score_kind", "imagery_year",
                  "acquisition_date", "in_label_cluster", "note"]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for f in self.export_features():
            g = shape(f["geometry"])
            c = g.centroid
            w.writerow({**f["properties"], "centroid_lon": round(c.x, 7), "centroid_lat": round(c.y, 7),
                        "geometry_wkt": g.wkt})
        return buf.getvalue()


# ------------------------------------------------------------------ metrics (human reviews only)
def _rate_block(decisions: Iterable[str], n_samples: int) -> dict[str, Any]:
    c = {d: 0 for d in DECISIONS}
    for d in decisions:
        c[d] += 1
    tp = c["accept"] + c["corrected"]
    fp = c["reject"]
    judged = tp + fp
    prec = round(tp / judged, 4) if judged else None
    return {
        "n_samples": n_samples, "n_reviewed": sum(c.values()), **c,
        "n_judged": judged,  # accept + corrected + reject: the precision denominator
        "precision": prec, "precision_ci95": wilson(tp, judged),
        "false_discovery_rate": round(fp / judged, 4) if judged else None,
        "false_discovery_rate_ci95": wilson(fp, judged),
        "not_counted": {"uncertain": c["uncertain"], "needs_review": c["needs_review"]},
    }


def _weighted_precision(rows: Sequence[tuple[dict[str, Any], str]]) -> float | None:
    """Population-weighted precision across strata (each judged sample weighted by stratum population / sampled)."""
    num = den = 0.0
    for s, d in rows:
        if d in NOT_COUNTED:
            continue
        w = float((s.get("sampling") or {}).get("weight") or 1.0)
        den += w
        if d in POSITIVE:
            num += w
    return round(num / den, 4) if den else None


def metrics(samples: Sequence[Mapping[str, Any]], latest: Mapping[str, Mapping[str, Any]],
            census_tiles: Mapping[str, Mapping[str, Any]] | None = None,
            census: Mapping[str, Mapping[str, Any]] | None = None, *,
            buckets: Sequence[float] = SCORE_BUCKETS, match_iou: float = MATCH_IOU,
            exclude_label_cluster: bool = False) -> dict[str, Any]:
    """Precision (Wilson 95 %), false discovery rate, IoU of corrected outlines, score calibration and (where a complete
    human census exists) recall, per class and per region / holdout group. Deterministic for a given input.

    precision = (accept + corrected) / (accept + corrected + reject); uncertain and needs_review are never positive and
    never in the denominator, they are reported separately."""
    census_tiles = census_tiles or {}
    census = census or {}
    samples = sorted(samples, key=lambda s: s["id"])
    if exclude_label_cluster:
        samples = [s for s in samples if not s.get("in_label_cluster")]
    by_cls: dict[str, list[dict[str, Any]]] = {}
    for s in samples:
        by_cls.setdefault(s["class"], []).append(s)

    out: dict[str, Any] = {"tool": "validation_metrics", "version": TOOL_VERSION, "dataset": f"validation/{VERSION}",
                           "computed_at": _now(), "exclude_label_cluster": exclude_label_cluster,
                           "n_samples": len(samples), "n_reviewed": sum(1 for s in samples if s["id"] in latest),
                           "definitions": {
                               "precision": "(accept + corrected) / (accept + corrected + reject); uncertain and "
                                            "needs_review are excluded and reported separately",
                               "ci95": "Wilson score interval, z = 1.96",
                               "false_discovery_rate": "reject / (accept + corrected + reject) = 1 − precision",
                               "false_positive_rate": "FP / (FP + TN) needs enumerated true negatives, which a "
                                                      "detection review does not have: not defined. Census tiles report "
                                                      "false positives per tile instead",
                               "iou": f"prediction vs the reviewer's corrected polygon, measured in EPSG:32617",
                               "calibration": "score bucket vs observed acceptance (accept + corrected) among judged "
                                              "samples; scores are heuristic and uncalibrated",
                               "recall": f"only inside census tiles with a complete human census: human objects matched "
                                         f"one-to-one by a sampled prediction with IoU ≥ {match_iou}; else '{NOT_MEASURABLE}'",
                               "weighted_precision": "precision with each judged sample weighted by its stratum "
                                                     "population / samples drawn (an estimate for the whole output)"},
                           "classes": {}}
    for cls in sorted(by_cls):
        ss = by_cls[cls]
        rows = [(s, latest[s["id"]]["decision"]) for s in ss if s["id"] in latest]
        block = _rate_block((d for _, d in rows), len(ss))
        block["weighted_precision"] = _weighted_precision(rows)
        for key in ("region", "holdout_group", "geometry_kind"):  # outline vs box predictions differ in kind
            groups: dict[str, list[dict[str, Any]]] = {}
            for s in ss:
                groups.setdefault(str(s.get(key)), []).append(s)
            block[f"by_{key}"] = {g: _rate_block((latest[s["id"]]["decision"] for s in gs if s["id"] in latest), len(gs))
                                  for g, gs in sorted(groups.items())}
        # IoU of corrected outlines
        corr = [s for s in ss if s["id"] in latest and latest[s["id"]]["decision"] == "corrected"
                and latest[s["id"]].get("polygon")]
        block["iou_corrected"] = _iou_block(corr, latest)
        block["iou_corrected"]["by_geometry_kind"] = {
            k: _iou_block([s for s in corr if str(s.get("geometry_kind")) == k], latest)
            for k in sorted({str(s.get("geometry_kind")) for s in corr})}
        # calibration
        cal = []
        for lo, hi in zip(buckets[:-1], buckets[1:]):
            inb = [(s, d) for s, d in rows if s.get("score") is not None and lo <= float(s["score"]) < hi
                   and d not in NOT_COUNTED]
            k = sum(1 for _, d in inb if d in POSITIVE)
            cal.append({"score_lo": lo, "score_hi": min(hi, 1.0), "n_judged": len(inb),
                        "accepted": k, "observed_acceptance": round(k / len(inb), 4) if inb else None,
                        "ci95": wilson(k, len(inb)),
                        "mean_score": round(sum(float(s["score"]) for s, _ in inb) / len(inb), 4) if inb else None})
        block["calibration"] = {"score_kind": next((s.get("score_kind") for s in ss if s.get("score_kind")), None),
                                "buckets": cal}
        block["recall"] = _recall(cls, ss, latest, census_tiles, census, match_iou)
        out["classes"][cls] = block
    return out


def _iou_block(ss: Sequence[Mapping[str, Any]], latest: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    v = sorted(round(iou_m(s["geometry"], latest[s["id"]]["polygon"]), 4) for s in ss)
    n = len(v)
    med = None if not n else (v[n // 2] if n % 2 else round((v[n // 2 - 1] + v[n // 2]) / 2, 4))
    return {"n": n, "median": med, "mean": round(sum(v) / n, 4) if n else None, "values": v}


def _recall(cls: str, ss: Sequence[Mapping[str, Any]], latest: Mapping[str, Mapping[str, Any]],
            census_tiles: Mapping[str, Mapping[str, Any]], census: Mapping[str, Mapping[str, Any]],
            match_iou: float) -> dict[str, Any]:
    tiles = [t for t in census_tiles.values() if t["class"] == cls]
    done = {tid: c for tid, c in census.items() if tid in census_tiles and census_tiles[tid]["class"] == cls
            and c.get("complete")}
    if not done:
        return {"status": NOT_MEASURABLE, "census_tiles": len(tiles), "census_complete": 0,
                "reason": "no complete human census of any tile for this class yet: recall cannot be measured from "
                          "reviews of predictions alone"}
    by_id = {s["id"]: s for s in ss}
    found = total = fp = 0
    per_tile = []
    for tid in sorted(done):
        t = census_tiles[tid]
        human = [shape(p) for p in done[tid]["polygons"]]
        preds = [shape(by_id[i]["geometry"]) for i in t.get("prediction_sample_ids", []) if i in by_id]
        pairs = sorted(((iou_m(h, p), hi, pi) for hi, h in enumerate(human) for pi, p in enumerate(preds)),
                       key=lambda x: (-x[0], x[1], x[2]))
        used_h, used_p = set(), set()
        for v, hi, pi in pairs:
            if v < match_iou:
                break
            if hi in used_h or pi in used_p:
                continue
            used_h.add(hi)
            used_p.add(pi)
        n_fp = len(preds) - len(used_p)
        found += len(used_h)
        total += len(human)
        fp += n_fp
        per_tile.append({"tile_id": tid, "region": t["region"], "human_objects": len(human), "predictions": len(preds),
                         "matched": len(used_h), "unmatched_predictions": n_fp,
                         "recall": round(len(used_h) / len(human), 4) if human else None})
    return {"status": "measured", "census_tiles": len(tiles), "census_complete": len(done),
            "human_objects": total, "matched": found, "recall": round(found / total, 4) if total else None,
            "recall_ci95": wilson(found, total), "unmatched_predictions_in_census_tiles": fp, "per_tile": per_tile,
            "scope": "census tiles only; not a county-wide recall"}
