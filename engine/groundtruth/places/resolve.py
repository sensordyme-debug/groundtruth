"""Cross-provider place resolution and de-duplication.

Rules (thresholds in conf/providers.json "resolve"):
- Two records may merge only if they come from DIFFERENT providers, and
    (a) distance <= max_dist_m AND name similarity >= name_min, or
    (b) address similarity >= address_min AND name similarity >= name_min_with_address AND
        distance <= max_dist_with_address_m, or
    (c) the same phone AND name similarity >= name_min_with_phone AND distance <= max_dist_with_phone_m.
  A similar name alone never merges two records. Incompatible categories (both known, disjoint) block a merge.
- Merging is greedy, best pair first, and a cluster never holds two records from one provider.
- Distances are measured in EPSG:32617 (UTM 17N) via groundtruth.geo.
- Cluster ``match_status``: single (one provider) | corroborated | conflict (merged members disagree: distance >
  conflict_dist_m, different phones, or disjoint categories).
- ``corroboration`` = number of distinct providers. Providers can share upstream data (e.g. licensed listings,
  the same chain feeds), so corroboration is NOT independent verification.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

import numpy as np

from .base import PlaceRecord, load_conf

_LEGAL = {"inc", "llc", "co", "corp", "corporation", "company", "ltd", "the", "pllc", "pc", "pa"}
_ADDR_ABBR = {
    "street": "st", "avenue": "ave", "av": "ave", "road": "rd", "drive": "dr", "boulevard": "blvd", "lane": "ln",
    "court": "ct", "place": "pl", "parkway": "pkwy", "highway": "hwy", "circle": "cir", "terrace": "ter",
    "north": "n", "south": "s", "east": "e", "west": "w", "northeast": "ne", "northwest": "nw", "southeast": "se",
    "southwest": "sw", "suite": "ste", "trail": "trl", "square": "sq", "expressway": "expy", "freeway": "fwy",
}
_UNIT = re.compile(r"\b(ste|suite|unit|apt|#)\s*[\w-]+$")


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def normalize_name(s: str | None) -> str:
    if not s:
        return ""
    s = _ascii(s).replace("&", " and ").replace("'", "")
    toks = [t for t in re.split(r"[^a-z0-9]+", s) if t and t not in _LEGAL]
    return " ".join(toks)


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def name_similarity(a: str | None, b: str | None) -> float:
    """max(plain ratio, sorted-token ratio, token containment) over normalized names, in [0, 1]."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = na.split(), nb.split()
    sorted_ratio = _ratio(" ".join(sorted(ta)), " ".join(sorted(tb)))
    sa, sb = set(ta), set(tb)
    # containment: "Amelie's French Bakery" vs "Amelie's French Bakery & Cafe"; needs >= 2 shared tokens
    inter = sa & sb
    contain = len(inter) / min(len(sa), len(sb)) if len(inter) >= 2 else 0.0
    return round(max(_ratio(na, nb), sorted_ratio, 0.95 * contain), 4)


def normalize_address(s: str | None) -> str:
    if not s:
        return ""
    s = _ascii(s)
    s = s.split(",")[0]  # first line only
    toks = [_ADDR_ABBR.get(t, t) for t in re.split(r"[^a-z0-9#]+", s) if t]
    out = " ".join(toks)
    return _UNIT.sub("", out).strip()


def address_similarity(a: str | None, b: str | None) -> float:
    na, nb = normalize_address(a), normalize_address(b)
    if not na or not nb:
        return 0.0
    # different house numbers => different addresses
    ha, hb = re.match(r"^(\d+)", na), re.match(r"^(\d+)", nb)
    if ha and hb and ha.group(1) != hb.group(1):
        return 0.0
    return round(_ratio(na, nb), 4)


