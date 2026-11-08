"""Application authentication for the Groundtruth API (docs/AUTH.md).

Model: a small set of named users, each with a high-entropy access token. Only SHA-256 hashes of tokens are stored
(data/app/v1/users.json, mode 0600). Browsers log in once (POST /api/v1/auth/login {token}) and get an HttpOnly,
SameSite=Strict session cookie; scripts send ``Authorization: Bearer <token>``. Every /api/ route requires a principal
except /api/v1/health and /api/v1/auth/*.

Ownership: the middleware REPLACES any client-supplied ``X-GT-Owner`` header with an id derived on the server from the
authenticated principal, so every owner-scoped route (sessions, areas, pins, investigations, validation reviews,
exports) is bound to the real user and a client cannot claim someone else's id.

Configuration (environment, never in the frontend bundle):
  GROUNDTRUTH_AUTH=on|off   default on. "off" is an explicit local development mode (reported by /health).
  GROUNDTRUTH_API_TOKEN     optional: a token for the user "owner" (in addition to users.json).
  GROUNDTRUTH_USERS_FILE    optional path to users.json (default data/app/v1/users.json).
If auth is on and no user exists, a random token for "owner" is generated ONCE into data/app/v1/admin_token (0600);
the log prints the file path, never the token.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/auth")
COOKIE = "gt_session"
SESSION_TTL_S = 12 * 3600
PUBLIC = ("/api/v1/health", "/api/v1/auth/")
_SESSIONS: dict[str, tuple[str, float]] = {}
_LOCK = threading.Lock()
_FAILS: dict[str, list[float]] = {}


def enabled() -> bool:
    return os.environ.get("GROUNDTRUTH_AUTH", "on").strip().lower() not in ("off", "0", "false", "no")


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def owner_id(user: str) -> str:
    """The server-side owner id for a principal (matches the X-GT-Owner format: 8–64 letters, digits, dashes)."""
    return "u-" + hashlib.sha256(f"groundtruth-owner:{user}".encode()).hexdigest()[:24]


def _data_dir() -> Path:
    from groundtruth import config
    return config.DATA / "app" / "v1"


def users_file() -> Path:
    return Path(os.environ.get("GROUNDTRUTH_USERS_FILE") or (_data_dir() / "users.json"))


def _users() -> dict[str, str]:
    """name → token sha256 (from users.json + GROUNDTRUTH_API_TOKEN)."""
    out: dict[str, str] = {}
    p = users_file()
    if p.exists():
        try:
            for name, v in json.loads(p.read_text()).get("users", {}).items():
                if isinstance(v, dict) and re.fullmatch(r"[0-9a-f]{64}", str(v.get("token_sha256", ""))):
                    out[name] = v["token_sha256"]
        except (OSError, ValueError):
            pass
    t = os.environ.get("GROUNDTRUTH_API_TOKEN", "")
    if len(t) >= 24:
        out["owner"] = _h(t)
    return out


def add_user(name: str) -> str:
    """Create (or rotate) a user; returns the NEW token once (the caller shows it to the human, nothing stores it)."""
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", name):
        raise ValueError("user names: 2–32 chars, a-z 0-9 _ -")
    token = "gt_" + secrets.token_urlsafe(32)
    p = users_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    d = json.loads(p.read_text()) if p.exists() else {"users": {}}
    d["users"][name] = {"token_sha256": _h(token), "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    return token


def ensure_bootstrap() -> str | None:
    """Auth on and nobody configured: create user 'owner' with a random token written to admin_token (0600).
    Returns the PATH of that file (never the token), or None."""
    if not enabled() or _users():
        return None
    token = add_user("owner")
    f = _data_dir() / "admin_token"
    f.write_text(token + "\n")
    os.chmod(f, 0o600)
    return str(f)


def check_token(token: str | None) -> str | None:
    if not token or len(token) > 512:
        return None
    th = _h(token)
    for name, h in _users().items():
        if hmac.compare_digest(h, th):
            return name
    return None


def principal(request: Request) -> str | None:
    """The authenticated user name, or None. A malformed Authorization header is treated as not authenticated."""
    auth = request.headers.get("authorization")
    if auth is not None:
        m = re.fullmatch(r"Bearer ([A-Za-z0-9_\-.~+/=]{16,512})", auth.strip())
        return check_token(m.group(1)) if m else None
    sid = request.cookies.get(COOKIE)
    if sid:
        with _LOCK:
            v = _SESSIONS.get(sid)
            if v and v[1] > time.time():
                return v[0]
            _SESSIONS.pop(sid, None)
    return None


async def middleware(request: Request, call_next):
    path = request.url.path
    if not enabled():
        return await call_next(request)
    if not path.startswith("/api/") or any(path == p or path.startswith(p) for p in PUBLIC):
        return await call_next(request)
    user = principal(request)
    if user is None:
        return JSONResponse({"detail": "authentication required"}, status_code=401,
                            headers={"WWW-Authenticate": 'Bearer realm="groundtruth"'})
    # bind every owner-scoped route to the authenticated user: replace any client X-GT-Owner with the server's id
    hdrs = [(k, v) for k, v in request.scope["headers"] if k.lower() != b"x-gt-owner"]
    hdrs.append((b"x-gt-owner", owner_id(user).encode()))
    request.scope["headers"] = hdrs
    request.state.user = user
    return await call_next(request)


class LoginIn(BaseModel):
    token: str = Field(..., min_length=1, max_length=512)


def _rate_limited(key: str) -> bool:
    now = time.time()
    xs = [t for t in _FAILS.get(key, []) if now - t < 300]
    _FAILS[key] = xs
    return len(xs) >= 10


@router.post("/login")
def login(b: LoginIn, request: Request, response: Response) -> dict[str, Any]:
    if not enabled():
        return {"user": "dev", "auth": "off"}
    key = request.client.host if request.client else "?"
    if _rate_limited(key):
        raise HTTPException(429, "too many failed attempts; wait 5 minutes")
    user = check_token(b.token.strip())
    if user is None:
        _FAILS.setdefault(key, []).append(time.time())
        raise HTTPException(401, "invalid token")
    sid = secrets.token_urlsafe(32)
    with _LOCK:
        _SESSIONS[sid] = (user, time.time() + SESSION_TTL_S)
    response.set_cookie(COOKIE, sid, max_age=SESSION_TTL_S, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", path="/")
    return {"user": user}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    sid = request.cookies.get(COOKIE)
    if sid:
        with _LOCK:
            _SESSIONS.pop(sid, None)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict[str, Any]:
    if not enabled():
        return {"user": "dev", "auth": "off"}
    user = principal(request)
    if user is None:
        raise HTTPException(401, "not logged in")
    return {"user": user, "auth": "on"}


def current_owner(request: Request) -> str | None:
    """The owner id routes should use: the server-derived id when auth is on, else the client's X-GT-Owner (dev)."""
    return request.headers.get("x-gt-owner")
