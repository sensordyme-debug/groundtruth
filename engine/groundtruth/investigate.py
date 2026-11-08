"""Investigations: an objective → a typed, validated, multi-step plan → dependency-ordered execution of approved engine
operations → an evidence ledger → graded findings. Built ON the existing planner (``planner.parse`` reads the target and
place; the chat brain may refine it) and the existing tools (find_derived, find_all, find_buildings, find_place,
filter, explain, compare_years). No second planner, no free-form execution: every step is one of ``OPS`` with
arguments validated by a pydantic model before anything runs. Every number in a finding comes from a step's envelope.

Lifecycle: compile(objective, context) → Investigation(plan) → run(inv, engine) mutates step states (queued → running →
done | failed | blocked | skipped) and appends events; the API polls the investigation (no fake streaming).
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from groundtruth import config

VERSION = "0.1.0"
StepStatus = Literal["queued", "running", "done", "failed", "blocked", "skipped"]
Strength = Literal["strong", "moderate", "weak", "insufficient"]
_RANK = {"strong": 3, "moderate": 2, "weak": 1, "insufficient": 0}


def conf() -> dict[str, Any]:
    return json.loads((config.SENSES_PATH.parent / "investigate.json").read_text())


# ---------------------------------------------------------------- approved operations (typed arguments)
class AreaArgs(BaseModel):
    place: str | None = None
    bbox: list[float] | None = Field(None, min_length=4, max_length=4)
    geojson: dict[str, Any] | None = None
    label: str | None = None
    kind: str | None = None  # county | city | municipality | neighbourhood | zip | radius | polygon | water_body | ...
    source: str | None = None
    source_date: str | None = None
    approximate: bool = False

    @model_validator(mode="after")
    def _one(self):
        if not (self.place or self.bbox or self.geojson):
            raise ValueError("an area needs a place, a bbox or a polygon")
        if self.bbox and not (self.bbox[0] < self.bbox[2] and self.bbox[1] < self.bbox[3]):
            raise ValueError("bbox must be w,s,e,n")
        return self


class DerivedArgs(BaseModel):
    place: str
    require: list[Literal["lakefront", "has_dock"]] = Field(default_factory=list)
    docks_new_since: int | None = None
    exclude_ids: list[str] = Field(default_factory=list)


class ConceptArgs(BaseModel):
    concept: str
    query: str
    place: str | None = None
    bbox: list[float] | None = None
    senses: list[str] = Field(default_factory=list)


class LikeArgs(BaseModel):
    examples: list[str] = Field(..., min_length=1)
    negatives: list[str] = Field(default_factory=list)
    place: str | None = None
    bbox: list[float] | None = None


class BuildingsArgs(BaseModel):
    kind: str = "building"
    place: str | None = None
    bbox: list[float] | None = None
    min_height_m: float | None = Field(None, ge=0)


class PlacesArgs(BaseModel):
    query: str
    place: str | None = None
    bbox: list[float] | None = None


class SimulateArgs(BaseModel):
    scenario: str
    offset_m: float


class ExposureArgs(BaseModel):
    target: Literal["buildings", "roads", "bridges", "facilities"]
    within_m: float = Field(0, ge=0, le=5000)  # facilities: proximity distance to the extent (0 = touching only)


class ClusterArgs(BaseModel):
    link_m: float = Field(120, gt=0, le=1000)
    min_size: int = Field(3, ge=2)


class FilterArgs(BaseModel):
    where: dict[str, Any]

    @model_validator(mode="after")
    def _known(self):
        allowed = {"in_flood_zone", "within_m_of_shore", "canopy_over_pct", "elevation_above_flood_m", "slope_deg",
                   "height_m", "within_minutes_of", "within_m_of", "intersects_layer", "not_intersects_layer",
                   "inside_place", "min_area_acres", "max_area_acres"}
        bad = set(self.where) - allowed
        if bad:
            raise ValueError(f"unsupported filter keys {sorted(bad)}")
        return self


class ExplainArgs(BaseModel):
    facts: list[Literal["flood", "elevation", "shore", "canopy", "slope", "height", "travel", "lakefront", "distance"]]
    label: str | None = None
    unit: str = "results"


class CompareArgs(BaseModel):
    from_year: Literal[2020] = 2020


class RankArgs(BaseModel):
    keys: list[str] = Field(default_factory=lambda: ["flood_kind", "elev_above_bfe_m", "shore_dist_m"])


class EmptyArgs(BaseModel):
    pass


class ProviderPlacesArgs(BaseModel):
    query: str
    place: str | None = None
    bbox: list[float] | None = None
    geojson: dict[str, Any] | None = None


OPS: dict[str, dict[str, Any]] = {
    "resolve_area": {"args": AreaArgs, "label": "Resolve the area", "why": "every later step is bounded by it"},
    "check_coverage": {"args": AreaArgs, "label": "Check what data covers it",
                       "why": "imagery years, lidar, flood maps and precomputed sets differ by area"},
    "find_derived": {"args": DerivedArgs, "label": "Find homes with pools",
                     "why": "verified pools assigned to residential buildings (precomputed per area)"},
    "find_concept": {"args": ConceptArgs, "label": "Find the objects", "why": "visual search over every image patch in the area"},
    "find_like": {"args": LikeArgs, "label": "Find objects like the examples", "why": "your examples, compared with every patch"},
    "find_buildings": {"args": BuildingsArgs, "label": "Find buildings", "why": "Overture building footprints and heights"},
    "find_places": {"args": PlacesArgs, "label": "Find places", "why": "Overture places by category or name"},
    "search_providers": {"args": ProviderPlacesArgs, "label": "Check commercial place providers",
                         "why": "Foursquare / Yelp listings (provider-reported, not verified here); optional"},
    "filter": {"args": FilterArgs, "label": "Apply a condition", "why": "keep only results that meet it"},
    "explain": {"args": ExplainArgs, "label": "Measure each result", "why": "the facts behind each result (its WHY line)"},
    "compare_years": {"args": CompareArgs, "label": "Compare with 2020", "why": "what changed between the two imagery years"},
    "rank": {"args": RankArgs, "label": "Order the results", "why": "a documented ordering, not an invented score"},
    "simulate": {"args": SimulateArgs, "label": "Run the hypothetical scenario",
                 "why": "SIMULATED: a flat water level on real lidar ground, connected to the lake (not a forecast)"},
    "exposure": {"args": ExposureArgs, "label": "Find what the scenario touches",
                 "why": "real features crossed with the hypothetical extent: potential exposure, not impact"},
    "cluster": {"args": ClusterArgs, "label": "Group exposed buildings",
                "why": "where exposure concentrates, ranked by a stated rule"},
    "conclude": {"args": EmptyArgs, "label": "Weigh the evidence", "why": "findings graded by their weakest evidence"},
}
_INPUT_OPS = {"filter", "explain", "compare_years", "rank"}  # need one upstream feature set


class PlanStep(BaseModel):
    id: str
    op: str
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    label: str = ""
    why: str = ""
    status: StepStatus = "queued"
    ms: float | None = None
    started: float | None = None
    detail: str = ""
    error: str | None = None
    count: int | None = None


class InvestigationPlan(BaseModel):
    objective: str
    scope: dict[str, Any] | None = None
    targets: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    time: dict[str, Any] = Field(default_factory=dict)
    questions: list[str] = Field(default_factory=list)
    steps: list[PlanStep]
    limitations: list[str] = Field(default_factory=list)
    unapplied: list[str] = Field(default_factory=list)  # condition phrases the planner could not turn into a step
    planner: str = "rules"

    @model_validator(mode="after")
    def _valid(self):
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate step ids")
        if len(self.steps) > conf()["limits"]["max_steps"]:
            raise ValueError("too many steps")
        seen: set[str] = set()
        for s in self.steps:
            if s.op not in OPS:
                raise ValueError(f"unknown operation {s.op!r}")
            OPS[s.op]["args"](**s.args)  # raises on bad arguments
            for d in s.depends_on:
                if d not in seen:
                    raise ValueError(f"step {s.id} depends on {d}, which does not come before it")
            if s.op in _INPUT_OPS and not s.depends_on:
                raise ValueError(f"step {s.id} ({s.op}) needs an input step")
            seen.add(s.id)
        return self


class Evidence(BaseModel):
    id: str
    step_id: str
    kind: Literal["scope", "coverage", "detection", "association", "spatial_intersection", "spatial_proximity", "simulation", "measurement",
                  "temporal", "ordering", "human"]
    statement: str
    source: dict[str, Any] = Field(default_factory=dict)  # name, url, date, licence
    method: str = ""
    measurement: dict[str, Any] = Field(default_factory=dict)  # name → value (units in the key)
    feature_ids: list[str] = Field(default_factory=list)
    strength: Strength = "moderate"
    confidence_note: str = ""
    limitations: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    id: str
    statement: str
    nature: Literal["observed", "computed", "model_prediction", "provider_reported", "human_verified", "inference", "assumption", "gap",
                    "simulated"]
    strength: Strength
    why_strength: str
    evidence_ids: list[str]
    feature_ids: list[str] = Field(default_factory=list)


class Investigation(BaseModel):
    id: str
    created: float
    updated: float
    status: Literal["planning", "needs_input", "running", "done", "partial", "failed", "cancelled", "declined"] = "planning"
    objective: str
    context: dict[str, Any] = Field(default_factory=dict)
    plan: InvestigationPlan | None = None
    question: str | None = None
    suggestions: list[str] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    summary: str = ""
    result: dict[str, Any] | None = None  # the final feature set (GeoJSON) + metrics
    result_step: str | None = None
    previous: str | None = None
    versions: dict[str, Any] = Field(default_factory=dict)
    declined_reason: str | None = None


# ---------------------------------------------------------------- compiling an objective into a plan
def _has(t: str, words: list[str]) -> bool:
    return any(w in t for w in words)


_UNITS_M = {"m": 1.0, "meter": 1.0, "meters": 1.0, "metre": 1.0, "metres": 1.0, "km": 1000.0, "kilometer": 1000.0,
            "kilometers": 1000.0, "kilometre": 1000.0, "kilometres": 1000.0, "mi": 1609.344, "mile": 1609.344,
            "miles": 1609.344, "ft": 0.3048, "feet": 0.3048, "foot": 0.3048}


def _near_condition(t: str, nl: dict[str, Any]) -> tuple[str, str, float, bool] | None:
    """'within 500 m of a light-rail station' / 'near I-485' → (layer, label, metres, assumed_distance). Distances to
    the shore / lake / water are the shore filter's, never a layer distance."""
    m = re.search(r"\bwithin (\d+(?:[.,]\d+)?) ?(m|meters?|metres?|km|kilometers?|kilometres?|miles?|mi|feet|foot|ft)\b of "
                  r"(?:a |an |the )?(?:nearest |closest )?(.{0,40})", t)
    near = None if m else re.search(r"\b(?:near|close to|next to|adjacent to|beside) (?:a |an |the )?(?:nearest |closest )?(.{0,40})", t)
    rest = (m.group(3) if m else near.group(1) if near else "")
    if not rest or re.match(r"(shore|shoreline|lake|water)\b", rest):
        return None
    for e in nl["layers"]:
        if any(re.match(r"" + re.escape(w) + r"s?\b", rest) for w in e["words"]):
            if m:
                return e["layer"], e["label"], round(float(m.group(1).replace(",", "")) * _UNITS_M[m.group(2)], 1), False
            return e["layer"], e["label"], float(nl["near_default_m"]), True
    return None