def normalize_phone(s: str | None) -> str:
    d = re.sub(r"\D", "", s or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 else ""


def categories_compatible(a: PlaceRecord, b: PlaceRecord, families: dict[str, Any] | None = None) -> bool:
    """Compatible unless both are known and share neither a category nor a family (conf resolve.category_families):
    the same business is often a 'bar' on one provider and a 'restaurant' on another."""
    if not (a.categories and b.categories) or set(a.categories) & set(b.categories):
        return True
    fam = {c: k for k, cs in (families or {}).items() if not k.startswith("_") for c in cs}
    fa, fb = {fam.get(c) for c in a.categories} - {None}, {fam.get(c) for c in b.categories} - {None}
    return bool(fa & fb)


def _project(records: list[PlaceRecord]) -> np.ndarray:
    from .. import geo

    t = geo.transformer(geo.WGS84, geo.MEASURE_CRS)
    lon = np.array([r.lon for r in records], dtype=float)
    lat = np.array([r.lat for r in records], dtype=float)
    x, y = t.transform(lon, lat)
    return np.column_stack([x, y])


def pair_evidence(a: PlaceRecord, b: PlaceRecord, dist_m: float, rc: dict[str, Any]) -> dict[str, Any]:
    """Similarity evidence + the rule that merges them (or None)."""
    ns = name_similarity(a.name, b.name)
    asim = address_similarity(a.address, b.address)
    pa, pb = normalize_phone(a.phone), normalize_phone(b.phone)
    phone_match = bool(pa and pa == pb)
    compat = categories_compatible(a, b, rc.get("category_families"))
    rule = None
    if a.provider != b.provider and compat:
        if dist_m <= rc["max_dist_m"] and ns >= rc["name_min"]:
            rule = "distance+name"
        elif asim >= rc["address_min"] and ns >= rc["name_min_with_address"] and dist_m <= rc["max_dist_with_address_m"]:
            rule = "address+name"
        elif phone_match and ns >= rc["name_min_with_phone"] and dist_m <= rc["max_dist_with_phone_m"]:
            rule = "phone+name+distance"
    score = ns + 0.5 * asim + (0.3 if phone_match else 0.0) - dist_m / 1000.0
    return {"a": a.id, "b": b.id, "dist_m": round(dist_m, 1), "name_sim": ns, "address_sim": asim,
            "phone_match": phone_match, "categories_compatible": compat, "rule": rule, "score": round(score, 4)}


def resolve(records: list[PlaceRecord], conf: dict[str, Any] | None = None,
            provider_order: list[str] | None = None) -> list[dict[str, Any]]:
    """Cluster records (which must all have valid coordinates). Returns clusters sorted deterministically:
    [{cluster_id, match_status, corroboration, providers, representative_id, members: [PlaceRecord],
      evidence: [...], conflicts: [...]}]. Each member's ``match_status`` is set to its cluster's status."""
    conf = conf or load_conf()
    rc = {**load_conf().get("resolve", {}), **(conf.get("resolve") or {})}
    order = provider_order or conf.get("order") or []
    recs = sorted(records, key=lambda r: r.id)
    n = len(recs)
    if n == 0:
        return []
    xy = _project(recs)
    max_reach = max(rc["max_dist_m"], rc["max_dist_with_address_m"], rc["max_dist_with_phone_m"])

    pairs: list[dict[str, Any]] = []
    for i in range(n):
        d = np.hypot(xy[i + 1:, 0] - xy[i, 0], xy[i + 1:, 1] - xy[i, 1])
        for k in np.nonzero(d <= max_reach)[0]:
            j = i + 1 + int(k)
            ev = pair_evidence(recs[i], recs[j], float(d[k]), rc)
            if ev["rule"]:
                pairs.append(ev)
    pairs.sort(key=lambda e: (-e["score"], e["a"], e["b"]))

    idx = {r.id: i for i, r in enumerate(recs)}
    parent = list(range(n))
    provs: list[set[str]] = [{r.provider} for r in recs]

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    used: list[dict[str, Any]] = []
    for ev in pairs:
        ra, rb = find(idx[ev["a"]]), find(idx[ev["b"]])
        if ra == rb or provs[ra] & provs[rb]:
            continue
        parent[rb] = ra
        provs[ra] |= provs[rb]
        used.append(ev)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    def prio(r: PlaceRecord) -> tuple[int, str]:
        return (order.index(r.provider) if r.provider in order else len(order), r.id)

    clusters = []
    for members_i in groups.values():
        members = sorted((recs[i] for i in members_i), key=prio)
        ids = {m.id for m in members}
        ev = [e for e in used if e["a"] in ids and e["b"] in ids]
        conflicts: list[str] = []
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                a, b = members[x], members[y]
                ia, ib = idx[a.id], idx[b.id]
                dist = float(math.hypot(*(xy[ia] - xy[ib])))
                if dist > rc["conflict_dist_m"]:
                    conflicts.append(f"{a.id} and {b.id} are {dist:.0f} m apart")
                pa, pb = normalize_phone(a.phone), normalize_phone(b.phone)
                if pa and pb and pa != pb:
                    conflicts.append(f"{a.id} and {b.id} report different phone numbers")
                if not categories_compatible(a, b, rc.get("category_families")):
                    conflicts.append(f"{a.id} and {b.id} report incompatible categories")
        nprov = len({m.provider for m in members})
        status = "single" if nprov == 1 else ("conflict" if conflicts else "corroborated")
        for m in members:
            m.match_status = status  # type: ignore[assignment]
        cid = "pc_" + hashlib.sha1("|".join(sorted(ids)).encode()).hexdigest()[:12]
        clusters.append({
            "cluster_id": cid, "match_status": status, "corroboration": nprov,
            "providers": sorted({m.provider for m in members}, key=lambda p: (order.index(p) if p in order else 99, p)),
            "representative_id": members[0].id, "members": members, "evidence": ev, "conflicts": conflicts,
        })
    clusters.sort(key=lambda c: (-c["corroboration"], (c["members"][0].name or "").lower(), c["cluster_id"]))
    return clusters
