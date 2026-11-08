"""Sessions / saved areas / pins API (api/sessions.py): owner isolation, CRUD, size guard."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import sessions


def client(tmp_path):
    sessions.reset_for_tests(tmp_path / "s.db")
    app = FastAPI()
    app.include_router(sessions.router)
    return TestClient(app)


A = {"X-GT-Owner": "owner-aaaaaaaa"}
B = {"X-GT-Owner": "owner-bbbbbbbb"}


def test_session_crud_and_isolation(tmp_path):
    c = client(tmp_path)
    s = c.post("/api/v1/sessions", json={"title": "Lake Norman · houses with pools", "data": {"msgs": [1]}}, headers=A).json()
    assert c.get("/api/v1/sessions", headers=A).json()["total"] == 1
    assert c.get("/api/v1/sessions", headers=B).json()["total"] == 0
    assert c.get(f"/api/v1/sessions/{s['id']}", headers=B).status_code == 404
    assert c.patch(f"/api/v1/sessions/{s['id']}", json={"title": "Docks"}, headers=A).status_code == 200
    got = c.get(f"/api/v1/sessions/{s['id']}", headers=A).json()
    assert got["title"] == "Docks" and got["data"] == {"msgs": [1]}
    assert c.delete(f"/api/v1/sessions/{s['id']}", headers=B).status_code == 404
    assert c.delete(f"/api/v1/sessions/{s['id']}", headers=A).status_code == 200
    assert c.get("/api/v1/sessions", headers=A).json()["total"] == 0


def test_owner_required_and_size_guard(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/v1/sessions").status_code == 400
    assert c.get("/api/v1/sessions", headers={"X-GT-Owner": "x"}).status_code == 400
    big = {"blob": "x" * (sessions.MAX_DATA_BYTES + 10)}
    assert c.post("/api/v1/sessions", json={"data": big}, headers=A).status_code == 413


def test_areas_and_pins(tmp_path):
    c = client(tmp_path)
    poly = {"type": "Polygon", "coordinates": [[[-80.9, 35.4], [-80.8, 35.4], [-80.8, 35.5], [-80.9, 35.4]]]}
    a = c.post("/api/v1/areas", json={"name": "My cove", "geometry": poly}, headers=A).json()
    assert [x["name"] for x in c.get("/api/v1/areas", headers=A).json()["areas"]] == ["My cove"]
    assert c.post("/api/v1/areas", json={"name": "pt", "geometry": {"type": "Point", "coordinates": [0, 0]}}, headers=A).status_code == 400
    p = c.post("/api/v1/pins", json={"session_id": "s1", "msg_id": "a3", "title": "63 lakefront houses"}, headers=A).json()
    assert len(c.get("/api/v1/pins", headers=A).json()["pins"]) == 1
    assert c.delete(f"/api/v1/pins/{p['id']}", headers=A).status_code == 200
    assert c.delete(f"/api/v1/areas/{a['id']}", headers=A).status_code == 200