def _scenario_request(t: str, scope: dict[str, Any] | None, c: dict[str, Any]):
    """A hypothetical high-water question → (scenario id, offset m, offset assumed?, exposure targets), or None.
    Only where a scenario is defined for the scope's place (conf/scenarios.json)."""
    sr = c["scenario_request"]
    if not scope or not re.search(sr["pattern"], t):
        return None
    from groundtruth import scenario as S
    d = next((x for x in S.conf()["scenarios"] if x["place"] == scope.get("place")), None)
    if d is None:
        return None
    m = re.search(r"\b(\d+(?:\.\d+)?) ?(?:m|meters?|metres?)\b(?: (?:above|over|higher|rise))", t)
    offs = [float(x) for x in d["offsets_m"]]
    off, assumed = (float(sr["default_offset_m"]), True)
    if m:
        want = float(m.group(1))
        off, assumed = min(offs, key=lambda o: abs(o - want)), False
    targets = ["buildings"]
    for tg, rx in sr["targets"].items():
        if re.search(rx, t) and tg not in targets:
            targets.append(tg)
    return d["id"], off, assumed, targets


def _unapplied(objective: str, t: str, c: dict[str, Any], res, p, cons, steps) -> list[str]:
    """Condition-like phrases (conf condition_cues) whose kind no plan step applies. Scope words are removed first."""
    scan = t
    for name in {x for x in ((getattr(res, "matched_text", None) if res is not None else None), p.place) if x}:
        scan = re.sub(r"\b(?:near|around|in|within|close to|across|inside|of|at)?\s*" + re.escape(name.lower()) + r"\b", " ", scan)
    applied = {f for f, _, _ in cons}
    if any(s.op == "simulate" for s in steps):
        applied |= {"distance", "flood", "near"}
    if any(s.op == "compare_years" for s in steps) or p.docks_new_since:
        applied |= {"compare", "age"}
    if getattr(p, "min_height_m", None):
        applied.add("height")
    ok = {"near": {"distance", "shore", "travel"}, "slope": {"slope"}, "flood": {"flood"}, "canopy": {"canopy"},
          "age": {"age"}, "size": set(), "height": {"height", "elevation"}, "compare": {"compare"}, "unsupported": set()}
    out: list[str] = []
    for rx, kind in c["condition_cues"]["cues"]:
        for mm in re.finditer(rx, scan):
            if applied & ok.get(kind, set()):
                continue
            a, b = mm.start(), mm.end()  # quote a few words around the cue so the person recognises it
            phrase = re.sub(r"\s+", " ", scan[max(0, scan.rfind(" ", 0, max(0, a - 1)) + 1): (scan.find(" ", b + 1) if scan.find(" ", b + 1) > 0 else len(scan))]).strip(" ,.")
            phrase = re.sub(r"\s+", " ", " ".join(scan[a:].split()[:6])).strip(" ,.") if kind in ("near", "size", "height") else phrase
            if phrase and phrase not in out:
                out.append(phrase)
    return out


