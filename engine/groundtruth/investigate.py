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
    "filter": {"args": FilterArgs, "label": "Apply a condition", "why": "keep only results that meet it"},
    "explain": {"args": ExplainArgs, "label": "Measure each result", "why": "the facts behind each result (its WHY line)"},
    "compare_years": {"args": CompareArgs, "label": "Compare with 2020", "why": "what changed between the two imagery years"},
    "rank": {"args": RankArgs, "label": "Order the results", "why": "a documented ordering, not an invented score"},
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
    kind: Literal["scope", "coverage", "detection", "association", "spatial_intersection", "measurement",
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
    nature: Literal["observed", "computed", "model_prediction", "inference", "gap"]
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

    # ---- scope: the composer's area chip > a place named in the objective > the previous investigation's scope
    area = ctx.get("area") or {}
    scope: dict[str, Any] | None = None
    if p.place:
        scope = {"place": p.place, "label": p.place}
    elif area.get("place"):
        scope = {"place": area["place"], "label": area.get("label") or area["place"]}
    elif area.get("bbox") or area.get("geojson"):
        scope = {k: area[k] for k in ("bbox", "geojson") if area.get(k)} | {"label": area.get("label") or "Your area"}
    elif follow and prev and prev.plan and prev.plan.scope:
        scope = prev.plan.scope

    # ---- constraints
    cons: list[tuple[str, dict[str, Any], str]] = []  # (fact, where, words)
    if _has(t, W["flood_100yr"]):
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
    flood_topic = _has(t, W["flood_any"] + W["flood_100yr"] + W["flood_500yr"])
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
        if homes:
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
    if facts or rank:
        last = add("explain", {"facts": facts or ["flood", "elevation", "shore"], "label": target_desc or None},
                   [last] if last and last != "prev" else [])
    if rank:
        last = add("rank", {"keys": [k["prop"] for k in c["rank_rule"]["keys"]]}, [last] if last and last != "prev" else [])
    if re.search(r"\b(since 20\d\d|new since|built since|changed|what changed|compare with 2020)\b", t) and \
            not p.docks_new_since and not homes:
        last = add("compare_years", {"from_year": 2020}, [last] if last and last != "prev" else [])
    add("conclude", {}, [s.id for s in steps])
    if follow:
        # follow-up steps read the previous set: mark the first data step's input explicitly
        for s in steps:
            if s.op in _INPUT_OPS and not s.depends_on:
                s.depends_on = []
        limitations.append("Starts from the previous investigation's results")
    try:
        plan = InvestigationPlan(objective=objective, scope=scope, targets=targets, constraints=[w for _, _, w in cons],
                                 time={"imagery": 2022, "compare": 2020 if any(s.op == "compare_years" for s in steps) else None},
                                 questions=_questions(target_desc, cons, flood_topic, rank), steps=steps,
                                 limitations=limitations)
    except ValidationError as e:
        if follow:  # follow-up input steps legitimately have no in-plan dependency
            plan = InvestigationPlan.model_construct(objective=objective, scope=scope, targets=targets,
                                                     constraints=[w for _, _, w in cons], time={"imagery": 2022},
                                                     questions=_questions(target_desc, cons, flood_topic, rank),
                                                     steps=steps, limitations=limitations, planner="rules")
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
            env, out = _exec(s, engine, src, inv, envs, feats)
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
        s.detail = f"{a.label or a.place or 'area'}: {_km2(bb):.1f} km²"
        _add_ev(inv, s, "scope", f"The search is bounded by {a.label or a.place or 'the drawn area'} ({_km2(bb):.1f} km²).",
                source={"name": "Groundtruth places (OSM Nominatim boundaries, ODbL)" if a.place else "Drawn by the user",
                        "url": "", "date": "", "licence": "ODbL" if a.place else ""},
                measurement={"bbox": [round(v, 6) for v in bb], "area_km2": round(_km2(bb), 2)}, strength="strong",
                confidence_note="a rectangle around the named area; edges are approximate")
        return None, None
    if s.op == "check_coverage":
        cov = coverage(eng, a)
        s.detail = "; ".join(f"{k}: {'yes' if v['covered'] else 'no'}" for k, v in cov.items())
        gaps = [v["why"] for v in cov.values() if not v["covered"]]
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
        fs = env.result.geojson["features"]
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
            s.detail = f"{len(fs)} places"
            _add_ev(inv, s, "detection", f"{len(fs)} {a.query} listed in Overture places.", source=_src(env, "Overture"),
                    method=env.method[:300], measurement={"count": len(fs)}, feature_ids=[_fid(f) for f in fs],
                    strength="moderate", confidence_note="listed places (points of interest), not seen in imagery")
        return env, fs
    if s.op == "filter":
        env = eng.filter(_fc(src), a.where)
        if env.status == "declined":
            return env, None
        fs = env.result.geojson["features"]
        prim_in = [f for f in src if f["properties"].get("kind") not in ("dock", "dock_link")]
        prim_out = [f for f in fs if f["properties"].get("kind") not in ("dock", "dock_link")]
        if "has_dock" in json.dumps(inv.plan.steps[0].args) or any(f["properties"].get("kind") == "dock" for f in src):
            keep = {_fid(f) for f in prim_out}
            fs = [f for f in fs if f["properties"].get("kind") not in ("dock", "dock_link") or str(f["properties"].get("house_id")) in keep]
        s.detail = f"{len(prim_out)} of {len(prim_in)}"
        risk = env.result.metrics.get("risk") or {}
        kind, ev_kind, srcname = _filter_kind(a.where)
        unmeasured = sum(int(v) for k, v in risk.items() if "unmeasured" in k or "unavailable" in k) if isinstance(risk, dict) else 0
        lims = list(env.limits)[-4:]
        strength = "strong" if ev_kind == "spatial_intersection" else "moderate"
        if unmeasured:
            lims.append(f"{unmeasured} could not be measured for this condition (never counted as passing)")
        _add_ev(inv, s, ev_kind, f"{len(prim_out)} of {len(prim_in)} {kind}.", source=_src(env, srcname), method=env.method[:300],
                measurement={"kept": len(prim_out), "of": len(prim_in), **({"unmeasured": unmeasured} if unmeasured else {})},
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
            _add_ev(inv, s, "measurement", f"Height above the base flood elevation is known for {known} of {len(prim)}.",
                    source=_src(env, "3DEP"), method="lidar ground (lowest 10 % within 2 m of the outline) minus FEMA's static BFE",
                    measurement={"known": known, "of": len(prim), **{f"status_{k}": v for k, v in st.items()}},
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
    if s.op == "conclude":
        conclude(inv, feats, envs)
        s.detail = f"{len(inv.findings)} findings"
        return None, None
    raise ValueError(f"no executor for {s.op}")


def _filter_kind(where: dict[str, Any]) -> tuple[str, str, str]:
    if "in_flood_zone" in where:
        k = (where["in_flood_zone"] or {}).get("kind", "any") if isinstance(where["in_flood_zone"], dict) else where["in_flood_zone"]
        return {"100yr": "touch a FEMA 100-year flood zone", "500yr": "touch a FEMA 100- or 500-year flood zone"}.get(
            k, "touch a FEMA flood zone"), "spatial_intersection", "FEMA"
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
        if e.kind in ("detection", "association", "spatial_intersection", "measurement", "temporal"):
            chain.append(e)
    # the headline: the last count-bearing evidence, supported by everything before it in the chain
    counted = [e for e in chain if "kept" in e.measurement or "homes" in e.measurement or "count" in e.measurement]
    if counted:
        head = counted[-1]
        sup = chain[: chain.index(head) + 1]
        weakest = min(sup, key=lambda e: _RANK[e.strength])
        findings.append(Finding(id="f1", statement=head.statement, nature="computed" if head.kind != "detection" else "model_prediction",
                                strength=weakest.strength, evidence_ids=[e.id for e in sup], feature_ids=head.feature_ids[:2000],
                                why_strength=_why_strength(weakest)))
    for e in chain:
        if e.kind == "measurement" and e is not (counted[-1] if counted else None):
            findings.append(Finding(id=f"f{len(findings) + 1}", statement=e.statement, nature="computed", strength=e.strength,
                                    evidence_ids=[e.id], feature_ids=e.feature_ids[:2000], why_strength=_why_strength(e)))
    gaps = [g for e in ev if e.kind == "coverage" for g in e.limitations]
    unav = [e for e in ev if e.measurement.get("known") is not None and e.measurement.get("known", 0) < e.measurement.get("of", 0)]
    for e in unav:
        miss = e.measurement["of"] - e.measurement["known"]
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
        inv.contradictions.append("Different dates: lidar heights are from 2016–2019, imagery from 2022, flood maps from FEMA's "
                                  "effective date; a change after the lidar flight is not reflected")
    inv.summary = _summary(inv)


def _summary(inv: Investigation) -> str:
    f = inv.findings
    if not f:
        return "I couldn't establish anything here; see the steps for what was missing."
    head = f[0].statement
    gaps = [x for x in f if x.nature == "gap"]
    return head + ((" 1 gap in the data is listed below." if len(gaps) == 1 else f" {len(gaps)} gaps in the data are listed below.")
                   if gaps else "")


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
