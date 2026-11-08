"""Shared pieces for third-party place providers: the PlaceRecord model, the Provider protocol, configuration, the
usage ledger, spend caps, a rate limiter and an HTTP helper with bounded retries.

Secrets: the HTTP helper receives auth headers/params from the provider and sends them; it never logs a URL, a header,
a query string or a response body. Error messages are built from status codes and exception class names only, never
from ``str(exc)`` (httpx messages can embed the request URL, which for Google Geocoding carries the key).
"""
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field

from groundtruth import config

from .credentials import get_secret, has_secret

CONF_PATH = Path(__file__).resolve().parents[1] / "conf" / "providers.json"
MatchStatus = Literal["single", "corroborated", "conflict"]


# --------------------------------------------------------------------------------------------------------- records
class PlaceRecord(BaseModel):
    """One place as one provider reported it. Every field except id/provider/retrieved_at is nullable.

    ``rating`` keeps the provider's own scale (``rating_scale_max``: Foursquare 10, Yelp 5, Google 5); ratings are
    never blended across providers. ``raw`` holds provider-specific extras, kept apart from the normalized fields.
    No people data: no reviewer/owner names, user ids or photos.
    """

    model_config = ConfigDict(extra="forbid")

    id: str  # internal: "<provider>:<provider_place_id>"
    provider: str
    provider_place_id: str | None = None
    name: str | None = None
    categories: list[str] = Field(default_factory=list)  # normalized keys (conf/providers.json "categories")
    categories_raw: list[str] = Field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    address: str | None = None
    city: str | None = None
    county: str | None = None
    state: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    website: str | None = None
    rating: float | None = None
    rating_scale_max: float | None = None
    review_count: int | None = None
    price: int | None = None  # 1..4
    source_url: str | None = None
    retrieved_at: str
    attribution: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    match_status: MatchStatus | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def valid_coords(lat: Any, lon: Any) -> bool:
    """Real, finite WGS84 coordinates (and not the 0,0 'null island' placeholder)."""
    if isinstance(lat, bool) or isinstance(lon, bool):
        return False
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(la) and math.isfinite(lo)):
        return False
    if not (-90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0):
        return False
    return not (la == 0.0 and lo == 0.0)


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


# ---------------------------------------------------------------------------------------------------------- errors
class ProviderError(Exception):
    """A provider call failed. ``code`` is one of: disabled, missing_key, spend_cap, auth, rate_limited, timeout,
    upstream_error, http_error, bad_response, not_supported. Messages never contain secrets."""

    def __init__(self, provider: str, code: str, message: str):
        super().__init__(f"{provider}: {message}")
        self.provider, self.code, self.message = provider, code, message


class SpendCapExceeded(ProviderError):
    def __init__(self, provider: str, message: str):
        super().__init__(provider, "spend_cap", message)


# ---------------------------------------------------------------------------------------------------------- config
def load_conf(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or CONF_PATH).read_text(encoding="utf-8"))


def ledger_path(conf: dict[str, Any] | None = None) -> Path:
    rel = (conf or {}).get("ledger") or "app/v1/provider_usage.jsonl"
    return Path(config.DATA) / rel


# ---------------------------------------------------------------------------------------------------------- ledger
LEDGER_FIELDS = (
    "ts", "provider", "endpoint", "category", "status", "http_status", "latency_ms", "retry_count", "attempts",
    "sent", "estimated_cost_usd", "rate_limited", "cached", "investigation_id", "error_code",
)


