"""Engine tools. Each returns an Envelope (docs/CONTRACT.md).

    eng = Engine(PublicDataProvider(), aoi="unc_charlotte")
    eng.search_text("swimming pool", k=24)

Examples ("ids") are ``chip_id`` or ``chip_id#cell`` (cell 0–15, row-major from the chip's NW corner). Without an
explicit cell, the chip's most salient cell (least like a typical patch) is used.
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
import uuid
from collections import OrderedDict
from contextlib import nullcontext as _null_ctx
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
from PIL import Image
from shapely.geometry import box, mapping, shape

from groundtruth import config
from groundtruth.envelope import Envelope, Result, Source, StepTimer, declined, feature_collection
from groundtruth.geo import SQM_PER_ACRE, length_m, to_measure, to_wgs84
from groundtruth.index import ChipIndex, view_cells
from groundtruth.providers.base import DataProvider

VERSION = "0.2.0"
PEOPLE_TERMS = ("owner", "who owns", " owns ", "owned by", "who lives", "lives at", "resident", "tenant", "occupant",
                "income", "race", "ethnicity", "crime", "school rating", "demographic", "home address of",
                "name of", "names of")
NOT_IMPLEMENTED = "not_implemented"
# T4 risk predicates for filter(where=...): each fills a per-feature property (docs/CONTRACT.md "Risk answers")
RISK_PREDICATES = ("in_flood_zone", "within_m_of_shore", "canopy_over_pct", "elevation_above_flood_m", "slope_deg",
                   "height_m")


_PEOPLE_RE = __import__("re").compile(
    r"\brat(e|es|ed|ing|ings)\b.*\bschools?\b|\bschools?\b.*\brat(e|es|ed|ing|ings)\b|\bbest schools?\b|"
    r"\bworst schools?\b|\bschool (scores?|quality|rankings?)\b|\bpeople\b|\bwho (lives?|owns?|is|are)\b|\bhousehold\b")


def _people(query: str) -> bool:
    """People-data policy (runs BEFORE any planner or model): owners, residents, demographics, crime, school ratings."""
    q = query.lower()
    # whole words only: "resident" must not match "residential", "race" must not match "terrace" / "racetrack"
    return bool(_PEOPLE_WORDS.search(q)) or bool(_PEOPLE_RE.search(q))


_PEOPLE_WORDS = __import__("re").compile(r"(?<![a-z])(?:" + "|".join(
    __import__("re").escape(t.strip()) + "s?" for t in PEOPLE_TERMS) + r")(?![a-z])")


def load_senses() -> dict[str, Any]:
    return {k: v for k, v in json.loads(config.SENSES_PATH.read_text()).items() if not k.startswith("_")}


def senses_for(query: str) -> tuple[str | None, list[dict[str, Any]]]:
    q = " ".join(query.lower().strip().split())
    for key, entry in load_senses().items():
        if q == key or q in entry["aliases"]:
            return key, entry["senses"]
    return None, []


def sense_colour_rule(sense: dict[str, Any], concept_key: str | None) -> dict[str, Any] | None:
    """A sense's colour requirement: 'concept' → the concept's colour rule; a name → senses.json _colours[name]."""
    c = sense.get("colour")
    if not c:
        return None
    if c == "concept":
        from groundtruth.rerank import concept_conf

        return concept_conf(concept_key)["rerank"].get("colour")
    return json.loads(config.SENSES_PATH.read_text()).get("_colours", {}).get(c)


def concepts_default_change() -> dict[str, float]:
    from groundtruth.rerank import concepts

    return dict(concepts()["_default"].get("change", {}))


def parse_example(s: str) -> tuple[str, int | None]:
    cid, cell, _ = parse_example_full(s)
    return cid, cell


def parse_example_full(s: str) -> tuple[str, int | None, int | None]:
    """``chip_id`` | ``chip_id#cell`` | ``chip_id#cell:patch`` (patch 0–255 = 14 px / 8.4 m, from find_all scans)."""
    if "#" not in s:
        return s, None, None
    cid, rest = s.split("#", 1)
    if ":" in rest:
        c, p = rest.split(":", 1)
        return cid, int(c), int(p)
    return cid, int(rest), None


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter == 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua


def memo(complete: Any = None):
    """Memoise a deterministic tool's envelope per engine (args + concept config as the key; GROUNDTRUTH_MEMO entries,
    default 256). ``complete(env)`` decides whether a result is final enough to cache (e.g. every outline done).
    A cached reply says so in its steps; callers get a deep copy."""
    import functools

    def deco(fn):
        import inspect

        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(self, *a, **kw):
            from groundtruth.rerank import concept_conf

            cache = self.__dict__.setdefault("_memo", OrderedDict())
            bound = sig.bind(self, *a, **kw)  # positional and keyword calls share one key
            bound.apply_defaults()
            args = {k: v for k, v in bound.arguments.items() if k != "self"}
            ck = args.get("concept")
            key = (fn.__name__, json.dumps(args, sort_keys=True, default=str),
                   json.dumps(concept_conf(ck), sort_keys=True) if ck else None)
            if key in cache:
                cache.move_to_end(key)
                from groundtruth.envelope import Step

                t0 = time.perf_counter()
                env = cache[key].model_copy(deep=True)
                orig = round(sum(s.ms for s in env.steps))
                env.steps = [Step(phase="memo", detail=f"identical request: cached envelope (first computed in {orig} ms: "
                                                       + ", ".join(s.phase for s in env.steps) + ")",
                                  ms=round((time.perf_counter() - t0) * 1000, 1))]
                return env
            env = fn(self, *a, **kw)
            if env.status != "declined" and (complete is None or complete(env)):
                cache[key] = env.model_copy(deep=True)
                while len(cache) > int(os.environ.get("GROUNDTRUTH_MEMO", "256")):
                    cache.popitem(last=False)
            return env

        return wrapper

    return deco


def _all_outlined(env: Envelope) -> bool:
    """Final when nothing is pending, or when this call queued no new outlines (objects beyond outline_top stay boxes)."""
    m = env.result.metrics
    return not m.get("pending_outline") or not m.get("outlines_queued")


@dataclass
class Session:
    id: str
    accept: list[str] = field(default_factory=list)
    reject: list[str] = field(default_factory=list)
    query: str = ""
    created: float = field(default_factory=time.time)
    pool: list[int] = field(default_factory=list)
    pool_key: Any = None