def compile_plan(objective: str, context: dict[str, Any] | None = None, prev: Investigation | None = None,
                 base_plan=None) -> tuple[InvestigationPlan | None, dict[str, Any]]:
    """→ (plan, info). info carries a question back to the user ("Where should I look?") when the plan cannot be
    built, or the reason it fell back to a single chat turn. Deterministic for identical inputs."""
    from groundtruth import planner as P

    c = conf()
    ctx = dict(context or {})
    t = " " + re.sub(r"\s+", " ", objective.lower().strip().rstrip("?.!")) + " "
    W = c["constraints"]
    follow = prev is not None and prev.result is not None and (_has(t, W["follow_up"]) or bool(ctx.get("follow_up")))
    p = base_plan or P.parse(objective)

    # ---- scope: a place named in the objective (TEXT WINS over the map's area chip) > the area chip > the previous
    # investigation's scope. The geography resolver (geography.py) turns county / city / town / ZIP / radius / lake
    # mentions into exact boundaries; it never guesses ("Mecklenburg County" can no longer fall back to a neighbourhood).
    area = ctx.get("area") or {}
    scope: dict[str, Any] | None = None
    geo_note: list[str] = []
    res = None
    try:
        from groundtruth import geography as G
        res = G.resolve(objective)  # text only: the map's area is applied below, after the text
    except Exception:  # noqa: BLE001 (boundary data missing: fall back to the planner's place names)
        res = None
    # "within N m of the shore / the lake / the water" is a per-object condition (the shore-distance filter), not a
    # search area: never let it replace the place scope
    if res is not None and res.kind == "radius" and re.search(r"of the (shore|shoreline|lake|water)", res.matched_text or "", re.I):
        res = None
    if res is not None and not res.resolved and "outside" in (res.reason or "").lower():
        return None, {"question": f"{res.reason[:1].upper()}{res.reason[1:]}. Groundtruth covers Mecklenburg County only.",
                      "suggestions": [f"in {x}" for x in c["suggest_places"]], "reason": "outside coverage"}
    # the rules planner only strips the 18 place names it knows: re-read the target with the resolved place removed
    if res is not None and res.resolved and res.matched_text and not p.place and base_plan is None:
        rest = re.sub(r"\b(in|within|near|around|across|inside|of)?\s*" + re.escape(res.matched_text) + r"\b", " ", objective,
                      flags=re.IGNORECASE)
        p2 = P.parse(rest)
        if p2.target_kind in ("concept", "place_category", "buildings", "derived") or (p2.target_kind and p.target_kind == "open"):
            p = p2
    if p.place and not (res is not None and res.resolved and res.kind in ("county", "city", "municipality", "zip", "radius")
                        and (res.name or "").lower() != p.place.lower()):
        scope = {"place": p.place, "label": p.place}
        # the same place with an exact boundary (a town, a mapped neighbourhood): keep the name for the engine's
        # per-place data, add the polygon so results are clipped to it (never the water body: homes are on land)
        if (res is not None and res.resolved and not res.approximate and res.kind in ("municipality", "city", "neighbourhood")
                and (res.name or "").lower() == p.place.lower()):
            scope.update(geojson=res.geometry, bbox=list(res.bbox), kind=res.kind, source=res.source, source_date=res.source_date)
    elif res is not None and res.resolved and res.kind not in ("address", "point"):
        scope = {"geojson": res.geometry, "bbox": list(res.bbox), "label": res.name, "kind": res.kind,
                 "source": res.source, "source_date": res.source_date, "approximate": bool(res.approximate)}
        if res.ambiguous:
            alts = ", ".join(a.get("name") or a.get("kind") or "?" for a in res.alternatives[:3])
            geo_note.append(f"“{res.matched_text or res.name}” can be read more than one way; I used {res.name} "
                            f"({(res.kind or '').replace('_', ' ')})" + (f"; other readings: {alts}" if alts else ""))
        if res.approximate:
            geo_note.append(f"{res.name}'s boundary is approximate (a bounding box)")
    elif area.get("place"):
        scope = {"place": area["place"], "label": area.get("label") or area["place"]}
    elif area.get("bbox") or area.get("geojson"):
        scope = {k: area[k] for k in ("bbox", "geojson") if area.get(k)} | {"label": area.get("label") or "Your area"}
    elif follow and prev and prev.plan and prev.plan.scope:
        scope = prev.plan.scope

    # ---- constraints
    cons: list[tuple[str, dict[str, Any], str]] = []  # (fact, where, words)
    assumptions: list[str] = []
    if re.search(r"\bfloodways?\b", t):
        cons.append(("flood", {"in_flood_zone": {"kind": "floodway"}}, "in a FEMA regulatory floodway"))
    elif _has(t, W["flood_100yr"]):
        cons.append(("flood", {"in_flood_zone": {"kind": "100yr"}}, "in a FEMA 100-year flood zone"))
    elif _has(t, W["flood_500yr"]):
        cons.append(("flood", {"in_flood_zone": {"kind": "500yr"}}, "in a FEMA 100- or 500-year flood zone"))
    elif _has(t, W["flood_any"]) and not re.search(r"\bexposure\b", t):
        cons.append(("flood", {"in_flood_zone": {"kind": "any"}}, "in a FEMA flood zone"))
    m = re.search(r"\b(less|lower|fewer) than (\d+(?:\.\d+)?) ?m(?:etres?|eters?)? above\b|\bwithin (\d+(?:\.\d+)?) ?m of the (?:base )?flood elevation", t)
    if m:
        v = float(m.group(2) or m.group(3))
        cons.append(("elevation", {"elevation_above_flood_m": {"lt": v}}, f"less than {v:g} m above the base flood elevation"))
    m = re.search(r"\b(more|higher|greater) than (\d+(?:\.\d+)?) ?m(?:etres?|eters?)? above\b", t)
    if m:
        v = float(m.group(2))
        cons.append(("elevation", {"elevation_above_flood_m": {"gt": v}}, f"more than {v:g} m above the base flood elevation"))
    m = re.search(r"\bwithin (\d+) ?m of the (?:shore|lake|water)", t)
    if m:
        cons.append(("shore", {"within_m_of_shore": float(m.group(1))}, f"within {m.group(1)} m of the shore"))
    if _has(t, W["trees_over"]):
        cons.append(("canopy", {"canopy_over_pct": {"min": 25}}, "at least 25 % under tree canopy"))
    m = re.search(r"\btaller than (\d+) ?m\b", t)
    if m and p.target_kind != "buildings":
        cons.append(("height", {"height_m": {"min": float(m.group(1))}}, f"taller than {m.group(1)} m"))
    m = re.search(r"\b(more|further|farther) than (\d+) minutes? (?:drive )?from (?:the )?(?:nearest )?([a-z ]+?)\s*$", t)
    if m:
        cons.append(("travel", {"within_minutes_of": {"category": m.group(3).strip(), "minutes": float(m.group(2)), "outside": True}},
                     f"more than {m.group(2)} minutes from the nearest {m.group(3).strip()}"))
    m = re.search(r"\bslopes? (?:of |steeper than |more than |greater than |over |above )(\d+(?:\.\d+)?) ?(?:°|deg|degrees?)\b", t)
    if m:
        cons.append(("slope", {"slope_deg": {"min": float(m.group(1))}}, f"on slopes steeper than {float(m.group(1)):g}°"))
    elif re.search(r"\bsteep(?:er)?\b|\bhillsides?\b|\bon (?:a |the )?(?:hill|slope)s?\b", t):
        dg = float(c["steep_slope"]["deg"])
        cons.append(("slope", {"slope_deg": {"min": dg}}, f"on steep slopes (more than {dg:g}°)"))
        assumptions.append(f"'Steep' read as a mean slope above {dg:g}° (about a 15 % grade), from the 2016 lidar ground model")
    near = _near_condition(t, c["near_layers"])
    if near:
        layer, label, metres, assumed = near
        cons.append(("distance", {"within_m_of": {"layer": layer, "m": metres}}, f"within {metres:g} m of {label}"))
        if assumed:
            assumptions.append(f"'Near' read as within {metres:g} m of {label}")
    flood_topic = _has(t, W["flood_any"] + W["flood_100yr"] + W["flood_500yr"]) or bool(re.search(r"\bfloodways?\b", t))
    rank = _has(t, W["rank"]) or flood_topic

    # ---- targets
    homes_words = bool(re.search(r"\b(homes?|houses?|properties|residential|residences?)\b.*\b(swimming )?pools?\b|"
                                 r"\bpools?\b.*\b(homes?|houses?|properties)\b", t))
    homes = p.target_kind == "derived" or _has(t, W["homes_with_pools"]) or homes_words or (
        follow and prev is not None and any(s.op == "find_derived" for s in (prev.plan.steps if prev.plan else [])))
    require = []
    if homes and (_has(t, W["lakefront"]) or "lakefront" in (p.require or [])):
        require.append("lakefront")
    if homes and (("has_dock" in (p.require or [])) or re.search(r"\b(with|and|own|their) (?:a )?docks?\b", t)):
        require += [r for r in ("lakefront", "has_dock") if r not in require]
    flood_exposure = flood_topic and re.search(r"\bexposure\b", t)
    if flood_exposure and homes and "lakefront" not in require and _has(t, W["lakefront"]):
        require.append("lakefront")

    steps: list[PlanStep] = []
    limitations: list[str] = []
    scen = _scenario_request(t, scope, c) if not follow else None

    def add(op: str, args: dict[str, Any], deps: list[str] | None = None, label: str | None = None) -> str:
        sid = f"s{len(steps) + 1}"
        steps.append(PlanStep(id=sid, op=op, args=args, depends_on=deps or [], label=label or OPS[op]["label"], why=OPS[op]["why"]))
        return sid

    target_desc = ""
    if follow and prev:
        # a follow-up starts from the previous investigation's final set
        targets = list(prev.plan.targets) if prev.plan else []
        target_desc = (prev.plan.targets[0] if prev.plan and prev.plan.targets else "the previous results")
        last = "prev"
    else:
        if not scope and not cons:
            # a plain search with no area: the chat turn handles it (pick grid, county overview, "Where should I look?")
            return None, {"fallback": "chat", "reason": "no area and no conditions: a search, not an investigation"}
        if not scope:
            return None, {"question": "Where should I look?", "suggestions": [f"in {x}" for x in c["suggest_places"]],
                          "reason": "no area: counts and flood checks need a bounded area"}
        targets = []
        last = None
        if scope:
            add("resolve_area", scope)
            add("check_coverage", scope, ["s1"])
        if scen:
            sid, off, assumed_off, targets_x = scen
            sim = add("simulate", {"scenario": sid, "offset_m": off}, ["s2"],
                      label=f"Run the hypothetical scenario: water {off:g} m above FEMA's base flood elevation")
            exp_ids = []
            for tg in targets_x:
                args = {"target": tg, "within_m": float(c["scenario_request"]["facility_within_m"]) if tg == "facilities" else 0}
                exp_ids.append(add("exposure", args, [sim], label={"buildings": "Find buildings the scenario touches",
                                                                   "roads": "Find major roads it crosses",
                                                                   "bridges": "Find bridges it crosses",
                                                                   "facilities": "Find community facilities near it"}[tg]))
            last = add("cluster", {}, exp_ids, label="Group exposed buildings and rank the clusters")
            target_desc = "infrastructure exposed to a hypothetical high-water scenario"
            targets.append(target_desc)
            if assumed_off:
                limitations.append(f"Scenario level not stated: I used FEMA's base flood elevation + {off:g} m")
            limitations.append("SIMULATED scenario: a flat water level on 2016 lidar ground, connected to the lake. Not a "
                               "rainfall, drainage or flood forecast")
            cons = []  # the scenario steps carry the conditions
        elif homes and scope.get("place") and not (config.data_dir("derived") / scope["place"].replace(" ", "_") / "houses.geojson").exists():
            homes = False  # no house linkage here: list the detected pools instead, and say so
            limitations.append(f"Homes are linked to their pools only for Lake Norman; in {scope['place']} I list the detected "
                               "pools themselves (a pool is not proof of which home it belongs to)")
            p.target_kind, p.concept, p.concept_query = "concept", "swimming_pool", "pools"
        if scen:
            pass  # the scenario steps were added above
        elif homes:
            if not scope.get("place"):
                return None, {"question": "Homes with pools are precomputed per neighbourhood. Which one?",
                              "suggestions": [f"in {x}" for x in c["suggest_places"][:1]], "reason": "derived sets need a place"}
            label = "Find lakefront homes with pools" if "lakefront" in require else "Find homes with pools"
            if "has_dock" in require:
                label = "Find lakefront homes with pools and their own dock"
            last = add("find_derived", {"place": scope["place"], "require": require,
                                        "docks_new_since": p.docks_new_since, "exclude_ids": list(ctx.get("rejected") or [])},
                       ["s2"], label=label)
            target_desc = {(): "homes with pools", ("lakefront",): "lakefront homes with pools"}.get(
                tuple(require), "lakefront homes with pools and their own dock")
            targets.append(target_desc)
        elif p.target_kind == "concept" and p.concept:
            last = add("find_concept", {"concept": p.concept, "query": p.concept_query or p.concept.replace("_", " "),
                                        "place": scope.get("place"), "bbox": scope.get("bbox"),
                                        "senses": list(ctx.get("picked") or [])}, ["s2"],
                       label=f"Find {p.concept_query or p.concept.replace('_', ' ')}")
            target_desc = p.concept_query or p.concept.replace("_", " ")
            targets.append(target_desc)
        elif p.target_kind == "buildings":
            last = add("find_buildings", {"kind": p.target or "building", "place": scope.get("place"), "bbox": scope.get("bbox"),
                                          "min_height_m": p.min_height_m}, ["s2"])
            target_desc = "buildings" if (p.target or "building") == "building" else str(p.target)
            targets.append(target_desc)
        elif p.target_kind == "place_category":
            last = add("find_places", {"query": p.target, "place": scope.get("place"), "bbox": scope.get("bbox")}, ["s2"])
            target_desc = str(p.target)
            add("search_providers", {"query": str(p.target), "place": scope.get("place"), "bbox": scope.get("bbox"),
                                     "geojson": scope.get("geojson")}, ["s2"])
            targets.append(target_desc)
        else:
            return None, {"fallback": "chat", "reason": "no investigable target: handled as a chat turn"}

    # ---- conditions, measurements, ordering, conclusion
    facts: list[str] = []
    for fact, where, words in cons:
        last = add("filter", {"where": where}, [last] if last != "prev" else [], label=f"Keep the ones {words}")
        facts.append(fact)
    if flood_topic:
        facts += [f for f in ("flood", "elevation", "shore") if f not in facts]
    if (facts or rank) and not scen:
        last = add("explain", {"facts": facts or ["flood", "elevation", "shore"], "label": target_desc or None},
                   [last] if last and last != "prev" else [])
    if rank and not scen:
        last = add("rank", {"keys": [k["prop"] for k in c["rank_rule"]["keys"]]}, [last] if last and last != "prev" else [])
    if re.search(r"\b(since 20\d\d|new since|built since|changed|what changed|compare with 2020|what'?s new)\b|"
                 r"\bcompare\b.*\b20(?:1\d|2[0-2])\b|\bbetween 20\d\d and 20\d\d\b|\b2020 (?:vs\.?|versus|and|to) 2022\b", t) and \
            not p.docks_new_since and not homes:
        last = add("compare_years", {"from_year": 2020}, [last] if last and last != "prev" else [])
    add("conclude", {}, [s.id for s in steps])
    if follow:
        # follow-up steps read the previous set: mark the first data step's input explicitly
        for s in steps:
            if s.op in _INPUT_OPS and not s.depends_on:
                s.depends_on = []
        limitations.append("Starts from the previous investigation's results")
    limitations += geo_note + assumptions
    unapplied = _unapplied(objective, t, c, res, p, cons, steps)
    for u in unapplied:
        limitations.insert(0, f"Not applied: “{u}”. Groundtruth can't test that condition yet, so these results ignore it")
    try:
        plan = InvestigationPlan(objective=objective, scope=scope, targets=targets, constraints=[w for _, _, w in cons],
                                 time={"imagery": 2022, "compare": 2020 if any(s.op == "compare_years" for s in steps) else None},
                                 questions=_questions(target_desc, cons, flood_topic, rank), steps=steps,
                                 limitations=limitations, unapplied=unapplied)
    except ValidationError as e:
        if follow:  # follow-up input steps legitimately have no in-plan dependency
            plan = InvestigationPlan.model_construct(objective=objective, scope=scope, targets=targets,
                                                     constraints=[w for _, _, w in cons], time={"imagery": 2022},
                                                     questions=_questions(target_desc, cons, flood_topic, rank),
                                                     steps=steps, limitations=limitations, unapplied=unapplied, planner="rules")
            _validate_follow_up(plan)
        else:
            return None, {"error": f"plan rejected before execution: {e.errors()[0]['msg']}"}
    return plan, {"follow_up": follow}


def _validate_follow_up(plan: InvestigationPlan) -> None:
    for s in plan.steps:
        if s.op not in OPS:
            raise ValueError(f"unknown operation {s.op!r}")
        OPS[s.op]["args"](**s.args)


def _questions(target: str, cons, flood_topic: bool, rank: bool) -> list[str]:
    q = []
    if target:
        q.append(f"Which {target} are in the area?")
    for _, _, words in cons:
        q.append(f"Which of them are {words}?")
    if flood_topic:
        q.append("Where is flood-elevation data missing?")
    if rank:
        q.append("Which ones come first under the documented ordering?")
    return q


# ---------------------------------------------------------------- execution
_STORE: dict[str, Investigation] = {}
_FEATS: dict[str, dict[str, list[dict[str, Any]]]] = {}  # inv id → step id → features (kept in memory, not persisted)
_LOCK = threading.Lock()
_CANCEL: set[str] = set()


def store_dir():
    d = config.DATA / "app" / "v1" / "investigations"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(inv: Investigation) -> None:
    inv.updated = time.time()
    p = store_dir() / f"{inv.id}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(inv.model_dump_json())
    tmp.replace(p)


def get(inv_id: str) -> Investigation | None:
    with _LOCK:
        if inv_id in _STORE:
            return _STORE[inv_id]
    p = store_dir() / f"{inv_id}.json"
    if p.exists():
        inv = Investigation.model_validate_json(p.read_text())
        with _LOCK:
            _STORE[inv_id] = inv
        return inv
    return None


def new(objective: str, context: dict[str, Any] | None = None) -> Investigation:
    now = time.time()
    inv = Investigation(id=uuid.uuid4().hex[:12], created=now, updated=now, objective=objective, context=dict(context or {}),
                        versions=_versions())
    with _LOCK:
        _STORE[inv.id] = inv
    return inv


def cancel(inv_id: str) -> None:
    """Cooperative: the running step finishes, later steps are skipped. A finished investigation is left as it is
    (and its id is not kept, so the set can't grow)."""
    inv = _STORE.get(inv_id)
    if inv is None or inv.status in ("planning", "running", "needs_input"):
        _CANCEL.add(inv_id)


def _versions() -> dict[str, Any]:
    dv = json.loads((config.SENSES_PATH.parent / "data_versions.json").read_text())
    return {"investigate": VERSION, "data": {k: v for k, v in dv.items() if not k.startswith("_")}}


