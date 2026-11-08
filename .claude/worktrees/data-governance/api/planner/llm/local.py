"""Local LLM adapter (OpenAI-compatible llama-server on the laptop, reached through the SSH tunnel).

Chat brain = policy → plan → execute → reply:
  1. policy (declines BEFORE any model call: people data never reaches the model)
  2. plan: the LLM fills the SAME plan schema as the rules planner, with JSON-schema-constrained decoding (closed
     enums for places, concepts, layers, categories) → validated → on timeout / error / invalid: the rules plan
  3. execute: the deterministic executor (groundtruth.planner) runs real tools on the plan
  4. reply: one or two sentences; the NUMBER GUARD drops it for the template reply unless every number in it appears
     in the step envelopes
Config (env): LOCAL_LLM_URL (default http://127.0.0.1:8081/v1), LOCAL_LLM_PLAN_TIMEOUT_S (4), LOCAL_LLM_REPLY_TIMEOUT_S (3).
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx

from groundtruth import config
from groundtruth.planner import HOSTS, LAYERS, Plan, load_senses

URL = os.environ.get("LOCAL_LLM_URL", "http://127.0.0.1:8081/v1")
PLAN_TIMEOUT = float(os.environ.get("LOCAL_LLM_PLAN_TIMEOUT_S", "4"))
REPLY_TIMEOUT = float(os.environ.get("LOCAL_LLM_REPLY_TIMEOUT_S", "3"))
BRAIN = "local LLM (Qwen3-4B, llama.cpp)"


def _enums() -> dict[str, list]:
    places = list(json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"])
    pc = json.loads((config.SENSES_PATH.parent / "places_categories.json").read_text())
    return {"places": places, "concepts": sorted(load_senses()), "layers": sorted(set(LAYERS.values())),
            "hosts": sorted(set(HOSTS.values())), "categories": sorted(pc["categories"]), "buildings": sorted(pc["buildings"])}


def plan_schema() -> dict[str, Any]:
    """Validation schema (all fields). Decoding uses decode_schema(): the target is constrained by its kind."""
    e = _enums()
    nul = lambda s: {"anyOf": [s, {"type": "null"}]}  # noqa: E731
    return {"type": "object", "additionalProperties": False,
            # only the target is required: the model emits just the fields that apply (17 tok/s on the laptop CPU,
            # so a short plan is the main latency lever)
            "required": ["target_kind", "target"],
            "properties": {
                "target_kind": nul({"enum": ["concept", "place_category", "buildings", "open", "derived", "canopy"]}),
                "target": nul({"type": "string", "maxLength": 60}),
                "place": nul({"enum": e["places"]}),
                "layer": nul({"enum": e["layers"]}),
                "within_m": nul({"type": "number", "minimum": 1, "maximum": 20000}),
                "inside_layer": {"type": "boolean"},
                "host_layer": nul({"enum": e["hosts"]}),
                "minutes": nul({"type": "number", "minimum": 1, "maximum": 60}),
                "mode": nul({"enum": ["drive", "walk", "bike"]}),
                "origin": nul({"type": "string", "maxLength": 80}),
                "min_height_m": nul({"type": "number", "minimum": 1, "maximum": 400}),
                "new_since": nul({"enum": [2012, 2014, 2016, 2018, 2020]}),
                "require": {"type": "array", "items": {"enum": ["lakefront", "has_dock"]}, "maxItems": 2},
                "sort": nul({"enum": ["pool_area"]}),
                "docks_new_since": nul({"enum": [2020]}),
                "follow_up": {"type": "boolean"}}}


def decode_schema() -> dict[str, Any]:
    """The decoding grammar: one branch per target_kind with its own closed target list (so a 4B model can't put a
    place in `target` or a concept under `buildings`), plus the optional fields."""
    e = _enums()
    base = plan_schema()["properties"]
    opt = {k: v for k, v in base.items() if k not in ("target_kind", "target")}
    branches = [("concept", {"enum": e["concepts"]}), ("derived", {"enum": ["house_with_pool"]}),
                ("place_category", {"enum": e["categories"]}), ("buildings", {"enum": e["buildings"]}),
                ("canopy", {"enum": ["trees"]}), ("open", {"type": "string", "minLength": 2, "maxLength": 60})]
    return {"anyOf": [{"type": "object", "additionalProperties": False, "required": ["target_kind", "target"],
                       "properties": {"target_kind": {"const": k}, "target": t, **opt}} for k, t in branches]}


SYSTEM = (
    "Turn a map request about Mecklenburg County, NC into a JSON plan. Output ONLY compact single-line JSON (no spaces "
    "or newlines). Omit fields that don't apply.\n"
    "Kinds: concept = a configured visual concept (target = its key: {concepts}); derived = 'houses with pools' "
    "(target house_with_pool); place_category = a place type (target: {categories}); buildings = building kinds "
    "(target: {buildings}); canopy = tree height (target trees); open = anything else visible from above (target = the "
    "thing as typed). Places: {places}. Layers: {layers}.\n"
    "If PREVIOUS is given and the REQUEST only adds a place or a condition, set follow_up true and COPY the previous "
    "target_kind and target, then add the new fields. A place name is never a target.\n"
    "Examples:\n"
    'REQUEST: houses with pools → {{"target_kind":"derived","target":"house_with_pool"}}\n'
    'PREVIOUS: houses with pools REQUEST: in Lake Norman → {{"target_kind":"derived","target":"house_with_pool","place":"Lake Norman","follow_up":true}}\n'
    'PREVIOUS: houses with pools in Lake Norman REQUEST: with lake and dock access → {{"target_kind":"derived","target":"house_with_pool","place":"Lake Norman","require":["lakefront","has_dock"],"follow_up":true}}\n'
    'PREVIOUS: houses with pools in Lake Norman with lake and dock access REQUEST: show the biggest ones → {{"target_kind":"derived","target":"house_with_pool","place":"Lake Norman","require":["lakefront","has_dock"],"sort":"pool_area","follow_up":true}}\n'
    'REQUEST: pools within 1 km of I-485 in Ballantyne → {{"target_kind":"concept","target":"swimming_pool","place":"Ballantyne","layer":"osm_i485","within_m":1000}}\n'
    'REQUEST: pools in flood zones in Matthews → {{"target_kind":"concept","target":"swimming_pool","place":"Matthews","layer":"fema_flood_zones","inside_layer":true}}\n'
    'REQUEST: restaurants near the light rail → {{"target_kind":"place_category","target":"restaurant","layer":"osm_light_rail","within_m":500}}\n'
    'REQUEST: houses within 10 minutes of Ardrey Kell High School → {{"target_kind":"buildings","target":"house","minutes":10,"mode":"drive","origin":"Ardrey Kell High School"}}\n'
    'REQUEST: buildings taller than 30 m in Uptown → {{"target_kind":"buildings","target":"building","place":"Uptown","min_height_m":30}}\n'
    'REQUEST: boat docks on Lake Norman → {{"target_kind":"open","target":"boat docks","place":"Lake Norman"}}\n'
    'REQUEST: how tall are the trees in Myers Park → {{"target_kind":"canopy","target":"trees","place":"Myers Park"}}\n'
    'REQUEST: new pools since 2020 in Ballantyne → {{"target_kind":"concept","target":"swimming_pool","place":"Ballantyne","new_since":2020}}'
)


def _system() -> str:
    e = _enums()
    return SYSTEM.format(**{k: ", ".join(map(str, v)) for k, v in e.items() if k != "hosts"})


def _post(path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    with httpx.Client(timeout=timeout) as c:
        r = c.post(f"{URL}{path}", json=body)
        r.raise_for_status()
        return r.json()


def health(timeout: float = 2.0) -> bool:
    try:
        with httpx.Client(timeout=timeout) as c:
            return c.get(URL.rsplit("/v1", 1)[0] + "/health").status_code == 200
    except Exception:
        return False


THINK_RE = re.compile(r"<think>|</think>", re.I)


_PLAN_CACHE: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
_REPLY_CACHE: dict[str, str] = {}


def llm_plan(text: str, previous: str | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """→ (plan dict or None, info {ms, error, think_leak}). Constrained to the plan schema. temperature 0 →
    deterministic, so valid plans are cached per (text, previous)."""
    key = (text.strip().lower(), (previous or "").strip().lower())
    if key in _PLAN_CACHE:
        plan, info = _PLAN_CACHE[key]
        return json.loads(json.dumps(plan)), {**info, "ms": 0, "cached": True}
    plan, info = _llm_plan(text, previous)
    if plan is not None:
        _PLAN_CACHE[key] = (plan, info)
    return plan, info


def _llm_plan(text: str, previous: str | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    t0 = time.perf_counter()
    user = (f"PREVIOUS: {previous}\n" if previous else "") + f"REQUEST: {text}"
    info: dict[str, Any] = {"brain": BRAIN}
    try:
        r = _post("/chat/completions", {
            "messages": [{"role": "system", "content": _system()}, {"role": "user", "content": user}],
            "temperature": 0, "max_tokens": 512, "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema", "json_schema": {"name": "plan", "strict": True,
                                                                         "schema": decode_schema()}}}, PLAN_TIMEOUT)
        content = r["choices"][0]["message"].get("content") or ""
        info["think_leak"] = bool(THINK_RE.search(content)) or bool(r["choices"][0]["message"].get("reasoning_content"))
        plan = json.loads(content)
        info["valid"] = validate(plan)
        info["usage"] = r.get("usage")
        if not info["valid"]:
            plan = None
    except Exception as e:  # timeout, tunnel down, server error, bad JSON → rules
        plan, info["error"] = None, f"{type(e).__name__}: {str(e)[:120]}"
    info["ms"] = round((time.perf_counter() - t0) * 1000)
    return plan, info


def validate(plan: dict[str, Any]) -> bool:
    """Schema check (keys, enums, types) + semantic checks the grammar can't express."""
    s = plan_schema()
    if not set(plan) <= set(s["properties"]) or not {"target_kind", "target"} <= set(plan):
        return False
    for k in s["properties"]:  # absent = null / empty
        plan.setdefault(k, [] if k == "require" else (False if k in ("inside_layer", "follow_up") else None))
    e = _enums()
    tk, tg = plan.get("target_kind"), plan.get("target")
    if tk == "concept" and tg not in e["concepts"]:
        return False
    if tk == "place_category" and tg not in e["categories"]:
        return False
    if tk == "buildings" and tg not in e["buildings"]:
        return False
    if tk == "derived" and tg != "house_with_pool":
        return False
    if tk in ("open", "concept", "place_category", "buildings", "derived") and not tg:
        return False
    if plan.get("minutes") and not plan.get("origin"):
        return False
    return True


