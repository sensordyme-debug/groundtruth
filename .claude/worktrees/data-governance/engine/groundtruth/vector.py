"""DuckDB spatial store for the local Overture layers (buildings, places): bbox / category / name / class queries.

All Overture queries go through here (Phase 1.3). Files: data/layers/overture_{buildings,places}.parquet (written by
scripts/fetch_layers.py). Geometry is WKB in EPSG:4326; bbox columns are added once into an in-memory table so bbox
filters are fast. One connection per process, guarded by a lock (DuckDB connections are not thread-safe).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Sequence

from groundtruth import config

BBox = Sequence[float]


class VectorStore:
    def __init__(self, data: Path | None = None) -> None:
        self.dir = Path(data or config.DATA) / "layers"
        self._con = None
        self._lock = threading.Lock()
        self.load_ms: float | None = None

    @property
    def con(self):
        if self._con is None:
            import duckdb

            t0 = time.perf_counter()
            con = duckdb.connect()
            con.execute("LOAD spatial")  # installed once by scripts/fetch_layers.py (offline afterwards)
            for t in ("buildings", "places"):
                p = self.dir / f"overture_{t}.parquet"
                if p.exists():
                    con.execute(f"""CREATE TABLE {t} AS SELECT * EXCLUDE (geometry_wkb),
                        ST_GeomFromWKB(geometry_wkb) AS geom,
                        ST_XMin(ST_GeomFromWKB(geometry_wkb)) AS xmin, ST_YMin(ST_GeomFromWKB(geometry_wkb)) AS ymin,
                        ST_XMax(ST_GeomFromWKB(geometry_wkb)) AS xmax, ST_YMax(ST_GeomFromWKB(geometry_wkb)) AS ymax
                        FROM read_parquet('{p}')""")
            self._con = con
            self.load_ms = round((time.perf_counter() - t0) * 1000, 1)
        return self._con

    def tables(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self.con.execute("SELECT table_name FROM information_schema.tables").fetchall()]

    def query(self, table: str, bbox: BBox | None = None, where: str = "", params: Sequence[Any] = (),
              columns: str = "*", limit: int | None = None) -> tuple[dict[str, Any], float]:
        """→ (GeoJSON FeatureCollection, ms). ``where`` is an extra SQL predicate with ? params (never user SQL)."""
        if table not in ("buildings", "places"):
            raise KeyError(f"unknown table {table!r}")
        conds, args = [], []
        if bbox is not None:
            conds.append("xmax >= ? AND xmin <= ? AND ymax >= ? AND ymin <= ?")
            args += [bbox[0], bbox[2], bbox[1], bbox[3]]
        if where:
            conds.append(f"({where})")
            args += list(params)
        cols = "*" if columns == "*" else columns
        sql = (f"SELECT {cols} EXCLUDE (geom, xmin, ymin, xmax, ymax), ST_AsGeoJSON(geom) AS gj FROM {table}"
               + (f" WHERE {' AND '.join(conds)}" if conds else "") + " ORDER BY id"
               + (f" LIMIT {int(limit)}" if limit else ""))
        t0 = time.perf_counter()
        with self._lock:
            cur = self.con.execute(sql, args)
            names = [d[0] for d in cur.description]
            rows = cur.fetchall()
        feats = []
        for r in rows:
            props = dict(zip(names, r))
            g = json.loads(props.pop("gj"))
            feats.append({"type": "Feature", "id": props.get("id"), "properties": props, "geometry": g})
        return {"type": "FeatureCollection", "features": feats}, round((time.perf_counter() - t0) * 1000, 1)

    def places(self, bbox: BBox | None = None, categories: Sequence[str] = (), name: str | None = None,
               limit: int | None = None, taxonomy: Sequence[str] = ()):
        where, params = [], []
        cat = []
        if categories:
            cat.append(f"category IN ({', '.join('?' for _ in categories)})")
            params += list(categories)
        if taxonomy:
            cat.append(f"taxonomy_primary IN ({', '.join('?' for _ in taxonomy)})")
            params += list(taxonomy)
        if cat:
            where.append("(" + " OR ".join(cat) + ")")
        if name:
            where.append("lower(name) LIKE ?")
            params.append(f"%{name.lower()}%")
        return self.query("places", bbox, " AND ".join(where), params, limit=limit)

    def buildings(self, bbox: BBox | None = None, classes: Sequence[str] = (), limit: int | None = None):
        where = f"class IN ({', '.join('?' for _ in classes)})" if classes else ""
        return self.query("buildings", bbox, where, list(classes), limit=limit)


_STORE: VectorStore | None = None


def store() -> VectorStore:
    global _STORE
    if _STORE is None:
        _STORE = VectorStore()
    return _STORE