class Engine:
    def __init__(self, provider: DataProvider, *, aoi: str = "county", year: int | None = None,
                 index: ChipIndex | None = None) -> None:
        self.provider = provider
        self.year = year or provider.imagery_years()[0]
        self.aoi = aoi
        self.index = index or ChipIndex(config.precompute_dir(self.year, aoi))
        self._clip = None
        self._outliner = None
        self.sessions: dict[str, Session] = {}

    # ------------------------------------------------------------------ shared
    def _clip_model(self):
        if self._clip is None:
            from groundtruth.embed import get_clip

            self._clip = get_clip()
        return self._clip

    def _sources(self):
        return [s for s in self.provider.sources() if str(self.year) in s.name]

    def _limits(self) -> list[str]:
        out = [f"NAIP {self.year} at 0.6 m/px: objects smaller than ~3 m are not resolvable",
               "Scores are embedding similarities, not calibrated probabilities",
               "Hits are 16.8 m cells of 134 m chips until outlined (`outline` / find_all's background outlines)"]
        if self.aoi != "county":
            out.append(f"Only the '{self.aoi}' area is indexed, not the whole county")
        return out

    def _status(self) -> str:
        return "ok" if self.aoi == "county" else "partial"

    def _env(self, tool: str, **kw: Any) -> Envelope:
        kw.setdefault("status", self._status())
        kw.setdefault("limits", self._limits())
        kw.setdefault("sources", self._sources())
        res = kw.get("result")
        concept = res.metrics.get("concept") if res is not None and isinstance(res.metrics, dict) else None
        if concept:
            from groundtruth.rerank import concept_conf

            exp = concept_conf(concept).get("experimental")
            if exp:
                kw["limits"] = list(kw["limits"]) + [f"EXPERIMENTAL concept '{concept}': {exp}"]
        if kw.get("confidence") is None:  # every envelope states a confidence (heuristic where that's all we have)
            feats = (kw.get("result").geojson.get("features", []) if kw.get("result") is not None else [])
            cs = [f["properties"].get("confidence") for f in feats if f.get("properties", {}).get("confidence") is not None]
            if cs:
                kw["confidence"] = round(float(np.mean(cs)), 3)
                kw["method"] = kw.get("method", "") + "; confidence = mean per-feature confidence (heuristic)"
            else:
                sc = [f["properties"]["score"] for f in feats if f.get("properties", {}).get("score") is not None]
                if sc:
                    lo, hi = min(sc), max(sc)
                    kw["confidence"] = round(float(np.mean([(s - lo) / max(hi - lo, 1e-6) for s in sc])), 3) if hi > lo else 0.5
                    kw["method"] = kw.get("method", "") + "; confidence = mean relative score within this result (heuristic)"
                else:
                    kw["confidence"] = 0.0
                    kw["method"] = kw.get("method", "") + "; confidence = 0 (no evidence returned)"
        return Envelope(tool=tool, version=VERSION, **kw)

    def _example_cells(self, examples: Sequence[str]) -> list[tuple[int, int]]:
        out = []
        for ex in examples:
            cid, cell = parse_example(ex)
            if cid not in self.index.pos:
                continue
            i = self.index.pos[cid]
            out.append((i, cell if cell is not None else self.index.salient_cell(i)))
        return out

    def localise_many(self, items: Sequence[tuple[int, np.ndarray]]) -> list[tuple[int, float]]:
        """Batched two-stage localisation for several chips: one CLIP pass for all 16 block windows of every chip,
        then one pass for the 4 cells inside each chip's best block (fp16 on GPU)."""
        from PIL import Image

        if not items:
            return []
        g, cp = self.index.grid, config.CHIP_PX // self.index.grid
        h = g // 2
        blocks = [(br, bc) for br in range(h) for bc in range(h)]
        imgs = [Image.open(io.BytesIO(self.index.thumb_jpeg(i))).convert("RGB") for i, _ in items]

        def crop(img, cx: float, cy: float, size: int) -> np.ndarray:
            x0 = int(min(max(cx - size / 2, 0), config.CHIP_PX - size))
            y0 = int(min(max(cy - size / 2, 0), config.CHIP_PX - size))
            return np.asarray(img.crop((x0, y0, x0 + size, y0 + size)).resize((224, 224), Image.BILINEAR))

        clip = self._clip_model()

        def embed(arr: np.ndarray) -> np.ndarray:
            import torch

            if str(clip.device_name).startswith("cuda"):
                with torch.autocast("cuda", dtype=torch.float16):
                    return clip.embed_images(arr, batch=128)
            return clip.embed_images(arr, batch=64)

        s1 = embed(np.stack([crop(im, (bc * 2 + 1) * cp, (br * 2 + 1) * cp, 3 * cp) for im in imgs for br, bc in blocks]))
        s1 = s1.reshape(len(items), len(blocks), -1)
        best_blocks = [blocks[int(np.argmax(s1[k] @ q))] for k, (_, q) in enumerate(items)]
        cells = [[(br * 2 + dr) * g + (bc * 2 + dc) for dr in (0, 1) for dc in (0, 1)] for br, bc in best_blocks]
        s2 = embed(np.stack([crop(im, (c % g) * cp + cp / 2, (c // g) * cp + cp / 2, int(1.5 * cp))
                             for im, cs in zip(imgs, cells) for c in cs])).reshape(len(items), 4, -1)
        out = []
        for k, (_, q) in enumerate(items):
            sc = s2[k] @ q
            j = int(np.argmax(sc))
            out.append((cells[k][j], float(sc[j])))
        return out

    def localise(self, i: int, qvec: np.ndarray) -> tuple[int, float]:
        """Which cell of the chip best matches a text vector (OpenCLIP on crops of the stored thumbnail).
        Two stages, 20 CLIP passes per chip: (1) 16 windows of 84 px centred on each 2×2 block of cells → best block;
        (2) its 4 cells, each with a 42 px context crop → best cell. Query-time only (pick_candidates' 6 tiles)."""
        from PIL import Image

        img = Image.open(io.BytesIO(self.index.thumb_jpeg(i))).convert("RGB")
        g, cp = self.index.grid, config.CHIP_PX // self.index.grid

        def crop(cx: float, cy: float, size: int) -> np.ndarray:
            x0 = int(min(max(cx - size / 2, 0), config.CHIP_PX - size))
            y0 = int(min(max(cy - size / 2, 0), config.CHIP_PX - size))
            return np.asarray(img.crop((x0, y0, x0 + size, y0 + size)).resize((224, 224), Image.BILINEAR))

        clip = self._clip_model()
        h = g // 2  # 2×2 blocks of cells
        blocks = [(br, bc) for br in range(h) for bc in range(h)]
        s1 = clip.embed_images(np.stack([crop((bc * 2 + 1) * cp, (br * 2 + 1) * cp, 3 * cp) for br, bc in blocks]),
                               batch=16) @ qvec
        br, bc = blocks[int(np.argmax(s1))]
        cells = [(br * 2 + dr) * g + (bc * 2 + dc) for dr in (0, 1) for dc in (0, 1)]
        s2 = clip.embed_images(np.stack([crop((c % g) * cp + cp / 2, (c // g) * cp + cp / 2, int(1.5 * cp)) for c in cells]),
                               batch=4) @ qvec
        k = int(np.argmax(s2))
        return cells[k], float(s2[k])

    def _clip_rank(self, qvec: np.ndarray, k_chips: int) -> list[tuple[int, float, int]]:
        """(chip, score, best_view) by max over CLIP views."""
        V = self.index.n_views
        D, I = self.index.search_rows("clip", qvec, k_chips * V * 3)
        best: dict[int, tuple[float, int]] = {}
        for d, r in zip(D, I):
            if r < 0:
                continue
            i, v = divmod(int(r), V)
            if i not in best or d > best[i][0]:
                best[i] = (float(d), v)
        ranked = sorted(best.items(), key=lambda t: (-round(t[1][0], 6), self.index.ids[t[0]]))
        return [(i, s, v) for i, (s, v) in ranked[:k_chips]]

    def _nms(self, hits: list[tuple[int, float, int]], k: int, iou: float = 0.3,
             block: Sequence[tuple[int, int]] = ()) -> list[tuple[int, float, int]]:
        """Drop hits whose cell overlaps an earlier hit's cell (same object seen in overlapping chips)."""
        kept: list[tuple[int, float, int]] = []
        boxes = [self.index.cell_bounds_m(i, c) for i, c in block]
        for i, s, c in hits:
            b = self.index.cell_bounds_m(i, c)
            if any(_iou(b, o) > iou for o in boxes):
                continue
            kept.append((i, s, c)); boxes.append(b)
            if len(kept) >= k:
                break
        return kept

    # ------------------------------------------------------------------ search_text
    def search_text(self, query: str, k: int = 50) -> Envelope:
        tool = "search_text"
        if not query.strip():
            return declined(tool, VERSION, "empty query")
        if _people(query):
            return declined(tool, VERSION, "Groundtruth does not search for information about people")
        t = StepTimer()
        with t.step("embed_query", "OpenCLIP text, 4-prompt ensemble"):
            q = self._clip_model().embed_query(query)
        with t.step("search", f"max over {self.index.n_views} CLIP views × {len(self.index)} chips"):
            ranked = self._clip_rank(q, k * 3)
        with t.step("localise+dedupe", "salient DINOv2 cell in best view; cell NMS"):
            hits = [(i, s, self.index.salient_cell(i, view_cells(v, self.index.grid))) for i, s, v in ranked]
            views = {i: v for i, _, v in ranked}
            hits = self._nms(hits, k)
        feats = [self.index.feature(i, cell=c, score=round(s, 4), rank=r + 1, view=views[i])
                 for r, (i, s, c) in enumerate(hits)]
        return self._env(tool, result=Result(geojson=feature_collection(feats), metrics={
            "count": len(feats), "indexed_chips": len(self.index),
            "score_max": feats[0]["properties"]["score"] if feats else None}),
            method=f"OpenCLIP ViT-B-16 (laion2b_s34b_b88k) text→image cosine, max over whole chip + 2×2 quadrant "
                   f"crops, NAIP {self.year}; hit cell = most salient DINOv2 cell in the best view",
            assumptions=[f"NAIP {self.year} imagery reflects the ground today"], steps=t.steps)

    # ------------------------------------------------------------------ pick_candidates
    @memo()
    def pick_candidates(self, query: str, n: int = 6, pool: int = 200, per_sense: int = 40,
                        max_sim: float = 0.80) -> Envelope:
        """One representative per *meaning*: the query is expanded into sense prompts (conf/senses.json); each
        candidate chip is assigned to the sense it matches best; per sense the best chip whose DINOv2 cell is
        not too similar (cos < max_sim) to already-chosen tiles becomes the option (MMR-style)."""
        tool = "pick_candidates"
        if not query.strip():
            return declined(tool, VERSION, "empty query")
        if _people(query):
            return declined(tool, VERSION, "Groundtruth does not search for information about people")
        t = StepTimer()
        key, senses = senses_for(query)
        clip = self._clip_model()
        with t.step("embed_query", f"raw query + {len(senses)} sense prompts ({key or 'no sense list'})"):
            qraw = clip.embed_query(query)
            prompted = [s for s in senses if s.get("prompt")]
            Q = np.stack([clip.embed_query(s["prompt"]) for s in prompted]) if prompted else np.zeros((0, 512))
        with t.step("candidate_pool", f"top {pool} raw + top {per_sense} per sense"):
            cand: set[int] = {i for i, _, _ in self._clip_rank(qraw, pool)}
            for qs in Q:
                cand |= {i for i, _, _ in self._clip_rank(qs, per_sense)}
            idx = np.array(sorted(cand))
            CV = self.index.clip_views(idx)  # (n, V, 512)
            raw = (CV @ qraw).max(1)
            S = np.einsum("nvd,sd->nsv", CV, Q) if len(Q) else np.zeros((len(idx), 0, CV.shape[1]))
            Smax, Sview = S.max(2) if len(Q) else S[:, :, 0], S.argmax(2) if len(Q) else None
            assigned = Smax.argmax(1) if len(Q) else None
        chosen: list[dict[str, Any]] = []
        chosen_vecs: list[np.ndarray] = []
        no_clear: list[dict[str, Any]] = []

        def rep_cell(j: int, view: int) -> int:
            return self.index.salient_cell(int(idx[j]), view_cells(int(view), self.index.grid))

        def cell_colour(i: int, rule: dict[str, Any]) -> np.ndarray:
            """(64,) colour fraction per 16.8 m cell: best 14 px patch, or the mean over the cell when the concept sets
            pick_colour_stat "mean" (solar: thin dark shadows along roof edges have a high max but a low mean)."""
            from groundtruth.rerank import _cell_patches, colour_cached, concept_conf

            img = np.asarray(Image.open(io.BytesIO(self.index.thumb_jpeg(i))).convert("RGB"))
            cp = colour_cached((str(self.index.root), i), img, rule).reshape(-1)
            stat = np.mean if concept_conf(key).get("pick_colour_stat") == "mean" else np.max
            return np.array([stat(cp[_cell_patches(c, self.index.grid)]) for c in range(self.index.n_cells)])

        def try_pick(order: Sequence[int], view_of, sense: dict[str, Any], score_of, limit: int = 60) -> bool:
            rule = sense_colour_rule(sense, key)
            cmin = float(sense.get("colour_min", 0))
            for n_tried, j in enumerate(order):
                if rule and n_tried >= limit:
                    break
                i = int(idx[j])
                if any(c["i"] == i for c in chosen):
                    continue
                cc = None
                if rule:
                    cc = cell_colour(i, rule)
                    if cc.max() < cmin:
                        continue  # no pixels of the sense's colour: not a clear example
                    cell = int(np.argmax(cc))
                else:
                    cell = rep_cell(j, view_of(j))
                v = self.index.cells(i)[cell]
                if chosen_vecs and max(float(v @ w) for w in chosen_vecs) >= max_sim:
                    continue
                chosen.append({"i": i, "cell": cell, "sense": sense, "score": float(score_of(j)), "j": j,
                               "cell_colour": cc, "rule": rule, "colour_min": cmin})
                chosen_vecs.append(v)
                return True
            if rule:
                no_clear.append(sense)
            return False

        with t.step("pick", f"one per sense, DINOv2 cell diversity cos<{max_sim}"):
            for s_i, sense in enumerate(prompted):
                mine = np.where(assigned == s_i)[0]
                order = mine[np.argsort(-Smax[mine, s_i])].tolist()
                order += [j for j in np.argsort(-Smax[:, s_i]).tolist() if j not in set(mine.tolist())]
                try_pick(order, lambda j, s_i=s_i: Sview[j, s_i], sense, lambda j, s_i=s_i: Smax[j, s_i])
                if len(chosen) >= n:
                    break
            unknowns = [s for s in senses if not s.get("prompt")] or [{"id": "other", "label": "Something else"}]
            k_i = 0
            raw_view = (CV @ qraw).argmax(1)
            while len(chosen) < n:
                if not try_pick(np.argsort(-raw).tolist(), lambda j: raw_view[j], unknowns[min(k_i, len(unknowns) - 1)],
                                lambda j: raw[j]):
                    break
                k_i += 1
        with t.step("localise", f"colour senses: concept/sense colour + OpenCLIP on the top-4 colour cells; others: "
                    f"OpenCLIP 2-stage; {len(chosen)} tiles"):
            plain = [c for c in chosen if c["rule"] is None]
            qs = []
            for c in plain:
                s_i = next((k for k, s in enumerate(prompted) if s is c["sense"]), None)
                qs.append((c["i"], Q[s_i] if s_i is not None else qraw))
            for c, (cell, sc) in zip(plain, self.localise_many(qs)):
                c["cell"], c["cell_score"] = cell, sc
            for c in [c for c in chosen if c["rule"] is not None]:
                s_i = next((k for k, s in enumerate(prompted) if s is c["sense"]), None)
                top = [int(x) for x in np.argsort(-c["cell_colour"])[:4] if c["cell_colour"][x] >= c["colour_min"] * 0.5]
                sc = self._clip_cells(c["i"], top, Q[s_i] if s_i is not None else qraw)
                best = max(range(len(top)), key=lambda k: sc[k] + 0.5 * float(c["cell_colour"][top[k]]))
                c["cell"], c["cell_score"] = top[best], float(sc[best])
        feats = []
        for rank, c in enumerate(chosen, 1):
            j = c["j"]
            s_i = next((k for k, s in enumerate(prompted) if s is c["sense"]), None)
            if s_i is not None:
                mine = np.where(assigned == s_i)[0]
                members = idx[mine[np.argsort(-Smax[mine, s_i])]][:12]
            else:
                members = idx[np.argsort(-raw)][:12]
            cc = c.get("cell_colour")
            feats.append(self.index.feature(c["i"], cell=c["cell"], score=round(c["score"], 4), rank=rank,
                                            raw_score=round(float(raw[j]), 4), sense_id=c["sense"]["id"],
                                            sense_label=c["sense"]["label"], example=f"{self.index.ids[c['i']]}#{c['cell']}",
                                            no_clear_example=False,
                                            cell_colour=None if cc is None else round(float(cc[c["cell"]]), 3),
                                            confidence=round(min(1.0, 0.5 + float(cc[c["cell"]])) if cc is not None else 0.6, 3),
                                            group_members=[self.index.ids[int(m)] for m in members]))
        # senses with no clear example: shown as such rather than as a wrong tile
        for sense in no_clear:
            feats.append({"type": "Feature", "id": f"no_clear:{sense['id']}", "geometry": None, "properties": {
                "sense_id": sense["id"], "sense_label": sense["label"], "no_clear_example": True,
                "rank": len(feats) + 1, "confidence": 0.0,
                "reason": f"no candidate with ≥{sense.get('colour_min')} of its colour ({sense.get('colour')}) in any patch"}})
        clear = sum(1 for f in feats if not f["properties"]["no_clear_example"])
        return self._env(tool, result=Result(geojson=feature_collection(feats), metrics={
            "count": len(feats), "clear_examples": clear, "pool": int(len(idx)), "sense_list": key,
            "senses": [f["properties"]["sense_id"] for f in feats]}),
            confidence=round(float(np.mean([f["properties"]["confidence"] for f in feats])), 3) if feats else None,
            method="OpenCLIP sense prompts (conf/senses.json) → each chip assigned to its best sense → one tile per "
                   f"sense, DINOv2 8×8-cell MMR diversity (cos<{max_sim}); colour senses must show their colour "
                   "(pool-cyan / tarp-blue / dark water) and their box goes on the best colour cell (CLIP among the top 4); "
                   "other senses localised by OpenCLIP in two stages. Confidence = heuristic (colour evidence), not a probability",
            assumptions=["The configured senses cover the plausible meanings of the query"], steps=t.steps)

    def _clip_cells(self, i: int, cells: Sequence[int], q: np.ndarray) -> list[float]:
        """OpenCLIP score of 1.5-cell context crops centred on the given cells (one batch)."""
        from PIL import Image as _I

        if not cells:
            return []
        img = _I.open(io.BytesIO(self.index.thumb_jpeg(i))).convert("RGB")
        g, cp = self.index.grid, config.CHIP_PX // self.index.grid
        size = int(1.5 * cp) + 2
        crops = []
        for c in cells:
            cx, cy = (c % g) * cp + cp / 2, (c // g) * cp + cp / 2
            x0 = int(min(max(cx - size / 2, 0), config.CHIP_PX - size))
            y0 = int(min(max(cy - size / 2, 0), config.CHIP_PX - size))
            crops.append(np.asarray(img.crop((x0, y0, x0 + size, y0 + size)).resize((224, 224), _I.BILINEAR)))
        return [float(s) for s in self._clip_model().embed_images(np.stack(crops), batch=8) @ q]

    # ------------------------------------------------------------------ find_similar
    def _cell_query(self, pos: list[tuple[int, int]], neg: list[tuple[int, int]], neg_weight: float) -> np.ndarray:
        q = np.mean([self.index.cells(i)[c] for i, c in pos], axis=0)
        if neg:
            q = q - neg_weight * np.mean([self.index.cells(i)[c] for i, c in neg], axis=0)
        return q / max(np.linalg.norm(q), 1e-12)

    def _cell_rank(self, q: np.ndarray, k_chips: int, skip: set[int]) -> list[tuple[int, float, int]]:
        C = self.index.n_cells
        D, I = self.index.search_rows("dino", q, (k_chips + len(skip)) * C * 2)
        best: dict[int, tuple[float, int]] = {}
        for d, r in zip(D, I):
            if r < 0:
                continue
            i, c = divmod(int(r), C)
            if i in skip:
                continue
            if i not in best or d > best[i][0]:
                best[i] = (float(d), c)
        ranked = sorted(best.items(), key=lambda t: (-round(t[1][0], 6), self.index.ids[t[0]]))
        return [(i, s, c) for i, (s, c) in ranked]

    def _bbox_rows(self, bbox: Sequence[float] | None) -> np.ndarray | None:
        if not bbox:
            return None
        w, s, e, n = bbox
        m = self.index.meta
        return np.where((m["east"] >= w) & (m["west"] <= e) & (m["north"] >= s) & (m["south"] <= n))[0]

    def _cell_rank_subset(self, q: np.ndarray, rows: np.ndarray, skip: set[int]) -> list[tuple[int, float, int]]:
        """Exact best-cell scores over a subset of chips (bbox search): numpy over the memory-mapped cells."""
        out = []
        for k in range(0, len(rows), 4096):
            r = rows[k:k + 4096]
            S = np.asarray(self.index.dino[r], dtype=np.float32) @ q  # (n, cells)
            best = S.argmax(1)
            out += [(int(i), float(S[j, best[j]]), int(best[j])) for j, i in enumerate(r) if int(i) not in skip]
        out.sort(key=lambda t: (-round(t[1], 6), self.index.ids[t[0]]))
        return out

    def find_similar(self, chip_ids: Sequence[str], exclude_ids: Sequence[str] = (), k: int = 50,
                     neg_weight: float = 0.5, bbox: Sequence[float] | None = None, rerank: bool = False,
                     concept: str | None = None) -> Envelope:
        tool = "find_similar"
        pos = self._example_cells(chip_ids)
        if not pos:
            return declined(tool, VERSION, "none of the example ids are in the index")
        neg = self._example_cells(exclude_ids)
        t = StepTimer()
        with t.step("query_vector", f"{len(pos)} positive cells, {len(neg)} negative cells, λ={neg_weight}"):
            q = self._cell_query(pos, neg, neg_weight)
        skip = {i for i, _ in pos} | {i for i, _ in neg}
        rows = self._bbox_rows(bbox)
        pool = 400
        if rerank:
            from groundtruth.rerank import concept_conf

            pool = int(concept_conf(concept)["rerank"].get("pool", 400))
        with t.step("search", f"best-matching 16.8 m cell over {len(self.index) if rows is None else len(rows)} chips"
                    + (f" in bbox" if rows is not None else "")):
            ranked = (self._cell_rank_subset(q, rows, skip) if rows is not None
                      else self._cell_rank(q, max(k * 3, pool if rerank else 0), skip))
        parts: dict[int, dict] = {}
        info: dict[str, Any] = {}
        if rerank and ranked:
            from groundtruth.rerank import rerank as do_rerank

            with t.step("rerank", f"full-res DINOv2 patches + colour ({concept or 'no concept'}) on top {pool}") as st:
                rr, info = do_rerank(self.index, pos, neg, [i for i, _, _ in ranked[:pool]], concept=concept,
                                     neg_weight=neg_weight)
                st.detail += f" · {info['device']}"
            ranked = [(i, s, c) for i, s, c, _ in rr]
            parts = {i: p for i, _, _, p in rr}
        with t.step("dedupe", "cell NMS (same object in overlapping chips)"):
            hits = self._nms(ranked, k, block=pos + neg)
        feats = [self.index.feature(i, cell=c, score=round(s, 4), rank=r + 1, **({"rerank": parts[i]} if i in parts else {}))
                 for r, (i, s, c) in enumerate(hits)]
        method = ("DINOv2 ViT-B/14 8×8-cell (16.8 m, PCA-192) cosine, best cell per chip; query = mean(positive cells) − "
                  f"{neg_weight}·mean(negative cells)")
        if rerank:
            method += (f"; top {pool} re-ranked by full-res DINOv2 patches (8.4 m) "
                       f"×{info.get('patch_weight')} + concept colour ×{info.get('colour_weight')}")
        return self._env(tool, result=Result(geojson=feature_collection(feats), metrics={
            "count": len(feats), "positives": len(pos), "negatives": len(neg), "reranked": bool(rerank and ranked),
            "bbox": list(bbox) if bbox else None}),
            method=method, assumptions=["The example cells show the intended object"], steps=t.steps)

    # ------------------------------------------------------------------ find_all (core product)
    def _outline_worker(self):
        if getattr(self, "_worker", None) is None:
            import queue
            import threading

            from groundtruth.outline import Outliner

            if self._outliner is None:
                self._outliner = Outliner()
            self._queue: "queue.Queue[tuple[int, int, int | None]]" = queue.Queue()
            self._queued: set[tuple[int, int, int | None]] = set()
            self.__dict__.setdefault("_busy", 0)  # >0 while a search request runs: the outline worker yields the GPU

            def run() -> None:
                while True:
                    i, c, p = self._queue.get()
                    while self._busy > 0:
                        time.sleep(0.05)
                    try:
                        self._outliner.outline(self.index, i, c, patch=p)  # thread-safe, cached
                    except Exception:
                        pass
                    finally:
                        self._queued.discard((i, c, p))

            self._worker = threading.Thread(target=run, daemon=True, name="outline-worker")
            self._worker.start()
        return self._queue

    def _cached_outline(self, i: int, c: int, patch: int | None = None) -> dict[str, Any] | None:
        """Outline from the in-memory cache, else disk (outlines never change for a given index version)."""
        from groundtruth.outline import Outliner

        mem = self.__dict__.setdefault("_outline_mem", {})
        key = (i, c, patch)
        if key in mem:
            return mem[key]
        p = Outliner._cache_path(self.index.ids[i], c, patch)
        if not p.exists():
            return None
        f = json.loads(p.read_text())
        mem[key] = f
        return f

    def object_check(self, outline: dict[str, Any], concept: str | None) -> dict[str, Any]:
        """Post-SAM object check (thresholds in conf/concepts.json "object_check"): area range, compactness and the
        fraction of the outline's pixels in the concept colour. → {pass, reason, area_m2, compactness, colour_frac}."""
        from groundtruth.rerank import _hsv, concept_conf

        cc = concept_conf(concept)
        oc = cc.get("object_check", {})
        rule = cc["rerank"].get("colour")
        p = outline.get("properties", {})
        cache = self.__dict__.setdefault("_check_cache", {})
        key = (outline.get("id"), concept)
        if key in cache:
            return cache[key]
        if not outline.get("geometry"):
            res = {"pass": False, "reason": "no outline"}
            cache[key] = res
            return res
        area = float(p.get("area_m2", 0))
        comp = p.get("compactness")
        if comp is None:
            gm = to_measure(shape(outline["geometry"]))
            comp = 4 * np.pi * gm.area / max(gm.length ** 2, 1e-9)
        colour_frac = colour_area = None
        refined_geom = None
        if rule and oc.get("colour_frac_min", 0) > 0:
            from PIL import Image
            from rasterio.features import rasterize, shapes
            from rasterio.transform import from_origin
            from shapely.geometry import Point

            i = self.index.pos.get(p.get("chip_id"))
            if i is not None:
                img = np.asarray(Image.open(io.BytesIO(self.index.thumb_jpeg(i))).convert("RGB"))
                x0, y0 = float(self.index.meta["x0_m"][i]), float(self.index.meta["y0_m"][i])
                tr = from_origin(x0, y0 + config.CHIP_M, config.GSD_M, config.GSD_M)
                mask = rasterize([to_measure(shape(outline["geometry"]))], out_shape=img.shape[:2], transform=tr) > 0
                if mask.any():
                    lo, hi = np.array(rule["hsv_lo"], np.float32), np.array(rule["hsv_hi"], np.float32)
                    cmask = np.all((_hsv(img) >= lo) & (_hsv(img) <= hi), -1)
                    if oc.get("outline_by_colour"):
                        # refined outline = SAM mask ∩ dilated colour pixels; keep the piece at the prompt (or the largest)
                        dil = cmask.copy()
                        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                            dil |= np.roll(np.roll(cmask, dy, 0), dx, 1)
                        ref = mask & dil
                        polys = [shape(g) for g, v in shapes(ref.astype(np.uint8), mask=ref, transform=tr) if v == 1]
                        if polys:
                            patch = p.get("patch")
                            if patch is not None:
                                pr, pc = divmod(int(patch), 16)
                                pm = config.CHIP_M / 16
                                pt = Point(x0 + (pc + 0.5) * pm, y0 + config.CHIP_M - (pr + 0.5) * pm)
                                best = min(polys, key=lambda g: (g.distance(pt), -g.area))
                            else:
                                best = max(polys, key=lambda g: g.area)
                            best = best.buffer(0).simplify(0.3)
                            area = best.area
                            comp = 4 * np.pi * best.area / max(best.length ** 2, 1e-9)
                            refined_geom = mapping(to_wgs84(best))
                            rmask = rasterize([best], out_shape=img.shape[:2], transform=tr) > 0
                            inside = cmask[rmask] if rmask.any() else np.zeros(1, bool)
                        else:
                            area, inside = 0.0, np.zeros(1, bool)
                    else:
                        inside = cmask[mask]
                    colour_frac = float(inside.mean())
                    colour_area = float(inside.sum()) * config.GSD_M ** 2
        lo_a, hi_a = oc.get("area_m2", [0, 1e12])
        reasons = []
        if not (lo_a <= area <= hi_a):
            reasons.append(f"area {area:.0f} m² outside {lo_a}–{hi_a}")
        if comp < oc.get("compactness_min", 0):
            reasons.append(f"compactness {comp:.2f} < {oc['compactness_min']}")
        if colour_frac is not None and colour_frac < oc.get("colour_frac_min", 0):
            reasons.append(f"colour {colour_frac:.2f} < {oc['colour_frac_min']}")
        if colour_area is not None and colour_area < oc.get("colour_area_min_m2", 0):
            reasons.append(f"colour area {colour_area:.0f} m² < {oc['colour_area_min_m2']}")
        height = None
        if oc.get("max_height_m") is not None and os.environ.get("GROUNDTRUTH_HEIGHT_CHECK", "1") == "1":
            from groundtruth import lidar

            gw = shape(refined_geom or outline["geometry"])
            lp = lidar.place_for(gw.bounds)
            if lp:  # pools sit at ground level; a blue roof or a shadowed roof edge has height
                v = lidar.values(to_measure(gw), lp)
                if len(v):
                    height = float(np.median(v))
                    if height > float(oc["max_height_m"]):
                        reasons.append(f"height {height:.1f} m > {oc['max_height_m']} (lidar)")
        bover = None
        if oc.get("max_building_overlap") is not None:  # on a building footprint → a roof (works without lidar)
            gm_ = to_measure(shape(refined_geom or outline["geometry"]))
            bover = self._building_overlap(gm_)
            if bover > float(oc["max_building_overlap"]):
                reasons.append(f"{100 * bover:.0f}% on a building footprint (Overture)")
        res = {"pass": not reasons, "reason": "; ".join(reasons) or "ok", "area_m2": round(area, 1),
               "compactness": round(float(comp), 3), "colour_frac": None if colour_frac is None else round(colour_frac, 3),
               "colour_area_m2": None if colour_area is None else round(colour_area, 1),
               "height_m": None if height is None else round(height, 2),
               "building_overlap": None if bover is None else round(bover, 2), "refined": refined_geom is not None}
        if refined_geom is not None:
            res["geometry"] = refined_geom
        cache[key] = res
        return res

    def place_bbox(self, place: str | None) -> list[float] | None:
        if not place:
            return None
        places = json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"]
        key = next((k for k in places if k.lower() == place.strip().lower()), None)
        return places[key]["bbox"] if key else None

    def _patch_box_wgs84(self, i: int, patch: int) -> list[float]:
        pm = config.CHIP_M / 16
        pr, pc = divmod(int(patch), 16)
        x0, y0 = float(self.index.meta["x0_m"][i]), float(self.index.meta["y0_m"][i])
        top = y0 + config.CHIP_M
        return self.index.bounds_wgs84((x0 + pc * pm, top - (pr + 1) * pm, x0 + (pc + 1) * pm, top - pr * pm))

    def _building_overlap(self, gm) -> float:
        """Fraction of a geometry (EPSG:32617) on Overture building footprints; buildings cached per ~1 km tile."""
        from shapely.strtree import STRtree

        from groundtruth.vector import store

        cache = self.__dict__.setdefault("_btiles", OrderedDict())
        c = gm.centroid
        key = (int(c.x // 1000), int(c.y // 1000))
        if key not in cache:
            tb = to_wgs84(box(key[0] * 1000 - 100, key[1] * 1000 - 100, key[0] * 1000 + 1100, key[1] * 1000 + 1100)).bounds
            fc, _ = store().buildings(tb)
            bg = [to_measure(shape(f["geometry"])) for f in fc["features"]]
            cache[key] = (bg, STRtree(bg) if bg else None)
            while len(cache) > 256:
                cache.popitem(last=False)
        bg, tree = cache[key]
        if tree is None:
            return 0.0
        return float(sum(gm.intersection(bg[int(j)]).area for j in tree.query(gm, predicate="intersects"))
                     / max(gm.area, 1e-9))

    def _object_feature(self, i: int, cell: int, patch: int | None, score: float, rank: int, concept: str | None,
                        base_conf: float, extra: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Feature for one detected object: outline + object check when cached, else its patch/cell box."""
        from shapely.geometry import box as sbox

        ex_id = f"{self.index.ids[i]}#{cell}" + (f":{patch}" if patch is not None else "")
        ol = self._cached_outline(i, cell, patch)
        chk = self.object_check(ol, concept) if ol else None
        f = self.index.feature(i, cell=cell, score=round(score, 4), rank=rank, example=ex_id, patch=patch, **extra)
        p = f["properties"]
        if ol and ol.get("geometry"):
            refined = chk is not None and "geometry" in chk
            f["geometry"] = chk["geometry"] if refined else ol["geometry"]
            a_m2 = chk["area_m2"] if refined else ol["properties"]["area_m2"]
            q = 0.5 + 0.5 * float(ol["properties"].get("sam_score", 0.8))
            p.update(geometry_kind="outline", area_m2=a_m2, area_acres=round(a_m2 / SQM_PER_ACRE, 5),
                     outline_refined_by_colour=refined, sam_area_m2=ol["properties"]["area_m2"],
                     object_check={k: v for k, v in (chk or {}).items() if k != "geometry"},
                     confidence=round(base_conf * q * (1.0 if chk and chk["pass"] else 0.0), 3))
        else:
            bb = self._patch_box_wgs84(i, patch) if patch is not None else p["cell_bbox"]
            f["geometry"] = mapping(sbox(*bb))
            p.update(geometry_kind="box", area_m2=None, area_acres=None, object_check=None,
                     confidence=round(base_conf * 0.85, 3))
        f["id"] = ex_id
        return f, chk

    @memo(_all_outlined)
    def find_all(self, examples: Sequence[str], exclude: Sequence[str] = (), k: int = 200,
                 bbox: Sequence[float] | None = None, place: str | None = None, concept: str | None = None,
                 rerank: bool = True, outline_top: int = 300, within_layer: str | None = None,
                 dense: bool | None = None, soft_exclude: Sequence[str] = ()) -> Envelope:
        """The count. With a place/bbox: EXHAUSTIVE scan of every chip and cell in the box (scan.py): every object above
        the concept threshold, deduped across chip borders. ``exclude`` = HARD negatives (the user's explicit "not
        this": an object closer to them than to the examples is dropped); ``soft_exclude`` = SOFT negatives (the
        unpicked senses: only nudge the query, never veto an object). Outlines for the top
        ``outline_top`` are queued (SAM 2, patch-prompted) and every cached outline passes an object check (area,
        compactness, colour; conf/concepts.json); failed objects drop out of the count. County-wide (no box): ranked
        top-k mode, stated in limits. Every count in the response is the same unit: deduped objects that are not
        rejected. ``features`` = exactly those objects."""
        tool = "find_all"
        from groundtruth.rerank import concept_conf

        if place and not bbox:
            bbox = self.place_bbox(place)
            if bbox is None:
                return declined(tool, VERSION, f"unknown place {place!r} (see GET /api/v1/places)")
        if within_layer:
            try:
                rows = self._layer_rows(within_layer, bbox)
            except KeyError as e:
                return declined(tool, VERSION, str(e))
            return self._find_all_scan(examples, exclude, bbox, place, concept, outline_top, rows=rows, soft=soft_exclude,
                                       scope=f"chips covering layer '{within_layer}'")
        if bbox:
            return self._find_all_scan(examples, exclude, bbox, place, concept, outline_top, dense=dense,
                                       soft=soft_exclude)
        self.__dict__["_busy"] = self.__dict__.get("_busy", 0) + 1
        try:
            base = self.find_similar(examples, list(exclude) + list(soft_exclude), k=k, bbox=bbox, rerank=rerank,
                                     concept=concept)
        finally:
            self._busy -= 1
        if base.status == "declined":
            return base.model_copy(update={"tool": tool})
        t = StepTimer()
        t.steps.extend(base.steps)
        feats = base.result.geojson["features"]
        conf_cut = float(concept_conf(concept).get("count_conf", 0.5))
        with t.step("objects", f"outline/object check; queue top {min(outline_top, len(feats))} for SAM 2"):
            q = self._outline_worker() if outline_top else None
            scores = [f["properties"]["score"] for f in feats] or [0]
            s_hi, s_lo = max(scores), min(scores)
            out, polys, rejected, n_queued = [], [], 0, 0
            for r, f in enumerate(feats):
                p = f["properties"]
                i, c = self.index.pos[p["chip_id"]], p["cell"]
                rel = float(np.clip((p["score"] - s_lo) / max(s_hi - s_lo, 1e-6), 0, 1))
                feat, chk = self._object_feature(i, c, None, p["score"], r + 1, concept, rel, {})
                if q is not None and chk is None and r < outline_top and (i, c, None) not in self._queued:
                    self._queued.add((i, c, None)); q.put((i, c, None)); n_queued += 1
                if chk is not None and not chk["pass"]:
                    rejected += 1
                    continue
                if feat["properties"]["confidence"] < conf_cut:
                    continue
                gm = to_measure(shape(feat["geometry"]))
                if any(gm.intersection(o).area > 0.5 * min(gm.area, o.area) for o in polys):
                    continue
                polys.append(gm)
                out.append(feat)
        for r, f in enumerate(out):
            f["properties"]["rank"] = r + 1
        metrics = self._object_metrics(out, rejected, n_queued, bbox, place, concept)
        metrics.update(mode="ranked_top_k", confidence_cut=conf_cut)
        limits = self._limits() + [
            f"County-wide mode: only the top {k} ranked matches are examined, so this is NOT an exhaustive count; "
            "give a place or bbox for a full scan",
            "count = deduped objects with confidence ≥ cut that did not fail the object check",
            "Confidence is a heuristic (relative similarity × outline quality × object check), not a calibrated probability"]
        return self._env(tool, result=Result(geojson=feature_collection(out), metrics=metrics),
                         confidence=self._mean_conf(out), method=base.method + "; object dedupe by geometry overlap > 50%; "
                         "SAM 2.1 outlines (lazy, cached) + object check", assumptions=base.assumptions,
                         limits=limits, steps=t.steps)

    @staticmethod
    def _mean_conf(feats: Sequence[dict[str, Any]]) -> float | None:
        cs = [f["properties"].get("confidence") for f in feats if f["properties"].get("confidence") is not None]
        return round(float(np.mean(cs)), 3) if cs else None

    @staticmethod
    def _object_metrics(out: Sequence[dict[str, Any]], rejected: int, queued: int, bbox, place, concept) -> dict[str, Any]:
        areas = [f["properties"]["area_m2"] for f in out if f["properties"].get("area_m2")]
        tot = float(sum(areas))
        return {"count_detected": len(out), "outlined": sum(1 for f in out if f["properties"]["geometry_kind"] == "outline"),
                "pending_outline": sum(1 for f in out if f["properties"]["geometry_kind"] != "outline"),
                "rejected_by_object_check": rejected, "outlines_queued": queued,
                "area_m2_total": round(tot, 1), "area_acres_total": round(tot / SQM_PER_ACRE, 3),
                "median_area_m2": round(float(np.median(areas)), 1) if areas else None,
                "bbox": list(bbox) if bbox else None, "place": place, "concept": concept,
                "count_unit": "deduped objects not rejected by the object check (same unit as features)"}

    def _layer_rows(self, layer: str, bbox: Sequence[float] | None = None) -> np.ndarray:
        """Chip rows whose footprint intersects any feature of a layer (optionally within a bbox)."""
        from shapely.strtree import STRtree

        fc = self.provider.get_layer(layer, tuple(bbox) if bbox else None)
        geoms = [shape(f["geometry"]) for f in fc["features"] if f.get("geometry")]
        if not geoms:
            return np.zeros(0, dtype=int)
        tree = STRtree(geoms)
        m = self.index.meta
        cand = self._bbox_rows(bbox) if bbox else np.arange(len(self.index))
        lb = np.array([g.bounds for g in geoms])
        w, s, e, n = lb[:, 0].min(), lb[:, 1].min(), lb[:, 2].max(), lb[:, 3].max()
        cand = cand[(m["east"][cand] >= w) & (m["west"][cand] <= e) & (m["north"][cand] >= s) & (m["south"][cand] <= n)]
        keep = [int(i) for i in cand if len(tree.query(box(m["west"][i], m["south"][i], m["east"][i], m["north"][i]),
                                                      predicate="intersects"))]
        return np.array(keep, dtype=int)

    def _find_all_scan(self, examples: Sequence[str], exclude: Sequence[str], bbox: Sequence[float] | None,
                       place: str | None, concept: str | None, outline_top: int, rows: np.ndarray | None = None,
                       scope: str | None = None, dense: bool | None = None, soft: Sequence[str] = ()) -> Envelope:
        from groundtruth.scan import scan

        tool = "find_all"
        pos = self._example_cells(examples)
        if not pos:
            return declined(tool, VERSION, "none of the example ids are in the index")
        neg = self._example_cells(exclude)
        softc = self._example_cells(soft)
        if rows is None:
            rows = self._bbox_rows(bbox)
        scope = scope or "the box"
        t = StepTimer()
        self.__dict__["_busy"] = self.__dict__.get("_busy", 0) + 1
        try:
            with t.step("scan", f"every chip + cell in {scope} ({len(rows)} chips, {len(rows) * self.index.n_cells} cells)") as st:
                # scan results are deterministic for (examples, negatives, chips, concept config): cache them so the
                # warm-up makes every demo place instant (GROUNDTRUTH_SCAN_CACHE entries, default 64)
                cache = self.__dict__.setdefault("_scan_cache", OrderedDict())
                from groundtruth.rerank import concept_conf as _cc

                key = (tuple(pos), tuple(neg), tuple(softc), hash(np.asarray(rows).tobytes()), concept,
                       json.dumps(_cc(concept), sort_keys=True))
                if key in cache:
                    cache.move_to_end(key)
                    dets, info = cache[key]
                    st.detail += " (cached scan)"
                else:
                    dets, info = scan(self, pos, neg, rows, concept, soft=softc)
                    cache[key] = (dets, info)
                    while len(cache) > int(os.environ.get("GROUNDTRUTH_SCAN_CACHE", "64")):
                        cache.popitem(last=False)
                st.detail += (f" → {info.get('chips_prefiltered', 0)} chips at full resolution ({info.get('device', '-')}) → "
                              f"{info.get('raw_detections', 0)} raw → {info.get('objects', 0)} objects after dedupe")
        finally:
            self._busy -= 1
        from groundtruth.rerank import concept_conf

        dconf = concept_conf(concept)["detect"]
        smin = float(dconf["score_min"]) if "sim_min" not in dconf else 0.6 * float(dconf["sim_min"]) + 0.4 * float(dconf["colour_min"])
        with t.step("objects", f"outline/object check; queue top {min(outline_top, len(dets))} for SAM 2"):
            q = self._outline_worker() if outline_top else None
            out, rejected, n_queued = [], 0, 0
            for r, d in enumerate(dets):
                base = float(np.clip(0.5 + (d.score - smin) / 0.3, 0, 1))
                feat, chk = self._object_feature(d.row, d.cell, d.patch, d.score, r + 1, concept, base,
                                                 {"patch_sim": round(d.patch_sim, 4), "colour": round(d.colour, 3),
                                                  "n_patches": d.n_patches, "x_m": round(d.x_m, 2), "y_m": round(d.y_m, 2)})
                if q is not None and chk is None and r < outline_top and (d.row, d.cell, d.patch) not in self._queued:
                    self._queued.add((d.row, d.cell, d.patch)); q.put((d.row, d.cell, d.patch)); n_queued += 1
                if chk is not None and not chk["pass"]:
                    rejected += 1
                    continue
                out.append(feat)
        dense_info = None
        if self._dense_wanted(dense, place, concept):
            with t.step("dense", "dense proposals (colour blobs in centred windows, SAM 2 + verifier, cached) matched "
                                 "to the examples, merged with the scan by distance") as st:
                out, dense_info = self._merge_dense(out, pos, neg, place, concept, smin, soft=softc)
                st.detail += (f": {dense_info['dense_matched']} dense matches, {dense_info['dense_added']} not found by "
                              f"the standard scan → {len(out)} objects")
        for r, f in enumerate(out):
            f["properties"]["rank"] = r + 1
        metrics = self._object_metrics(out, rejected, n_queued, bbox, place, concept)
        metrics.update(mode="exhaustive_scan", **{k: v for k, v in info.items() if k != "device"}, device=info.get("device"))
        metrics["dense"] = dense_info is not None
        if dense_info:
            metrics.update(dense_info)
        limits = self._limits() + [
            f"Exhaustive within {scope}: all {info['chips_in_box']} chips / {info['cells_scanned']} cells scored; objects "
            "below the concept threshold (conf/concepts.json) are not counted",
            "count = deduped objects (merged within the concept's nms_m) that did not fail the object check; "
            "objects not yet outlined are counted on their image evidence and may still be rejected once outlined",
            "Confidence is a heuristic (margin over the threshold × outline quality × object check), not a probability"]
        method = ("Exhaustive scan: DINOv2 8×8 cells for every chip in the box → prefilter (cell similarity "
                  "or concept colour) → full-res DINOv2 patches + concept colour → connected patch groups = "
                  "objects → closer to positives than to the 'not this' negatives → dedupe by distance → "
                  "SAM 2.1 outline (patch-prompted, lazy) → object check (area, compactness, colour)")
        if dense_info:
            method = ("DENSE mode: " + method + "; PLUS every concept-colour blob in the place re-tiled into a centred "
                      "224 px window → DINOv2 patch token + SAM 2.1 outline + the same object check (precomputed, "
                      f"{dense_info['dense_built']}) → matched to the examples with the same rule → merged by distance")
            limits.append(f"Dense mode (precomputed for {place}): adds colour-blob proposals the chip grid missed; objects "
                          "without the concept colour are only found by the standard scan")
        return self._env(tool, result=Result(geojson=feature_collection(out), metrics=metrics),
                         confidence=self._mean_conf(out), method=method,
                         assumptions=["The picked examples show the intended object; the unpicked senses are negatives"],
                         limits=limits, steps=t.steps)

    def _dense_wanted(self, dense: bool | None, place: str | None, concept: str | None) -> bool:
        """dense=None → on when a dense build exists for this place/concept/year and conf says dense.auto (default on)."""
        from groundtruth import dense as D
        from groundtruth.rerank import concept_conf

        if dense is False or not D.available(place, concept, self.year):
            return False
        dc = concept_conf(concept).get("dense", {})
        if place in dc.get("disabled_places", []) and not dense:  # measured worse there; explicit dense=true still works
            return False
        return bool(dense) or bool(dc.get("auto", True))

    def _merge_dense(self, out: list[dict[str, Any]], pos, neg, place: str, concept: str, smin: float, soft=()):
        from groundtruth import dense as D
        from groundtruth.rerank import concept_conf
        from groundtruth.scan import query_vectors

        cache = self.__dict__.setdefault("_dense_sets", {})
        key = (place, concept, self.year)
        if key not in cache:
            cache[key] = D.DenseSet(place, concept, self.year)
        ds: D.DenseSet = cache[key]
        det = concept_conf(concept)["detect"]
        P, N, qp = query_vectors(self, pos, neg, concept, soft=soft)
        match = ds.match(P, N, qp, det)
        S = ds.sims(qp)
        ids = ds.index_ids(self.index)
        r2 = float(det["nms_m"]) ** 2
        std_pts = np.array([[f["properties"].get("x_m", np.nan), f["properties"].get("y_m", np.nan)] for f in out]) \
            if out else np.zeros((0, 2))
        if out and np.isnan(std_pts).any():  # standard features carry no x/y: use the geometry centroid
            std_pts = np.array([to_measure(shape(f["geometry"])).centroid.coords[0] for f in out])
        merged, added, replaced = list(out), 0, 0
        for k in match:
            f = json.loads(json.dumps(ds.feats[k]))
            if f.get("geometry") is None or ids[k] is None:
                continue
            p = f["properties"]
            conf = float(np.clip(0.5 + (float(S[k]) - smin) / 0.3, 0, 1)) * (0.5 + 0.5 * float(p.get("sam_score", 0)))
            p.update(example=ids[k], dense_id=f["id"], score=round(float(S[k]), 4), patch_sim=round(float(S[k]), 4),
                     colour=round(float(ds.colour[k]), 3), confidence=round(conf, 3), geometry_kind="outline")
            # matched ⇒ passed the dense verifier (current thresholds), whatever the build-time strict check said
            p["object_check"] = {**p.get("object_check", {}), "pass": True, "reason": "ok (dense verifier)"}
            reg = self.__dict__.setdefault("_dense_by_id", {})  # refine re-scores these without losing the outline
            near = (((std_pts - (p["x_m"], p["y_m"])) ** 2).sum(1) < r2) if len(std_pts) else np.zeros(0, bool)
            if near.any():
                j = int(np.argmax(near))
                if merged[j]["properties"].get("geometry_kind") != "outline":  # dense outline replaces a pending box
                    merged[j] = {**f, "id": merged[j].get("id", f["id"])}
                    reg[p["example"]] = merged[j]
                    replaced += 1
                continue
            reg[p["example"]] = f
            merged.append(f)
            std_pts = np.vstack([std_pts, (p["x_m"], p["y_m"])]) if len(std_pts) else np.array([[p["x_m"], p["y_m"]]])
            added += 1
        merged.sort(key=lambda f: -float(f["properties"].get("confidence") or 0))
        return merged, {"dense_matched": len(match), "dense_added": added, "dense_replaced_boxes": replaced,
                        "dense_proposals": ds.meta["proposals"], "dense_passed_verifier": ds.meta["passed_verifier"],
                        "dense_built": ds.meta["built"]}

    # ------------------------------------------------------------------ compare_years (search across time)
    @memo()
    def compare_years(self, examples: Sequence[str], from_year: int = 2020, concept: str | None = None) -> Envelope:
        """For each result (``chip#cell`` in this engine's year), look at the same cell in ``from_year``: DINOv2 cell
        similarity across years + concept colour fraction then vs now → change = new | existing | unsure."""
        tool = "compare_years"
        from groundtruth.rerank import colour_patches, concept_conf, _cell_patches
        from PIL import Image

        other_dir = config.precompute_dir(from_year, self.aoi)
        if not (other_dir / "index.json").exists():
            return declined(tool, VERSION, f"no {from_year} index for aoi '{self.aoi}' yet")
        cache = self.__dict__.setdefault("_other_index", {})
        if from_year not in cache:
            cache[from_year] = ChipIndex(other_dir)
        other: ChipIndex = cache[from_year]
        cc = concept_conf(concept)
        th = {**concepts_default_change(), **cc.get("change", {})}
        rule = cc["rerank"].get("colour")
        t = StepTimer()
        feats, counts = [], {"new": 0, "existing": 0, "unsure": 0, "no_data": 0}
        with t.step("compare", f"{len(examples)} cells vs {from_year} (cell similarity + {'colour' if rule else 'no colour'})"):
            for ex in examples:
                cid, cell = parse_example(ex)
                if cid not in self.index.pos:
                    continue
                i = self.index.pos[cid]
                if cell is None:
                    cell = self.index.salient_cell(i)
                oid = f"{from_year}_" + cid.split("_", 1)[1]
                if oid not in other.pos:
                    counts["no_data"] += 1
                    feats.append(self.index.feature(i, cell=cell, change="no_data", from_year=from_year))
                    continue
                j = other.pos[oid]
                sim = float(self.index.cells(i)[cell] @ other.cells(j)[cell])
                col_now = col_then = None
                if rule:
                    ps = _cell_patches(cell, self.index.grid)
                    now = np.asarray(Image.open(io.BytesIO(self.index.thumb_jpeg(i))).convert("RGB"))
                    then = np.asarray(Image.open(io.BytesIO(other.thumb_jpeg(j))).convert("RGB"))
                    col_now = float(colour_patches(now, rule).reshape(-1)[ps].max())
                    col_then = float(colour_patches(then, rule).reshape(-1)[ps].max())
                if rule and col_now is not None:
                    if sim >= th["sim_existing_min"] or (col_now > 0 and col_then >= th["colour_then_keep"] * col_now):
                        change = "existing"
                    elif col_now >= th["colour_now_min"] and sim <= th["sim_new_max"]:
                        change = "new"
                    else:
                        change = "unsure"
                else:
                    change = "new" if sim <= th["sim_new_max"] else "existing" if sim >= th["sim_existing_min"] else "unsure"
                counts[change] += 1
                feats.append(self.index.feature(i, cell=cell, change=change, from_year=from_year,
                                                change_sim=round(sim, 3),
                                                colour_now=None if col_now is None else round(col_now, 3),
                                                colour_then=None if col_then is None else round(col_then, 3),
                                                thumb_url_then=f"/api/v1/chips/{oid}.jpg?year={from_year}"))
        return self._env(tool, status=self._status(), result=Result(geojson=feature_collection(feats), metrics={
            **counts, "from_year": from_year, "to_year": self.year, "concept": concept}),
            confidence=round((counts["new"] + counts["existing"]) / max(1, sum(counts.values())), 3),
            method=(f"Same 16.8 m cell in NAIP {from_year} vs {self.year} (fixed EPSG:32617 grid, shared PCA basis): DINOv2 "
                    "cell cosine" + ("; concept colour fraction then vs now (HSV range from conf/concepts.json)" if rule else "")
                    + f"; thresholds {th}"),
            assumptions=[f"The {from_year} and {self.year} orthoimages are co-registered to within ~1–2 m"],
            limits=self._limits() + [
                f"NAIP {from_year} and {self.year} differ in season, sun angle and colour balance; covered or drained pools look 'not a pool'",
                "'new' means the concept's appearance is present now and absent then in this cell, not a permit or construction date",
                "unsure = signals disagree; treat as needing a look"], steps=t.steps)

    # ------------------------------------------------------------------ refine
    def refine(self, session_id: str | None, accept_ids: Sequence[str] = (), reject_ids: Sequence[str] = (),
               k: int = 50, pool: int = 1500, bbox: Sequence[float] | None = None, place: str | None = None,
               concept: str | None = None, candidates: Sequence[str] | None = None) -> Envelope:
        """Per-session logistic regression on DINOv2 cell vectors (accepted vs rejected + background cells).
        With ``candidates`` (the object ids from find_all): re-scores EXACTLY those objects, so every count stays in one
        unit (deduped objects): accepted → kept (P = 1), rejected → removed, others counted when P ≥ 0.5 and not
        rejected by the object check. Without candidates: legacy mode over a cell-search pool (place/bbox if given).
        Each hit says why: its nearest accepted example. Target < 1 s."""
        from sklearn.linear_model import LogisticRegression

        tool = "refine"
        sid = session_id or uuid.uuid4().hex[:12]
        sess = self.sessions.setdefault(sid, Session(sid))
        sess.accept += [a for a in accept_ids if a not in sess.accept]
        sess.reject = [r for r in sess.reject if r not in accept_ids]
        sess.accept = [a for a in sess.accept if a not in reject_ids]
        sess.reject += [r for r in reject_ids if r not in sess.reject]
        pos = self._example_cells(sess.accept)
        neg = self._example_cells(sess.reject)
        if not pos:
            return declined(tool, VERSION, "refine needs at least one accepted example")
        if place and not bbox:
            bbox = self.place_bbox(place)
        if candidates:
            return self._refine_objects(sid, sess, pos, neg, candidates, concept)
        t = StepTimer()
        skip = {i for i, _ in pos} | {i for i, _ in neg}
        key = (tuple(bbox) if bbox else None, concept)
        with t.step("pool", "candidate chips (cached per session)" if getattr(sess, "pool_key", None) == key
                    else f"cell search from the first accepted example → {pool} chips"):
            if getattr(sess, "pool_key", None) != key or not getattr(sess, "pool", None):
                q = self._cell_query(pos[:1], [], 0.5)
                rows = self._bbox_rows(bbox)
                ranked = (self._cell_rank_subset(q, rows, set()) if rows is not None else self._cell_rank(q, pool, set()))
                sess.pool = [i for i, _, _ in ranked[:pool]]
                sess.pool_key = key
            cand = [i for i in sess.pool if i not in skip]
        with t.step("fit", f"logistic regression: {len(pos)} accepted, {len(neg)} rejected + background"):
            rng = np.random.default_rng(0)
            bg_i = rng.choice(len(self.index), size=min(150, len(self.index)), replace=False)
            bg = np.stack([self.index.cells(int(i))[int(rng.integers(self.index.n_cells))] for i in bg_i])
            Xp = np.stack([self.index.cells(i)[c] for i, c in pos])
            Xn = np.concatenate([np.stack([self.index.cells(i)[c] for i, c in neg])] if neg else [] + [bg])
            X = np.concatenate([Xp, Xn])
            y = np.r_[np.ones(len(Xp)), np.zeros(len(Xn))]
            clf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=500).fit(X, y)
        with t.step("score", f"{len(cand)} chips × {self.index.n_cells} cells"):
            cells = np.asarray(self.index.dino[np.array(cand)], dtype=np.float32) if cand else np.zeros((0, 16, 768))
            P = clf.predict_proba(cells.reshape(-1, cells.shape[-1]))[:, 1].reshape(len(cand), -1) if cand else cells
            best_c = P.argmax(1) if cand else []
            ranked = sorted(((cand[j], float(P[j, best_c[j]]), int(best_c[j])) for j in range(len(cand))),
                            key=lambda h: (-round(h[1], 6), self.index.ids[h[0]]))
            hits = self._nms(ranked, k, block=pos + neg)
        with t.step("why", "nearest accepted example per hit"):
            A = np.stack([self.index.cells(i)[c] for i, c in pos])
            feats = []
            for r, (i, p, c) in enumerate(hits):
                sims = A @ self.index.cells(i)[c]
                j = int(np.argmax(sims))
                f = self.index.feature(i, cell=c, score=round(p, 4), rank=r + 1, confidence=round(p, 3),
                                       example=f"{self.index.ids[i]}#{c}",
                                       why={"nearest_accepted": f"{self.index.ids[pos[j][0]]}#{pos[j][1]}",
                                            "similarity": round(float(sims[j]), 3)})
                ol = self._cached_outline(i, c)
                if ol and ol.get("geometry"):
                    f["geometry"] = ol["geometry"]
                    f["properties"].update(geometry_kind="outline", area_m2=ol["properties"]["area_m2"],
                                           area_acres=ol["properties"]["area_acres"])
                else:
                    f["geometry"] = mapping(box(*f["properties"]["cell_bbox"]))
                    f["properties"].update(geometry_kind="cell", area_m2=None, area_acres=None)
                feats.append(f)
        total_ms = sum(s.ms for s in t.steps)
        return self._env(tool, result=Result(geojson=feature_collection(feats), metrics={
            "count": len(feats), "session_id": sid, "accepted": len(pos), "rejected": len(neg),
            "pool_chips": len(cand), "refine_ms": round(total_ms, 1)}),
            confidence=None,
            method="Logistic regression (sklearn, C=1, balanced) on DINOv2 8×8-cell (16.8 m, PCA-192) vectors; positives = accepted "
                   "cells, negatives = rejected cells + 150 random background cells; scores = P(match) of best cell",
            assumptions=["Accepted/rejected examples are representative"], steps=t.steps)

    def _refine_objects(self, sid: str, sess: Session, pos, neg, candidates: Sequence[str],
                        concept: str | None) -> Envelope:
        from sklearn.linear_model import LogisticRegression

        tool = "refine"
        t = StepTimer()
        acc_ids, rej_ids = set(sess.accept), set(sess.reject)
        items = []
        for ex in candidates:
            cid, c, p = parse_example_full(ex)
            if cid in self.index.pos and ex not in rej_ids:
                i = self.index.pos[cid]
                items.append((ex, i, c if c is not None else self.index.salient_cell(i), p))
        with t.step("fit", f"logistic regression: {len(pos)} accepted, {len(neg)} rejected + background"):
            rng = np.random.default_rng(0)
            bg_i = rng.choice(len(self.index), size=min(150, len(self.index)), replace=False)
            bg = np.stack([self.index.cells(int(i))[int(rng.integers(self.index.n_cells))] for i in bg_i])
            Xp = np.stack([self.index.cells(i)[c] for i, c in pos])
            Xn = np.concatenate(([np.stack([self.index.cells(i)[c] for i, c in neg])] if neg else []) + [bg])
            clf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=500).fit(
                np.concatenate([Xp, Xn]), np.r_[np.ones(len(Xp)), np.zeros(len(Xn))])
        with t.step("score", f"{len(items)} objects (the find_all set)"):
            X = np.stack([self.index.cells(i)[c] for _, i, c, _ in items]) if items else np.zeros((0, Xp.shape[1]))
            P = clf.predict_proba(X)[:, 1] if len(items) else np.zeros(0)
            A = Xp
        counted, below, rejected_check = [], 0, 0
        with t.step("objects", "outline + object check + why"):
            order = sorted(range(len(items)), key=lambda j: (-(1.0 if items[j][0] in acc_ids else float(P[j])), items[j][0]))
            for j in order:
                ex, i, c, p = items[j]
                prob = 1.0 if ex in acc_ids else float(P[j])
                sims = A @ self.index.cells(i)[c]
                b = int(np.argmax(sims))
                extra = {"accepted": ex in acc_ids,
                         "why": {"nearest_accepted": sess.accept[b] if b < len(sess.accept) else None,
                                 "similarity": round(float(sims[b]), 3)}}
                dense_f = self.__dict__.get("_dense_by_id", {}).get(ex)
                if dense_f is not None:  # keep the dense outline + its verification (no fallback to a pending box)
                    feat = json.loads(json.dumps(dense_f))
                    feat["properties"].update(extra, score=round(prob, 4))
                    chk = feat["properties"].get("object_check")
                else:
                    feat, chk = self._object_feature(i, c, p, prob, 0, concept, prob, extra)
                if chk is not None and not chk["pass"]:
                    rejected_check += 1
                    continue
                if prob < 0.5:
                    below += 1
                    continue
                feat["properties"]["confidence"] = round(prob if chk is None or chk["pass"] else 0.0, 3)
                counted.append(feat)
        for r, f in enumerate(counted):
            f["properties"]["rank"] = r + 1
        metrics = self._object_metrics(counted, rejected_check, 0, None, None, concept)
        metrics.update(session_id=sid, accepted=len(pos), rejected=len(neg), user_rejected=len(rej_ids),
                       below_cut=below, candidates=len(candidates), refine_ms=round(sum(s.ms for s in t.steps), 1),
                       mode="objects")
        return self._env(tool, result=Result(geojson=feature_collection(counted), metrics=metrics),
                         confidence=self._mean_conf(counted),
                         method="Logistic regression (sklearn, C=1, balanced) on DINOv2 8×8-cell vectors of the find_all "
                                "objects; positives = accepted, negatives = rejected + 150 background cells; counted = "
                                "accepted, or P(match) ≥ 0.5 and not rejected by the object check",
                         assumptions=["Accepted/rejected examples are representative"],
                         limits=self._limits() + ["count = deduped objects from the find_all set (same unit as find_all)",
                                                  "P(match) is the classifier's output on few examples: indicative, not calibrated"],
                         steps=t.steps)

    # ------------------------------------------------------------------ outline
    def outline(self, examples: Sequence[str], max: int = 12, cached_only: bool = False,  # noqa: A002
                concept: str | None = None) -> Envelope:
        """SAM 2.1 polygons prompted by each id's patch (``chip#cell:patch``) or cell. Lazy: only the ids given (the
        results on screen), at most ``max``; cached. ``cached_only`` never runs SAM (for polling while find_all's
        background queue works). With ``concept``, each outline carries ``object_check`` {pass, reason, …}; rejected
        objects are listed in ``metrics.rejected`` so a client can drop them from its count."""
        tool = "outline"
        items = []
        for ex in examples:
            cid, c, p = parse_example_full(ex)
            if cid in self.index.pos:
                i = self.index.pos[cid]
                items.append((i, c if c is not None else self.index.salient_cell(i), p))
        items = items[:max]
        if not items:
            return declined(tool, VERSION, "none of the example ids are in the index")

        def checked(fs: list[dict[str, Any]]) -> list[str]:
            rej = []
            if concept:
                for f in fs:
                    chk = self.object_check(f, concept)
                    if "geometry" in chk:
                        f["properties"]["sam_area_m2"] = f["properties"].get("area_m2")
                        f["geometry"] = chk["geometry"]
                        f["properties"]["area_m2"] = chk["area_m2"]
                        f["properties"]["area_acres"] = round(chk["area_m2"] / SQM_PER_ACRE, 5)
                        f["properties"]["outline_refined_by_colour"] = True
                    f["properties"]["object_check"] = {k: v for k, v in chk.items() if k != "geometry"}
                    if not chk["pass"]:
                        rej.append(f["properties"].get("example") or f.get("id"))
            return rej

        if cached_only:
            t = StepTimer()
            with t.step("cache", f"{len(items)} ids, cached only"):
                feats = [f for f in (self._cached_outline(i, c, p) for i, c, p in items) if f]
                feats = [json.loads(json.dumps(f)) for f in feats]
                rej = checked(feats)
            done = [f for f in feats if f.get("geometry")]
            tot = sum(f["properties"]["area_m2"] for f in done if (f["properties"].get("example") or f.get("id")) not in rej)
            return self._env(tool, status="ok" if len(feats) == len(items) else "partial",
                             result=Result(geojson=feature_collection(feats), metrics={
                                 "count": len(done), "pending": len(items) - len(feats), "cached": len(feats),
                                 "rejected": rej, "area_m2_total": round(tot, 2),
                                 "area_acres_total": round(tot / SQM_PER_ACRE, 4)}),
                             confidence=1.0, method="cached SAM 2.1 outlines only (no new computation)"
                             + (" + object check" if concept else ""), steps=t.steps)
        if self._outliner is None:
            from groundtruth.outline import Outliner

            self._outliner = Outliner()
        t = StepTimer()
        feats = []
        with t.step("sam2", f"{len(items)} outlines (SAM 2.1 hiera-tiny, {self._outliner.device_name})"):
            for i, c, p in items:
                feats.append(json.loads(json.dumps(self._outliner.outline(self.index, i, c, patch=p))))
            rej = checked(feats)
        done = [f for f in feats if f.get("geometry")]
        fresh = [f["properties"]["ms"] for f in feats if not f["properties"].get("cached")]
        tot = sum(f["properties"]["area_m2"] for f in done)
        return self._env(tool, status="ok" if len(done) == len(feats) else "partial",
                         result=Result(geojson=feature_collection(feats), metrics={
                             "count": len(done), "empty": len(feats) - len(done),
                             "area_m2_total": round(tot, 2), "area_acres_total": round(tot / SQM_PER_ACRE, 4),
                             "ms_per_outline": round(float(np.mean(fresh)), 1) if fresh else None,
                             "cached": len(feats) - len(fresh), "rejected": rej}),
                         confidence=round(float(np.mean([f["properties"].get("sam_score", 0) for f in done])), 3) if done else None,
                         method="SAM 2.1 hiera-tiny (Apache-2.0); prompt = the object's 8.4 m patch (or 16.8 m cell) centre "
                                "point + box; component containing the prompt; simplified 0.3 m; areas in EPSG:32617"
                                + ("; object check (area, compactness, colour; conf/concepts.json)" if concept else ""),
                         assumptions=["The object of interest is inside the prompted box"],
                         limits=self._limits() + ["Outlines from 0.6 m imagery: edges ±1 px (0.6 m)"], steps=t.steps)

    # ------------------------------------------------------------------ measure
    def _layer_geom(self, name: str, where: dict[str, Any] | None = None, near: Sequence[dict[str, Any]] | None = None):
        """Union of a layer in EPSG:32617 (cached). With ``near`` (features), only layer parts within ~5 km of their
        extent are merged, which keeps big layers (FEMA) fast."""
        from shapely import make_valid
        from shapely.ops import unary_union

        cache = self.__dict__.setdefault("_layer_cache", {})
        clip = None
        if near:
            xs = [shape(f["geometry"]).bounds for f in near]
            clip = box(min(b[0] for b in xs) - 0.05, min(b[1] for b in xs) - 0.05,
                       max(b[2] for b in xs) + 0.05, max(b[3] for b in xs) + 0.05)
        key = (name, json.dumps(where or {}, sort_keys=True), tuple(round(v, 2) for v in clip.bounds) if clip else None)
        if key not in cache:
            fc = self.provider.get_layer(name, tuple(clip.bounds) if clip else None)
            gs = [make_valid(shape(f["geometry"])) for f in fc["features"] if f.get("geometry") and
                  all(f["properties"].get(k) == v for k, v in (where or {}).items())]
            cache[key] = to_measure(unary_union(gs).buffer(0) if any(g.area for g in gs) else unary_union(gs)) if gs else None
        return cache[key]

    def measure(self, geojson: dict[str, Any], *, to_layer: str | None = None,
                to_layers: Sequence[str] = ()) -> Envelope:
        """Per feature: area_m2, area_acres, perimeter_m, dist_m_to_<layer>. Totals: count, area_acres_total,
        median_area_m2, min/median dist per layer. Measured in EPSG:32617; geometry I/O in EPSG:4326."""
        tool = "measure"
        feats = geojson.get("features", [])
        if not feats:
            return declined(tool, VERSION, "no features to measure")
        layers = [l for l in ([to_layer] if to_layer else []) + list(to_layers) if l]
        t = StepTimer()
        geoms = {}
        for l in layers:
            with t.step("load_layer", l):
                geoms[l] = self._layer_geom(l, near=feats)
        out, areas = [], []
        dists: dict[str, list[float]] = {l: [] for l in layers}
        from shapely.ops import unary_union

        from groundtruth import lidar

        fb = unary_union([shape(f["geometry"]) for f in feats]).bounds
        lidar_place = lidar.place_for(fb)
        heights: list[float] = []
        with t.step("measure", f"{len(feats)} features in EPSG:32617" + (f" + lidar heights ({lidar_place})" if lidar_place else "")):
            for f in feats:
                g = shape(f["geometry"])
                gm = to_measure(g)
                p = dict(f.get("properties") or {})
                p["area_m2"] = round(gm.area, 2)
                p["area_acres"] = round(gm.area / SQM_PER_ACRE, 5)
                p["perimeter_m"] = round(gm.length if gm.geom_type in ("Polygon", "MultiPolygon") else length_m(g), 2)
                if lidar_place and gm.geom_type in ("Polygon", "MultiPolygon"):
                    hs = lidar.height_stats(gm, lidar_place)
                    if hs:
                        p.update(hs)
                        heights.append(hs["height_m"])
                for l, lg in geoms.items():
                    if lg is not None:
                        d = round(gm.distance(lg), 1)
                        p[f"dist_m_to_{l}"] = d
                        dists[l].append(d)
                areas.append(gm.area)
                out.append({"type": "Feature", "id": f.get("id"), "geometry": f["geometry"], "properties": p})
        tot = float(sum(areas))
        metrics = {"count": len(out), "area_m2_total": round(tot, 2), "area_acres_total": round(tot / SQM_PER_ACRE, 4),
                   "median_area_m2": round(float(np.median(areas)), 2)}
        for l, ds in dists.items():
            if ds:
                metrics[f"min_dist_m_to_{l}"] = min(ds)
                metrics[f"median_dist_m_to_{l}"] = round(float(np.median(ds)), 1)
        lim = ["UTM 17N distortion < 0.1% in Mecklenburg",
               "Distances are to the simplified web build of a layer when one exists (~3 m)"]
        if heights:
            metrics.update(median_height_m=round(float(np.median(heights)), 2), max_height_m=round(max(heights), 2),
                           heights_from="USGS 3DEP lidar 2016 (nDSM, 1 m)")
            lim.append("Heights: 90th percentile of lidar height-above-ground inside each outline (3DEP 2016: anything "
                       "built or grown since then is not reflected); volume_m3 = Σ height × 1 m²")
        return self._env(tool, status="ok", result=Result(geojson=feature_collection(out), metrics=metrics),
                         confidence=1.0, method="Planar measurement after reprojection EPSG:4326 → EPSG:32617 (UTM 17N)"
                         + ("; height/volume from the lidar nDSM" if heights else ""),
                         assumptions=["Input geometry is EPSG:4326"],
                         limits=lim,
                         sources=[s for l in layers for s in (self.provider.source_for(
                             {"osm": "openstreetmap"}.get(l.split("_")[0], l.split("_")[0])))], steps=t.steps)

    # ------------------------------------------------------------------ layers
    @memo()
    def add_layer(self, name: str, bbox: Sequence[float] | None = None) -> Envelope:
        tool = "add_layer"
        try:
            t = StepTimer()
            with t.step("load", name):
                fc = self.provider.get_layer(name, tuple(bbox) if bbox else None)
        except KeyError as e:
            return declined(tool, VERSION, str(e))
        return self._env(tool, status="ok", result=Result(geojson=fc, metrics={"count": len(fc["features"])}),
                         confidence=1.0, method=f"reference layer '{name}' from {self.provider.name} provider",
                         limits=[], sources=self.provider.source_for(
                             {"osm": "openstreetmap"}.get(name.split("_")[0], name.split("_")[0])) or self.provider.sources(),
                         steps=t.steps)

    def filter(self, geojson: dict[str, Any], where: dict[str, Any]) -> Envelope:
        """``where`` keys (all optional, ANDed):
        ``within_m_of``: {layer, m} or a list of them (e.g. {"layer": "osm_i485", "m": 1000});
        ``inside_place``: a name from GET /places (e.g. "Ballantyne");
        ``intersects_layer`` (+ ``layer_where`` {prop: value}), ``not_intersects_layer``;
        ``min_area_acres`` / ``max_area_acres`` (uses the feature's area_acres when present, else its geometry);
        ``min_confidence``, ``min_score``. Legacy: ``dist_layer`` + ``max_dist_m``."""
        tool = "filter"
        t = StepTimer()
        feats = geojson.get("features", [])
        within = where.get("within_m_of") or []
        within = [within] if isinstance(within, dict) else list(within)
        if where.get("dist_layer"):
            within.append({"layer": where["dist_layer"], "m": float(where.get("max_dist_m", 0))})
        with t.step("prepare", json.dumps(where)[:160]):
            try:
                inter = (self._layer_geom(where["intersects_layer"], where.get("layer_where"), near=feats)
                         if where.get("intersects_layer") and feats else None)
                notinter = self._layer_geom(where["not_intersects_layer"], near=feats) if where.get("not_intersects_layer") and feats else None
                wgeoms = [(w["layer"], float(w["m"]), self._layer_geom(w["layer"], near=feats)) for w in within] if feats else []
            except KeyError as e:
                return declined(tool, VERSION, str(e))
            place_g = None
            if where.get("inside_place"):
                bb = self.place_bbox(where["inside_place"])
                if bb is None:
                    return declined(tool, VERSION, f"unknown place {where['inside_place']!r}")
                place_g = to_measure(box(*bb))
            iso_g, iso_info, travel_key = None, None, None
            wm = where.get("within_minutes_of")
            outside = bool(wm.get("outside")) if isinstance(wm, dict) else False
            if wm and feats:
                origin = wm.get("origin", wm) if isinstance(wm, dict) else wm
                o_s = origin.get("category") if isinstance(origin, dict) else origin
                cat = (self.resolve_category(o_s) if isinstance(o_s, str)
                       and str(o_s).lower().strip() not in self._pc()["landmarks"] else None)
                try:
                    if cat:  # a category: per-object drive-time band to the nearest one (minutes_to_<category>)
                        from groundtruth import risk as R

                        mins = float(wm.get("minutes", 10))
                        facts, minfo = R.minutes_facts(self, feats, cat[0], mins, wm.get("mode", "drive"))
                        feats = [{"type": "Feature", "id": f.get("id"), "geometry": f["geometry"],
                                  "properties": {**(f.get("properties") or {}), **d}} for f, d in zip(feats, facts)]
                        travel_key = f"minutes_to_{cat[0]}"
                        iso_info = {**minfo, "minutes": mins, "outside": outside,
                                    "origins": minfo["origins"][:30], "property": travel_key}
                    else:
                        iso_g, iso_info = self._travel_area(wm, feats)
                        from shapely.prepared import prep

                        iso_p = prep(iso_g)
                        iso_info["outside"] = outside
                except (KeyError, ValueError, FileNotFoundError) as e:
                    return declined(tool, VERSION, str(e))
        risk_keys = [k for k in RISK_PREDICATES if where.get(k) is not None]
        rinfo: dict[str, Any] = {}
        if risk_keys and feats:
            try:
                feats, rinfo = self._risk_annotate(feats, where, t)
            except (FileNotFoundError, KeyError, ValueError) as e:
                return declined(tool, VERSION, f"risk data unavailable: {e}")
        keep = []
        with t.step("filter", f"{len(feats)} features"):
            for f in feats:
                gm = to_measure(shape(f["geometry"]))
                p = f.get("properties") or {}
                if risk_keys and not self._risk_pass(p, where, rinfo):
                    continue
                a = p.get("area_acres") if p.get("area_acres") is not None else gm.area / SQM_PER_ACRE
                if inter is not None and not gm.intersects(inter):
                    continue
                if notinter is not None and gm.intersects(notinter):
                    continue
                if any(lg is None or gm.distance(lg) > m for _, m, lg in wgeoms):
                    continue
                if place_g is not None and not gm.intersects(place_g):
                    continue
                if iso_g is not None and iso_p.intersects(gm) == outside:
                    continue
                if travel_key is not None:
                    v = p.get(travel_key)
                    if (v is not None and v <= iso_info["minutes"]) == outside:
                        continue
                if "min_area_acres" in where and a < where["min_area_acres"]:
                    continue
                if "max_area_acres" in where and a > where["max_area_acres"]:
                    continue
                if "min_score" in where and float(p.get("score", 0)) < where["min_score"]:
                    continue
                if "min_confidence" in where and float(p.get("confidence", 0)) < where["min_confidence"]:
                    continue
                keep.append(f)
        limits = ["Distances are to the simplified web build of a layer when one exists (~3 m)"]
        sources = []
        metrics: dict[str, Any] = {"count_in": len(feats), "count_out": len(keep)}
        if iso_info:
            metrics["travel"] = iso_info
            limits.append("Travel times: Valhalla on OpenStreetMap roads at free-flow speeds (no live traffic); "
                          "an object counts when its outline touches the travel-time area")
            sources.append(self.provider.routing_source())
        method = ("Spatial/attribute predicates evaluated in EPSG:32617 (distances to layer unions"
                  + ("; travel-time areas = union of Valhalla isochrones from the origins" if iso_info else "") + ")")
        if risk_keys:
            metrics["risk"] = {k: v for k, v in rinfo.items() if not k.startswith("_")}
            limits += rinfo.get("_limits", [])
            sources += rinfo.get("_sources", [])
            method += "; risk predicates " + ", ".join(risk_keys) + " (per-feature facts kept as properties: " \
                + ", ".join(rinfo.get("_props", [])) + ")"
        return self._env(tool, status="ok", result=Result(geojson=feature_collection(keep), metrics=metrics),
                         confidence=1.0, method=method, limits=limits, sources=sources, steps=t.steps)

    # ------------------------------------------------------------------ risk facts + predicates (T4)
    def risk_sources(self, kinds: Sequence[str]) -> list[Source]:
        from groundtruth import risk as R

        out = []
        if "flood" in kinds:
            m = R.flood_meta()
            out.append(Source(name="FEMA NFHL flood hazard zones (layer 28, DFIRM 37119C)", url=R.conf()["flood"]["nfhl_url"],
                              date=(m.get("built") or "")[:10], licence="Public domain (US Government work, FEMA)"))
        if "shore" in kinds:
            out += self.provider.source_for("openstreetmap water")
        if "lidar" in kinds:
            out.append(Source(name="USGS 3DEP lidar USGS_LPC_NC_Phase4_Mecklenburg_2016_LAS_2019",
                              url="https://s3-us-west-2.amazonaws.com/usgs-lidar-public/USGS_LPC_NC_Phase4_Mecklenburg_2016_LAS_2019/ept.json",
                              date="2016 (published 2019)", licence="Public domain (USGS 3DEP)"))
        if "canopy" in kinds:
            out += [s for s in self.provider.sources() if "2022" in s.name and "NAIP" in s.name]
        if "travel" in kinds:
            out += [self.provider.routing_source(), self.provider.overture_source("places")]
        return out

    def _risk_annotate(self, feats: Sequence[dict[str, Any]], where: dict[str, Any], t: StepTimer | None = None,
                       facts: Sequence[str] = (), concept: str | None = None):
        """Copies of the features with the risk facts the predicates (or ``facts``) need → (features, info)."""
        from groundtruth import risk as R

        t = t or StepTimer()
        need = set(facts)
        if where.get("in_flood_zone") is not None:
            need.add("flood")
        if where.get("elevation_above_flood_m") is not None:
            need |= {"flood", "elevation"}
        if where.get("within_m_of_shore") is not None:
            need.add("shore")
        if where.get("canopy_over_pct") is not None:
            need.add("canopy")
        if where.get("slope_deg") is not None:
            need.add("slope")
        if where.get("height_m") is not None:
            need.add("height")
        out = [{"type": "Feature", "id": f.get("id"), "geometry": f["geometry"], "properties": dict(f.get("properties") or {})}
               for f in feats]
        prim = [f for f in out if f["properties"].get("kind") not in ("dock", "dock_link")]
        info: dict[str, Any] = {"_limits": [], "_sources": [], "_props": []}
        concept = concept or where.get("concept")

        def put(facts_l):
            for f, d in zip(prim, facts_l):
                f["properties"].update(d)
            for d in facts_l[:1]:
                info["_props"] += [k for k in d if k not in info["_props"]]

        if "flood" in need and not all("flood_kind" in f["properties"] for f in prim):
            with t.step("flood", f"{len(prim)} objects vs FEMA NFHL zones (accurate, unsimplified)"):
                put(R.flood_facts(prim, self.provider.get_boundary()))
        if "flood" in need:
            unc = sum(1 for f in prim if not f["properties"].get("flood_covered", True))
            info["flood_not_covered"] = unc
            info["_limits"].append("Flood zones: FEMA NFHL effective zones for Mecklenburg (DFIRM 37119C); an object is "
                                   "'in' a zone when its outline touches it (the most severe zone wins); FEMA zones are "
                                   "regulatory maps, not a flood forecast")
            if unc:
                info["_limits"].append(f"{unc} objects lie outside the mapped area (FEMA data for Mecklenburg only): "
                                       "their zone is unknown, not 'outside a zone'")
            info["_sources"] += self.risk_sources(["flood"])
        if "elevation" in need and not all("elev_bfe_status" in f["properties"] for f in prim):
            with t.step("elevation", "lidar DTM (lowest adjacent grade proxy) − FEMA static BFE"):
                put(R.elevation_facts(prim, [f["properties"] for f in prim]))
        if "elevation" in need:
            st: dict[str, int] = {}
            for f in prim:
                s_ = f["properties"].get("elev_bfe_status")
                st[s_] = st.get(s_, 0) + 1
            info["elev_bfe_status"] = st
            nb = st.get("no_bfe", 0) + st.get("no_lidar", 0)
            info["_limits"].append("Height above the base flood elevation (BFE) = lidar bare-earth ground (10th percentile "
                                   "within 2 m of the outline, a proxy for the lowest adjacent grade; 3DEP 2016, metres "
                                   "NAVD88) − FEMA STATIC_BFE (feet → metres). Not a surveyed elevation certificate")
            if nb:
                info["_limits"].append(f"Height above the flood elevation unavailable for {nb} objects in the 100-year zone "
                                       f"({st.get('no_bfe', 0)}: FEMA publishes no static BFE for their zone; "
                                       f"{st.get('no_lidar', 0)}: no lidar built there). They are marked, never estimated")
            info["_sources"] += self.risk_sources(["lidar"])
        if "shore" in need and not all("shore_dist_m" in f["properties"] for f in prim):
            with t.step("shore", "distance to the shoreline of Lake Norman, Mountain Island Lake, Lake Wylie, Catawba River"):
                put(R.shore_facts(prim))
        if "shore" in need:
            info["_limits"].append("Shore = OSM water outline of the main lakes/river (county-line edges removed); "
                                   "shore_dist_m is from the object's outline")
            info["_sources"] += self.risk_sources(["shore"])
        if "canopy" in need:
            with t.step("canopy", "lidar nDSM ≥ 3 m AND green NAIP 2022 pixel, per object footprint (+ buffer)"):
                put(R.canopy_facts(prim, concept))
            n_none = sum(1 for f in prim if f["properties"].get("canopy_over_pct") is None)
            info["canopy_unmeasured"] = n_none
            cb = R.conf()["canopy"]
            info["_limits"].append(f"Canopy = lidar height ≥ {cb['min_height_m']} m (3DEP 2016) AND a green NAIP "
                                   f"{cb['naip_year']} pixel (colour rule, no NDVI); trees grown or cut since 2016 are partly "
                                   "missed. " + cb["buffer_note"])
            if n_none:
                info["_limits"].append(f"canopy_over_pct unmeasured for {n_none} objects (no lidar/canopy raster there)")
            info["_sources"] += self.risk_sources(["lidar", "canopy"])
        if "slope" in need:
            with t.step("slope", "median DTM slope in each footprint"):
                put(R.slope_facts(prim))
            info["slope_unmeasured"] = sum(1 for f in prim if f["properties"].get("slope_deg") is None)
            info["_limits"].append("Slope from the 1 m lidar bare-earth DTM (2016): ground under buildings is interpolated")
            info["_sources"] += self.risk_sources(["lidar"])
        if "height" in need:
            with t.step("height", "feature height_m, else lidar nDSM 90th percentile"):
                put(R.height_facts(prim))
            info["height_unmeasured"] = sum(1 for f in prim if f["properties"].get("height_m") is None)
            info["_sources"] += self.risk_sources(["lidar"])
        info["_limits"] = list(dict.fromkeys(info["_limits"]))
        seen, srcs = set(), []
        for s in info["_sources"]:
            if s.name not in seen:
                seen.add(s.name)
                srcs.append(s)
        info["_sources"] = srcs
        return out, info

    def explain(self, geojson: dict[str, Any], facts: Sequence[str] = (), label: str | None = None,
                unit: str = "results", base_count: int | None = None, concept: str | None = None,
                filter_kind: str | None = None) -> Envelope:
        """The answer layer (T4): every feature gets ``properties.why`` (plain words: its basic fact + every risk fact
        it carries) and the envelope gets summary numbers + ``metrics.insights`` (1–3 strings built ONLY from those
        numbers). ``facts`` ⊆ {flood, elevation, shore, canopy, slope, height, travel, lakefront, distance}: facts to
        measure when missing and to mention. Supplementary features (derived docks / links) are kept, not counted."""
        tool = "explain"
        from groundtruth import risk as R

        t = StepTimer()
        feats = geojson.get("features", [])
        measurable = [f for f in facts if f in ("flood", "elevation", "shore", "canopy", "slope", "height")]
        info: dict[str, Any] = {"_limits": [], "_sources": []}
        if feats and measurable:
            try:
                feats, info = self._risk_annotate(feats, {}, t, facts=measurable, concept=concept)
            except (FileNotFoundError, KeyError, ValueError) as e:
                return declined(tool, VERSION, f"risk data unavailable: {e}")
        else:
            feats = [{"type": "Feature", "id": f.get("id"), "geometry": f["geometry"],
                      "properties": dict(f.get("properties") or {})} for f in feats]
        prim = [f for f in feats if f["properties"].get("kind") not in ("dock", "dock_link")]
        with t.step("why", f"WHY line for {len(feats)} features (conf/risk.json why)"):
            for f in feats:
                p = f["properties"]
                if isinstance(p.get("why"), dict):  # refine's similarity explanation keeps its own key
                    p["why_detail"] = p.pop("why")
                if p.get("kind") == "dock":
                    p["why"] = "Dock" + (" · new since 2020" if p.get("new_since_2020") else "")
                elif p.get("kind") == "dock_link":
                    p["why"] = f"house → its dock, {float(p.get('dist_m') or 0):.0f} m"
                else:
                    p["why"] = R.why_line(p, label, facts)
        P = [f["properties"] for f in prim]
        n = len(P)
        s: dict[str, Any] = {"count": n, "of_count": base_count, "unit": unit, "filter_kind": filter_kind}
        if "flood" in facts:
            s.update(in_100yr=sum(1 for p in P if p.get("flood_kind") == "100yr"),
                     in_500yr_only=sum(1 for p in P if p.get("flood_kind") == "500yr"),
                     flood_unknown=sum(1 for p in P if not p.get("flood_covered", True)))
            zones: dict[str, int] = {}
            for p in P:
                if p.get("flood_zone"):
                    zones[p["flood_zone"]] = zones.get(p["flood_zone"], 0) + 1
            s["zones"] = dict(sorted(zones.items()))
        if "elevation" in facts:
            ok = [p["elev_above_bfe_m"] for p in P if p.get("elev_above_bfe_m") is not None]
            s.update(bfe_known=len(ok), median_elev_above_bfe_m=R.median(ok),
                     min_elev_above_bfe_m=min(ok) if ok else None)
            s.setdefault("in_100yr", sum(1 for p in P if p.get("flood_kind") == "100yr"))
        if "shore" in facts or "lakefront" in facts:
            s["median_shore_dist_m"] = R.median([p.get("shore_dist_m", p.get("dist_shore_m")) for p in P])
        if "canopy" in facts:
            s["median_canopy_over_pct"] = R.median([p.get("canopy_over_pct") for p in P])
        if "height" in facts:
            s["median_height_m"] = R.median([p.get("height_m") for p in P])
        if "travel" in facts:
            keys = sorted({k for p in P for k in p if k.startswith("minutes_to_") and not k.endswith(("_over", "_from"))})
            if keys:
                k = keys[0]
                s.update(travel_label=k[len("minutes_to_"):].replace("_", " "),
                         median_minutes=R.median([p.get(k) for p in P]),
                         beyond_bands=sum(1 for p in P if p.get(k) is None))
        topics = [f for f in facts if f in ("flood", "elevation", "shore", "canopy", "travel", "height")]
        s["insights"] = (R.insights(s, topics, unit) if topics
                         else [R._fmt(R.conf()["insights"]["count"], n=f"{n:,}", unit=unit)])
        return self._env(tool, status="ok", result=Result(geojson=feature_collection(feats), metrics=s),
                         confidence=1.0,
                         method="WHY line per feature from its own properties (conf/risk.json why); insights are built "
                                "only from the numbers in these metrics"
                                + ("; facts measured: " + ", ".join(measurable) if measurable else ""),
                         limits=info.get("_limits", []), sources=info.get("_sources", []), steps=t.steps)

    @staticmethod
    def _risk_pass(p: dict[str, Any], where: dict[str, Any], info: dict[str, Any]) -> bool:
        if p.get("kind") in ("dock", "dock_link"):
            return True  # supplementary geometry of a derived house; follows its house (planner drops orphans)

        def rng(val, spec) -> bool:
            if val is None:
                return False  # unmeasured never passes (counted in metrics.risk)
            spec = spec if isinstance(spec, dict) else {"min": spec}
            if spec.get("min") is not None and float(val) < float(spec["min"]):
                return False
            if spec.get("max") is not None and float(val) > float(spec["max"]):
                return False
            if spec.get("lt") is not None and float(val) >= float(spec["lt"]):
                return False
            if spec.get("gt") is not None and float(val) <= float(spec["gt"]):
                return False
            return True

        fz = where.get("in_flood_zone")
        if fz is not None:
            kind = fz.get("kind", "any") if isinstance(fz, dict) else (fz if isinstance(fz, str) else "any")
            k = p.get("flood_kind")
            ok = {"100yr": k == "100yr", "500yr": k in ("100yr", "500yr"), "500yr_only": k == "500yr",
                  "any": k in ("100yr", "500yr"), "none": k is None and p.get("flood_covered", True)}.get(kind, False)
            if not ok:
                return False
        if where.get("within_m_of_shore") is not None:
            m = where["within_m_of_shore"]
            m = m.get("m") if isinstance(m, dict) else m
            if p.get("shore_dist_m") is None or float(p["shore_dist_m"]) > float(m):
                return False
        for key, prop in (("canopy_over_pct", "canopy_over_pct"), ("elevation_above_flood_m", "elev_above_bfe_m"),
                          ("slope_deg", "slope_deg"), ("height_m", "height_m")):
            if where.get(key) is not None and not rng(p.get(prop), where[key]):
                return False
        return True

    # ------------------------------------------------------------------ places + travel time (Phase 5)
    @staticmethod
    def _pc() -> dict[str, Any]:
        """conf/places_categories.json + the risk categories of conf/risk.json (fire stations, marinas, ...)."""
        pc = json.loads((config.SENSES_PATH.parent / "places_categories.json").read_text())
        rp = config.SENSES_PATH.parent / "risk.json"
        if rp.exists():
            extra = {k: v for k, v in json.loads(rp.read_text()).get("categories", {}).items() if not k.startswith("_")}
            pc["categories"] = {**pc["categories"], **extra}
        return pc

    def resolve_category(self, text: str) -> tuple[str, dict[str, Any]] | None:
        t = text.lower().strip()
        for k, v in self._pc()["categories"].items():
            if t == k or t in v["words"]:
                return k, v
        return None

    def resolve_origin(self, spec: Any) -> tuple[list[tuple[float, float, str]], str]:
        """→ ([(lon, lat, label)], how). spec: {lon, lat} | {name: "..."} | {category: "park"} | a string (landmark,
        category word or place name)."""
        from groundtruth.vector import store

        if isinstance(spec, str):
            s = spec.lower().strip()
            lm = self._pc()["landmarks"].get(s)
            if lm:
                spec = {"name": lm["name"]}
            elif self.resolve_category(s):
                spec = {"category": self.resolve_category(s)[0]}
            else:
                spec = {"name": spec}
        if "lon" in spec and "lat" in spec:
            return [(float(spec["lon"]), float(spec["lat"]), spec.get("label", "point"))], "point"
        if spec.get("name"):
            fc, _ = store().places(name=spec["name"], limit=50)
            if not fc["features"]:
                raise KeyError(f"no place named like {spec['name']!r} in Overture places")
            nm = spec["name"].lower()
            best = sorted(fc["features"], key=lambda f: (-(1 if (f["properties"].get("name") or "").lower() == nm else 0),
                                                          -float(f["properties"].get("confidence") or 0),
                                                          len(f["properties"].get("name") or ""), f["id"]))[0]
            c = shape(best["geometry"]).centroid
            return [(c.x, c.y, best["properties"].get("name") or spec["name"])], "name"
        if spec.get("category"):
            rc = self.resolve_category(spec["category"])
            if rc is None:
                raise KeyError(f"unknown place category {spec['category']!r} (conf/places_categories.json)")
            return [], rc[0]
        raise ValueError(f"cannot resolve origin {spec!r}")

    def _travel_area(self, spec: dict[str, Any], feats: Sequence[dict[str, Any]] = ()):
        """Union of isochrones (EPSG:32617) for within_minutes_of {origin, minutes, mode}. A category origin uses every
        place of that category that could reach the features (nearest 30)."""
        from shapely.ops import unary_union

        from groundtruth.vector import store

        pc = self._pc()
        mode = pc["modes"].get(str(spec.get("mode", "drive")).lower(), "auto")
        minutes = float(spec.get("minutes", 10))
        pts, how = self.resolve_origin(spec.get("origin", spec))
        if how not in ("point", "name"):
            cat = pc["categories"][how]
            speed = {"auto": 25.0, "bicycle": 6.0, "pedestrian": 1.5}[mode]  # m/s upper bound for reach
            reach = speed * minutes * 60
            if feats:
                fb = to_measure(unary_union([shape(f["geometry"]) for f in feats])).bounds
                bb = to_wgs84(box(fb[0] - reach, fb[1] - reach, fb[2] + reach, fb[3] + reach)).bounds
            else:
                bb = None
            fc, _ = store().places(bb, cat.get("category", []), taxonomy=cat.get("taxonomy", []))
            cand = [(shape(f["geometry"]).centroid, f["properties"].get("name") or how) for f in fc["features"]]
            if feats:
                fu = to_measure(unary_union([shape(f["geometry"]) for f in feats]))
                cand.sort(key=lambda c: fu.distance(to_measure(c[0])))
            pts = [(c.x, c.y, nm) for c, nm in cand[:30]]
            if not pts:
                raise KeyError(f"no '{how}' places within reach")
        polys = [to_measure(shape(self.provider.isochrone(lo, la, minutes, mode)["geometry"])) for lo, la, _ in pts]
        area = unary_union(polys).buffer(0)
        return area, {"origins": [{"lon": round(lo, 6), "lat": round(la, 6), "name": nm} for lo, la, nm in pts[:30]],
                      "minutes": minutes, "mode": mode, "area_km2": round(area.area / 1e6, 3)}

    def find_place(self, query: str, bbox: Sequence[float] | None = None, place: str | None = None,
                   limit: int = 5000) -> Envelope:
        """Overture places by category word (conf/places_categories.json) or by name; within a place/bbox if given."""
        tool = "find_place"
        if _people(query):
            return declined(tool, VERSION, "Groundtruth does not search for information about people")
        from groundtruth.vector import store

        if place and not bbox:
            bbox = self.place_bbox(place)
        t = StepTimer()
        rc = self.resolve_category(query)
        with t.step("query", f"DuckDB spatial: {'category ' + rc[0] if rc else 'name ~ ' + repr(query)}"):
            if rc:
                fc, ms = store().places(bbox, rc[1].get("category", []), taxonomy=rc[1].get("taxonomy", []), limit=limit)
            else:
                fc, ms = store().places(bbox, name=query, limit=limit)
        feats = []
        for r, f in enumerate(fc["features"]):
            p = f["properties"]
            feats.append({"type": "Feature", "id": f["id"], "geometry": f["geometry"],
                          "properties": {"id": p.get("id"), "name": p.get("name"), "category": p.get("category"),
                                         "confidence": round(float(p.get("confidence") or 0), 3), "rank": r + 1,
                                         "source_dataset": p.get("source_dataset")}})
        return self._env(tool, status="ok", result=Result(geojson=feature_collection(feats), metrics={
            "count": len(feats), "match": "category" if rc else "name", "category": rc[0] if rc else None,
            "bbox": list(bbox) if bbox else None, "place": place, "truncated": len(feats) >= limit}),
            confidence=self._mean_conf(feats) or 0.0,
            method="Overture places (local parquet) queried with DuckDB spatial by category or name; "
                   "confidence = Overture's own place confidence",
            limits=["Overture places are crowd/partner-sourced: some are missing, closed or misplaced",
                    "Category words map to Overture categories via conf/places_categories.json"],
            sources=[self.provider.overture_source("places")], steps=t.steps)

    def isochrone(self, origin: Any, minutes: float = 10, mode: str = "drive") -> Envelope:
        """Travel-time area from an origin ({lon, lat}, a place name or a landmark) by drive | walk | bike."""
        tool = "isochrone"
        t = StepTimer()
        with t.step("route", f"Valhalla isochrone {minutes} min {mode}"):
            try:
                area, info = self._travel_area({"origin": origin, "minutes": minutes, "mode": mode})
            except (KeyError, ValueError, FileNotFoundError) as e:
                return declined(tool, VERSION, str(e))
        f = {"type": "Feature", "id": "isochrone", "geometry": mapping(to_wgs84(area)),
             "properties": {"minutes": info["minutes"], "mode": info["mode"], "area_km2": info["area_km2"],
                            "origin": info["origins"][0] if info["origins"] else None}}
        return self._env(tool, status="ok", result=Result(geojson=feature_collection([f]), metrics=info),
                         confidence=0.8,
                         method="Valhalla isochrone on the local OpenStreetMap road graph (Mecklenburg + 5 km)",
                         limits=["Free-flow speeds, no live traffic or time of day; confidence is a fixed heuristic"],
                         sources=[self.provider.routing_source()], steps=t.steps)

    @memo()
    def find_anything(self, text: str, bbox: Sequence[float] | None = None, place: str | None = None) -> Envelope:
        """Open vocabulary (concepts not in senses.json): CLIP chips → Grounding DINO boxes → SAM 2.1 → verifier."""
        tool = "find_anything"
        if _people(text):
            return declined(tool, VERSION, "Groundtruth does not search for information about people")
        from groundtruth import openvocab as OV

        if place and not bbox:
            bbox = self.place_bbox(place)
            if bbox is None:
                return declined(tool, VERSION, f"unknown place {place!r}")
        t = StepTimer()
        self.__dict__["_busy"] = self.__dict__.get("_busy", 0) + 1
        try:
            with t.step("detect", f"open vocabulary '{text}'") as st:
                feats, info = OV.find_anything(self, text, bbox, place)
                st.detail += (f": {info['chips_searched']} chips → {info['raw_boxes']} boxes → "
                              f"{info['boxes_after_dedupe']} after dedupe → {len(feats)} kept ({info['device']})")
        finally:
            self._busy -= 1
        c = OV.conf()
        metrics = {**self._object_metrics(feats, info["rejected_by_area"], 0, bbox, place, info["concept"]), **info,
                   "mode": "open_vocabulary"}
        limits = self._limits() + [
            f"Open vocabulary: {info['chips_searched']} chips searched "
            + ("(every chip in the place, CLIP-ranked)" if bbox else f"(county-wide CLIP top {c['top_k_chips']}: NOT exhaustive)"),
            "Detector: Grounding DINO (zero-shot) on 0.6 m NAIP chips; small or unusual objects are often missed or confused"]
        if info["low_confidence"]:
            limits.append("LOW CONFIDENCE concept: eye-checked precision "
                          + (f"{info['eye_precision_12']}/12" if info["eye_precision_12"] is not None else "not measured")
                          + f" (below {c['low_confidence_below']}/12)")
        return self._env(tool, result=Result(geojson=feature_collection(feats), metrics=metrics),
                         confidence=self._mean_conf(feats) or 0.0,
                         method=f"CLIP ranks chips for '{info['prompt']}' → {c['detector']} boxes (box ≥ "
                                f"{c['box_threshold']}, text ≥ {c['text_threshold']}) → SAM 2.1 mask per box → area check "
                                f"(conf/openvocab.json) → dedupe within {c['nms_m']} m. Confidence = detector score × "
                                "(0.5 + 0.5 × SAM score), a heuristic",
                         assumptions=["The text names something visible from above at 0.6 m"], limits=limits,
                         steps=t.steps)

    def _building_heights(self, feats: Sequence[dict[str, Any]], bbox) -> dict[str, dict[str, Any]]:
        """Per building id: {height_m, source}. Lidar nDSM (Phase 6) when cached for the area, else Overture's height."""
        from groundtruth import lidar

        out = {}
        if lidar.available_for(bbox):
            out = lidar.building_heights(feats, bbox)
        for f in feats:
            p = f["properties"]
            if p.get("id") not in out and p.get("height") is not None:
                out[p.get("id")] = {"height_m": float(p["height"]), "source": "overture"}
        return out

    @memo()
    def find_derived(self, kind: str = "house_with_pool", place: str | None = None, require: Sequence[str] = (),
                     sort: str | None = None, top: int | None = None, docks_new_since: int | None = None,
                     exclude_ids: Sequence[str] = ()) -> Envelope:
        """Derived concepts over verified objects (conf/derived.json): houses with pools, optionally lakefront and with
        their own dock. Precomputed per place (scripts/build_derived.py → data/derived/<version>/<place>/).
        ``require`` ⊆ {lakefront, has_dock}; ``sort`` = "pool_area" (biggest first) with ``top``; ``docks_new_since``
        keeps houses with a dock that compare_years calls new since that year. Features: houses (kind house_with_pool)
        + their docks (kind dock) + house→dock links (kind dock_link); counts are houses."""
        tool = "find_derived"
        if kind != "house_with_pool":
            return declined(tool, VERSION, f"unknown derived concept {kind!r}")
        if not place:
            return declined(tool, VERSION, "houses with pools need a place (precomputed per place)")
        d = config.data_dir("derived") / place.replace(" ", "_")
        if not (d / "houses.geojson").exists():
            return declined(tool, VERSION, f"houses with pools are not precomputed for {place} yet")
        t = StepTimer()
        meta = json.loads((d / "meta.json").read_text())
        with t.step("load", f"precomputed {place} ({meta.get('built')})"):
            houses = json.loads((d / "houses.geojson").read_text())["features"]
            links = json.loads((d / "links.geojson").read_text())["features"]
        n_all = len(houses)
        if exclude_ids:  # the user said "Not this" on these houses
            houses = [h for h in houses if h["properties"]["id"] not in set(exclude_ids)]
        with t.step("filter", f"require {list(require) or 'nothing'}"):
            for r in require:
                houses = [h for h in houses if h["properties"].get(r)]
        new_info = None
        if docks_new_since:
            pre = all("dock_new_2020" in h["properties"] for h in houses) and int(docks_new_since) == 2020
            if not pre:
                return declined(tool, VERSION, f"docks since {docks_new_since} are only precomputed against 2020")
            with t.step("compare_years", "each dock's footprint: deck in 2022, water in 2020"):
                checked = sum(len(h["properties"].get("dock_new_2020", [])) for h in houses)
                unsure = sum(1 for h in houses for r in h["properties"]["dock_new_2020"] if r.get("new") is None)
                houses = [h for h in houses if any(r.get("new") for r in h["properties"]["dock_new_2020"])]
                n_new = sum(1 for h in houses for r in h["properties"]["dock_new_2020"] if r.get("new"))
            new_info = {"docks_checked": checked, "docks_new": n_new, "unsure": unsure}
        if sort == "pool_area":
            houses = sorted(houses, key=lambda h: (-float(h["properties"].get("pool_area_m2") or 0), h["properties"]["id"]))
            houses = houses[: int(top or json.loads((config.SENSES_PATH.parent / "derived.json").read_text())["biggest_n"])]
        ids = {h["properties"]["id"] for h in houses}
        feats = []
        for r, h in enumerate(houses):
            h = json.loads(json.dumps(h))
            h["properties"]["rank"] = r + 1
            h["properties"]["geometry_kind"] = "outline"
            feats.append(h)
        if "has_dock" in require:
            for h in houses:
                chg = h["properties"].get("dock_new_2020") or []
                for k, dg in enumerate(h["properties"].get("dock_geometries", [])):
                    is_new = bool(k < len(chg) and chg[k].get("new"))
                    feats.append({"type": "Feature", "id": f"dock:{h['properties']['id']}:{k}", "geometry": dg,
                                  "properties": {"kind": "dock", "house_id": h["properties"]["id"], "geometry_kind": "outline",
                                                 "new_since_2020": is_new}})
            feats += [l for l in links if l["properties"]["house_id"] in ids]
        hs = [f for f in feats if f["properties"].get("kind") == "house_with_pool"]
        pa = sum(float(f["properties"].get("pool_area_m2") or 0) for f in hs)
        metrics = {"count": len(hs), "count_detected": len(hs), "count_unit": "houses (Overture buildings) with a verified pool",
                   "place": place, "require": list(require), "sort": sort, "of_houses_with_pools": n_all,
                   "pool_area_m2_total": round(pa, 1), "pool_area_acres_total": round(pa / SQM_PER_ACRE, 3),
                   "docks": sum(1 for f in feats if f["properties"].get("kind") == "dock"), "mode": "exhaustive_scan",
                   "built": meta.get("built"), "pools_in_place": meta.get("pools")}
        if new_info:
            metrics["docks_new_since"] = {"year": docks_new_since, **new_info}
        c = json.loads((config.SENSES_PATH.parent / "derived.json").read_text())
        limits = self._limits() + [
            "A 'house' is an Overture building (residential where classed, else a small non-commercial footprint): "
            "never its owners or residents",
            f"A pool belongs to the nearest such building within {c['house_with_pool']['pool_to_house_max_m']} m; pools "
            "in mapped water or on a building are excluded",
            f"Lakefront: within {c['lakefront']['max_dist_m']} m of the lake with no major road or other building between",
            f"Own dock: a verified dock touching the lake within {c['has_dock']['max_dist_from_shore_point_m']} m of the "
            "house's shore point, nearest to it among all lakefront homes (one dock → one house)",
            "Docks come from open-vocabulary detection (boat dock: 12/12 by eye)"]
        return self._env(tool, status="ok", result=Result(geojson=feature_collection(feats), metrics=metrics),
                         confidence=self._mean_conf(hs) or 0.0,
                         method="Verified pools (find_all, dense) → nearest residential Overture building → lakefront "
                                "test vs the OSM lake polygon + major roads + buildings → docks (Grounding DINO + SAM 2.1) "
                                "assigned to their nearest lakefront home",
                         assumptions=["'houses with pools' = backyard pools at residences"], limits=limits,
                         sources=[self.provider.overture_source("buildings")], steps=t.steps)

    def canopy(self, place: str | None = None, bbox: Sequence[float] | None = None, min_tree_m: float = 2.5) -> Envelope:
        """Tree canopy from lidar: cells with height-above-ground ≥ min_tree_m that are not under an Overture building.
        → cover %, tree-height percentiles. Needs a lidar build for the place."""
        tool = "canopy"
        from rasterio.features import rasterize

        from groundtruth import lidar
        from groundtruth.vector import store

        if place and not bbox:
            bbox = self.place_bbox(place)
        lp = lidar.place_for(bbox)
        if lp is None:
            return declined(tool, VERSION, f"no lidar built for this area yet (built: {lidar.built_places()})")
        t = StepTimer()
        with t.step("canopy", f"nDSM ≥ {min_tree_m} m outside Overture buildings ({lp})"):
            arr, tr = lidar.array(lp)
            fc, _ = store().buildings(bbox)
            bmask = rasterize([to_measure(shape(f["geometry"])).buffer(1.5) for f in fc["features"]], out_shape=arr.shape,
                              transform=tr) > 0 if fc["features"] else np.zeros(arr.shape, bool)
            valid = np.isfinite(arr) & (arr > -9000)
            trees = valid & ~bmask & (arr >= min_tree_m)
            h = arr[trees]
            land = valid & ~bmask
        cover = float(trees.sum() / max(land.sum(), 1))
        metrics = {"place": lp, "canopy_cover_pct": round(100 * cover, 1), "canopy_area_acres": round(trees.sum() / SQM_PER_ACRE, 1),
                   "tree_height_m_median": round(float(np.median(h)), 1) if len(h) else None,
                   "tree_height_m_p90": round(float(np.percentile(h, 90)), 1) if len(h) else None,
                   "tree_height_m_max": round(float(h.max()), 1) if len(h) else None, "min_tree_m": min_tree_m}
        meta = json.loads((config.data_dir("lidar") / lp.replace(" ", "_") / "meta.json").read_text())
        return self._env(tool, status="ok", result=Result(geojson=feature_collection([]), metrics=metrics),
                         confidence=0.8,
                         method=f"USGS 3DEP lidar nDSM at 1 m: canopy = height ≥ {min_tree_m} m, excluding Overture building "
                                "footprints (+1.5 m); heights are per 1 m cell (not per tree)",
                         limits=["Lidar acquired 2016: growth and removals since are not reflected",
                                 "Tall non-building structures (towers, power lines) count as canopy",
                                 "Per-cell heights, not individual trees; confidence is a fixed heuristic"],
                         sources=[Source(name=f"USGS 3DEP {meta['project']}", url=meta["source"],
                                         date=meta["acquired"], licence=meta["licence"])], steps=t.steps)

    def find_buildings(self, kind: str = "building", bbox: Sequence[float] | None = None, place: str | None = None,
                       limit: int = 20000, min_height_m: float | None = None) -> Envelope:
        """Overture building footprints in a place/bbox, optionally by kind (conf/places_categories.json buildings)."""
        tool = "find_buildings"
        from groundtruth.vector import store

        if place and not bbox:
            bbox = self.place_bbox(place)
        pc = self._pc()
        k = next((key for key, v in pc["buildings"].items() if kind.lower() in v["words"] or kind.lower() == key), None)
        if k is None:
            return declined(tool, VERSION, f"unknown building kind {kind!r}")
        classes = pc["buildings"][k]["classes"]
        t = StepTimer()
        layer = pc["buildings"][k].get("layer")
        with t.step("query", f"layer {layer}" if layer else f"DuckDB spatial: buildings{' class in ' + str(classes) if classes else ''}"):
            if layer:  # footprints from a reference layer (e.g. OSM warehouses), ids = layer feature index
                lf = self.provider.get_layer(layer, tuple(bbox) if bbox else None)
                fc = {"features": [{**f, "properties": {**(f.get("properties") or {}), "id": f.get("id") or f"{layer}:{n}",
                                                        "class": k}} for n, f in enumerate(lf["features"])][:limit]}
            else:
                fc, ms = store().buildings(bbox, classes, limit=limit)
        feats = []
        from groundtruth import lidar

        want_h = min_height_m is not None or lidar.available_for(bbox)
        with t.step("heights", "lidar nDSM (90th pct in footprint) where built, else Overture height") if want_h \
                else _null_ctx():
            heights = self._building_heights(fc["features"], bbox) if want_h else {}
        srcs: dict[str, int] = {}
        for f in fc["features"]:
            p = f["properties"]
            hh = heights.get(p.get("id"), {})
            h = hh.get("height_m", p.get("height"))
            if min_height_m is not None and (h is None or h < min_height_m):
                continue
            if h is not None:
                s_ = hh.get("source", "overture")
                srcs[s_] = srcs.get(s_, 0) + 1
            gm = to_measure(shape(f["geometry"]))
            feats.append({"type": "Feature", "id": p.get("id"), "geometry": f["geometry"],
                          "properties": {"id": p.get("id"), "class": p.get("class"),
                                         "height_m": None if h is None else round(float(h), 1),
                                         "height_source": hh.get("source", "overture" if h is not None else None),
                                         "num_floors": p.get("num_floors"), "area_m2": round(gm.area, 1),
                                         "area_acres": round(gm.area / SQM_PER_ACRE, 5), "confidence": 0.9,
                                         "rank": len(feats) + 1, "geometry_kind": "outline"}})
        limits = ["Overture building footprints (OSM + ML-derived), not checked against the imagery"]
        if classes:
            limits.append(pc["buildings_class_coverage_note"])
        if want_h:
            limits.append(f"Heights by source: {srcs or 'none'}; buildings without a height are "
                          + ("excluded by the height filter" if min_height_m is not None else "shown flat"))
        if want_h and not lidar.available_for(bbox):
            limits.append("No lidar built for this area yet: heights are Overture's (often missing)")
        return self._env(tool, status="ok" if len(feats) < limit else "partial",
                         result=Result(geojson=feature_collection(feats), metrics={
                             "count": len(feats), "kind": k, "classes": classes, "bbox": list(bbox) if bbox else None,
                             "place": place, "truncated": len(feats) >= limit,
                             "area_m2_total": round(sum(f["properties"]["area_m2"] for f in feats), 1)}),
                         confidence=0.9 if not classes else 0.6,
                         method="Overture buildings (local parquet) queried with DuckDB spatial by bbox and class",
                         limits=limits, sources=[self.provider.overture_source("buildings")], steps=t.steps)

    def export(self, geojson: dict[str, Any], fmt: str = "geojson", title: str | None = None,
               summary: dict[str, Any] | None = None) -> Envelope:
        tool = "export"
        feats = geojson.get("features", [])
        if len(feats) > 50_000:  # body cap: exports are of result sets, not bulk copies of a layer
            return declined(tool, VERSION, f"too many features to export ({len(feats)} > 50,000)")
        t = StepTimer()
        with t.step("serialise", fmt):
            if fmt == "geojson":
                content = json.dumps(feature_collection(feats))
            elif fmt == "csv":
                buf = io.StringIO()
                keys = sorted({k for f in feats for k in (f.get("properties") or {}) if not isinstance(f["properties"][k], (list, dict))})
                w = csv.writer(buf, lineterminator="\n")
                w.writerow(["id", "lon", "lat", *keys])
                for f in feats:
                    c = shape(f["geometry"]).representative_point()
                    w.writerow([f.get("id"), round(c.x, 7), round(c.y, 7), *[f["properties"].get(k) for k in keys]])
                content = buf.getvalue()
            elif fmt == "pdf":
                content = None
            else:
                return declined(tool, VERSION, f"unsupported format {fmt!r} (geojson, csv, pdf)")
            out = config.DATA / "out" / "exports"
            out.mkdir(parents=True, exist_ok=True)
            # unique per call (two exports in the same second no longer overwrite each other)
            name = f"export_{time.strftime('%Y%m%dT%H%M%S')}_{len(feats)}_{uuid.uuid4().hex[:6]}.{fmt}"
            if fmt == "pdf":
                nbytes = self._pdf(out / name, feats, title or "Groundtruth results", summary or {})
            else:
                (out / name).write_text(content)
                nbytes = len(content)
        return self._env(tool, status="ok", result=Result(metrics={
            "count": len(feats), "format": fmt, "file": f"data/out/exports/{name}", "download": f"/api/v1/exports/{name}",
            "bytes": nbytes, "content": content if content is not None and len(content) < 2_000_000 else None}),
            confidence=1.0, method=f"serialised {len(feats)} features as {fmt}", limits=[], sources=[], steps=t.steps)

    def _pdf(self, path, feats: list[dict[str, Any]], title: str, summary: dict[str, Any]) -> int:
        """One-page PDF (reportlab, BSD): title, summary metrics, top results with thumbnails, sources + limits."""
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas

        c = canvas.Canvas(str(path), pagesize=letter)
        W, H = letter
        c.setFont("Helvetica-Bold", 16); c.drawString(0.6 * inch, H - 0.8 * inch, title[:80])
        c.setFont("Helvetica", 9); c.setFillGray(0.35)
        c.drawString(0.6 * inch, H - 1.0 * inch, f"Groundtruth · Mecklenburg County, NC · NAIP {self.year} · "
                     f"generated {time.strftime('%Y-%m-%d %H:%M')}")
        c.setFillGray(0); c.setFont("Helvetica", 10)
        y = H - 1.35 * inch
        for k, v in list(summary.items())[:8]:
            c.drawString(0.6 * inch, y, f"{k}: {v}"); y -= 13
        y -= 6
        tile = 1.05 * inch
        for n, f in enumerate(feats[:24]):
            p = f.get("properties") or {}
            cid = p.get("chip_id")
            if cid in self.index.pos:
                x0 = 0.6 * inch + (n % 6) * (tile + 0.12 * inch)
                y0 = y - tile - (n // 6) * (tile + 0.32 * inch)
                c.drawImage(ImageReader(io.BytesIO(self.index.thumb_jpeg(self.index.pos[cid]))), x0, y0, tile, tile)
                c.setFont("Helvetica", 6.5)
                a = p.get("area_m2")
                c.drawString(x0, y0 - 8, f"#{n + 1} conf {p.get('confidence', p.get('score', ''))}"
                             + (f" · {a:.0f} m²" if a else ""))
        c.setFont("Helvetica", 7); c.setFillGray(0.35)
        foot = ["Imagery: USDA NAIP (public domain) via Microsoft Planetary Computer. Layers: OpenStreetMap (ODbL, © "
                "OpenStreetMap contributors), FEMA NFHL (public domain), US Census.",
                "Counts are detected objects in ranked results, not a census. Confidence is heuristic, not calibrated."]
        for k, line in enumerate(foot):
            c.drawString(0.6 * inch, 0.6 * inch + (len(foot) - k) * 9, line)
        c.showPage(); c.save()
        return path.stat().st_size