def _event(inv: Investigation, step: PlanStep | None, kind: str, text: str) -> None:
    inv.events.append({"t": round(time.time(), 3), "step": step.id if step else None, "kind": kind, "text": text})


def run(inv: Investigation, engine, prev: Investigation | None = None) -> Investigation:
    """Execute the plan in dependency order. A failed or declined step blocks the steps that need it; independent
    steps still run; the investigation ends done (all ran), partial (some blocked/failed but findings exist) or failed.
    Steps run one at a time: the engine shares one GPU and a memory-mapped index, so parallel model calls would only
    contend (documented in docs/INTELLIGENCE_ARCHITECTURE.md)."""
    plan = inv.plan
    assert plan is not None
    inv.status = "running"
    feats: dict[str, list[dict[str, Any]]] = {}
    envs: dict[str, Any] = {}
    if prev is not None and prev.result_step:
        feats["prev"] = list(_FEATS.get(prev.id, {}).get(prev.result_step) or (prev.result or {}).get("features") or [])
    lim = conf()["limits"]
    for s in plan.steps:
        if inv.id in _CANCEL:
            s.status = "skipped"
            continue
        blocked = [d for d in s.depends_on if next(x for x in plan.steps if x.id == d).status in ("failed", "blocked")]
        if blocked and s.op != "conclude":
            s.status = "blocked"
            s.detail = f"needs {', '.join(blocked)}, which did not complete"
            _event(inv, s, "blocked", s.detail)
            continue
        s.status, s.started = "running", time.time()
        _event(inv, s, "start", s.label)
        save(inv)
        t0 = time.perf_counter()
        try:
            src = _input_of(s, plan, feats, prev)
            env, out = _with_timeout(lambda: _exec(s, engine, src, inv, envs, feats), lim["step_timeout_s"], s.op)
            envs[s.id] = env
            if out is not None:
                feats[s.id] = out[: lim["max_features_kept"]]
            if env is not None and getattr(env, "status", "ok") == "declined":
                s.status, s.error = "failed", env.declined_reason
                _event(inv, s, "failed", env.declined_reason or "declined")
            else:
                s.status = "done"
                s.count = len(out) if out is not None else None
                _event(inv, s, "done", s.detail or s.label)
        except _Skip as e:
            s.status, s.detail = "skipped", str(e)
            _event(inv, s, "skipped", s.detail)
        except Exception as e:  # noqa: BLE001 (a tool error must not take the investigation down)
            s.status, s.error = "failed", f"{type(e).__name__}: {e}"[:300]
            _event(inv, s, "failed", s.error)
        s.ms = round((time.perf_counter() - t0) * 1000, 1)
        save(inv)
    # the final feature set = the last data step that completed
    data_steps = [s for s in plan.steps if s.id in feats and s.status == "done"]
    if data_steps:
        fin = data_steps[-1]
        inv.result_step = fin.id
        inv.result = {"type": "FeatureCollection", "features": feats[fin.id],
                      "metrics": dict(getattr(getattr(envs.get(fin.id), "result", None), "metrics", {}) or {})}
    _FEATS[inv.id] = feats
    bad = [s for s in plan.steps if s.status in ("failed", "blocked")]
    inv.status = "cancelled" if inv.id in _CANCEL else ("done" if not bad else ("partial" if inv.findings else "failed"))
    _CANCEL.discard(inv.id)
    save(inv)
    return inv


class StepTimeout(RuntimeError):
    pass


def _display_allowed(pr) -> bool:
    """A provider whose content may be shown to users (conf providers.json display_enabled; default true)."""
    return bool(pr.pconf.get("display_enabled", True))


class _Skip(RuntimeError):
    """An optional step that could not run for a stated, expected reason (e.g. no provider keys): skipped, not failed."""