def to_plan(d: dict[str, Any], text: str) -> Plan:
    """The executor's Plan from a validated LLM plan (same fields as the rules planner)."""
    p = Plan(text=text)
    for k in ("target_kind", "target", "place", "layer", "within_m", "inside_layer", "host_layer", "minutes", "mode",
              "origin", "min_height_m", "new_since", "sort", "docks_new_since"):
        setattr(p, k, d.get(k))
    p.require = list(d.get("require") or [])
    p.inside_layer = bool(d.get("inside_layer"))
    if p.target_kind == "concept":
        p.concept, p.concept_query = p.target, p.target.replace("_", " ")
    if p.target_kind == "derived":
        p.concept, p.concept_query = "swimming_pool", "pools"
    if p.target_kind == "place_category" and p.layer and not p.within_m and not p.inside_layer:
        p.within_m = 500.0
    return p


NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(s: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM_RE.findall(s)}


def llm_reply(template: str, steps: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Rewrite the template answer as 1–2 calm sentences. NUMBER GUARD: every number in the reply must appear in the
    template or the step envelopes' metrics, else the template is used."""
    t0 = time.perf_counter()
    info: dict[str, Any] = {}
    if template in _REPLY_CACHE:
        return _REPLY_CACHE[template], {"ms": 0, "cached": True}
    if len(re.sub(r"\s*\(step_\d+\)", "", template).split()) <= int(os.environ.get("LOCAL_LLM_REPLY_MIN_WORDS", "20")):
        return template, {"ms": 0, "skipped": "already one short sentence"}  # nothing to gain from a rewrite
    allowed = _numbers(template)
    for s in steps:
        allowed |= _numbers(json.dumps((s.get("envelope", {}).get("result") or {}).get("metrics", {})))
    try:
        r = _post("/chat/completions", {
            "messages": [{"role": "system", "content": "Rewrite the map result below as ONE short, plain sentence (under 25 words) "
                                                       "for the user. Keep every number exactly. No step references, no "
                                                       "lists, no markdown, no new facts."},
                         {"role": "user", "content": re.sub(r"\s*\(step_\d+\)", "", template)}],
            "temperature": 0.3, "max_tokens": 80, "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False}}, REPLY_TIMEOUT)
        out = (r["choices"][0]["message"].get("content") or "").strip()
        info["think_leak"] = bool(THINK_RE.search(out))
        bad = _numbers(out) - allowed
        if not out or bad or info["think_leak"]:
            info["guard"] = f"template used (numbers not in envelopes: {sorted(bad)[:5]})" if bad else "template used"
            out = template
    except Exception as e:
        out, info["error"] = template, f"{type(e).__name__}: {str(e)[:120]}"
    info["ms"] = round((time.perf_counter() - t0) * 1000)
    if not info.get("error"):
        _REPLY_CACHE[template] = out
    return out, info


FILL = ("place", "layer", "within_m", "host_layer", "minutes", "mode", "origin", "min_height_m", "year", "new_since",
        "docks_new_since", "sort")


def hybrid_plan(d: dict[str, Any], text: str, previous: str | None) -> tuple[Plan, list[str]]:
    """LLM plan + rules parse of the same effective request: the LLM wins on what it set (paraphrases, typos,
    follow-ups); the rules fill what it left out (travel time, heights, years, hosts); a 'concept' the text never
    mentions (an invented match) falls back to the rules target. → (Plan, notes)."""
    from groundtruth.planner import parse

    p = to_plan(d, text)
    eff = f"{previous} {text}" if previous and (d.get("follow_up") or not parse(text).target_kind) else text
    r = parse(eff)
    notes = []
    if p.target_kind == "concept":
        senses = load_senses()
        words = [a.lower() for a in senses.get(p.concept, {}).get("aliases", [])] + [p.concept.replace("_", " ")]
        if not any(re.search(rf"\b{re.escape(w)}", eff.lower()) for w in words) and r.target_kind:
            p.target_kind, p.target, p.concept, p.concept_query = r.target_kind, r.target, r.concept, r.concept_query
            notes.append("concept not in the text: rules target used")
    elif p.target_kind == "open" and r.target_kind in ("concept", "derived"):
        # a configured concept beats open-vocabulary detection ("parking lots", "warehouses")
        p.target_kind, p.target, p.concept, p.concept_query = r.target_kind, r.target, r.concept, r.concept_query
        notes.append("configured concept preferred over open vocabulary")
    for k in FILL:
        if getattr(p, k, None) in (None, "") and getattr(r, k, None) not in (None, ""):
            setattr(p, k, getattr(r, k))
            notes.append(f"{k} from rules")
    if r.inside_layer and not p.inside_layer and p.layer == r.layer:
        p.inside_layer = True
    for q in r.require:
        if q not in p.require:
            p.require.append(q)
    if p.place and p.minutes and r.place is None and not re.search(re.escape(p.place.lower()), eff.lower()):
        p.place = None  # a travel-time area defines its own extent
    return p, notes
