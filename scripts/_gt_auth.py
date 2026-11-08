"""Credentials for local scripts that call the API (docs/AUTH.md). Reads GROUNDTRUTH_API_TOKEN, else the bootstrap
token file data/app/v1/admin_token. Never prints the token."""
import os
from pathlib import Path

_DATA = Path(os.environ.get("GROUNDTRUTH_DATA", "/mnt/c/Users/orynt/GeoSpatial/Geospatial-SearchEngine/data"))


def token() -> str | None:
    t = os.environ.get("GROUNDTRUTH_API_TOKEN")
    if t:
        return t.strip()
    f = _DATA / "app" / "v1" / "admin_token"
    return f.read_text().strip() if f.exists() else None


def headers(extra: dict | None = None) -> dict:
    h = dict(extra or {})
    t = token()
    if t:
        h["Authorization"] = f"Bearer {t}"
    return h


def login_browser(page, base: str) -> None:
    """Playwright: log the page's context in (session cookie), so the app behaves as for a signed-in person."""
    t = token()
    if t:
        r = page.request.post(f"{base}/api/v1/auth/login", data={"token": t})
        if not r.ok:
            raise RuntimeError(f"login failed: HTTP {r.status}")