class UsageLedger:
    """Append-only JSONL of provider calls. Only the whitelisted LEDGER_FIELDS are written: no keys, no headers,
    no URLs or query strings, no response bodies."""

    _locks: dict[str, threading.Lock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, path: Path):
        self.path = Path(path)
        with self._locks_guard:
            self._lock = self._locks.setdefault(str(self.path), threading.Lock())

    def append(self, **entry: Any) -> dict[str, Any]:
        row = {k: entry.get(k) for k in LEDGER_FIELDS}
        row["ts"] = row["ts"] or utc_now_iso()
        line = json.dumps(row, sort_keys=False, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return row

    def rows(self, day: str | None = None) -> list[dict[str, Any]]:
        """Rows of one UTC day (``YYYY-MM-DD``; default today). Unreadable lines are skipped."""
        day = day or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        out: list[dict[str, Any]] = []
        with self._lock:
            if not self.path.exists():
                return out
            text = self.path.read_text(encoding="utf-8")
        for line in text.splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict) and str(r.get("ts", "")).startswith(day):
                out.append(r)
        return out

    def usage_today(self) -> dict[str, dict[str, Any]]:
        """Per provider: requests (attempts actually sent), calls, estimated_cost_usd, rate_limited, cached, errors,
        refused (spend-cap refusals)."""
        agg: dict[str, dict[str, Any]] = {}
        for r in self.rows():
            a = agg.setdefault(str(r.get("provider")), {
                "requests": 0, "calls": 0, "estimated_cost_usd": 0.0, "rate_limited": 0, "cached": 0, "errors": 0,
                "refused": 0})
            if r.get("sent"):
                a["requests"] += int(r.get("attempts") or 1)
                a["calls"] += 1
            if r.get("cached"):
                a["cached"] += 1
            if r.get("rate_limited"):
                a["rate_limited"] += 1
            if r.get("status") == "refused":
                a["refused"] += 1
            elif r.get("status") not in ("ok", "cached"):
                a["errors"] += 1
            a["estimated_cost_usd"] = round(a["estimated_cost_usd"] + float(r.get("estimated_cost_usd") or 0.0), 6)
        return agg


# ---------------------------------------------------------------------------------------------------- rate limiter
class RateLimiter:
    """Minimum spacing between requests (1/qps), shared per (provider, endpoint bucket) within the process."""

    _registry: dict[tuple[str, str], "RateLimiter"] = {}
    _guard = threading.Lock()

    def __init__(self, qps: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.interval = 1.0 / qps if qps and qps > 0 else 0.0
        self.clock, self.sleep = clock, sleep
        self._next = 0.0
        self._lock = threading.Lock()

    @classmethod
    def shared(cls, provider: str, bucket: str, qps: float) -> "RateLimiter":
        with cls._guard:
            rl = cls._registry.get((provider, bucket))
            if rl is None or rl.interval != (1.0 / qps if qps and qps > 0 else 0.0):
                rl = cls._registry[(provider, bucket)] = cls(qps)
            return rl

    def acquire(self) -> float:
        """Blocks until a slot is free; returns the seconds waited."""
        if self.interval <= 0:
            return 0.0
        with self._lock:
            now = self.clock()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if wait > 0:
            self.sleep(wait)
        return wait


# --------------------------------------------------------------------------------------------------------- caching
class _TTLCache:
    """In-memory only (never on disk), per provider, honouring each provider's caching terms via cache_ttl_s."""

    def __init__(self) -> None:
        self._d: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, k: str, now: float) -> Any:
        with self._lock:
            hit = self._d.get(k)
            if hit and hit[0] > now:
                return hit[1]
            self._d.pop(k, None)
            return None

    def put(self, k: str, v: Any, expires: float) -> None:
        with self._lock:
            if len(self._d) > 512:
                self._d.clear()
            self._d[k] = (expires, v)


_CACHES: dict[str, _TTLCache] = {}


def _cache_for(provider: str) -> _TTLCache:
    return _CACHES.setdefault(provider, _TTLCache())


def clear_caches() -> None:
    _CACHES.clear()
    RateLimiter._registry.clear()