def _with_timeout(fn: Callable[[], Any], seconds: float, op: str):
    """Run one step with a hard wait limit. Python can't kill a thread, so an overrunning tool keeps running in the
    background, but the investigation stops waiting: the step is marked failed ("timed out") and its dependents are
    blocked. Its late result is discarded. Generous by default (conf limits.step_timeout_s)."""
    box: dict[str, Any] = {}

    def work():
        try:
            box["v"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["e"] = e
    th = threading.Thread(target=work, daemon=True, name=f"step-{op}")
    th.start()
    th.join(seconds)
    if th.is_alive():
        raise StepTimeout(f"timed out after {seconds:g} s (the tool kept running in the background; its result is discarded)")
    if "e" in box:
        raise box["e"]
    return box["v"]


def _input_of(s: PlanStep, plan: InvestigationPlan, feats, prev) -> list[dict[str, Any]] | None:
    if s.op not in _INPUT_OPS:
        return None
    for d in reversed(s.depends_on):
        if d in feats:
            return feats[d]
    if "prev" in feats:
        return feats["prev"]
    raise RuntimeError("no input feature set")


def _fc(fs):
    return {"type": "FeatureCollection", "features": fs}


def _exec(s: PlanStep, eng, src, inv: Investigation, envs, feats):
    a = OPS[s.op]["args"](**s.args)
    if s.op == "resolve_area":
        bb = a.bbox or (eng.place_bbox(a.place) if a.place else None)
        if a.geojson and not bb:
            from shapely.geometry import shape
            bb = list(shape(a.geojson).bounds)
        if not bb:
            raise ValueError(f"unknown area {a.place!r}")
        km2 = _km2_geom(a.geojson) if a.geojson else _km2(bb)
        what = f"{a.label or a.place or 'the drawn area'}" + (f" ({a.kind.replace('_', ' ')})" if a.kind else "")
        s.detail = f"{what}: {km2:.1f} km²" + (" · approximate boundary" if a.approximate else "")
        _add_ev(inv, s, "scope", f"The search is bounded by {what}, {km2:.1f} km²" + (" (approximate boundary)." if a.approximate else "."),
                source={"name": a.source or ("Groundtruth places (OSM Nominatim boundaries, ODbL)" if a.place else "Drawn by the user"),
                        "url": "", "date": a.source_date or "", "licence": "ODbL / public domain (see data/boundaries/v1/meta.json)" if a.source else ("ODbL" if a.place else "")},
                measurement={"bbox": [round(v, 6) for v in bb], "area_km2": round(km2, 2)}, strength="moderate" if a.approximate or not a.geojson else "strong",
                confidence_note="the exact boundary polygon" if a.geojson and not a.approximate else "a rectangle around the named area; edges are approximate")
        return None, None
    if s.op == "check_coverage":
        cov = coverage(eng, a)
        s.detail = "; ".join(f"{k}: {'yes' if v['covered'] else 'no'}" for k, v in cov.items())
        need = _coverage_needs(inv.plan)
        gaps = [v["why"] for k, v in cov.items() if not v["covered"] and k in need]
        _add_ev(inv, s, "coverage", "Data available here: " + ", ".join(k for k, v in cov.items() if v["covered"]) +
                (". Missing: " + ", ".join(k for k, v in cov.items() if not v["covered"]) if gaps else "."),
                measurement={k: v["covered"] for k, v in cov.items()}, strength="strong", limitations=gaps,
                source={"name": "Groundtruth data registry (conf/data_versions.json)", "url": "", "date": "", "licence": ""})
        inv.plan.limitations += [g for g in gaps if g not in inv.plan.limitations]
        return None, None
    if s.op == "find_derived":
        env = eng.find_derived("house_with_pool", place=a.place, require=tuple(a.require),
                               docks_new_since=a.docks_new_since, exclude_ids=tuple(a.exclude_ids))
        if env.status == "declined":
            return env, None
        fs = env.result.geojson["features"]
        houses = [f for f in fs if f["properties"].get("kind") == "house_with_pool"]
        m = env.result.metrics
        s.detail = f"{len(houses)} of {m.get('of_houses_with_pools')} homes with pools"
        q = conf()["detection_quality"]
        dq = q.get("has_dock" if "has_dock" in a.require else "lakefront" if "lakefront" in a.require else "house_with_pool", {})
        _add_ev(inv, s, "detection", f"{m.get('pools_in_place')} pools were detected in imagery across {a.place}; "
                f"{m.get('of_houses_with_pools')} sit next to a residential building.",
                source=_imagery_src(eng, env), method="DINOv2 patch search + dense colour blobs + verifier (size, colour, lidar height)",
                measurement={"pools_detected": m.get("pools_in_place"), "homes_with_pools": m.get("of_houses_with_pools")},
                feature_ids=[], strength=_model_strength(dq), confidence_note=_dq_note(dq),
                limitations=["Exhaustive scan of the area's imagery, but recall is not guaranteed: a pool under trees or "
                             "covered may be missed"])
        _add_ev(inv, s, "association", f"{len(houses)} homes meet the conditions: {', '.join(a.require) or 'a pool within 40 m'}.",
                source=_src(env, "Overture"), method="nearest residential Overture building within 40 m; lakefront = within "
                "100 m of the lake with no major road or building between; dock = one verified dock per house",
                measurement={"homes": len(houses)}, feature_ids=[_fid(f) for f in houses], strength=_model_strength(dq),
                confidence_note=_dq_note(dq), limitations=list(env.limits)[-3:])
        return env, fs
    if s.op == "find_concept":
        pc = eng.pick_candidates(a.query)
        tiles = [f for f in pc.result.geojson["features"] if not f["properties"].get("no_clear_example")]
        from groundtruth.planner import DEFAULT_SENSE
        chosen = [f for f in tiles if f["properties"]["sense_id"] in a.senses] or \
            [next((f for f in tiles if f["properties"]["sense_id"] == DEFAULT_SENSE.get(a.concept)), tiles[0])]
        neg = [f["properties"]["example"] for f in tiles if f not in chosen]
        env = eng.find_all([c["properties"]["example"] for c in chosen], [], place=a.place if not a.bbox else None,
                           bbox=a.bbox, concept=a.concept, soft_exclude=neg)
        if env.status == "declined":
            return env, None
        fs = _clip_to_scope(inv, s, env.result.geojson["features"], a.query)
        m = env.result.metrics
        s.detail = f"{len(fs)} {a.query}"
        dq = conf()["detection_quality"].get(a.concept, {})
        _add_ev(inv, s, "detection", f"{len(fs)} {a.query} detected in the area ({m.get('mode', 'scan').replace('_', ' ')}).",
                source=_src(env, "NAIP"), method=env.method[:300], measurement={"count": len(fs)}, feature_ids=[_fid(f) for f in fs],
                strength=_model_strength(dq), confidence_note=_dq_note(dq) + " Scores are uncalibrated similarities, not probabilities.",
                limitations=list(env.limits)[-3:])
        return env, fs
    if s.op == "find_like":
        env = eng.find_all(a.examples, a.negatives, place=a.place if not a.bbox else None, bbox=a.bbox, concept=None)
        fs = env.result.geojson["features"] if env.status != "declined" else None
        if fs is not None:
            _add_ev(inv, s, "detection", f"{len(fs)} patches look like the {len(a.examples)} example(s).", source=_src(env, "NAIP"),
                    method="DINOv2 cell similarity to the examples, minus the negatives", measurement={"count": len(fs)},
                    feature_ids=[_fid(f) for f in fs], strength="weak",
                    confidence_note="similarity to your examples; precision not measured for this kind of object")
        return env, fs
    if s.op == "find_buildings":
        env = eng.find_buildings(a.kind, bbox=a.bbox, place=a.place, min_height_m=a.min_height_m)
        fs = env.result.geojson["features"] if env.status != "declined" else None
        if fs is not None:
            fs = _clip_to_scope(inv, s, fs, "buildings")
        if fs is not None:
            s.detail = f"{len(fs)} buildings"
            _add_ev(inv, s, "detection", f"{len(fs)} {a.kind if a.kind != 'building' else 'buildings'} in Overture footprints"
                    + (f" taller than {a.min_height_m:g} m" if a.min_height_m else "") + ".", source=_src(env, "Overture"),
                    method=env.method[:300], measurement={"count": len(fs)}, feature_ids=[_fid(f) for f in fs[:2000]],
                    strength="strong", confidence_note="mapped footprints (not detected); heights from Overture or lidar",
                    limitations=list(env.limits)[-3:])
        return env, fs
    if s.op == "find_places":
        env = eng.find_place(a.query, bbox=a.bbox, place=a.place)
        fs = env.result.geojson["features"] if env.status != "declined" else None
        if fs is not None:
            fs = _clip_to_scope(inv, s, fs, a.query)
        if fs is not None:
            s.detail = f"{len(fs)} places"
            _add_ev(inv, s, "detection", f"{len(fs)} {a.query} listings in Overture places.", source=_src(env, "Overture"),
                    method=env.method[:300], measurement={"count": len(fs)}, feature_ids=[_fid(f) for f in fs],
                    strength="moderate", confidence_note="listed places (points of interest), not seen in imagery")
        return env, fs
    if s.op == "search_providers":
        from groundtruth.places import build_providers, search_places
        provs = build_providers()
        usable = [n for n, pr in provs.items() if pr.status()["enabled"] and _display_allowed(pr)]
        hidden = [n for n, pr in provs.items() if pr.status()["enabled"] and not _display_allowed(pr)]
        for n in hidden:  # e.g. Yelp: enabled for testing, but its display requirements are not met → never shown
            note = f"{n.title()} listings are not shown: {(provs[n].pconf.get('display_blocker') or 'display requirements not met')}"
            if note not in inv.plan.limitations:
                inv.plan.limitations.append(note)
        if not usable:
            s.detail = "skipped: no place-provider key is installed (Foursquare / Yelp); Google is disabled by policy"
            raise _Skip(s.detail)
        area = {k: getattr(a, k) for k in ("geojson", "bbox", "place") if getattr(a, k)}
        env = search_places(a.query, area, providers=usable, investigation_id=inv.id)
        if env.status == "declined":
            s.detail = f"skipped: {env.declined_reason}"
            raise _Skip(s.detail)
        fs = env.result.geojson["features"]
        s.detail = f"{len(fs)} listings from {', '.join(usable)}"
        _add_ev(inv, s, "detection", f"{len(fs)} {a.query} listings from {', '.join(u.title() for u in usable)} inside the area.",
                source={"name": " / ".join(u.title() for u in usable), "url": "", "date": time.strftime("%Y-%m-%d"),
                        "licence": "provider terms (docs/compliance/API_TERMS_REVIEW.md)"},
                method="provider search, validated against the area geometry, deduplicated across providers",
                measurement={"listings": len(fs)}, feature_ids=[_fid(f) for f in fs], strength="weak",
                confidence_note="provider-reported; several providers may share upstream data, so agreement is not verification",
                limitations=list(env.limits)[-3:])
        return env, None  # supporting evidence only: it never replaces the mapped set

    if s.op == "filter":
        # denominator integrity: for a MEASURED condition, measure every input first so the step can report
        # passing / failing / unknown separately (unknown is never counted as passing or failing)
        measured = next(((k, *_MEASURED[k]) for k in a.where if k in _MEASURED), None)
        if measured and any(f["properties"].get(measured[1]) is None for f in src
                            if f["properties"].get("kind") not in ("dock", "dock_link")):
            ann = eng.explain(_fc(src), facts=[measured[2]])
            if ann.status != "declined":
                src = ann.result.geojson["features"]
        env = eng.filter(_fc(src), a.where)
        if env.status == "declined":
            return env, None
        fs = env.result.geojson["features"]
        prim_in = [f for f in src if f["properties"].get("kind") not in ("dock", "dock_link")]
        prim_out = [f for f in fs if f["properties"].get("kind") not in ("dock", "dock_link")]
        if "has_dock" in json.dumps(inv.plan.steps[0].args) or any(f["properties"].get("kind") == "dock" for f in src):
            keep = {_fid(f) for f in prim_out}
            fs = [f for f in fs if f["properties"].get("kind") not in ("dock", "dock_link") or str(f["properties"].get("house_id")) in keep]
        kind, ev_kind, srcname = _filter_kind(a.where)
        lims = list(env.limits)[-4:]
        strength = "strong" if ev_kind == "spatial_intersection" else "moderate"
        if measured:
            prop, label = measured[1], measured[3]
            known = [f for f in prim_in if f["properties"].get(prop) is not None]
            unknown = len(prim_in) - len(known)
            passing, failing = len(prim_out), len(known) - len(prim_out)
            s.detail = f"{passing} pass · {failing} fail · {unknown} unknown (of {len(prim_in)})"
            stmt = (f"{passing} of the {len(known)} with a known {label} {kind}"
                    + (f"; {unknown} of {len(prim_in)} have no {label} and stay unknown." if unknown else "."))
            meas = {"total": len(prim_in), "known": len(known), "passing": passing, "failing": failing, "unknown": unknown,
                    "kept": passing, "of": len(prim_in)}
            if unknown:
                lims.append(f"{unknown} have no {label}: they are neither counted as passing nor as failing")
        else:
            nc = sum(1 for f in prim_in if f["properties"].get("flood_covered") is False) if "in_flood_zone" in a.where else 0
            s.detail = f"{len(prim_out)} of {len(prim_in)}" + (f" ({nc} outside the mapped area: unknown)" if nc else "")
            stmt = f"{len(prim_out)} of {len(prim_in)} {kind}." + (f" {nc} lie outside FEMA's mapped area: unknown." if nc else "")
            meas = {"kept": len(prim_out), "of": len(prim_in), **({"unknown": nc} if nc else {})}
            if "in_flood_zone" in a.where:  # the FEMA maps these results were judged against, by effective date
                eff: dict[str, int] = {}
                for f in prim_out:
                    k = f["properties"].get("flood_map_effective") or "unrecorded"
                    eff[k] = eff.get(k, 0) + 1
                if eff:
                    meas["flood_map_effective"] = dict(sorted(eff.items()))
                    lims.append("FEMA flood maps (FIRM panels) in effect for these results: "
                                + ", ".join(f"{d} ({n})" for d, n in sorted(eff.items())))
        _add_ev(inv, s, ev_kind, stmt, source=_src(env, srcname), method=env.method[:300], measurement=meas,
                feature_ids=[_fid(f) for f in prim_out], strength=strength,
                confidence_note="a geometric test against the published layer" if ev_kind == "spatial_intersection" else
                "measured from lidar / layers; see each result's facts", limitations=lims)
        return env, fs
    if s.op == "explain":
        env = eng.explain(_fc(src), facts=a.facts, label=a.label, unit=a.unit)
        if env.status == "declined":
            return env, None
        fs = env.result.geojson["features"]
        m = env.result.metrics
        s.detail = "; ".join(m.get("insights") or [])[:200]
        prim = [f for f in fs if f["properties"].get("kind") not in ("dock", "dock_link")]
        if "elevation" in a.facts:
            st = {}
            for f in prim:
                k = f["properties"].get("elev_bfe_status") or "unknown"
                st[k] = st.get(k, 0) + 1
            known = sum(1 for f in prim if f["properties"].get("elev_above_bfe_m") is not None)
            # name the set: after a filter this describes what the filter kept, not the set the filter started from
            kept = any(x.op == "filter" for x in inv.plan.steps if x.id in s.depends_on)
            setname = f"the {len(prim)} kept by the filter" if kept else f"{len(prim)}"
            _add_ev(inv, s, "measurement", f"Height above the base flood elevation is known for {known} of {setname}.",
                    source=_src(env, "3DEP"), method="lidar ground (lowest 10 % within 2 m of the outline) minus FEMA's static BFE",
                    measurement={"known": known, "of": len(prim), "unavailable": len(prim) - known,
                                 **{f"status_{k}": v for k, v in st.items()}},
                    feature_ids=[_fid(f) for f in prim if f["properties"].get("elev_above_bfe_m") is not None],
                    strength="moderate" if known else "insufficient",
                    confidence_note="lidar 2016–2019 vs FEMA effective maps; ground height, not floor height",
                    limitations=["FEMA publishes a base flood elevation for very few polygons; where it is missing the "
                                 "height is unavailable, never estimated", "Ground next to the building stands in for the "
                                 "lowest floor: a raised house can be safer than this suggests"])
        if "shore" in a.facts:
            vals = sorted(f["properties"]["shore_dist_m"] for f in prim if f["properties"].get("shore_dist_m") is not None)
            if vals:
                _add_ev(inv, s, "measurement", f"Median distance to the shore: {vals[len(vals) // 2]:.0f} m.", source=_src(env, "OpenStreetMap"),
                        method="distance from the outline to the OSM water edge (EPSG:32617)",
                        measurement={"median_shore_dist_m": round(vals[len(vals) // 2], 1), "n": len(vals)},
                        feature_ids=[], strength="strong", confidence_note="OSM shoreline; lake level varies by season")
        return env, fs
    if s.op == "compare_years":
        exs = [f["properties"].get("example") for f in src if f["properties"].get("example")]
        if not exs:
            raise ValueError("these results have no imagery patch to compare (only detected objects can be compared)")
        env = eng.compare_years(exs, from_year=a.from_year)
        if env.status == "declined":
            return env, None
        fs = env.result.geojson["features"]
        ch = {}
        for f in fs:
            k = f["properties"].get("change", "unsure")
            ch[k] = ch.get(k, 0) + 1
        s.detail = ", ".join(f"{v} {k}" for k, v in sorted(ch.items()))
        _add_ev(inv, s, "temporal", f"Compared with 2020: {s.detail}.", source=_src(env, "NAIP"), method=env.method[:300],
                measurement={f"change_{k}": v for k, v in ch.items()},
                feature_ids=[_fid(f) for f in fs if f["properties"].get("change") == "new"], strength="weak",
                confidence_note="two acquisitions only; seasonal light and misalignment can look like change",
                limitations=["Two imagery years (2020, 2022), not a history; 'unsure' is kept apart, not counted as new"])
        return env, fs
    if s.op == "rank":
        rule = conf()["rank_rule"]["keys"]
        prim = [f for f in src if f["properties"].get("kind") not in ("dock", "dock_link")]
        rest = [f for f in src if f["properties"].get("kind") in ("dock", "dock_link")]

        def key(f):
            p = f["properties"]
            out = []
            for k in rule:
                v = p.get(k["prop"])
                if "order" in k:
                    out.append(k["order"].index(v) if v in k["order"] else len(k["order"]))
                else:
                    out += [v is None, float(v) if v is not None else 0.0]
            return (*out, _fid(f))

        prim = sorted(prim, key=key)
        for i, f in enumerate(prim):
            f["properties"]["rank"] = i + 1
        s.detail = "ordered by " + ", then ".join(k["label"] for k in rule)
        _add_ev(inv, s, "ordering", f"Results are ordered by {', then '.join(k['label'] for k in rule)}. "
                "This is an ordering, not a risk score.", method="stable sort on measured facts; missing values last",
                measurement={"n": len(prim)}, feature_ids=[_fid(f) for f in prim[:conf()["limits"]["top_findings"]]],
                strength="strong", confidence_note="deterministic; inherits the uncertainty of the facts it sorts on")
        return None, prim + rest
    if s.op == "simulate":
        return _op_simulate(s, a, inv)
    if s.op == "exposure":
        return _op_exposure(s, a, inv, eng, feats)
    if s.op == "cluster":
        return _op_cluster(s, a, inv, feats)
    if s.op == "conclude":
        conclude(inv, feats, envs)
        s.detail = f"{len(inv.findings)} findings"
        return None, None
    raise ValueError(f"no executor for {s.op}")


_SIM_SRC = {"name": "Groundtruth scenario engine (SIMULATED) on USGS 3DEP lidar 2016 + FEMA static BFE", "url": "",
            "date": "lidar acquired 2016; FEMA panels effective 2009-03-02 to 2018-11-16", "licence": "derived from public-domain inputs"}


def _sim_extent(inv: Investigation, feats) -> tuple[list[dict[str, Any]], Any]:
    from groundtruth import exposure as X

    sid = next((x.id for x in inv.plan.steps if x.op == "simulate"), None)
    ext = feats.get(sid) or []
    return ext, X.union_extent(ext)


def _op_simulate(s: PlanStep, a: SimulateArgs, inv: Investigation):
    from groundtruth import scenario as S

    try:
        m = S.meta(a.scenario)
    except FileNotFoundError:
        raise _Skip(f"the scenario {a.scenario} has not been built on this machine (scripts/build_scenarios.py); "
                    "nothing is simulated") from None
    lv = next((x for x in m["levels"] if abs(float(x["offset_m"]) - a.offset_m) < 1e-6), None)
    if lv is None:
        raise ValueError(f"level +{a.offset_m:g} m is not built for {a.scenario}")
    ext = S.extent(a.scenario, a.offset_m)["features"]
    d = m["definition"]
    s.detail = (f"SIMULATED water surface {lv['level_m']:.2f} m {d['reference']['datum']} (FEMA BFE + {a.offset_m:g} m): "
                f"{lv['land_area_m2'] / 1e6:.2f} km² of land connected to the lake")
    lims = list(d["assumptions"])
    if m.get("unmeasured_land_m2"):
        lims.append(f"{m['unmeasured_land_m2'] / 1e6:.2f} km² of land has no lidar ground return: its outcome is unknown, not dry")
    cons = next((x.get("fema_consistency") for x in m["levels"] if x.get("fema_consistency")), None)
    _add_ev(inv, s, "simulation", f"Hypothetical: if Lake Norman stood at {lv['level_m']:.2f} m {d['reference']['datum']} "
            f"({a.offset_m:g} m above FEMA's base flood elevation of {d['reference']['value_m']} m), "
            f"{lv['land_area_m2'] / 1e6:.2f} km² of land connected to the lake would lie below that level.",
            source=_SIM_SRC, method=f"connected bathtub on the lidar ground model at {m['grid_m']} m: cells at or below the "
            "level that connect to the lake through other such cells; unmeasured cells never wet",
            measurement={"level_m": lv["level_m"], "offset_m": a.offset_m, "land_area_m2": lv["land_area_m2"],
                         "unmeasured_land_m2": m.get("unmeasured_land_m2"), "is_synthetic": True,
                         **({"fema_consistency_at_bfe": cons} if cons else {})},
            feature_ids=[], strength="moderate", limitations=lims,
            confidence_note="a simplified exposure scenario, not a hydraulic or rainfall model")
    if cons:
        inv.plan.limitations.append(f"Method check: at FEMA's BFE the bathtub covers {cons['share_of_fema_fringe_covered'] * 100:.0f} % "
                                    f"of FEMA's mapped lake fringe (IoU {cons['iou']:.2f}); a plausibility check, not a validation")
    return None, ext


def _op_exposure(s: PlanStep, a: ExposureArgs, inv: Investigation, eng, feats):
    from groundtruth import exposure as X
    from groundtruth import risk as R
    from groundtruth import scenario as S

    ext, eg = _sim_extent(inv, feats)
    if eg is None:
        raise _Skip("no scenario extent to compare with")
    sim = next(x for x in inv.plan.steps if x.op == "simulate").args
    bb = eg.bounds
    if a.target == "buildings":
        raw = [{"type": "Feature", "geometry": __import__("shapely.geometry", fromlist=["mapping"]).mapping(b["geom"]),
                "properties": {"id": b["id"], "class": b.get("class"), "kind": "building"}} for b in S._buildings(bb)]
        hit = X.touching(eg, raw)
        for f, fact in zip(hit, R.flood_facts(hit) if hit else []):
            f["properties"].update(fact)
        in100 = sum(1 for f in hit if f["properties"].get("flood_kind") == "100yr")
        s.detail = f"{len(hit)} buildings touch the extent; {in100} also in FEMA's 100-year zone"
        _add_ev(inv, s, "spatial_intersection", f"{len(hit)} buildings (Overture footprints) touch the hypothetical extent at "
                f"+{sim['offset_m']:g} m; {in100} of them are already inside FEMA's mapped 100-year zone and "
                f"{len(hit) - in100} are outside it.", source={"name": "Overture Maps buildings 2026-10-21.0 × scenario extent; FEMA NFHL",
                "url": "", "date": "Overture release 2026-10-21; FEMA effective 2009–2018", "licence": "ODbL / public domain"},
                method="footprint intersects the simulated extent (EPSG:4326 geometry test); FEMA relation per footprint",
                measurement={"kept": len(hit), "of": len(raw), "in_fema_100yr": in100, "outside_fema_100yr": len(hit) - in100,
                             "is_synthetic_outcome": True},
                feature_ids=[str(f["properties"]["id"]) for f in hit], strength="moderate",
                limitations=["Touching the extent is potential exposure under the scenario's assumptions, not a prediction that "
                             "a building floods: floor heights, foundations and drainage are unknown",
                             "Building footprints are not parcel boundaries"])
        return None, hit
    if a.target == "roads":
        p = config.DATA / "layers" / "osm_major_roads.geojson"
        raw = json.loads(p.read_text())["features"] if p.exists() else []
        hit = X.touching(eg, raw, measure_length=True)
        for f in hit:
            f["properties"]["kind"] = "road"
        tot = round(sum(f["properties"]["length_in_extent_m"] for f in hit))
        s.detail = f"{len(hit)} major-road segments, {tot} m inside the extent"
        _add_ev(inv, s, "spatial_intersection", f"{len(hit)} major-road segments (OSM) cross the hypothetical extent, "
                f"{tot:,} m of road in total.", source={"name": "OpenStreetMap major roads × scenario extent", "url": "",
                "date": "OSM extract 2026-11-06", "licence": "ODbL"}, method="line ∩ extent, length in EPSG:32617",
                measurement={"kept": len(hit), "length_in_extent_m": tot, "is_synthetic_outcome": True},
                feature_ids=[str(f["properties"].get("osm_id") or "") for f in hit], strength="moderate",
                limitations=["Only major roads (OSM) are assessed; local streets are not", "A road crossing the extent is "
                             "potentially impassable under the assumptions; road and bridge elevations are not modelled"])
        return None, hit
    layer = "community_bridges" if a.target == "bridges" else "community_facilities"
    try:
        env = eng.provider.get_layer(layer, tuple(bb)) if a.target == "bridges" else eng.provider.get_layer(layer, None)
        raw = (env or {}).get("features", []) if isinstance(env, dict) else getattr(env, "features", None) or []
    except Exception:  # noqa: BLE001 (layer not built: unknown, never "none")
        raw = []
    if not raw:
        raise _Skip(f"the {a.target} layer (OpenStreetMap community infrastructure, data/community/v1) is not built on "
                    f"this machine: {a.target} exposure is unknown, not zero")
    hit = X.touching(eg, raw, measure_length=True) if a.target == "bridges" else X.near(eg, raw, a.within_m)
    n_touch = sum(1 for f in hit if f["properties"].get("dist_to_extent_m", 0) == 0)
    s.detail = f"{len(hit)} {a.target}" + (f" within {a.within_m:g} m ({n_touch} touching)" if a.target == "facilities" else "")
    _add_ev(inv, s, "spatial_intersection" if a.target == "bridges" else "spatial_proximity",
            (f"{len(hit)} bridges (OSM) cross the hypothetical extent." if a.target == "bridges" else
             f"{len(hit)} community facilities (OSM) lie within {a.within_m:g} m of the hypothetical extent; {n_touch} touch it."),
            source={"name": "OpenStreetMap community infrastructure × scenario extent", "url": "", "date": "OSM extract 2026-11-06",
                    "licence": "ODbL"}, method="geometry test in EPSG:32617",
            measurement={"kept": len(hit), "touching": n_touch, "is_synthetic_outcome": True},
            feature_ids=[str(f["properties"].get("osm_id") or "") for f in hit], strength="moderate",
            limitations=["Proximity is not impact", "OpenStreetMap completeness for facilities is not verified here"])
    return None, hit


def _op_cluster(s: PlanStep, a: ClusterArgs, inv: Investigation, feats):
    from groundtruth import exposure as X

    ops = {x.id: x for x in inv.plan.steps}
    def of(target):
        sid = next((i for i, x in ops.items() if x.op == "exposure" and x.args.get("target") == target), None)
        return feats.get(sid) or []
    bld, roads, fac = of("buildings"), of("roads"), of("facilities")
    cl = X.clusters(bld, link_m=a.link_m, min_size=a.min_size, roads=roads, facilities=fac)
    s.detail = f"{len(cl)} clusters of ≥ {a.min_size} exposed buildings" + (f"; largest {cl[0]['properties']['n_buildings']}" if cl else "")
    in_cl = sum(c["properties"]["n_buildings"] for c in cl)
    _add_ev(inv, s, "spatial_intersection", f"{len(cl)} clusters hold {in_cl} of the {len(bld)} exposed buildings"
            + (f"; the largest (C1) has {cl[0]['properties']['n_buildings']}." if cl else "."),
            source=_SIM_SRC, method=f"single-linkage grouping of footprints within {a.link_m:g} m (EPSG:32617), groups of ≥ "
            f"{a.min_size}; ranked by building count, then major-road length (an ordering, not a risk score)",
            measurement={"kept": len(cl), "count": len(cl), "buildings_in_clusters": in_cl, "of": len(bld), "is_synthetic_outcome": True},
            feature_ids=[c["properties"]["cluster_id"] for c in cl], strength="moderate",
            limitations=["Clusters inherit the scenario's assumptions"])
    return None, cl + bld + roads + fac


# measured predicates: where key → (feature property, explain fact, words)
_MEASURED = {
    "elevation_above_flood_m": ("elev_above_bfe_m", "elevation", "height above the base flood elevation"),
    "within_m_of_shore": ("shore_dist_m", "shore", "shore distance"),
    "canopy_over_pct": ("canopy_over_pct", "canopy", "canopy measurement"),
    "slope_deg": ("slope_deg", "slope", "slope measurement"),
    "height_m": ("height_m", "height", "height"),
}


_MODEL_OPS = {"find_derived", "find_concept", "find_like", "compare_years"}  # imagery detectors / comparisons
_LISTING_OPS = {"find_places", "search_providers"}  # listed places (Overture / commercial providers)


def _coverage_needs(plan: InvestigationPlan) -> set[str]:
    """Which coverage items matter for this plan (a restaurant search doesn't need lidar or homes-with-pools)."""
    ops = {s.op for s in plan.steps}
    facts = {f for s in plan.steps if s.op == "explain" for f in s.args.get("facts", [])}
    where = {k for s in plan.steps if s.op == "filter" for k in s.args.get("where", {})}
    need = set()
    if ops & {"find_derived", "find_concept", "find_like"}:
        need.add("imagery 2022")
    if "compare_years" in ops:
        need.add("imagery 2020")
    if "find_derived" in ops:
        need.add("homes with pools")
    if facts & {"elevation", "canopy", "slope", "height"} or where & {"elevation_above_flood_m", "canopy_over_pct", "slope_deg", "height_m"}:
        need.add("lidar")
    if "flood" in facts or "in_flood_zone" in where or "elevation_above_flood_m" in where or "simulate" in ops:
        need.add("flood maps")
    if "simulate" in ops:
        need |= {"lidar"}
    return need


def _filter_kind(where: dict[str, Any]) -> tuple[str, str, str]:
    if "in_flood_zone" in where:
        k = (where["in_flood_zone"] or {}).get("kind", "any") if isinstance(where["in_flood_zone"], dict) else where["in_flood_zone"]
        return {"100yr": "touch a FEMA 100-year flood zone", "500yr": "touch a FEMA 100- or 500-year flood zone",
                "floodway": "touch a FEMA regulatory floodway"}.get(k, "touch a FEMA flood zone"), "spatial_intersection", "FEMA"
    if "within_m_of" in where:
        w = where["within_m_of"] if isinstance(where["within_m_of"], dict) else (where["within_m_of"] or [{}])[0]
        return f"are within {float(w.get('m', 0)):g} m of {str(w.get('layer', 'the layer')).replace('_', ' ')}", "spatial_proximity", \
            ("OpenStreetMap" if str(w.get("layer", "")).startswith(("osm_", "community_")) else "")
    if "slope_deg" in where:
        return f"sit on slopes steeper than {float(where['slope_deg'].get('min', 0)):g}°", "measurement", "3DEP"
    if "elevation_above_flood_m" in where:
        spec = where["elevation_above_flood_m"]
        w = f"less than {spec['lt']:g} m" if spec.get("lt") is not None else f"more than {spec.get('gt', spec.get('min', 0)):g} m"
        return f"sit {w} above the base flood elevation", "measurement", "3DEP"
    if "within_m_of_shore" in where:
        return f"are within {where['within_m_of_shore']} m of the shore", "spatial_intersection", "OpenStreetMap"
    if "canopy_over_pct" in where:
        return "are partly under tree canopy", "measurement", "3DEP"
    if "within_minutes_of" in where:
        w = where["within_minutes_of"]
        return (f"are more than {w.get('minutes'):g} minutes from the nearest {w.get('category')}" if w.get("outside") else
                f"are within {w.get('minutes'):g} minutes of {w.get('category')}"), "measurement", "Valhalla"
    if "height_m" in where:
        return "meet the height condition", "measurement", "Overture"
    return "meet the condition", "spatial_intersection", ""


def coverage(eng, a: AreaArgs) -> dict[str, dict[str, Any]]:
    """What data exists for this area (no guessing): imagery years, lidar, precomputed homes-with-pools, flood maps."""
    from groundtruth import risk as R

    bb = a.bbox or eng.place_bbox(a.place) or []
    out: dict[str, dict[str, Any]] = {}
    out["imagery 2022"] = {"covered": True, "why": ""}
    try:
        n20 = len(eng.provider.list_scenes(tuple(bb), 2020)) if bb else 0
        local = all(eng.provider.local_scene_path(sc.scene_id, 2020).exists() for sc in eng.provider.list_scenes(tuple(bb), 2020)) if n20 else False
    except Exception:  # noqa: BLE001
        n20, local = 0, False
    out["imagery 2020"] = {"covered": bool(n20 and local), "why": "2020 imagery is cached only for the demo neighbourhoods"}
    from shapely.geometry import box

    from groundtruth.geo import to_measure
    lp = R.lidar_places()
    area_m = to_measure(box(*bb)) if bb else None
    hit = [p[0] for p in lp if area_m is not None and area_m.intersects(box(*p[1]))]
    out["lidar"] = {"covered": bool(hit), "why": "" if hit else f"lidar heights are built for {len(lp)} areas; not this one",
                    "areas": hit}
    pk = (a.place or "").replace(" ", "_")
    dd = config.data_dir("derived") / pk
    out["homes with pools"] = {"covered": bool(a.place) and (dd / "houses.geojson").exists(),
                               "why": "homes with pools are precomputed only for Lake Norman"}
    out["flood maps"] = {"covered": R.available().get("flood", False), "why": "FEMA NFHL covers Mecklenburg County only"}
    return out


def conclude(inv: Investigation, feats, envs) -> None:
    """Findings from the evidence: each states what was computed, its nature (observed / computed / model prediction /
    gap) and a strength = its weakest supporting evidence. Numbers are copied from evidence measurements only."""
    ev = inv.evidence
    findings: list[Finding] = []
    chain: list[Evidence] = []
    for e in ev:
        if e.kind in ("detection", "association", "spatial_intersection", "spatial_proximity", "simulation", "measurement", "temporal"):
            chain.append(e)
    # the headline: the last count-bearing evidence, supported by everything before it in the chain
    counted = [e for e in chain if "kept" in e.measurement or "homes" in e.measurement or "count" in e.measurement]
    if counted:
        head = counted[-1]
        sup = chain[: chain.index(head) + 1]
        weakest = min(sup, key=lambda e: _RANK[e.strength])
        # a count computed over AI-detected objects is still an AI prediction (never shown as a plain fact)
        ops = {st.id: st.op for st in inv.plan.steps}
        on_model = any(e.kind == "detection" and ops.get(e.step_id) in _MODEL_OPS for e in sup)
        listed = ops.get(head.step_id) in _LISTING_OPS
        simulated = any(e.kind == "simulation" or e.measurement.get("is_synthetic_outcome") for e in sup + [head])
        nature = "simulated" if simulated else "model_prediction" if on_model else ("provider_reported" if listed else "computed")
        findings.append(Finding(id="f1", statement=head.statement, nature=nature,
                                strength=weakest.strength, evidence_ids=[e.id for e in sup], feature_ids=head.feature_ids[:2000],
                                why_strength=_why_strength(weakest)))
    for e in ev:  # every scenario step is its own finding, labelled SIMULATED
        if (e.kind == "simulation" or e.measurement.get("is_synthetic_outcome")) and e is not (counted[-1] if counted else None):
            findings.append(Finding(id=f"f{len(findings) + 1}", statement=e.statement, nature="simulated", strength=e.strength,
                                    evidence_ids=[e.id], feature_ids=e.feature_ids[:2000], why_strength=_why_strength(e)))
    for e in chain:
        if e.kind == "measurement" and e is not (counted[-1] if counted else None):
            findings.append(Finding(id=f"f{len(findings) + 1}", statement=e.statement, nature="computed", strength=e.strength,
                                    evidence_ids=[e.id], feature_ids=e.feature_ids[:2000], why_strength=_why_strength(e)))
    ops = {st.id: st.op for st in inv.plan.steps}
    for e in ev:
        if ops.get(e.step_id) == "search_providers" and e.kind == "detection":
            findings.append(Finding(id=f"f{len(findings) + 1}", statement=e.statement, nature="provider_reported",
                                    strength=e.strength, evidence_ids=[e.id], feature_ids=e.feature_ids[:2000],
                                    why_strength=_why_strength(e)))
    gaps = [g for e in ev if e.kind == "coverage" for g in e.limitations]
    gaps += [x.detail for x in inv.plan.steps if x.status == "skipped" and x.op in ("simulate", "exposure") and x.detail]
    unav = [e for e in ev if e.measurement.get("known") is not None and e.measurement.get("known", 0) < e.measurement.get("of", 0)]
    for e in unav:
        miss = e.measurement.get("unavailable", e.measurement["of"] - e.measurement["known"])
        findings.append(Finding(id=f"f{len(findings) + 1}", statement=f"{miss} of {e.measurement['of']} have no published base "
                                "flood elevation here, so their height above it is unavailable.", nature="gap",
                                strength="insufficient", evidence_ids=[e.id], why_strength="FEMA did not publish the input"))
    for g in gaps:
        findings.append(Finding(id=f"f{len(findings) + 1}", statement=g[:1].upper() + g[1:] + ".", nature="gap",
                                strength="insufficient", evidence_ids=[x.id for x in ev if x.kind == "coverage"],
                                why_strength="data not available for this area"))
    inv.findings = findings
    # contradictions and cross-checks (recorded, never silently resolved)
    fin = None
    for sid in reversed([s.id for s in inv.plan.steps]):
        if sid in feats:
            fin = feats[sid]
            break
    if fin:
        nc = sum(1 for f in fin if f["properties"].get("flood_covered") is False)
        if nc:
            inv.contradictions.append(f"{nc} results lie outside FEMA's mapped area: their flood zone is unknown, not 'none'")
        newboth = sum(1 for f in fin if f["properties"].get("change") == "new" and f["properties"].get("present_both_years"))
        if newboth:
            inv.contradictions.append(f"{newboth} marked new also look present in 2020")
    lid = [e for e in ev if e.source.get("name", "").startswith("USGS 3DEP")]
    if lid:
        inv.contradictions.append("Different dates: lidar heights are from 2016–2019, imagery from 2022, flood maps "
                                  f"{_fema_effective()}; a change after the lidar flight is not reflected")
    inv.summary = _summary(inv)


def _fema_effective() -> str:
    """FEMA FIRM effective dates from the source registry (fema_nfhl.effective_date), never a fetch or file date."""
    import re as _re
    try:
        from pathlib import Path as _P
        rows = json.loads((_P(__file__).parent / "conf" / "sources.json").read_text())["sources"]
        eff = next(r for r in rows if r["source_id"] == "fema_nfhl").get("effective_date") or ""
    except (OSError, ValueError, KeyError, StopIteration):
        eff = ""
    m = _re.search(r"effective (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})", eff)
    return f"effective {m.group(1)} to {m.group(2)} (FEMA FIRM panels)" if m else "with an unrecorded FEMA effective date"


def _predicted(inv: Investigation, depth: int = 0) -> bool:
    """Are the objects AI-detected? True if this plan ran a detector, or it narrows a previous one that did."""
    if inv.plan and any(x.op in _MODEL_OPS for x in inv.plan.steps):
        return True
    prev = get(inv.context.get("previous")) if inv.context.get("previous") and depth < 5 else None
    return bool(prev and _predicted(prev, depth + 1))


def _summary(inv: Investigation) -> str:
    """The headline, built only from evidence: what was investigated (scope + target), whether the objects are
    AI-detected candidates or verified, the count with its denominator, unknowns kept apart, the ranking rule, and the
    main limits. Never implies human verification unless human evidence exists."""
    f = inv.findings
    if not f:
        return "I couldn't establish anything here; see the steps for what was missing."
    plan = inv.plan
    scope = (plan.scope or {}).get("label") if plan else None
    target = plan.targets[0] if plan and plan.targets else "results"
    head = f[0]
    humans = [e for e in inv.evidence if e.kind == "human"]
    parts = []
    if plan and plan.unapplied:
        parts.append("Not applied: " + "; ".join(f"“{u}”" for u in plan.unapplied) + " (no data or tool for it yet), "
                     "so the results below ignore " + ("it." if len(plan.unapplied) == 1 else "them."))
    predicted = _predicted(inv)
    if predicted:
        verified = f"{len(humans)} reviewed by a person" if humans else "none human-verified"
        parts.append(f"Investigated {target}{f' around {scope}' if scope else ''}: AI-detected candidates ({verified}).")
    elif scope:
        parts.append(f"Investigated {target} in {scope}.")
    parts.append(head.statement)
    meas = [e for e in inv.evidence if e.measurement.get("unknown") or e.measurement.get("unavailable")]
    for e in meas[:1]:
        unk = e.measurement.get("unknown") or e.measurement.get("unavailable")
        total = e.measurement.get("total") or e.measurement.get("of")
        if f"{unk} of {total}" not in head.statement:  # the headline finding may already say it
            parts.append(e.statement.rstrip(".") + "; the rest are counted as unknown, not as passing or failing.")
    if any(s.op == "rank" for s in (plan.steps if plan else [])):
        rule = conf()["rank_rule"]["keys"]
        parts.append("Ordered by " + ", then ".join(k["label"] for k in rule) + "; this is an ordering, not a risk score.")
    lims = []
    if head.strength == "weak":
        lims.append("the detector's precision isn't measured for this data version")
    if inv.contradictions:
        lims.append("sources differ in date (lidar 2016–19, imagery 2022)" if any("lidar" in c for c in inv.contradictions)
                    else inv.contradictions[0])
    gaps = [x for x in f if x.nature == "gap"]
    if gaps:
        lims.append("1 data gap is listed below" if len(gaps) == 1 else f"{len(gaps)} data gaps are listed below")
    if lims:
        parts.append("Limits: " + "; ".join(lims) + ".")
    return " ".join(parts)


def _why_strength(e: Evidence) -> str:
    return {"strong": "computed from a published layer", "moderate": "a measured detector or a lidar measurement",
            "weak": "a detector whose precision is not measured for this data", "insufficient": "the input data is missing"}[e.strength] + \
        (f": {e.confidence_note}" if e.confidence_note else "")


def _model_strength(dq: dict[str, Any]) -> Strength:
    c = conf()["strength"]
    if dq.get("precision") is None:
        return c["unmeasured_model"]
    return c["measured_model_ok"] if dq["precision"] >= c["measured_model_min_precision"] else c["unmeasured_model"]


def _dq_note(dq: dict[str, Any]) -> str:
    if not dq:
        return "precision not measured"
    if dq.get("precision") is None:
        return f"precision not measured for this version ({dq.get('note', '')})"
    return f"measured precision {dq['precision']:.2f} on n={dq['n']} ({dq.get('note', '')})"


def _add_ev(inv: Investigation, s: PlanStep, kind: str, statement: str, **kw) -> None:
    inv.evidence.append(Evidence(id=f"e{len(inv.evidence) + 1}", step_id=s.id, kind=kind, statement=statement, **kw))


def _imagery_src(eng, env) -> dict[str, Any]:
    """The NAIP scene source for detections (from the envelope, else the provider's own source list)."""
    for x in list(getattr(env, "sources", []) or []) + list(getattr(eng.provider, "sources", lambda: [])() or []):
        if "naip" in x.name.lower() and "2022" in (x.name + x.date):
            return x.model_dump()
    return {"name": "NAIP 2022 (USDA)", "url": "", "date": "2022", "licence": "Public domain"}


def _src(env, prefer: str) -> dict[str, Any]:
    srcs = list(getattr(env, "sources", []) or [])
    pick = next((x for x in srcs if prefer.lower() in x.name.lower()), srcs[0] if srcs else None)
    return pick.model_dump() if pick is not None else {"name": prefer, "url": "", "date": "", "licence": ""}


def _fid(f: dict[str, Any]) -> str:
    p = f.get("properties") or {}
    return str(p.get("id") or p.get("example") or f.get("id") or "")


def _clip_to_scope(inv: Investigation, s: PlanStep, fs: list[dict[str, Any]], what: str) -> list[dict[str, Any]]:
    """Tools search a bounding box; keep only results inside the exact boundary (representative point), and record it."""
    g = (inv.plan.scope or {}).get("geojson") if inv.plan else None
    if not g or not fs:
        return fs
    from shapely.geometry import shape
    from shapely.prepared import prep
    poly = prep(shape(g))
    kept = [f for f in fs if f.get("geometry") and poly.covers(shape(f["geometry"]).representative_point())]
    if len(kept) != len(fs):
        _add_ev(inv, s, "spatial_intersection", f"{len(kept)} of {len(fs)} {what} found in the bounding box lie inside the "
                f"{inv.plan.scope.get('label')} boundary; the rest were dropped.", method="point-in-polygon (representative point)",
                measurement={"inside": len(kept), "in_bbox": len(fs)}, feature_ids=[], strength="strong",
                source={"name": inv.plan.scope.get("source") or "boundary", "url": "", "date": inv.plan.scope.get("source_date") or "", "licence": ""},
                confidence_note="exact boundary polygon")
    return kept


def _km2_geom(g) -> float:
    from shapely.geometry import shape

    from groundtruth.geo import to_measure
    return to_measure(shape(g)).area / 1e6


def _km2(bb) -> float:
    from shapely.geometry import box

    from groundtruth.geo import to_measure
    return to_measure(box(*bb)).area / 1e6


def feature_evidence(inv: Investigation, fid: str) -> dict[str, Any]:
    """Everything that supports one result: its facts, the evidence items that name it, and what is missing."""
    feats = _FEATS.get(inv.id, {})
    f = next((x for x in (feats.get(inv.result_step or "") or (inv.result or {}).get("features") or []) if _fid(x) == fid), None)
    if f is None:
        return {"found": False}
    p = f["properties"]
    evs = [e for e in inv.evidence if fid in e.feature_ids or not e.feature_ids]
    missing = []
    if "elev_bfe_status" in p and p.get("elev_above_bfe_m") is None:
        missing.append("height above the base flood elevation (FEMA published none here, or no lidar)")
    if p.get("flood_covered") is False:
        missing.append("flood zone (outside FEMA's mapped area)")
    return {"found": True, "id": fid, "why": p.get("why"), "properties": {k: v for k, v in p.items() if not isinstance(v, (list, dict))},
            "evidence": [e.model_dump() for e in evs], "missing": missing}


def report_markdown(inv: Investigation) -> str:
    """The investigation report: objective, scope, summary, findings with their evidence, sources, method, gaps,
    contradictions, reproducibility. Plain Markdown (exportable; no heavy dependency)."""
    L = [f"# Groundtruth investigation {inv.id}", "", f"**Objective:** {inv.objective}", "",
         f"**Scope:** {(inv.plan.scope or {}).get('label', 'county') if inv.plan else 'n/a'}  ",
         f"**Status:** {inv.status}  ", f"**Generated:** {time.strftime('%Y-%m-%d %H:%M %Z', time.localtime(inv.updated))}", "",
         "## Summary", "", inv.summary or "(none)", "", "## Findings", ""]
    evmap = {e.id: e for e in inv.evidence}
    for f in inv.findings:
        L.append(f"- **{f.statement}** _(nature: {f.nature}; strength: {f.strength} — {f.why_strength})_")
        for eid in f.evidence_ids:
            e = evmap.get(eid)
            if e:
                L.append(f"  - [{e.id}] {e.statement} — {e.source.get('name', '')} {e.source.get('date', '')}".rstrip())
    L += ["", "## Steps", "", "| # | Step | Status | Result | Time |", "|---|---|---|---|---|"]
    for s in (inv.plan.steps if inv.plan else []):
        L.append(f"| {s.id} | {s.label} | {s.status} | {(s.detail or s.error or '').replace('|', '/')[:120]} | "
                 f"{'' if s.ms is None else f'{s.ms:.0f} ms'} |")
    top = [f for f in (inv.result or {}).get("features", []) if f["properties"].get("kind") not in ("dock", "dock_link")][:20]
    if top:
        L += ["", "## Results (first 20 in order)", ""]
        for f in top:
            c = _centroid(f["geometry"])
            L.append(f"{f['properties'].get('rank', '-')}. {f['properties'].get('why') or _fid(f)} — {c[1]:.5f}, {c[0]:.5f}")
    srcs = {}
    for e in inv.evidence:
        if e.source.get("name"):
            srcs[e.source["name"]] = e.source
    L += ["", "## Sources", ""] + [f"- {s['name']} {s.get('date', '')} {('(' + s['licence'] + ')') if s.get('licence') else ''}".rstrip()
                                    for s in srcs.values()]
    lims = [x for e in inv.evidence for x in e.limitations] + (inv.plan.limitations if inv.plan else [])
    L += ["", "## Limitations and gaps", ""] + [f"- {x}" for x in dict.fromkeys(lims)]
    if inv.contradictions:
        L += ["", "## Cross-checks and contradictions", ""] + [f"- {x}" for x in inv.contradictions]
    L += ["", "## Reproducibility", "", f"- Investigation engine {inv.versions.get('investigate')}; data versions "
          f"{json.dumps(inv.versions.get('data', {}))}", "- Same objective + same data versions → same plan and results "
          "(deterministic engine, stable sort orders).", "",
          "_Homes are buildings, never their owners or residents. Flood zones are FEMA regulatory maps, not a forecast._"]
    return "\n".join(L) + "\n"


def _centroid(g) -> tuple[float, float]:
    from shapely.geometry import shape
    c = shape(g).centroid
    return (c.x, c.y)


def start(objective: str, context: dict[str, Any], engine_for: Callable[[int], Any], policy: Callable[[str], str | None],
          plan_hint=None, background: bool = True) -> Investigation:
    """Create, plan and (in a background thread) run an investigation. Policy declines run before anything else."""
    inv = new(objective, context)
    reason = policy(objective)
    if reason:
        inv.status, inv.declined_reason, inv.summary = "declined", reason, reason
        save(inv)
        return inv
    prev = get(context.get("previous")) if context.get("previous") else None
    inv.previous = prev.id if prev else None
    plan, info = compile_plan(objective, context, prev, base_plan=plan_hint)
    if plan is None:
        if info.get("question"):
            inv.status, inv.question, inv.suggestions = "needs_input", info["question"], info.get("suggestions", [])
            inv.summary = info["question"]
        elif info.get("fallback"):
            inv.status, inv.summary = "needs_input", "fallback:chat"
        else:
            inv.status, inv.summary = "failed", info.get("error", "could not plan this")
        save(inv)
        return inv
    if plan_hint is not None:
        plan.planner = getattr(plan_hint, "_brain", "rules")
    inv.plan = plan
    _event(inv, None, "plan", f"{len(plan.steps)} steps")
    save(inv)

    def go():
        run(inv, engine_for(2022), prev)
    if background:
        threading.Thread(target=go, daemon=True, name=f"inv-{inv.id}").start()
    else:
        go()
    return inv
