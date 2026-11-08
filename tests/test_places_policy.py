"""/places/search: the people-data policy runs before any third-party provider is called."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.places_api as P


def test_people_query_declined_before_providers(monkeypatch):
    called = []
    monkeypatch.setattr(P, "search_places", lambda *a, **k: called.append(1))
    app = FastAPI()
    app.include_router(P.router)
    c = TestClient(app)
    r = c.post("/api/v1/places/search", json={"query": "who owns the houses on this street", "area": {"bbox": [-80.9, 35.1, -80.8, 35.2]}})
    assert r.status_code == 200 and r.json()["status"] == "declined" and not called
    r = c.post("/api/v1/places/search", json={"query": "restaurants", "categories": ["residents"], "area": {"bbox": [-80.9, 35.1, -80.8, 35.2]}})
    assert r.json()["status"] == "declined" and not called
