"""Sessions, saved areas and pinned results for the conversational app (plain JSON, no envelope).

Stored server-side in SQLite (data/app/v1/sessions.db, gitignored), keyed by an anonymous browser id sent in the
X-GT-Owner header (a random UUID the app keeps in localStorage). No login, no people data: a session holds the user's
prompts, the assistant's replies, step summaries, result-set ids (feature ids + metrics, never the full GeoJSON: the app
re-hydrates results by re-running the stored request against the engine caches), map state, filters and examples.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from groundtruth import config

router = APIRouter(prefix="/api/v1")
_LOCK = threading.Lock()
_DB: sqlite3.Connection | None = None
_OWNER_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
MAX_DATA_BYTES = 2_000_000  # a session is ids + text, never full result sets


def db_path() -> Path:
    return config.DATA / "app" / "v1" / "sessions.db"


def _db() -> sqlite3.Connection:
    global _DB
    if _DB is None:
        p = db_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(p, check_same_thread=False, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
              created REAL NOT NULL, updated REAL NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS sessions_owner ON sessions(owner, updated);
            CREATE TABLE IF NOT EXISTS areas (id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
              geometry TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS pins (id TEXT PRIMARY KEY, owner TEXT NOT NULL, session_id TEXT NOT NULL,
              msg_id TEXT NOT NULL, title TEXT NOT NULL, created REAL NOT NULL);
        """)
        _DB = c
    return _DB


def reset_for_tests(path: Path | None = None) -> None:
    """Point the store at another file (tests)."""
    global _DB, db_path
    if _DB is not None:
        _DB.close()
    _DB = None
    if path is not None:
        db_path = lambda: path  # noqa: E731


def _owner(x: str | None) -> str:
    if not x or not _OWNER_RE.match(x):
        raise HTTPException(400, "X-GT-Owner header: an anonymous browser id (8–64 letters, digits, dashes)")
    return x


def _data(d: dict[str, Any] | None) -> str:
    s = json.dumps(d or {}, separators=(",", ":"))
    if len(s) > MAX_DATA_BYTES:
        raise HTTPException(413, "session too large (store result ids, not result sets)")
    return s


# ---------------- sessions ----------------
class SessionIn(BaseModel):
    title: str = Field("New search", max_length=200)
    data: dict[str, Any] = Field(default_factory=dict)


class SessionPatch(BaseModel):
    title: str | None = Field(None, max_length=200)
    data: dict[str, Any] | None = None