# ------------------------------------------------------------------------------------------------------------ http
def _retry_after_s(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return max(0.0, (when - dt.datetime.now(dt.timezone.utc)).total_seconds())


class ProviderHttp:
    """HTTP for one provider: spend caps -> cache -> rate limit -> request with bounded retries -> one ledger row.

    Retries: 429 and 5xx (honouring Retry-After up to ``retry_after_max_s``; a longer wait is not retried) and
    timeouts/transport errors, with exponential backoff, at most ``max_retries`` extra attempts.
    401/403 -> ``auth``; other 4xx -> ``http_error``. A provider may pass ``no_retry`` to stop retrying a 429 whose body
    says the daily quota is gone (Yelp ACCESS_LIMIT_REACHED).
    """

    def __init__(self, provider: str, pconf: dict[str, Any], http_conf: dict[str, Any], ledger: UsageLedger, *,
                 transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.provider, self.pconf, self.http_conf, self.ledger = provider, pconf, http_conf, ledger
        self.transport, self.sleep, self.clock = transport, sleep, clock

    # -- spend safeguards
    def price(self, category: str) -> float | None:
        p = (self.pconf.get("price_usd_per_1000") or {}).get(category)
        return None if p is None else round(float(p) / 1000.0, 6)

    def check_caps(self, category: str, investigation_id: str | None = None, endpoint: str = "") -> None:
        use = self.ledger.usage_today().get(self.provider, {})
        req_cap = self.pconf.get("daily_request_cap")
        usd_cap = self.pconf.get("daily_usd_cap")
        cost = self.price(category) or 0.0
        msg = None
        if req_cap is not None and use.get("requests", 0) + 1 > int(req_cap):
            msg = f"daily request cap reached ({use.get('requests', 0)}/{int(req_cap)} today, UTC; conf/providers.json)"
        elif usd_cap is not None and use.get("estimated_cost_usd", 0.0) + cost > float(usd_cap) + 1e-9:
            msg = (f"estimated daily spend cap reached (${use.get('estimated_cost_usd', 0.0):.4f} + ${cost:.4f} > "
                   f"${float(usd_cap):.2f} today, UTC; conf/providers.json)")
        if msg:
            self.ledger.append(provider=self.provider, endpoint=endpoint, category=category, status="refused",
                               latency_ms=0.0, retry_count=0, attempts=0, sent=False, estimated_cost_usd=0.0,
                               rate_limited=False, cached=False, investigation_id=investigation_id,
                               error_code="spend_cap")
            raise SpendCapExceeded(self.provider, msg)

    def remaining(self, category: str) -> bool:
        """Would one more call of this category fit within today's caps? (no ledger write)"""
        use = self.ledger.usage_today().get(self.provider, {})
        req_cap, usd_cap = self.pconf.get("daily_request_cap"), self.pconf.get("daily_usd_cap")
        if req_cap is not None and use.get("requests", 0) + 1 > int(req_cap):
            return False
        cost = self.price(category) or 0.0
        return not (usd_cap is not None and use.get("estimated_cost_usd", 0.0) + cost > float(usd_cap) + 1e-9)

    # -- request
    def request(self, method: str, url: str, *, endpoint: str, category: str, headers: dict[str, str] | None = None,
                params: dict[str, Any] | None = None, json_body: Any = None, investigation_id: str | None = None,
                no_retry: Callable[[httpx.Response], bool] | None = None,
                ok_body: Callable[[Any], str | None] | None = None) -> Any:
        """Returns the parsed JSON body. ``ok_body(body)`` may return an error code for HTTP-200 error bodies
        (Google Geocoding ``status``): 'auth' | 'rate_limited' | other -> ProviderError."""
        self.check_caps(category, investigation_id, endpoint)
        ttl = float(self.pconf.get("cache_ttl_s") or 0)
        ck = None
        if ttl > 0:
            safe_params = {k: v for k, v in sorted((params or {}).items()) if k.lower() not in ("key", "api_key")}
            ck = hashlib.sha256(json.dumps([method, url, safe_params, json_body], sort_keys=True, default=str)
                                .encode()).hexdigest()
            hit = _cache_for(self.provider).get(ck, time.monotonic())
            if hit is not None:
                self._log(endpoint, category, "cached", None, 0.0, 0, 0, False, 0.0, False, True, investigation_id, None)
                return hit

        hc = self.http_conf
        max_retries = int(hc.get("max_retries", 2))
        base, cap = float(hc.get("backoff_base_s", 0.5)), float(hc.get("backoff_max_s", 8.0))
        ra_max = float(hc.get("retry_after_max_s", 20.0))
        qps = (self.pconf.get("endpoint_qps") or {}).get(category, self.pconf.get("rate_limit_qps", 0))
        limiter = RateLimiter.shared(self.provider, category if category in (self.pconf.get("endpoint_qps") or {})
                                     else "*", float(qps or 0))
        limiter.sleep = self.sleep

        t0 = time.perf_counter()
        attempts, rate_limited, last_status = 0, False, None
        error: ProviderError | None = None
        body: Any = None
        with httpx.Client(transport=self.transport, timeout=float(hc.get("timeout_s", 10.0))) as client:
            while True:
                attempts += 1
                limiter.acquire()
                wait: float | None = None
                try:
                    resp = client.request(method, url, headers=headers, params=params, json=json_body)
                except httpx.TimeoutException as e:
                    error = ProviderError(self.provider, "timeout", f"request timed out ({type(e).__name__})")
                    wait = min(cap, base * 2 ** (attempts - 1))
                except httpx.TransportError as e:
                    error = ProviderError(self.provider, "upstream_error", f"network error ({type(e).__name__})")
                    wait = min(cap, base * 2 ** (attempts - 1))
                else:
                    last_status = resp.status_code
                    if resp.status_code in (401, 403):
                        error = ProviderError(self.provider, "auth",
                                              f"authentication failed (HTTP {resp.status_code}); check the API key "
                                              "and that this API is enabled for it")
                    elif resp.status_code == 429:
                        rate_limited = True
                        error = ProviderError(self.provider, "rate_limited", "rate limited (HTTP 429)")
                        if not (no_retry and no_retry(resp)):
                            ra = _retry_after_s(resp.headers.get("Retry-After"))
                            if ra is None:
                                wait = min(cap, base * 2 ** (attempts - 1))
                            elif ra <= ra_max:
                                wait = ra
                            else:
                                error = ProviderError(self.provider, "rate_limited",
                                                      f"rate limited (HTTP 429; Retry-After {ra:.0f}s exceeds "
                                                      f"{ra_max:.0f}s, not retried)")
                    elif resp.status_code >= 500:
                        error = ProviderError(self.provider, "upstream_error",
                                              f"provider error (HTTP {resp.status_code})")
                        ra = _retry_after_s(resp.headers.get("Retry-After"))
                        wait = min(ra_max, ra) if ra is not None else min(cap, base * 2 ** (attempts - 1))
                    elif resp.status_code >= 400:
                        error = ProviderError(self.provider, "http_error", f"request rejected (HTTP {resp.status_code})")
                    else:
                        try:
                            body = resp.json()
                            error = None
                        except ValueError:
                            error = ProviderError(self.provider, "bad_response", "response was not JSON")
                        if error is None and ok_body is not None:
                            code = ok_body(body)
                            if code == "auth":
                                error = ProviderError(self.provider, "auth", "request denied by the provider "
                                                      "(check the key and that this API is enabled)")
                            elif code == "rate_limited":
                                rate_limited = True
                                error = ProviderError(self.provider, "rate_limited", "provider quota/rate limit")
                                wait = min(cap, base * 2 ** (attempts - 1))
                            elif code:
                                error = ProviderError(self.provider, "http_error", f"provider status {code}")
                if error is None or wait is None or attempts > max_retries:
                    break
                self.sleep(wait)

        latency = round((time.perf_counter() - t0) * 1000, 2)
        cost = self.price(category) if error is None else None
        self._log(endpoint, category, "ok" if error is None else "error", last_status, latency, attempts - 1, attempts,
                  rate_limited, cost, True, False, investigation_id, error.code if error else None)
        if error is not None:
            raise error
        if ck is not None:
            _cache_for(self.provider).put(ck, body, time.monotonic() + ttl)
        return body

    def _log(self, endpoint: str, category: str, status: str, http_status: int | None, latency_ms: float,
             retry_count: int, attempts: int, rate_limited: bool, cost: float | None, sent: bool, cached: bool,
             investigation_id: str | None, error_code: str | None) -> None:
        self.ledger.append(provider=self.provider, endpoint=endpoint, category=category, status=status,
                           http_status=http_status, latency_ms=latency_ms, retry_count=retry_count, attempts=attempts,
                           sent=sent, estimated_cost_usd=cost, rate_limited=rate_limited, cached=cached,
                           investigation_id=investigation_id, error_code=error_code)


# -------------------------------------------------------------------------------------------------------- provider
@runtime_checkable
class Provider(Protocol):
    name: str
    capabilities: frozenset[str]

    def status(self) -> dict[str, Any]: ...

    def search(self, query: str, bbox: tuple[float, float, float, float], categories: list[str] | None = None,
               limit: int = 20, investigation_id: str | None = None) -> list[PlaceRecord]: ...


class BaseProvider:
    """Common plumbing. Subclasses set name/capabilities/doc/attribution and implement ``search`` (+ extras)."""

    name = "base"
    capabilities: frozenset[str] = frozenset()
    source_name = ""
    source_url = ""
    licence = ""
    attribution = ""

    def __init__(self, conf: dict[str, Any] | None = None, *, transport: httpx.BaseTransport | None = None,
                 ledger: UsageLedger | None = None, sleep: Callable[[float], None] = time.sleep):
        self.conf = conf or load_conf()
        self.pconf: dict[str, Any] = (self.conf.get("providers") or {}).get(self.name, {})
        self.ledger = ledger or UsageLedger(ledger_path(self.conf))
        self.http = ProviderHttp(self.name, self.pconf, self.conf.get("http") or {}, self.ledger,
                                 transport=transport, sleep=sleep)

    # -- state
    @property
    def env_vars(self) -> list[str]:
        return list(self.pconf.get("env") or [])

    def key_present(self) -> bool:
        return all(has_secret(v) for v in self.env_vars) if self.env_vars else False

    def status(self) -> dict[str, Any]:
        """enabled + reason + key presence (never the value) + caps + capabilities."""
        key_present = self.key_present()
        if not self.pconf:
            enabled, reason = False, f"no '{self.name}' entry in conf/providers.json"
        elif not self.pconf.get("enabled", False):
            enabled, reason = False, self.pconf.get("reason") or "disabled in conf/providers.json"
        elif not key_present:
            enabled, reason = False, f"{' / '.join(self.env_vars)} is not set (process environment or repo .env)"
        else:
            enabled, reason = True, None
        return {
            "provider": self.name, "enabled": enabled, "reason": reason, "key_present": key_present,
            "env_vars": self.env_vars,
            "optional_env_present": {v: has_secret(v) for v in self.pconf.get("env_optional") or []},
            "capabilities": sorted(self.capabilities),
            "daily_request_cap": self.pconf.get("daily_request_cap"),
            "daily_usd_cap": self.pconf.get("daily_usd_cap"),
            "price_usd_per_1000": self.pconf.get("price_usd_per_1000") or {},
        }

    def require_enabled(self, *, allow_when_disabled: bool = False) -> str:
        """Returns the key (to be put in a header by the caller, never logged) or raises ProviderError."""
        st = self.status()
        if not st["key_present"]:
            raise ProviderError(self.name, "missing_key", st["reason"] or "API key not set")
        if not st["enabled"] and not allow_when_disabled:
            raise ProviderError(self.name, "disabled", st["reason"] or "disabled")
        key = get_secret(self.env_vars[0])
        assert key is not None
        return key

    # -- helpers
    def normalize_categories(self, raw: list[str], field: str) -> list[str]:
        """Map raw provider categories to normalized keys (conf 'categories'). ``field`` = yelp | google | fsq_words."""
        cats = {k: v for k, v in (self.conf.get("categories") or {}).items() if not k.startswith("_")}
        out: list[str] = []
        low = [r.lower() for r in raw if r]
        for key, spec in cats.items():
            vals = [v.lower() for v in spec.get(field) or []]
            if field == "fsq_words":  # whole-word match ("bar" must not hit "Barber", "inn" not "Dinner")
                hit = any(re.search(rf"\b{re.escape(w)}\b", r) for w in vals for r in low)
            else:
                hit = any(r in vals for r in low)
            if hit and key not in out:
                out.append(key)
        return sorted(out)

    def provider_filters(self, categories: list[str] | None, field: str) -> list[str]:
        cats = self.conf.get("categories") or {}
        out: list[str] = []
        for c in categories or []:
            for v in (cats.get(c) or {}).get(field) or []:
                if v not in out:
                    out.append(v)
        return out

    def source(self) -> dict[str, str]:
        return {"name": self.source_name, "url": self.source_url, "date": utc_now_iso()[:10], "licence": self.licence}
