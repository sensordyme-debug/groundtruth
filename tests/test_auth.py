"""Authentication + ownership (api/auth.py): unauthenticated / malformed / wrong credentials are rejected; one user can
never read another user's investigations, sessions or reports, even by forging X-GT-Owner."""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import auth, investigations_api, sessions
from groundtruth import investigate as I


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GROUNDTRUTH_AUTH", "on")
    monkeypatch.setenv("GROUNDTRUTH_USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.delenv("GROUNDTRUTH_API_TOKEN", raising=False)
    monkeypatch.setattr(I, "store_dir", lambda: tmp_path)
    auth._SESSIONS.clear()
    auth._FAILS.clear()
    sessions.reset_for_tests(tmp_path / "s.db")
    alice, bob = auth.add_user("alice"), auth.add_user("bob")
    app = FastAPI()
    app.middleware("http")(auth.middleware)
    app.include_router(auth.router)
    app.include_router(sessions.router)
    app.include_router(investigations_api.router)

    @app.get("/api/v1/health")
    def health():
        return {"ok": True}

    return TestClient(app), alice, bob


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def _inv(owner_user):
    inv = I.new("homes with pools in Lake Norman", {"_owner": auth.owner_id(owner_user)})
    I.save(inv)
    return inv.id


def test_unauthenticated_and_malformed_rejected(env):
    c, alice, _ = env
    assert c.get("/api/v1/health").status_code == 200  # public
    assert c.get("/api/v1/sessions").status_code == 401
    assert c.get("/api/v1/investigations/abc").status_code == 401
    for bad in ("Basic abc", "Bearer", "Bearer short", "Bearer " + "x" * 600, "Token " + alice):
        assert c.get("/api/v1/sessions", headers={"Authorization": bad}).status_code == 401, bad
    assert c.get("/api/v1/sessions", headers=H("gt_" + "a" * 43)).status_code == 401  # well-formed but unknown
    assert c.get("/api/v1/sessions", headers=H(alice)).status_code == 200


def test_login_cookie_logout(env):
    c, alice, _ = env
    assert c.post("/api/v1/auth/login", json={"token": "wrong-token-value"}).status_code == 401
    r = c.post("/api/v1/auth/login", json={"token": alice})
    assert r.status_code == 200 and r.json()["user"] == "alice"
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc and alice.lower() not in sc  # the cookie is a session id
    assert c.get("/api/v1/auth/me").json()["user"] == "alice"
    assert c.get("/api/v1/sessions").status_code == 200
    c.post("/api/v1/auth/logout")
    assert c.get("/api/v1/sessions").status_code == 401


def test_login_rate_limited(env):
    c, alice, _ = env
    for _ in range(10):
        c.post("/api/v1/auth/login", json={"token": "nope-nope-nope"})
    assert c.post("/api/v1/auth/login", json={"token": alice}).status_code == 429


def test_investigation_ownership_no_idor(env):
    c, alice, bob = env
    a_id, b_id = _inv("alice"), _inv("bob")
    assert c.get(f"/api/v1/investigations/{a_id}", headers=H(alice)).status_code == 200
    for path in (f"/api/v1/investigations/{b_id}", f"/api/v1/investigations/{b_id}/report.md",
                 f"/api/v1/investigations/{b_id}/features/x/evidence"):
        assert c.get(path, headers=H(alice)).status_code == 404, path  # bob's: indistinguishable from missing
    assert c.post(f"/api/v1/investigations/{b_id}/cancel", headers=H(alice)).status_code == 404
    assert c.post(f"/api/v1/investigations/{b_id}/feedback", headers=H(alice),
                  json={"feature_id": "x", "verdict": "reject"}).status_code == 404
    # forging bob's owner id in X-GT-Owner does not help: the server replaces it
    forged = {**H(alice), "X-GT-Owner": auth.owner_id("bob")}
    assert c.get(f"/api/v1/investigations/{b_id}", headers=forged).status_code == 404
    # legacy investigations without an owner are not readable when auth is on
    legacy = I.new("x", {})
    I.save(legacy)
    assert c.get(f"/api/v1/investigations/{legacy.id}", headers=H(alice)).status_code == 404


def test_sessions_are_per_user_even_with_forged_header(env):
    c, alice, bob = env
    s = c.post("/api/v1/sessions", json={"title": "alice's"}, headers=H(alice)).json()
    assert c.get(f"/api/v1/sessions/{s['id']}", headers=H(bob)).status_code == 404
    forged = {**H(bob), "X-GT-Owner": auth.owner_id("alice")}
    assert c.get(f"/api/v1/sessions/{s['id']}", headers=forged).status_code == 404
    assert c.get("/api/v1/sessions", headers=H(bob)).json()["total"] == 0


def test_dev_mode_is_explicit(env, monkeypatch):
    c, _, _ = env
    monkeypatch.setenv("GROUNDTRUTH_AUTH", "off")
    assert c.get("/api/v1/sessions", headers={"X-GT-Owner": "dev-browser-1"}).status_code == 200


def test_bootstrap_writes_token_file_not_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("GROUNDTRUTH_AUTH", "on")
    monkeypatch.setenv("GROUNDTRUTH_USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.delenv("GROUNDTRUTH_API_TOKEN", raising=False)
    monkeypatch.setattr(auth, "_data_dir", lambda: tmp_path)
    path = auth.ensure_bootstrap()
    tok = open(path).read().strip()
    assert tok.startswith("gt_") and len(tok) > 40 and tok not in path
    assert oct((tmp_path / "admin_token").stat().st_mode)[-3:] == "600"
    assert tok not in (tmp_path / "users.json").read_text()  # only its hash is stored
    assert auth.check_token(tok) == "owner"
    assert auth.ensure_bootstrap() is None  # once


def test_pin_only_into_own_session(env):
    c, alice, bob = env
    s = c.post("/api/v1/sessions", json={"title": "a"}, headers=H(alice)).json()
    assert c.post("/api/v1/pins", headers=H(bob), json={"session_id": s["id"], "msg_id": "m1", "title": "t"}).status_code == 404
    assert c.post("/api/v1/pins", headers=H(alice), json={"session_id": s["id"], "msg_id": "m1", "title": "t"}).status_code == 200
    assert c.post("/api/v1/pins", headers=H(alice), json={"session_id": "x" * 65, "msg_id": "m", "title": "t"}).status_code == 422
