"""Durable human label store. Labels must never be lost.

Write path for every change (add / undo / delete), under a lock:
  1. append one JSON line to ``labels.jsonl`` (the append-only journal = source of truth), flush + fsync;
  2. rewrite ``labels.geojson`` atomically (tmp file + fsync + os.replace);
  3. copy the snapshot to ``backups/labels_<utc timestamp>.geojson`` (most recent ``keep_backups`` kept;
     the journal is never pruned).
On start, state is rebuilt from the journal. If the journal is missing but a snapshot exists, the
snapshot seeds a new journal. Nothing in this module ever generates labels: it only stores human clicks.
"""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

Kind = Literal["true_match", "look_alike"]
KINDS: tuple[str, ...] = ("true_match", "look_alike")

CONCEPTS: tuple[str, ...] = (
    "swimming_pool",
    "construction_site",
    "warehouse",
    "parking_lot",
    "solar_installation",
)


@dataclass
class Label:
    id: str
    concept: str
    kind: str
    lon: float
    lat: float
    created_at: str
    naip_year: int | None = None
    map_zoom: float | None = None
    note: str = ""

    def feature(self) -> dict[str, Any]:
        props = {k: v for k, v in asdict(self).items() if k not in ("lon", "lat")}
        return {
            "type": "Feature",
            "id": self.id,
            "properties": props,
            "geometry": {"type": "Point", "coordinates": [self.lon, self.lat]},
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _fsync_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class LabelStore:
    def __init__(self, root: Path, *, keep_backups: int = 500) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "backups").mkdir(exist_ok=True)
        self.journal = self.root / "labels.jsonl"
        self.snapshot = self.root / "labels.geojson"
        self.keep_backups = keep_backups
        self._lock = threading.RLock()
        self._labels: dict[str, Label] = {}
        self._order: list[str] = []
        self._load()

    # ---------- loading ----------
    def _load(self) -> None:
        if self.journal.exists():
            with open(self.journal, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        # a torn final line (power loss mid-write) is skipped, never fatal
                        continue
                    self._apply(ev)
        elif self.snapshot.exists():
            fc = json.loads(self.snapshot.read_text(encoding="utf-8"))
            for feat in fc.get("features", []):
                p = feat["properties"]
                lon, lat = feat["geometry"]["coordinates"][:2]
                lab = Label(lon=lon, lat=lat, **{k: p.get(k) for k in ("id", "concept", "kind", "created_at")},
                            naip_year=p.get("naip_year"), map_zoom=p.get("map_zoom"), note=p.get("note", ""))
                self._journal_append({"op": "add", "at": _now(), "label": asdict(lab), "seeded": True})
                self._apply({"op": "add", "label": asdict(lab)})

    def _apply(self, ev: dict[str, Any]) -> None:
        op = ev.get("op")
        if op == "add":
            lab = Label(**ev["label"])
            if lab.id not in self._labels:
                self._labels[lab.id] = lab
                self._order.append(lab.id)
        elif op in ("undo", "delete"):
            lid = ev.get("id")
            if lid in self._labels:
                del self._labels[lid]
                self._order.remove(lid)

    # ---------- writing ----------
    def _journal_append(self, ev: dict[str, Any]) -> None:
        with open(self.journal, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(ev, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _persist(self) -> None:
        text = json.dumps(self.geojson(), indent=1)
        _fsync_write(self.snapshot, text)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        shutil.copyfile(self.snapshot, self.root / "backups" / f"labels_{stamp}.geojson")
        backups = sorted((self.root / "backups").glob("labels_*.geojson"))
        for old in backups[: max(0, len(backups) - self.keep_backups)]:
            old.unlink(missing_ok=True)

    def _commit(self, ev: dict[str, Any]) -> None:
        self._journal_append(ev)
        self._apply(ev)
        self._persist()

    # ---------- public API ----------
    def add(self, *, concept: str, kind: str, lon: float, lat: float, naip_year: int | None = None,
            map_zoom: float | None = None, note: str = "", label_id: str | None = None) -> Label:
        if concept not in CONCEPTS:
            raise ValueError(f"unknown concept {concept!r}; expected one of {CONCEPTS}")
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}; expected one of {KINDS}")
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("lon/lat out of range")
        with self._lock:
            lid = label_id or uuid.uuid4().hex
            if lid in self._labels:  # idempotent retry from the client queue
                return self._labels[lid]
            lab = Label(id=lid, concept=concept, kind=kind, lon=round(lon, 7), lat=round(lat, 7),
                        created_at=_now(), naip_year=naip_year, map_zoom=map_zoom, note=note)
            self._commit({"op": "add", "at": lab.created_at, "label": asdict(lab)})
            return lab

    def undo(self) -> Label | None:
        with self._lock:
            if not self._order:
                return None
            lab = self._labels[self._order[-1]]
            self._commit({"op": "undo", "at": _now(), "id": lab.id})
            return lab

    def delete(self, label_id: str) -> Label | None:
        with self._lock:
            lab = self._labels.get(label_id)
            if lab is None:
                return None
            self._commit({"op": "delete", "at": _now(), "id": label_id})
            return lab

    def labels(self) -> list[Label]:
        with self._lock:
            return [self._labels[i] for i in self._order]

    def counts(self) -> dict[str, dict[str, int]]:
        out = {c: {"true_match": 0, "look_alike": 0} for c in CONCEPTS}
        for lab in self.labels():
            out.setdefault(lab.concept, {"true_match": 0, "look_alike": 0})[lab.kind] += 1
        return out

    def geojson(self) -> dict[str, Any]:
        return {
            "type": "FeatureCollection",
            "name": "groundtruth_labels",
            "crs_note": "EPSG:4326",
            "features": [lab.feature() for lab in self.labels()],
        }

    def csv(self) -> str:
        buf = io.StringIO()
        fields = ["id", "concept", "kind", "lon", "lat", "created_at", "naip_year", "map_zoom", "note"]
        w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for lab in self.labels():
            w.writerow(asdict(lab))
        return buf.getvalue()
