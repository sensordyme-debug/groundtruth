"""Rules planner: turns a request into real tool calls + map actions (deterministic, no LLM).

Targets (first match wins):
  1. a configured visual concept (conf/senses.json aliases)        → pick_candidates + find_all (exhaustive in a place)
  2. a place category (conf/places_categories.json)                → find_place (Overture places)
  3. buildings / houses (conf/places_categories.json "buildings")  → find_buildings (Overture; heights lidar/Overture)
  4. anything else that names a thing                              → find_anything (open vocabulary)
Constraints: place ("in Ballantyne"), distance + layer ("within 1 km of I-485", "near the light rail", "in flood zones"),
host layer ("on warehouses"), travel time ("within 10 minutes of <origin>", "within a 5 minute walk of a park"),
height ("taller than 30 m", "over 10 m tall"), year ("in 2020") and "new since 2020". Every number in the final `say`
cites the step envelope it came from. The plan has the same JSON schema whichever planner made it (`plan_schema`).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from groundtruth import config
from groundtruth.tools import _people, load_senses

PLANNER = "rules planner (deterministic)"
LAYERS = {
    "i-485": "osm_i485", "i485": "osm_i485", "485": "osm_i485",
    "i-77": "osm_i77", "i77": "osm_i77", "i-85": "osm_i85", "i85": "osm_i85",
    "light rail": "osm_light_rail", "lynx": "osm_light_rail", "light-rail": "osm_light_rail",
    "flood zone": "fema_flood_zones", "flood zones": "fema_flood_zones", "floodplain": "fema_flood_zones",
    "flood": "fema_flood_zones",
}
HOSTS = {"warehouse": "osm_warehouses", "warehouses": "osm_warehouses"}
LAYER_LABELS = {"osm_i485": "I-485", "osm_i77": "I-77", "osm_i85": "I-85", "osm_light_rail": "the LYNX light rail",
                "fema_flood_zones": "FEMA flood zones", "osm_warehouses": "warehouses"}
DEFAULT_SENSE = {"swimming_pool": "backyard_pool", "solar_installation": "rooftop_solar_comm",
                 "parking_lot": "surface_lot_full", "warehouse": "distribution_center", "construction_site": "cleared_land"}
FILLER = r"^(please |can you |could you |show me |show |find me |find all |find |where are |where is |list |count |how many )+"


@dataclass
class Plan:
    text: str
    target_kind: str | None = None  # concept | place_category | buildings | open | None
    target: str | None = None  # concept key / category key / building kind / open-vocabulary text
    concept: str | None = None
    concept_query: str | None = None
    place: str | None = None
    layer: str | None = None
    within_m: float | None = None
    inside_layer: bool = False
    host_layer: str | None = None
    minutes: float | None = None
    mode: str | None = None
    origin: str | None = None
    min_height_m: float | None = None
    year: int | None = None
    new_since: int | None = None
    require: list[str] = field(default_factory=list)  # derived: lakefront, has_dock
    sort: str | None = None  # derived: pool_area (biggest first)
    docks_new_since: int | None = None
    follow_up_of: str | None = None
    effective_text: str | None = None
    notes: list[str] = field(default_factory=list)


def plan_schema() -> dict[str, Any]:
    """JSON schema of a plan (shared by the rules planner and any LLM planner; validated before execution)."""
    return {"type": "object", "required": ["text", "target_kind", "target"], "additionalProperties": True,
            "properties": {k: {} for k in Plan.__dataclass_fields__}}


def _conf_pc() -> dict[str, Any]:
    return json.loads((config.SENSES_PATH.parent / "places_categories.json").read_text())


def _places() -> dict[str, Any]:
    return json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"]


def parse(text: str) -> Plan:
    t = " " + re.sub(r"\s+", " ", text.lower().strip().rstrip("?.!")) + " "
    t = t.replace("(experimental)", " ")
    p = Plan(text=text)
    places = _places()
    for name in sorted(places, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name.lower())}\b", t):
            p.place = name
            break
    # travel time: "within 10 minutes of X", "within a 5 minute walk of X", "10 min drive from X"
    tm = re.search(r"within (?:a |an )?(\d+(?:\.\d+)?)[- ]?(?:minutes?|mins?)(?:'s)?(?: (walk|walking|drive|driving|bike|"
                   r"biking|cycle|cycling|ride))?(?: (walk|drive|bike|ride))? (?:of|from) (.+?)(?= in [a-z ]+ $| $)", t) \
        or re.search(r"(\d+(?:\.\d+)?)[- ]?(?:minutes?|mins?) (walk|drive|bike|ride) (?:of|from) (.+?)(?= in [a-z ]+ $| $)", t)
    origin_span = None
    if tm:
        groups = tm.groups()
        p.minutes = float(groups[0])
        mode_word = next((g for g in groups[1:-1] if g), "drive")
        p.mode = {"walking": "walk", "driving": "drive", "biking": "bike", "cycle": "bike", "cycling": "bike",
                  "ride": "bike"}.get(mode_word, mode_word)
        p.origin = groups[-1].strip()
        if p.place and p.origin.endswith(" in " + p.place.lower()):
            p.origin = p.origin[: -len(" in " + p.place.lower())]
        p.origin = re.sub(r"^(the nearest |a |an |any )", "", p.origin) if not p.origin.startswith("the airport") else p.origin
        origin_span = tm.span()
    # distance to a layer
    m = re.search(r"within\s+([\d.]+)\s*(km|kilometers?|m|meters?|metres?|mi|miles?)\s+(?:of|from)\s+([a-z0-9\- ]+?)"
                  r"(?:\s+in\s|\s*$|,)", t)
    if m and not tm:
        v, u, what = float(m.group(1)), m.group(2), m.group(3).strip()
        p.within_m = v * (1000 if u.startswith("k") else 1609.34 if u.startswith("mi") else 1)
        p.layer = next((lay for k, lay in LAYERS.items() if k in what), None)
    elif re.search(r"\bnear\b", t) and not tm:
        lay = next((lay for k, lay in LAYERS.items() if k in t), None)
        if lay:
            p.within_m, p.layer = 500.0, lay
            p.notes.append("'near' read as within 500 m")
        else:  # near a named place / landmark / category → within a 5-minute drive (conf: near_minutes)
            nm = re.search(r"\bnear (?:the nearest |a |an |any )?(.+?)(?= in [a-z ]+ $| $)", t)
            if nm:
                p.minutes, p.mode = float(_conf_pc().get("near_minutes", 5)), "drive"
                p.origin = nm.group(1).strip()
                origin_span = nm.span()
                p.notes.append(f"'near {p.origin}' read as within a {p.minutes:g} minute drive")
    if re.search(r"\bin (a |the )?flood ?(zones?|plain)", t):
        p.layer, p.inside_layer, p.within_m = "fema_flood_zones", True, None
    hm = re.search(r"\bon (warehouses?)\b", t)
    if hm:
        p.host_layer = HOSTS[hm.group(1)]
    hh = re.search(r"(?:taller than|higher than|over|above|more than) (\d+(?:\.\d+)?) ?(m|metres?|meters?|ft|feet)\b(?: tall| high)?", t)
    if hh:
        p.min_height_m = float(hh.group(1)) * (0.3048 if hh.group(2) in ("ft", "feet") else 1.0)
    ym = re.search(r"\bnew\b.*?\bsince (20\d\d)\b", t)
    if ym:
        p.new_since = int(ym.group(1))
    yy = re.search(r"\bin (20\d\d)\b", t)
    if yy and not ym:
        p.year = int(yy.group(1))
    # the target: strip the constraint phrases, then match concept / category / buildings / open vocabulary
    core = t
    if origin_span:
        core = core[: origin_span[0]] + " " + core[origin_span[1]:]
    core = re.sub(r"within\s+[\d.]+\s*\w+\s+(?:of|from)\s+[a-z0-9\- ]+?(?=\s+in\s|\s*$|,)", " ", core)
    core = re.sub(r"\bnear (the )?[a-z0-9\- ]+", " ", core)
    core = re.sub(r"\bin (a |the )?flood ?(zones?|plain)", " ", core)
    core = re.sub(r"\bon (warehouses?)\b", " ", core)
    core = re.sub(r"(?:taller than|higher than|over|above|more than) \d+(?:\.\d+)? ?(?:m|metres?|meters?|ft|feet)\b(?: tall| high)?", " ", core)
    core = re.sub(r"\bnew\b|\bsince 20\d\d\b|\bin 20\d\d\b", " ", core)
    if p.place:
        core = re.sub(rf"\b(in|around|near|on|at) {re.escape(p.place.lower())}\b", " ", core)
        core = core.replace(p.place.lower(), " ")
    core = re.sub(FILLER, "", re.sub(r"\s+", " ", core).strip()).strip()
    if re.search(r"\b(how tall|how high|height|canopy|cover)\b.*\btrees?\b|\btrees?\b.*\b(tall|height|canopy|cover)\b|"
                 r"\btree (canopy|height|cover)\b", core):
        p.target_kind, p.target = "canopy", "trees"
        return p
    # derived modifiers (conversation follow-ups): lake / dock access, docks since <year>, biggest
    if re.search(r"\b(lake ?front|waterfront|on the lake|lake (and dock )?access|by the lake|on the water)\b", t):
        p.require.append("lakefront")
    if re.search(r"\b(dock access|(their |its |own |a |with )?(own )?docks?|boat docks? of their own)\b", t) and \
            re.search(r"\b(with|have|has|their|own|access|and)\b.*\bdocks?\b|\bdocks?\b.*\baccess\b", t):
        p.require += [r for r in ("lakefront", "has_dock") if r not in p.require]
    dm = re.search(r"\bdocks?\b.*\b(?:added|new|built)\b.*\bsince (20\d\d)\b|"
                   r"\b(?:added|new|built|got|gained|put in)\b.*\bdocks?\b.*\bsince (20\d\d)\b", t)
    if dm:
        p.docks_new_since = int(dm.group(1) or dm.group(2))
        p.new_since = None
        p.require += [r for r in ("lakefront", "has_dock") if r not in p.require]
    if re.search(r"\b(biggest|largest)\b", t):
        p.sort = "pool_area"
    if re.search(r"\b(houses?|homes?)\b.*\b(with|have|has)\b.*\bpools?\b|\bpool (houses|homes)\b", core + " " + t):
        p.target_kind, p.target, p.concept, p.concept_query = "derived", "house_with_pool", "swimming_pool", "pools"
        return p
    if (p.require or p.sort or p.docks_new_since) and not re.sub(
            r"\b(with|and|lake|dock|docks|access|their|own|which|that|have|has|added|since|show|the|biggest|largest|"
            r"ones|lakefront|waterfront|on|water|only|me|20\d\d)\b", "", core).strip(" ,"):
        return p  # modifiers only: a follow-up (resolved against the previous request in run())
    senses = load_senses()
    best = None
    for key, entry in senses.items():
        for a in entry["aliases"] + [key.replace("_", " ")]:
            if re.search(rf"\b{re.escape(a)}\b", core) and (best is None or len(a) > len(best[1])):
                best = (key, a)
    pc = _conf_pc()
    # place categories / building kinds must be the WHOLE remaining phrase ("school bus lots" is not "school")
    whole = re.sub(r"^(all |the |any |every )", "", core).strip(" ,")
    bkind = next((k for k, v in pc["buildings"].items() if whole in v["words"]), None)
    if best and not (bkind and p.min_height_m is not None and best[0] == "warehouse"):
        p.target_kind, p.target, p.concept, p.concept_query = "concept", best[0], best[0], best[1]
        if best[0] == "warehouse" and p.min_height_m is not None:
            p.target_kind, p.target = "buildings", "warehouse"
        return p
    cat = next((k for k, v in pc["categories"].items() if whole in v["words"]), None)
    if bkind:
        p.target_kind, p.target = "buildings", bkind
    elif cat:
        p.target_kind, p.target = "place_category", cat
    else:
        noun = re.sub(r"^(all |the |any |every )", "", core).strip(" ,")
        if noun and not re.fullmatch(r"(what|what's|whats|is|are|it|this|that|here|there)( .*)?", noun):
            p.target_kind, p.target = "open", noun
    return p


_CACHE: "OrderedDict[str, dict[str, Any]]" = None  # type: ignore[assignment]
EXPORT_RE = re.compile(r"^\s*(?:please\s+)?(?:export|download|save)\b(?:.*?\b(csv|geojson|pdf)\b)?", re.I)
MAX_INTERMEDIATE_FEATURES = 500


def run(engine_for, text: str, context: dict[str, Any] | None = None, plan_override: Plan | None = None,
        follow_up: bool | None = None) -> dict[str, Any]:
    """engine_for(year) → Engine. ``context`` (from the UI): {place: the place on screen, last_text: the previous
    request}; a request without a place uses context.place, and "export them as CSV" exports the last request's result.
    ``plan_override``: a plan made elsewhere (the local LLM, already validated) executed by the same deterministic
    executor; ``follow_up`` says whether it refines the previous request. Deterministic, so results are cached per
    (text, context, plan) (GROUNDTRUTH_PLANNER_CACHE entries, default 64).
    Returns {planner, plan, steps:[{ref, tool, envelope}], actions:[...], status, cached}."""
    import copy
    import os
    from collections import OrderedDict

    global _CACHE
    if _CACHE is None:
        _CACHE = OrderedDict()
    context = {k: v for k, v in (context or {}).items() if v}
    # a follow-up with no target of its own ("in Lake Norman", "with lake and dock access", "show the biggest ones")
    # refines the previous request: the effective request is the previous one + this one
    if context.get("last_text") and not EXPORT_RE.search(text) and not _people(text):
        p0 = parse(text)
        is_fu = follow_up if follow_up is not None else (
            not p0.target_kind and (p0.place or p0.require or p0.sort or p0.docks_new_since or p0.layer or p0.minutes))
        if is_fu:
            prev = context["last_text"]
            text = f"{prev} {text}"
            context = {k: v for k, v in context.items() if k != "last_text"}
            context["_follow_up_of"] = prev
    key = json.dumps([text.strip().lower(), context, asdict(plan_override) if plan_override else None],
                     sort_keys=True, default=str)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        out = copy.deepcopy(_CACHE[key])
        out["cached"] = True
        return out
    out = _run(engine_for, text, context, plan_override)
    if out["status"] in ("ok", "declined"):
        _CACHE[key] = copy.deepcopy(out)
        while len(_CACHE) > int(os.environ.get("GROUNDTRUTH_PLANNER_CACHE", "64")):
            _CACHE.popitem(last=False)
    out["cached"] = False
    return out


def _slim(steps: list[dict[str, Any]], keep_refs: set[str], actions: list[dict[str, Any]] | None = None,
          final_ref: str | None = None) -> None:
    """Big intermediate feature sets are left out of the response (never silently: metrics.features_omitted). Map
    actions that pointed at a slimmed step are redirected to the final result set, so the map and every count show the
    final set (a client must never render an emptied intermediate step as "0 detected")."""
    slimmed = set()
    for s in steps:
        res = s["envelope"].get("result") or {}
        fc = res.get("geojson") or {}
        n = len(fc.get("features", []))
        if s["ref"] not in keep_refs and n > MAX_INTERMEDIATE_FEATURES:
            fc["features"] = []
            res.setdefault("metrics", {})["features_omitted"] = n
            s["envelope"].setdefault("limits", []).append(
                f"{n} features omitted from this intermediate step in the agent response; the final step carries the set")
            slimmed.add(s["ref"])
    if actions is not None and final_ref:
        seen = False
        for a in list(actions):
            if a.get("action") in ("add_results", "set_filter") and a.get("envelope_ref") in slimmed | {final_ref}:
                if seen:  # one results action for the final set is enough
                    actions.remove(a)
                    continue
                a.update(action="add_results", envelope_ref=final_ref, layer_id="results")
                seen = True


def _run(engine_for, text: str, context: dict[str, Any], plan_override: Plan | None = None) -> dict[str, Any]:
    plan = plan_override if plan_override is not None else parse(text)
    if plan_override is not None:
        plan.text = text
    steps: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    extra: dict[str, Any] = {}
    keep: set[str] = set()

    def step(tool: str, env) -> str:
        ref = f"step_{len(steps) + 1}"
        steps.append({"ref": ref, "tool": tool, "envelope": env.model_dump(mode="json")})
        return ref

    def done(status: str) -> dict[str, Any]:
        _slim(steps, keep, actions, extra.get("_final_ref"))
        extra.pop("_final_ref", None)
        return {"plan": {**asdict(plan), **extra}, "steps": steps, "actions": actions, "status": status, "planner": PLANNER}

    if _people(text):
        actions.append({"action": "say", "text": "I can't help with information about people (owners, residents, "
                                                 "demographics, crime or school ratings). Ask about things visible from above."})
        return done("declined")
    ex_m = EXPORT_RE.search(text)
    if ex_m and not plan.target_kind or (ex_m and re.search(r"\b(them|these|those|it|results?|this)\b", text, re.I)):
        last = context.get("last_text")
        if not last:
            actions.append({"action": "say", "text": "Export what? Run a search first, then say 'export them as CSV'."})
            return done("declined")
        prev = run(engine_for, last, {k: v for k, v in context.items() if k != "last_text"})
        cur_ref = prev.get("current_ref")
        cur = next((s for s in prev["steps"] if s["ref"] == cur_ref), None)
        if prev["status"] != "ok" or cur is None:
            actions.append({"action": "say", "text": f"Nothing to export from '{last}'."})
            return done("declined")
        fmt = (ex_m.group(1) or "csv").lower()
        eng = engine_for(None)
        geo = cur["envelope"]["result"]["geojson"]
        ex_env = eng.export(geo, fmt)
        r = step("export", ex_env)
        keep.add(r)
        extra["exported_from"] = last
        actions.append({"action": "offer_download", "envelope_ref": r, "format": fmt})
        actions.append({"action": "say", "text": f"Exported {ex_env.result.metrics.get('count')} results of '{last}' as "
                                                 f"{fmt.upper()} ({r})."})
        return {**done("ok"), "current_ref": r}
    if plan.place is None and context.get("place") and plan.target_kind and plan.target_kind != "canopy" \
            and not plan.minutes:  # travel-time requests define their own area
        plan.place = context["place"]
        plan.notes.append(f"place '{plan.place}' taken from the map (context)")
    if not plan.target_kind:
        actions.append({"action": "say", "text": "I didn't recognise what to look for. Try: houses with pools, boat docks "
                                                 "on Lake Norman, or buildings taller than 30 m in Uptown."})
        return done("declined")
    if context.get("_follow_up_of"):
        plan.follow_up_of = context["_follow_up_of"]
    plan.effective_text = text
    eng = engine_for(plan.year)
    places = _places()
    bbox = places[plan.place]["bbox"] if plan.place else None
    if plan.place:
        actions.append({"action": "fly_to", "bbox": bbox, "label": plan.place})
    r_cur, current, r_fa, what = None, None, None, plan.target
    if plan.target_kind == "derived":
        return _run_derived(eng, plan, step, actions, extra, keep, done, context)
    if plan.target_kind == "canopy":
        cv = eng.canopy(place=plan.place, bbox=bbox)
        r_cv = step("canopy", cv)
        if cv.status == "declined":
            actions.append({"action": "say", "text": f"I can't answer that here: {cv.declined_reason} ({r_cv})."})
            return done("partial")
        mm = cv.result.metrics
        actions.append({"action": "show_metrics", "envelope_ref": r_cv})
        actions.append({"action": "say", "text": f"Trees{' in ' + plan.place if plan.place else ''}: median "
                                                 f"{mm['tree_height_m_median']} m, 90th percentile {mm['tree_height_m_p90']} m, "
                                                 f"tallest {mm['tree_height_m_max']} m; canopy covers {mm['canopy_cover_pct']}% "
                                                 f"of the non-building land (lidar 2016) ({r_cv})."})
        return done("ok")
    # ---- 1. the target set
    if plan.target_kind == "concept":
        pc = eng.pick_candidates(plan.concept_query or plan.concept.replace("_", " "))
        r_pick = step("pick_candidates", pc)
        actions.append({"action": "show_candidates", "envelope_ref": r_pick})
        tiles = [f for f in pc.result.geojson["features"] if not f["properties"].get("no_clear_example")]
        picked, nots = list(context.get("picked") or []), list(context.get("not") or [])
        # ask "which do you mean?" in the chat when the request is a bare multi-meaning concept (no place, no filter,
        # nothing picked yet); the UI answers with context {picked, not} (sense ids)
        if not picked and not plan.place and not plan.layer and not plan.host_layer and not plan.minutes \
                and plan.concept in ASK_PICK and context.get("ask_pick", True):
            actions.append({"action": "ask_pick", "envelope_ref": r_pick})
            actions.append({"action": "say", "text": ASK_PICK[plan.concept]})
            extra["_final_ref"] = r_pick
            return {**done("ok"), "current_ref": r_pick, "ask": "pick"}
        want = DEFAULT_SENSE.get(plan.concept)
        chosen_l = [f for f in tiles if f["properties"]["sense_id"] in picked] or \
            [next((f for f in tiles if f["properties"]["sense_id"] == want), tiles[0] if tiles else None)]
        chosen_l = [c for c in chosen_l if c is not None]
        if not chosen_l:
            actions.append({"action": "say", "text": f"I couldn't find a clear example of {plan.concept_query} ({r_pick})."})
            return done("partial")
        chosen = chosen_l[0]
        exs = [c["properties"]["example"] for c in chosen_l]
        ex = exs[0]
        hard = [f["properties"]["example"] for f in tiles if f["properties"]["sense_id"] in nots]
        neg = [f["properties"]["example"] for f in tiles if f not in chosen_l and f["properties"]["sense_id"] not in nots]
        plan.notes.append(f"examples: senses {[c['properties']['sense_id'] for c in chosen_l]}; others used as negatives")
        extra.update(chosen_sense=chosen["properties"]["sense_id"], chosen_example=ex, negatives=neg)
        if picked and not plan.place and not plan.layer and not plan.minutes:  # just picked: overview + where to look
            env = eng.find_all(exs, hard, concept=plan.concept, soft_exclude=neg)
            r = step("find_all", env)
            labels = " and ".join(c["properties"]["sense_label"].lower() for c in chosen_l)
            actions.append({"action": "heatmap", "envelope_ref": r, "label": labels})
            actions.append({"action": "say", "text": f"Got it: {labels}. Where should I look?"})
            actions.append({"action": "suggest_places", "places": SUGGEST_PLACES})
            extra["_final_ref"] = r
            return {**done("ok"), "current_ref": r, "ask": "where"}
        env = eng.find_all(exs, hard, place=plan.place if eng.aoi == "county" else None, concept=plan.concept,
                           soft_exclude=neg, within_layer=plan.host_layer)
        what = (plan.concept_query or plan.concept).replace("_", " ")
        r_fa = step("find_all", env)
    elif plan.target_kind == "place_category":
        sb = bbox
        if plan.minutes and not sb:  # bound the query by the travel area
            iso = eng.isochrone(plan.origin, plan.minutes, plan.mode or "drive")
            r_iso = step("isochrone", iso)
            if iso.status == "declined":
                actions.append({"action": "say", "text": f"I couldn't route from '{plan.origin}': {iso.declined_reason} ({r_iso})."})
                return done("partial")
            actions.append({"action": "add_results", "layer_id": "agent", "envelope_ref": r_iso})
            from shapely.geometry import shape as _shape

            sb = list(_shape(iso.result.geojson["features"][0]["geometry"]).bounds)
        env = eng.find_place(plan.target, bbox=sb)
        what = plan.target + ("s" if not plan.target.endswith("s") else "")
        r_fa = step("find_place", env)
    elif plan.target_kind == "buildings":
        sb = bbox
        if plan.minutes and not sb:
            iso = eng.isochrone(plan.origin, plan.minutes, plan.mode or "drive")
            r_iso = step("isochrone", iso)
            if iso.status == "declined":
                actions.append({"action": "say", "text": f"I couldn't route from '{plan.origin}': {iso.declined_reason} ({r_iso})."})
                return done("partial")
            actions.append({"action": "add_results", "layer_id": "agent", "envelope_ref": r_iso})
            from shapely.geometry import shape as _shape

            sb = list(_shape(iso.result.geojson["features"][0]["geometry"]).bounds)
        if sb is None:
            actions.append({"action": "say", "text": "Buildings need an area: add 'in <place>' or a travel-time limit."})
            return done("declined")
        env = eng.find_buildings(plan.target, bbox=sb, place=None, min_height_m=plan.min_height_m)
        what = {"house": "houses", "apartment": "apartment buildings", "building": "buildings"}.get(plan.target, plan.target)
        if plan.min_height_m is not None:
            what += f" taller than {plan.min_height_m:g} m"
        r_fa = step("find_buildings", env)
    else:  # open vocabulary
        env = eng.find_anything(plan.target, bbox=bbox, place=plan.place)
        what = plan.target
        r_fa = step("find_anything", env)
    if env.status == "declined":
        actions.append({"action": "say", "text": f"That didn't work: {env.declined_reason} ({r_fa})."})
        return done("partial")
    actions.append({"action": "add_results", "layer_id": "results", "envelope_ref": r_fa})
    current, r_cur = env.result.geojson, r_fa
    # ---- 2. constraints
    for lay, kind in ((plan.layer, "filter"), (plan.host_layer if plan.target_kind != "concept" else None, "host")):
        if not lay:
            continue
        al = eng.add_layer(lay, bbox)
        r_l = step("add_layer", al)
        if al.status != "declined":
            actions.append({"action": "add_layer", "layer_id": lay, "source": "envelope", "envelope_ref": r_l})
        where = ({"intersects_layer": lay} if (kind == "host" or plan.inside_layer)
                 else {"within_m_of": {"layer": lay, "m": plan.within_m or 1000}})
        fl = eng.filter(current, where)
        r_f = step("filter", fl)
        if fl.status != "declined":
            actions.append({"action": "set_filter", "layer_id": "results", "envelope_ref": r_f})
            current, r_cur = fl.result.geojson, r_f
    origin_label = plan.origin
    if plan.minutes:
        fl = eng.filter(current, {"within_minutes_of": {"origin": plan.origin, "minutes": plan.minutes,
                                                        "mode": plan.mode or "drive"}})
        r_f = step("filter", fl)
        if fl.status == "declined":
            actions.append({"action": "say", "text": f"Travel-time filter failed: {fl.declined_reason} ({r_f})."})
            return done("partial")
        og = fl.result.metrics.get("travel", {}).get("origins", [])
        origin_label = og[0]["name"] if len(og) == 1 else f"a {plan.origin} ({len(og)} considered)"
        actions.append({"action": "set_filter", "layer_id": "results", "envelope_ref": r_f})
        current, r_cur = fl.result.geojson, r_f
    # ---- 3. new since <year>
    r_cy = None
    if plan.new_since and plan.target_kind == "concept":
        cy = eng.compare_years([f["properties"]["example"] for f in current["features"]][:300], from_year=plan.new_since,
                               concept=plan.concept)
        r_cy = step("compare_years", cy)
    # ---- 4. measure + export
    dist_layers = [plan.layer] if plan.layer and not plan.inside_layer else []
    ms = None
    if current["features"] and plan.target_kind != "place_category":
        ms = eng.measure(current, to_layers=dist_layers)
        r_ms = step("measure", ms)
        actions.append({"action": "show_metrics", "envelope_ref": r_ms})
    if current["features"]:
        ex_env = eng.export(current, "csv")
        actions.append({"action": "offer_download", "envelope_ref": step("export", ex_env), "format": "csv"})
    # ---- 5. say (every number cites its step)
    n = len(current["features"])
    m_ = env.result.metrics
    if plan.place:
        scope = f" in {plan.place}"
    elif plan.host_layer:
        scope = f" on {plan.host_layer.replace('osm_', '')} (every chip touching one scanned)"
    elif plan.target_kind == "concept":
        scope = " (county-wide, ranked top results: not an exhaustive count; add 'in <place>' for one)"
    elif plan.target_kind == "open" and not bbox:
        scope = f" (county-wide, top {m_.get('chips_searched')} chips by CLIP: not exhaustive)"
    else:
        scope = ""
    if plan.minutes:
        scope += f" within a {plan.minutes:g} minute {plan.mode or 'drive'} of {origin_label}"
    if plan.layer and plan.inside_layer:
        scope += " inside FEMA flood zones (1% + 0.2% annual chance)"
    elif plan.layer:
        scope += f" within {plan.within_m:g} m of {LAYER_LABELS.get(plan.layer, plan.layer)}"
    parts = [f"{n} {what}{scope} ({r_cur})"]
    if plan.target_kind == "concept":
        from groundtruth.rerank import concept_conf

        if concept_conf(plan.concept).get("experimental"):  # solar / parking: typed requests work, but say so
            parts.insert(0, f"Experimental: {plan.concept.replace('_', ' ')} detection is not yet reliable")
    if ms is not None and ms.status != "declined" and plan.target_kind != "place_category":
        parts.append(f"{ms.result.metrics.get('area_acres_total')} acres in total ({steps[-2]['ref'] if current['features'] else ''})")
        if dist_layers:
            md = ms.result.metrics.get(f"median_dist_m_to_{plan.layer}")
            if md is not None:
                parts.append(f"median {round(md)} m to {LAYER_LABELS.get(plan.layer, plan.layer)}")
    if plan.target_kind == "open":
        ep = m_.get("eye_precision_12")
        parts.append("LOW CONFIDENCE concept" + (f" (eye-checked {ep}/12)" if ep is not None else " (not yet eye-checked)")
                     if m_.get("low_confidence") else f"eye-checked precision {ep}/12")
    if r_cy:
        cy_env = next(s["envelope"] for s in steps if s["ref"] == r_cy)
        if cy_env["status"] == "declined":
            parts.append(f"'new since {plan.new_since}' isn't available: {cy_env['declined_reason']} ({r_cy})")
        else:
            mm = cy_env["result"]["metrics"]
            parts.append(f"{mm.get('new', 0)} new since {plan.new_since}, {mm.get('existing', 0)} existing, "
                         f"{mm.get('unsure', 0)} unsure ({r_cy})")
    actions.append({"action": "say", "text": "; ".join(parts) + "."})
    keep.add(r_cur)
    extra["_final_ref"] = r_cur
    return {**done("ok"), "current_ref": r_cur}


SUGGEST_PLACES = ["Lake Norman", "Ballantyne", "Myers Park"]
ASK_PICK = {"swimming_pool": "“Pools” can mean a few things from above. Which do you mean?"}


def _run_derived(eng, plan: Plan, step, actions, extra, keep, done, context=None) -> dict[str, Any]:
    """'houses with pools' (+ lake / dock access, biggest, docks since <year>). No place → a county overview (heat glow of
    residential pools, NOT a count) and 'Where should I look?'. With a place → the precomputed derived set."""
    assume = "I took “houses with pools” as backyard pools at homes."
    if not plan.place:
        pc = eng.pick_candidates("swimming pool")
        r_pick = step("pick_candidates", pc)
        tiles = [f for f in pc.result.geojson["features"] if not f["properties"].get("no_clear_example")]
        chosen = next((f for f in tiles if f["properties"]["sense_id"] == DEFAULT_SENSE["swimming_pool"]), tiles[0])
        neg = [f["properties"]["example"] for f in tiles if f is not chosen]
        env = eng.find_all([chosen["properties"]["example"]], [], concept="swimming_pool", soft_exclude=neg)
        r = step("find_all", env)
        keep.add(r)
        extra.update(chosen_sense=chosen["properties"]["sense_id"], assumed=assume)
        actions.append({"action": "heatmap", "envelope_ref": r, "label": "residential pools (top matches)"})
        actions.append({"action": "say", "text": f"Backyard pools next to houses, across the county. Where should I look? ({r})"})
        actions.append({"action": "suggest_places", "places": SUGGEST_PLACES})
        extra["_final_ref"] = r
        return {**done("ok"), "current_ref": r, "ask": "where"}
    env = eng.find_derived("house_with_pool", place=plan.place, require=tuple(plan.require), sort=plan.sort,
                           exclude_ids=tuple((context or {}).get("rejected") or ()),
                           docks_new_since=plan.docks_new_since)
    r = step("find_derived", env)
    if env.status == "declined":
        actions.append({"action": "say", "text": f"{env.declined_reason[:1].upper()}{env.declined_reason[1:]}. "
                                                 f"Try Lake Norman ({r})."})
        return done("partial")
    keep.add(r)
    m = env.result.metrics
    n = m["count"]
    actions.append({"action": "add_results", "layer_id": "results", "envelope_ref": r})
    where = f"around {plan.place}" if plan.place == "Lake Norman" else f"in {plan.place}"
    if plan.docks_new_since:
        dn = m.get("docks_new_since") or {}
        nd, nc = dn.get("docks_new", 0), dn.get("docks_checked", 0)
        if n == 0:
            txt = f"None of them: all {nc} of their docks were already there in {plan.docks_new_since}."
        else:
            txt = f"{nd} of their {nc} docks weren't there in {plan.docks_new_since}."
    elif "has_dock" in plan.require:
        txt = f"{n:,} of them sit on the lake and have their own dock." if plan.follow_up_of else             f"{n:,} lakefront houses with a pool and their own dock."
    elif "lakefront" in plan.require:
        txt = f"{n:,} lakefront houses with a pool {where}."
    else:
        txt = f"Found {n:,} houses with pools {where}."
    if plan.sort == "pool_area":
        txt = f"The {n} biggest pools among them, largest first." if plan.follow_up_of else f"{txt} Showing the {n} biggest."
    if not plan.follow_up_of:
        txt = f"{assume} {txt}"
    actions.append({"action": "say", "text": f"{txt} ({r})"})
    if plan.docks_new_since:
        nxt = []
    elif "has_dock" in plan.require:
        nxt = ["which got docks since 2020"]
    elif plan.place == "Lake Norman":
        nxt = ["with lake and dock access", "only the biggest pools"]
    else:
        nxt = ["only the biggest pools"]
    if nxt:
        actions.append({"action": "suggest", "pills": nxt})
    extra["_final_ref"] = r
    return {**done("ok"), "current_ref": r}