@router.get("/sessions")
def list_sessions(limit: int = 20, q: str = "", x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    """Newest first. ``q`` searches titles and the stored objective / area / findings text (case-insensitive).
    Each row carries the latest investigation status and area the app stored (data.status, data.scope)."""
    o = _owner(x_gt_owner)
    where, args = "owner=?", [o]
    if q.strip():
        where += " AND (lower(title) LIKE ? OR lower(data) LIKE ?)"
        like = f"%{q.strip().lower()[:80]}%"
        args += [like, like]
    with _LOCK:
        rows = _db().execute(f"SELECT id, title, created, updated, json_extract(data, '$.status'), json_extract(data, '$.scope') "
                             f"FROM sessions WHERE {where} ORDER BY updated DESC, id LIMIT ?",
                             (*args, max(1, min(limit, 500)))).fetchall()
        total = _db().execute(f"SELECT COUNT(*) FROM sessions WHERE {where}", args).fetchone()[0]
    return {"sessions": [{"id": r[0], "title": r[1], "created": r[2], "updated": r[3], "status": r[4], "scope": r[5]}
                         for r in rows], "total": total}


@router.post("/sessions")
def create_session(b: SessionIn, x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    o, now, sid = _owner(x_gt_owner), time.time(), uuid.uuid4().hex[:12]
    with _LOCK:
        _db().execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)", (sid, o, b.title.strip() or "New search", now, now, _data(b.data)))
    return {"id": sid, "title": b.title, "created": now, "updated": now}


@router.get("/sessions/{sid}")
def get_session(sid: str, x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    o = _owner(x_gt_owner)
    with _LOCK:
        r = _db().execute("SELECT id, title, created, updated, data FROM sessions WHERE id=? AND owner=?", (sid, o)).fetchone()
    if not r:
        raise HTTPException(404, "no such session")
    return {"id": r[0], "title": r[1], "created": r[2], "updated": r[3], "data": json.loads(r[4])}


@router.patch("/sessions/{sid}")
def patch_session(sid: str, b: SessionPatch, x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    o, now = _owner(x_gt_owner), time.time()
    sets, args = ["updated=?"], [now]
    if b.title is not None:
        sets.append("title=?"); args.append(b.title.strip() or "Untitled")
    if b.data is not None:
        sets.append("data=?"); args.append(_data(b.data))
    with _LOCK:
        n = _db().execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id=? AND owner=?", (*args, sid, o)).rowcount
    if not n:
        raise HTTPException(404, "no such session")
    return {"id": sid, "updated": now}


@router.delete("/sessions/{sid}")
def delete_session(sid: str, x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    o = _owner(x_gt_owner)
    with _LOCK:
        n = _db().execute("DELETE FROM sessions WHERE id=? AND owner=?", (sid, o)).rowcount
        _db().execute("DELETE FROM pins WHERE session_id=? AND owner=?", (sid, o))
    if not n:
        raise HTTPException(404, "no such session")
    return {"deleted": sid}


# ---------------- saved areas ----------------
class AreaIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    geometry: dict[str, Any]


@router.get("/areas")
def list_areas(x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    o = _owner(x_gt_owner)
    with _LOCK:
        rows = _db().execute("SELECT id, name, geometry, created FROM areas WHERE owner=? ORDER BY created DESC, id", (o,)).fetchall()
    return {"areas": [{"id": r[0], "name": r[1], "geometry": json.loads(r[2]), "created": r[3]} for r in rows]}


@router.post("/areas")
def create_area(b: AreaIn, x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    o, aid = _owner(x_gt_owner), uuid.uuid4().hex[:12]
    if b.geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise HTTPException(400, "geometry must be a Polygon or MultiPolygon (EPSG:4326)")
    with _LOCK:
        _db().execute("INSERT INTO areas VALUES (?,?,?,?,?)", (aid, o, b.name.strip(), json.dumps(b.geometry), time.time()))
    return {"id": aid, "name": b.name}


@router.delete("/areas/{aid}")
def delete_area(aid: str, x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    o = _owner(x_gt_owner)
    with _LOCK:
        n = _db().execute("DELETE FROM areas WHERE id=? AND owner=?", (aid, o)).rowcount
    if not n:
        raise HTTPException(404, "no such area")
    return {"deleted": aid}


# ---------------- pinned results ----------------
class PinIn(BaseModel):
    session_id: str = Field(..., max_length=64)
    msg_id: str = Field(..., max_length=64)
    title: str = Field(..., max_length=200)


@router.get("/pins")
def list_pins(x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    o = _owner(x_gt_owner)
    with _LOCK:
        rows = _db().execute("SELECT id, session_id, msg_id, title, created FROM pins WHERE owner=? ORDER BY created DESC, id",
                             (o,)).fetchall()
    return {"pins": [{"id": r[0], "session_id": r[1], "msg_id": r[2], "title": r[3], "created": r[4]} for r in rows]}


@router.post("/pins")
def create_pin(b: PinIn, x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    o, pid = _owner(x_gt_owner), uuid.uuid4().hex[:12]
    with _LOCK:
        if not _db().execute("SELECT 1 FROM sessions WHERE id=? AND owner=?", (b.session_id, o)).fetchone():
            raise HTTPException(404, "no such session")  # pins only into the caller's own sessions
        _db().execute("INSERT INTO pins VALUES (?,?,?,?,?,?)", (pid, o, b.session_id, b.msg_id, b.title, time.time()))
    return {"id": pid}


@router.delete("/pins/{pid}")
def delete_pin(pid: str, x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    o = _owner(x_gt_owner)
    with _LOCK:
        n = _db().execute("DELETE FROM pins WHERE id=? AND owner=?", (pid, o)).rowcount
    if not n:
        raise HTTPException(404, "no such pin")
    return {"deleted": pid}
