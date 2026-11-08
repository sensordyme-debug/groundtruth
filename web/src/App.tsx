import { MutableRefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bbox, Envelope, FC, Feature, Health, boxPoly, exampleOf, health, places as fetchPlaces, setThumbAoi, sleep, thumb, tool,
} from "./api";
import { AgentTarget, runRulesPlanner } from "./agent";
import {
  AGENT_CHIPS, AGENT_DEMO_PROMPT, COLORS, CONTEXT_LAYERS, NEAR_LAYERS, fmtAcres, fmtM, fmtM2, layerLabel, matchPlace, parseQuery, pct, pretty,
} from "./config";
import { MapView, inside, unionBbox } from "./mapview";
import { AskScreen, PickScreen, CellThumb } from "./screens";
import WorkLog from "./WorkLog";

type Stage = "ask" | "pick" | "results";
type Outline = { geometry: GeoJSON.Geometry | null; area_m2: number | null; area_acres: number | null };
type FilterState = { label: string; where: Record<string, any>; keep: Set<string>; measure?: Record<string, any>; countIn: number };
type AgentMsg =
  | { kind: "say"; text: string }
  | { kind: "fly"; label: string; approximate?: boolean }
  | { kind: "candidates"; feats: Feature[] }
  | { kind: "prompt"; text: string; planner: string }
  | { kind: "status"; status: string }
  | { kind: "results"; detected: number; outlined: number; rejected: number; exhaustive: boolean; layer: string }
  | { kind: "filter"; n: number; of: number }
  | { kind: "layer"; layer: string; n: number }
  | { kind: "metrics"; metrics: Record<string, any> }
  | { kind: "download"; url: string; format: string }
  | { kind: "error"; text: string };

const OUTLINE_BATCH = 24;   // outline polls ask for at most 24 ids (in view first)
const OUTLINE_POLL_MS = 2500;
const OUTLINE_POLL_MAX = 150;
const LIST_MAX = 120;

/** A feature whose geometry is still the cell/box placeholder (outline pending). */
const isPending = (f: Feature) => f.properties.geometry_kind === "box" || f.properties.geometry_kind === "cell";
/** Clear pick tiles (not `no_clear_example`) that were not picked. Tiles the user marked "not this" go as HARD negatives
 *  (`exclude`: can veto an object); the rest are other meanings of the word and go as SOFT negatives (`soft_exclude`). */
const negativesFor = (cands: Feature[], picks: string[]) =>
  cands.filter((c) => !c.properties.no_clear_example && c.properties.example && !picks.includes(c.properties.example))
    .map((c) => c.properties.example as string);

function useStateRef<T>(init: T): [T, (v: T) => void, MutableRefObject<T>] {
  const [s, set] = useState<T>(init);
  const r = useRef<T>(init);
  const setter = useCallback((v: T) => { r.current = v; set(v); }, []);
  return [s, setter, r];
}

const confOf = (f: Feature) => Number(f.properties.confidence ?? f.properties.score ?? 0);

export default function App() {
  // ---------------- boot data ----------------
  const [hl, setHl] = useState<Health | null>(null);
  const [placeMap, setPlaceMap] = useState<Record<string, Bbox>>({});
  const [placeSource, setPlaceSource] = useState("");
  const [year, setYear, yearRef] = useStateRef<number>(2022);
  const [aoi, setAoi, aoiRef] = useStateRef<string>("");
  setThumbAoi(aoi); // before children render their thumbnails
  const [stage, setStage] = useState<Stage>("ask");
  const [tab, setTab] = useState<"log" | "agent">("log");

  // ---------------- search state ----------------
  const [query, setQuery] = useState("");
  const [askedText, setAskedText] = useState("");
  const [place, setPlace, placeRef] = useStateRef<string | null>(null);
  const [cands, setCands] = useState<Feature[]>([]);
  const [concept, setConcept, conceptRef] = useStateRef<string | null>(null);
  const [picks, setPicks, picksRef] = useStateRef<string[]>([]);
  const [nots, setNots, notsRef] = useStateRef<string[]>([]);
  const [results, setResults, resultsRef] = useStateRef<Feature[]>([]);
  const [faMetrics, setFaMetrics] = useState<Record<string, any> | null>(null);
  const [faLimits, setFaLimits] = useState<string[]>([]);
  // find_all scan mode + object-check rejections (server count + ids dropped later by outline polls)
  const [scanMode, setScanMode] = useState<string | null>(null);
  const [objRejected, setObjRejected] = useState(0);
  const negRef = useRef<string[]>([]);
  const [outlines, setOutlines, outlinesRef] = useStateRef<Record<string, Outline>>({});
  const [accepted, setAccepted, acceptedRef] = useStateRef<Feature[]>([]);
  const [rejected, setRejected, rejectedRef] = useStateRef<string[]>([]);
  const [, setSession, sessionRef] = useStateRef<string | null>(null);
  const [mode, setMode] = useState<"find_all" | "refine">("find_all");
  const [sel, setSel] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterState | null>(null);
  const [nearLayer, setNearLayer] = useState("osm_i485");
  const [nearM, setNearM] = useState(1000);
  const [minAcres, setMinAcres] = useState("");
  const [inPlace, setInPlace] = useState("");
  const [download, setDownload] = useState<{ url: string; format: string } | null>(null);
  const [streaming, setStreaming] = useState(false);

  // ---------------- layers / years ----------------
  const [layersOn, setLayersOn] = useState<Record<string, boolean>>({});
  const layerCache = useRef<Record<string, FC>>({});
  const [changes, setChanges] = useState<Record<string, string>>({});
  const [compare, setCompare] = useState<"off" | "loading" | "on">("off");
  const [compareNote, setCompareNote] = useState<string | null>(null);
  const [layersOpen, setLayersOpen] = useState(() => window.innerHeight > 860 && window.innerWidth > 1400);

  // ---------------- agent ----------------
  const [agentMsgs, setAgentMsgs] = useState<AgentMsg[]>([]);
  const [agentRunning, setAgentRunning] = useState(false);
  const lastAgentText = useRef<string | null>(null);  // context for follow-ups ("export them as CSV")
  const [agentText, setAgentText] = useState(AGENT_DEMO_PROMPT);

  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const mapEl = useRef<HTMLDivElement>(null);
  const mv = useRef<MapView | null>(null);
  const runRef = useRef(0);

  const call = useCallback((name: string, body: Record<string, unknown>) =>
    tool(name, { year: yearRef.current, aoi: aoiRef.current || undefined, ...body }), [yearRef, aoiRef]);

  // ---------------- boot ----------------
  useEffect(() => {
    health().then((h) => {
      setHl(h);
      const qs = new URLSearchParams(window.location.search);
      const y = Number(qs.get("year")) || h.primary_year;
      setYear(y);
      setAoi(qs.get("aoi") ?? defaultAoi(h, y));
    }).catch((e) => setErr(`API not reachable: ${e}`));
    fetchPlaces().then((p) => { setPlaceMap(p.places); setPlaceSource(p.source); }).catch(() => setPlaceMap({}));
  }, [setAoi, setYear]);

  useEffect(() => {
    const m = new MapView(mapEl.current!);
    mv.current = m;
    m.onResultClick = (ex) => {
      setSel(ex);
      document.getElementById(`res-${ex}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    };
    const ro = new ResizeObserver(() => m.resize());
    ro.observe(mapEl.current!);
    return () => { ro.disconnect(); m.remove(); mv.current = null; };
  }, []);

  useEffect(() => { if (year) mv.current?.setYear(year); }, [year]);

  // ?debug=1: print the map layer stack (used to verify rendering in headless screenshots)
  const [dbg, setDbg] = useState("");
  useEffect(() => {
    const qs = new URLSearchParams(window.location.search);
    // ?shot=1: headless screenshots use virtual time, which skips map repaints when no timer is pending; keep one alive.
    const hb = qs.has("shot") ? setInterval(() => mv.current?.map.triggerRepaint(), 1000) : undefined;
    if (!qs.has("debug")) return () => clearInterval(hb);
    const t = setInterval(() => { const m = mv.current?.map; if (!m || !m.isStyleLoaded()) return;
      setDbg(m.getStyle().layers.map((l) => `${l.id}:${(l as any).layout?.visibility ?? "v"}`).join(" ")); }, 1500);
    return () => { clearInterval(t); clearInterval(hb); };
  }, []);

  // ---------------- derived ----------------
  // v0.4: one counting unit. Every feature find_all / refine returns IS a counted object (deduped, not rejected by the
  // object check), so the counted set is exactly `results` minus local ✗ (until refine answers) minus ids the outline
  // polls reject. Filter, insight card and export all use this same set. `cut` only shades the confidence bar.
  const cut = 0.5;
  const rejSet = useMemo(() => new Set(rejected), [rejected]);
  const accSet = useMemo(() => {
    const s = new Set(accepted.map(exampleOf));
    for (const f of results) if (f.properties.accepted) s.add(exampleOf(f));
    return s;
  }, [accepted, results]);
  const pool = useMemo(() => {
    const live = results.filter((f) => !rejSet.has(exampleOf(f)));
    return [...live.filter((f) => accSet.has(exampleOf(f))), ...live.filter((f) => !accSet.has(exampleOf(f)))];
  }, [results, rejSet, accSet]);
  const acceptedFeats = useMemo(() => pool.filter((f) => accSet.has(exampleOf(f))), [pool, accSet]);
  const ranked = useMemo(() => pool.filter((f) => !accSet.has(exampleOf(f))), [pool, accSet]);
  const passes = useCallback((f: Feature) => !filter || filter.keep.has(exampleOf(f)), [filter]);
  const counted = useMemo(() => pool.filter(passes), [pool, passes]);
  const stats = useMemo(() => {
    const areas = counted.map((f) => outlines[exampleOf(f)]?.area_m2).filter((x): x is number => x != null).sort((a, b) => a - b);
    const acres = counted.reduce((s, f) => s + (outlines[exampleOf(f)]?.area_acres ?? 0), 0);
    const median = areas.length ? (areas.length % 2 ? areas[(areas.length - 1) / 2] : (areas[areas.length / 2 - 1] + areas[areas.length / 2]) / 2) : null;
    const outlinedAll = pool.filter((f) => outlines[exampleOf(f)]?.geometry).length;
    return { count: counted.length, total: pool.length, acres, median, outlinedCounted: areas.length, outlinedAll };
  }, [counted, outlines, pool]);

  // ---------------- map sync ----------------
  useEffect(() => {
    const feats: Feature[] = [];
    for (const f of pool) {
      const ex = exampleOf(f);
      const o = outlines[ex];
      const isAcc = accSet.has(ex);
      feats.push({ type: "Feature", geometry: o?.geometry ?? boxPoly(f.properties.cell_bbox), properties: {
        example: ex, kind: o?.geometry ? "outline" : "cell", state: isAcc ? "accepted" : "hit",
        change: compare === "on" ? changes[ex] ?? "" : "", sel: sel === ex,
        dim: !passes(f),
      } });
    }
    mv.current?.setResults({ type: "FeatureCollection", features: feats });
  }, [pool, outlines, accSet, sel, passes, changes, compare]);

  useEffect(() => {
    const fc: FC = { type: "FeatureCollection", features: cands.filter((c) => picks.includes(c.properties.example))
      .map((c) => ({ type: "Feature", geometry: boxPoly(c.properties.cell_bbox), properties: {} })) };
    mv.current?.setPick(stage === "results" ? fc : { type: "FeatureCollection", features: [] });
  }, [cands, picks, stage]);

  // ---------------- outlines: stream in ----------------
  const mergeOutlines = useCallback((feats: Feature[]) => {
    if (!feats.length) return;
    const next = { ...outlinesRef.current };
    for (const f of feats) {
      if (!f.geometry) continue;
      const ex = (f.properties.example as string) ?? String(f.id);
      next[ex] = { geometry: f.geometry, area_m2: f.properties.area_m2 ?? null, area_acres: f.properties.area_acres ?? null };
    }
    setOutlines(next);
  }, [outlinesRef, setOutlines]);

  /** Poll `outline {cached_only, concept}` while any result is still a box (pending outline): swap outlines in and drop
   *  the ids the object check rejects (metrics.rejected) from the results, so the count goes down with them. */
  const streamOutlines = useCallback(async (run: number) => {
    const pending = () => resultsRef.current.filter((f) => outlinesRef.current[exampleOf(f)] === undefined && isPending(f)
      && !rejectedRef.current.includes(exampleOf(f)));
    if (!pending().length) { setStreaming(false); return; }
    setStreaming(true);
    try {
      for (let iter = 0; iter < OUTLINE_POLL_MAX && runRef.current === run; iter++) {
        const pend = pending();
        if (!pend.length) break;
        const view = mv.current?.bounds();
        const inView = view ? pend.filter((f) => inside(f.properties.cell_bbox, view)) : [];
        const pick = (inView.length ? inView : pend).slice()
          .sort((a, b) => Number(a.properties.rank ?? 0) - Number(b.properties.rank ?? 0));
        const batch = pick.slice(0, OUTLINE_BATCH).map(exampleOf);
        const env = await call("outline", { examples: batch, max: OUTLINE_BATCH, cached_only: true, concept: conceptRef.current ?? undefined });
        if (runRef.current !== run) return;
        if (env.status === "declined") break;
        const drop = new Set<string>((env.result.metrics.rejected ?? []).map(String));
        mergeOutlines(env.result.geojson.features.filter((f) => !drop.has((f.properties.example as string) ?? String(f.id))));
        if (drop.size) {
          const before = resultsRef.current.length;
          const kept = resultsRef.current.filter((f) => !drop.has(exampleOf(f)));
          setResults(kept);
          setObjRejected((n) => n + (before - kept.length));
        }
        await sleep(OUTLINE_POLL_MS);
      }
    } catch (e) { setErr(String(e)); }
    finally { if (runRef.current === run) setStreaming(false); }
  }, [call, mergeOutlines, resultsRef, outlinesRef, rejectedRef, conceptRef, setResults]);

  // ---------------- flow ----------------
  const resetResults = () => {
    runRef.current++;
    setResults([]); setFaMetrics(null); setFaLimits([]); setAccepted([]); setRejected([]); setSession(null);
    setScanMode(null); setObjRejected(0);
    setMode("find_all"); setSel(null); setFilter(null); setDownload(null); setChanges({}); setCompare("off"); setCompareNote(null);
    setStreaming(false);
  };

  const ask = useCallback(async (raw: string): Promise<Feature[]> => {
    if (!raw.trim()) return [];
    const parsed = parseQuery(raw, placeMap);
    const pl = parsed.place ?? placeRef.current;
    setQuery(raw); setAskedText(parsed.text); setPlace(pl);
    setErr(null); setBusy("Finding the different things you might mean…");
    resetResults(); setCands([]); setPicks([]); setNots([]);
    try {
      const env = await call("pick_candidates", { query: parsed.text, n: 6 });
      if (env.status === "declined") { setErr(`Declined: ${env.declined_reason}`); setStage("ask"); return []; }
      setCands(env.result.geojson.features);
      setConcept(env.result.metrics.sense_list ?? null);
      setStage("pick");
      if (pl && placeMap[pl]) mv.current?.fitBbox(placeMap[pl], { maxZoom: 15, duration: 0 });
      return env.result.geojson.features;
    } catch (e) { setErr(String(e)); return []; }
    finally { setBusy(null); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [placeMap, call]);

  const applyFindAll = useCallback((env: Envelope, fit = true) => {
    const run = ++runRef.current;
    const feats = env.result.geojson.features;
    const m = env.result.metrics;
    setResults(feats); setFaMetrics(m); setFaLimits(env.limits);
    setScanMode(m.mode ?? null); setObjRejected(Number(m.rejected_by_object_check ?? 0));
    setMode("find_all"); setFilter(null); setDownload(null);
    mergeOutlines(feats.filter((f) => f.properties.geometry_kind === "outline"));
    setStage("results");
    if (fit) {
      const b = unionBbox(feats.slice(0, 15).map((f) => f.properties.cell_bbox));
      const pl = placeRef.current;
      if (b) mv.current?.fitBbox(b, { padding: 70, maxZoom: 17 });
      else if (pl && placeMap[pl]) mv.current?.fitBbox(placeMap[pl], { maxZoom: 15 });
    }
    streamOutlines(run);
  }, [mergeOutlines, streamOutlines, placeRef, placeMap, setResults]);

  const findAll = useCallback(async (posEx: string[], negEx: string[]) => {
    if (!posEx.length) return;
    negRef.current = negEx;
    setErr(null); setBusy("Finding every match…");
    try {
      const hard = negEx.filter((e) => notsRef.current.includes(e)), soft = negEx.filter((e) => !notsRef.current.includes(e));
      const env = await call("find_all", { examples: posEx, exclude: hard, soft_exclude: soft, k: 200, concept: conceptRef.current ?? undefined,
        rerank: true, outline_top: 200, ...(placeRef.current ? { place: placeRef.current } : {}) });
      if (env.status === "declined") { setErr(`Declined: ${env.declined_reason}`); return; }
      setAccepted([]); setRejected([]); setSession(null);
      applyFindAll(env);
    } catch (e) { setErr(String(e)); }
    finally { setBusy(null); }
  }, [call, conceptRef, placeRef, applyFindAll, setAccepted, setRejected, setSession]);

  const judge = useCallback(async (f: Feature, ok: boolean) => {
    const ex = exampleOf(f);
    const acc = acceptedRef.current.filter((x) => exampleOf(x) !== ex);
    const rej = rejectedRef.current.filter((x) => x !== ex);
    if (ok) acc.push(f); else rej.push(ex);
    setAccepted(acc); setRejected(rej);
    setBusy(ok ? "Learning from ✓…" : "Learning from ✗…"); setErr(null);
    try {
      const first = !sessionRef.current;
      const accept = first ? [...picksRef.current, ...acc.map(exampleOf)] : ok ? [ex] : [];
      const reject = first ? [...new Set([...notsRef.current, ...rej])] : ok ? [] : [ex];  // only HARD negatives seed refine
      // v0.4: re-score exactly the current objects; the returned features ARE the new counted set
      const candidates = resultsRef.current.map(exampleOf);
      const env = await call("refine", { session_id: sessionRef.current, accept, reject, k: 200, candidates,
        concept: conceptRef.current ?? undefined, ...(placeRef.current ? { place: placeRef.current } : {}) });
      if (env.status === "declined") { setErr(`Declined: ${env.declined_reason}`); return; }
      setSession(env.result.metrics.session_id ?? null);
      const run = ++runRef.current;
      const feats = env.result.geojson.features;
      setResults(feats); setMode("refine"); setFilter(null); setDownload(null);
      mergeOutlines(feats.filter((f) => f.properties.geometry_kind === "outline"));
      streamOutlines(run);
    } catch (e) { setErr(String(e)); }
    finally { setBusy(null); }
  }, [call, acceptedRef, rejectedRef, sessionRef, picksRef, notsRef, conceptRef, placeRef, resultsRef, setAccepted, setRejected,
    setSession, setResults, mergeOutlines, streamOutlines]);

  // ---------------- layers ----------------
  const toggleLayer = useCallback(async (key: string, on: boolean) => {
    const def = CONTEXT_LAYERS.find((c) => c.key === key);
    if (!def) return;
    setLayersOn((s) => ({ ...s, [key]: on }));
    try {
      const vector = !!(await mv.current?.hasVector(key));
      if (!vector && def.vectorOnly) throw new Error(`${def.label}: vector tiles not available from this API`);
      if (on && !vector && !layerCache.current[key]) {
        setBusy(`Loading ${def.label}…`);
        let bbox: number[] | undefined;
        if (def.bboxed && mv.current) { const [w, s, e, n] = mv.current.bounds(); const dx = (e - w) * 1.5, dy = (n - s) * 1.5;
          bbox = [w - dx, s - dy, e + dx, n + dy]; }
        const env = await call("add_layer", { name: key, ...(bbox ? { bbox } : {}) });
        if (env.status === "declined") throw new Error(`${def.label}: ${env.declined_reason}`);
        layerCache.current[key] = env.result.geojson;
      }
      await mv.current?.setContext(def, on ? layerCache.current[key] ?? null : null, on);
    } catch (e) { setErr(String(e)); setLayersOn((s) => ({ ...s, [key]: false })); }
    finally { setBusy(null); }
  }, [call]);

  const toggleCompare = useCallback(async (on: boolean) => {
    if (!on) { setCompare("off"); return; }
    setCompare("loading"); setCompareNote(null);
    try {
      const exs = resultsRef.current.slice(0, 200).map(exampleOf);
      const env = await call("compare_years", { examples: exs, from_year: 2020, to_year: yearRef.current, concept: conceptRef.current ?? undefined });
      if (env.status === "declined") {
        setCompare("off");
        setCompareNote(`2020 index not ready — compare_years declined (${env.declined_reason ?? "no reason given"}).`);
        return;
      }
      const ch: Record<string, string> = {};
      for (const f of env.result.geojson.features) ch[exampleOf(f)] = f.properties.change ?? "unsure";
      setChanges(ch); setCompare("on");
    } catch (e) { setCompare("off"); setCompareNote(`compare_years failed: ${e}`); }
  }, [call, resultsRef, yearRef, conceptRef]);

  // ---------------- filter / measure / export ----------------
  const visibleFC = useCallback((feats: Feature[]): FC => ({ type: "FeatureCollection", features: feats.map((f) => {
    const ex = exampleOf(f); const o = outlinesRef.current[ex];
    return { type: "Feature", id: ex, geometry: o?.geometry ?? boxPoly(f.properties.cell_bbox), properties: {
      example: ex, rank: f.properties.rank, confidence: confOf(f), geometry_kind: o?.geometry ? "outline" : "box",
      area_m2: o?.area_m2 ?? null, area_acres: o?.area_acres ?? null, chip_id: f.properties.chip_id, cell: f.properties.cell,
      thumb_url: f.properties.thumb_url, accepted: accSet.has(ex) || undefined } };
  }) }), [outlinesRef, accSet]);

  const applyFilter = useCallback(async (where: Record<string, any>, label: string) => {
    setBusy("Filtering…"); setErr(null);
    try {
      const fc = visibleFC(pool);
      const env = await call("filter", { geojson: fc, where });
      if (env.status === "declined") { setErr(`filter declined: ${env.declined_reason}`); return; }
      const keep = new Set(env.result.geojson.features.map((f) => (f.properties.example as string) ?? String(f.id)));
      const near = where.within_m_of?.layer;
      const ms = await call("measure", { geojson: env.result.geojson, to_layers: near ? [near] : [] });
      setFilter({ label, where, keep, measure: ms.result.metrics, countIn: fc.features.length });
    } catch (e) { setErr(String(e)); }
    finally { setBusy(null); }
  }, [call, visibleFC, pool]);

  const runFilter = () => {
    const where: Record<string, any> = {};
    const bits: string[] = [];
    if (nearLayer) { where.within_m_of = { layer: nearLayer, m: nearM }; bits.push(`within ${fmtM(nearM)} of ${layerLabel(nearLayer)}`); }
    if (minAcres && Number(minAcres) > 0) { where.min_area_acres = Number(minAcres); bits.push(`≥ ${minAcres} ac`); }
    if (inPlace) { where.inside_place = inPlace; bits.push(`in ${inPlace}`); }
    if (!bits.length) { setFilter(null); return; }
    applyFilter(where, bits.join(" · "));
  };

  const doExport = useCallback(async (format: "csv" | "geojson" | "pdf") => {
    setBusy(`Exporting ${format.toUpperCase()}…`); setErr(null);
    try {
      const feats = counted;   // the same set the insight card counts
      const title = `${pretty(conceptRef.current ?? askedText ?? "results")}${placeRef.current ? ` — ${placeRef.current}` : ""}`;
      const env = await call("export", { geojson: visibleFC(feats), format, title, summary: {
        query: askedText, place: placeRef.current, aoi: aoiRef.current, year: yearRef.current,
        count: feats.length, area_acres_total: Number(stats.acres.toFixed(3)), median_area_m2: stats.median,
        outlined: stats.outlinedCounted, filter: filter?.label ?? null, scan_mode: scanMode, mode } });
      const url = env.result.metrics.download;
      if (url) { setDownload({ url, format }); window.open(url, "_blank"); }
      else setErr("export returned no download URL");
    } catch (e) { setErr(String(e)); }
    finally { setBusy(null); }
  }, [call, counted, visibleFC, askedText, conceptRef, placeRef, aoiRef, yearRef, stats, filter, scanMode, mode]);

  // ---------------- agent (server rules planner: POST /api/v1/agent/run) ----------------
  const agentBusy = useRef(false);
  const runAgent = useCallback(async (raw: string) => {
    const text = raw.trim();
    if (!text || agentBusy.current) return;
    agentBusy.current = true;
    setTab("agent"); setAgentRunning(true); setAgentText(text);
    setAgentMsgs([{ kind: "prompt", text, planner: "rules planner" }]);
    setStage("results"); resetResults(); setCands([]); setPicks([]); setNots([]); negRef.current = [];
    setQuery(text); setErr(null);
    const push = (m: AgentMsg) => setAgentMsgs((l) => [...l, m]);
    let plan: Record<string, any> = {};
    const filterLabel = () => {
      const lay = plan.layer ? layerLabel(plan.layer) : null;
      if (lay && plan.within_m != null) return `agent: within ${fmtM(Number(plan.within_m))} of ${lay}`;
      if (lay && plan.inside_layer) return `agent: inside ${lay}`;
      if (plan.host_layer) return `agent: on ${layerLabel(plan.host_layer)}`;
      return "agent filter";
    };
    const target: AgentTarget = {
      flyTo: (bbox, label, approximate) => { mv.current?.fitBbox(bbox, { maxZoom: 15 }); push({ kind: "fly", label: label ?? "area", approximate }); },
      addLayer: (layerId, env) => {
        const def = CONTEXT_LAYERS.find((c) => c.key === layerId);
        if (def) { layerCache.current[layerId] = env.result.geojson; setLayersOn((s) => ({ ...s, [layerId]: true }));
          mv.current?.setContext(def, env.result.geojson, true); }
        else mv.current?.setAgentLayer(layerId, env.result.geojson);
        push({ kind: "layer", layer: layerLabel(layerId), n: env.result.geojson.features.length });
      },
      showCandidates: (env) => {
        // The planner picks one sense itself and sends the other clear senses as negatives; it does not say which one,
        // so no tile is marked as "your example" here.
        setCands(env.result.geojson.features); setConcept(env.result.metrics.sense_list ?? null);
        push({ kind: "candidates", feats: env.result.geojson.features });
      },
      addResults: (layerId, env) => {
        applyFindAll(env);
        const m = env.result.metrics;
        push({ kind: "results", detected: env.result.geojson.features.length, outlined: Number(m.outlined ?? 0),
          rejected: Number(m.rejected_by_object_check ?? 0), exhaustive: m.mode === "exhaustive_scan", layer: layerId });
      },
      setFilter: (_layerId, env) => {
        const keep = new Set(env.result.geojson.features.map((f) => exampleOf(f)));
        const countIn = Number(env.result.metrics.count_in ?? resultsRef.current.length);
        setFilter({ label: filterLabel(), where: {}, keep, countIn });
        push({ kind: "filter", n: Number(env.result.metrics.count_out ?? keep.size), of: countIn });
      },
      showMetrics: (env) => {
        setFilter((f) => (f ? { ...f, measure: env.result.metrics } : f));
        push({ kind: "metrics", metrics: env.result.metrics });
      },
      offerDownload: (env, format) => {
        const url = env.result.metrics.download;
        if (url) { setDownload({ url, format: format ?? env.result.metrics.format }); push({ kind: "download", url, format: format ?? env.result.metrics.format ?? "file" }); }
      },
      say: (text) => push({ kind: "say", text }),
    };
    try {
      const res = await runRulesPlanner(text, target, {
        aoi: aoiRef.current || undefined,
        context: { place: placeRef.current, last_text: lastAgentText.current },
        onError: (msg) => push({ kind: "error", text: msg }),
        onResponse: (r) => {
          plan = r.plan ?? {};
          setAgentMsgs((l) => l.map((m) => (m.kind === "prompt" ? { ...m, planner: r.planner || m.planner } : m)));
          if (plan.place) setPlace(matchPlace(String(plan.place), placeMap) ?? String(plan.place));
          setAskedText(String(plan.concept_query ?? plan.concept ?? text));
        },
      });
      if (res.status === "ok" && !res.plan?.exported_from) lastAgentText.current = text;
      if (res.status === "declined") {
        push({ kind: "status", status: "declined" });
        if (!res.actions?.some((a) => a.action === "say")) push({ kind: "error", text: "The planner declined this request." });
      } else if (res.status === "partial") push({ kind: "status", status: "partial" });
    } catch (e) { push({ kind: "error", text: String(e) }); }
    finally { agentBusy.current = false; setAgentRunning(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [placeMap, applyFindAll, aoiRef]);

  // ---------------- deep link ----------------
  // ?q=find+swimming+pools&pick=backyard_pool[,community_pool]&place=Ballantyne&layers=osm_i485,fema_flood_zones
  //  extras: &aoi=south_charlotte &year=2022 &refine=1 (✓ top result) &filter=osm_i485 (within 1 km) &compare=1
  //          &agent=run[&prompt=...] (rules planner; agent=mock is kept as an alias) &tab=log|agent &layers_panel=0|1 &view=w,s,e,n (final framing) &shot=1 (headless repaint heartbeat) &debug=1 (layer stack)
  const latest = useRef({ applyFilter, toggleCompare, toggleLayer, judge });
  latest.current = { applyFilter, toggleCompare, toggleLayer, judge };
  const linked = useRef(false);
  useEffect(() => {
    if (linked.current || !aoi || !hl || !Object.keys(placeMap).length) return;
    linked.current = true;
    const qs = new URLSearchParams(window.location.search);
    const pl = qs.get("place"); if (pl) setPlace(matchPlace(pl, placeMap) ?? pl);
    if (qs.get("tab") === "agent") setTab("agent");
    if (qs.get("layers_panel")) setLayersOpen(qs.get("layers_panel") !== "0");
    const layerKeys = (qs.get("layers") ?? "").split(",").map((s) => s.trim()).filter(Boolean)
      .map((k) => CONTEXT_LAYERS.find((c) => c.key === k || c.key === `osm_${k}`)?.key ?? k);
    (async () => {
      if (qs.get("agent")) {
        await runAgent(qs.get("prompt") || AGENT_DEMO_PROMPT);
        if (qs.get("tab") === "log") setTab("log");
      }
      else if (qs.get("q")) {
        const cs = await ask(qs.get("q")!);
        const want = (qs.get("pick") ?? "").split(",").map((s) => s.trim()).filter(Boolean);
        if (cs.length && want.length) {
          const clear = cs.filter((c) => !c.properties.no_clear_example && c.properties.example);
          const chosen = want.map((w) => clear.find((c) => c.properties.sense_id === w)?.properties.example).filter(Boolean) as string[];
          const pos = chosen.length ? chosen : clear.length ? [clear[0].properties.example as string] : [];
          setPicks(pos);
          await findAll(pos, negativesFor(cs, pos));
          if (qs.get("refine") === "1" && resultsRef.current[0]) await latest.current.judge(resultsRef.current[0], true);
          const fl = qs.get("filter");
          if (fl) { setNearLayer(fl); await sleep(4000); await latest.current.applyFilter({ within_m_of: { layer: fl, m: 1000 } }, `within 1 km of ${layerLabel(fl)}`); }
          if (qs.get("compare") === "1") await latest.current.toggleCompare(true);
        }
      } else if (pl && placeMap[matchPlace(pl, placeMap) ?? ""]) {
        setStage("results"); mv.current?.fitBbox(placeMap[matchPlace(pl, placeMap)!], { maxZoom: 15, duration: 0 });
      }
      for (const k of layerKeys) await latest.current.toggleLayer(k, true);
      const view = (qs.get("view") ?? "").split(",").map(Number);
      if (view.length === 4 && view.every(Number.isFinite)) mv.current?.fitBbox(view, { maxZoom: 19, padding: 0, duration: 0 });
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aoi, hl, placeMap]);

  // ---------------- small handlers ----------------
  const flyToFeature = (f: Feature) => {
    setSel(exampleOf(f));
    mv.current?.fitBbox(f.properties.chip_bbox, { maxZoom: 18.5, padding: 40 });
  };
  const jumpTo = (name: string) => {
    const b = placeMap[name]; if (!b) return;
    mv.current?.fitBbox(b, { maxZoom: 15.5 });
  };
  const switchYear = (y: number) => {
    if (!hl) return;
    setYear(y); setAoi(defaultAoi(hl, y)); resetResults(); setStage("ask");
  };
  const years = hl ? new Set(hl.indexes.map((i) => i.year)) : new Set<number>();
  const aois = hl ? hl.indexes.filter((i) => i.year === year) : [];
  const isSlice = !!aoi && aoi !== "county";
  const thumbById = useMemo(() => {
    const m: Record<string, string> = {};
    for (const f of [...cands, ...results, ...accepted]) m[exampleOf(f)] = f.properties.thumb_url;
    return m;
  }, [cands, results, accepted]);
  const pickedFeats = cands.filter((c) => picks.includes(c.properties.example));
  const countLimits = faLimits.filter((l) => /^count|areas are summed|Confidence is a heuristic|Only the '|^Exhaustive|not exhaustive/i.test(l));
  const topK = scanMode === "ranked_top_k";
  const nAcc = acceptedFeats.length;

  return (
    <div className={`app stage-${stage}`}>
      <div className="main">
        {/* ---------------- left: results ---------------- */}
        <aside className="panel left">
          <div className="brand-row">
            <button className="brand" onClick={() => setStage("ask")} title="New search">
              <span className="logo" aria-hidden>◎</span>Groundtruth
            </button>
            <span className="brand-sub">Mecklenburg County, NC</span>
          </div>
          <form className="ask-mini" onSubmit={(e) => { e.preventDefault(); ask(query); }}>
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="What are you looking for?" />
            <button className="btn primary" disabled={!!busy || !aoi}>Ask</button>
          </form>
          {place && <div className="pill-row"><span className="pill">in {place}<button onClick={() => setPlace(null)} aria-label="clear place">×</button></span>
            {mode === "refine" && <span className="pill soft">refined · {nAcc} ✓ {rejected.length} ✗</span>}</div>}
          {!place && mode === "refine" && <div className="pill-row"><span className="pill soft">refined · {nAcc} ✓ {rejected.length} ✗</span></div>}

          {results.length === 0 && !busy && (
            <div className="section empty">
              <div className="empty-title">{agentRunning ? "Agent is working…" : "No results yet"}</div>
              <div className="hint">{faMetrics ? "Nothing matched here. " : ""}Ask for something above — pools, parking lots, solar panels — then pick an example and Groundtruth finds the rest.
                {isSlice && <> Today only the <b>{pretty(aoi)}</b> slice is indexed, so searches elsewhere return nothing.</>}</div>
            </div>)}

          {results.length > 0 && (
            <>
              <div className="section stats-sec">
                <div className="stats">
                  <div className="stat big"><b>{stats.count}</b><span>{filter ? `of ${stats.total} match the filter` : topK ? "top results" : "detected"}<br />
                    <em>{topK ? "not exhaustive" : scanMode === "exhaustive_scan" ? "every object in the area" : "objects"}</em></span></div>
                  <div className="stat"><b>{fmtAcres(stats.acres)}<small> ac</small></b><span>total area<br /><em>{stats.outlinedCounted} of {stats.count} outlined</em></span></div>
                  <div className="stat"><b>{stats.median == null ? "—" : Math.round(stats.median)}<small> m²</small></b><span>median area</span></div>
                </div>
                <div className="count-line">
                  {faMetrics?.dense && <span className="tag dense" title={`Dense mode: ${faMetrics.dense_added ?? 0} objects added from ${faMetrics.dense_proposals ?? "?"} pre-computed colour-blob proposals (built ${faMetrics.dense_built ?? "?"})`}>dense</span>}{" "}
                  <b>{stats.total}</b> {topK ? "top results (not exhaustive)" : "detected"} · <b>{stats.outlinedAll}</b> outlined
                  {objRejected > 0 && <> · <b>{objRejected}</b> rejected by object check</>}
                  {scanMode === "exhaustive_scan" && faMetrics?.chips_in_box != null &&
                    <span className="muted"> · scanned {Number(faMetrics.chips_in_box).toLocaleString()} chips / {Number(faMetrics.cells_scanned ?? 0).toLocaleString()} cells</span>}
                </div>
                {stats.outlinedAll < stats.total && (
                  <div className={`stream ${streaming ? "on" : ""}`}>
                    <div className="bar"><i style={{ width: `${stats.total ? (stats.outlinedAll / stats.total) * 100 : 0}%` }} /></div>
                    <span>{streaming ? "Outlining with SAM 2 · " : "Outlines · "}{stats.outlinedAll}/{stats.total} · {stats.total - stats.outlinedAll} pending (may still be rejected)</span>
                  </div>)}
                {countLimits.length > 0 && <ul className="caveats">{countLimits.map((l, i) => <li key={i}>{l}</li>)}</ul>}
              </div>

              <div className="section tools-sec">
                <div className="sec-title">Filter · measure</div>
                <div className="frow">
                  <span className="flabel">within</span>
                  <select value={nearM} onChange={(e) => setNearM(Number(e.target.value))}>
                    {[250, 500, 1000, 2000].map((m) => <option key={m} value={m}>{fmtM(m)}</option>)}
                  </select>
                  <span className="flabel">of</span>
                  <select value={nearLayer} onChange={(e) => setNearLayer(e.target.value)}>
                    <option value="">— any —</option>
                    {NEAR_LAYERS.map((k) => <option key={k} value={k}>{layerLabel(k)}</option>)}
                  </select>
                </div>
                <div className="frow">
                  <span className="flabel">min area</span>
                  <input className="num" type="number" min={0} step={0.01} placeholder="ac" value={minAcres} onChange={(e) => setMinAcres(e.target.value)} />
                  <span className="flabel">in</span>
                  <select value={inPlace} onChange={(e) => setInPlace(e.target.value)}>
                    <option value="">anywhere</option>
                    {Object.keys(placeMap).sort().map((p) => <option key={p} value={p}>{p}</option>)}
                  </select>
                </div>
                <div className="frow end">
                  {filter && <button className="btn ghost" onClick={() => setFilter(null)}>Clear</button>}
                  <button className="btn" disabled={!!busy} onClick={runFilter}>Apply filter</button>
                </div>
                {filter && (
                  <div className="filter-out">
                    <b>{stats.count}</b> kept of {stats.total} · {filter.label}
                    {filter.measure && Object.entries(filter.measure).filter(([k]) => k.startsWith("median_dist") || k.startsWith("min_dist")).map(([k, v]) =>
                      <div key={k} className="mrow"><span>{k.startsWith("min") ? "nearest" : "median"} distance to {layerLabel(k.replace(/^(min|median)_dist_m_to_/, ""))}</span><b>{fmtM(v as number)}</b></div>)}
                  </div>)}
                {filter && !filter.measure && filter.where.min_area_acres && <div className="hint small">Area filters drop objects that are not outlined yet.</div>}
                <div className="sec-title" style={{ marginTop: 14 }}>Export {stats.count}</div>
                <div className="frow">
                  {(["csv", "geojson", "pdf"] as const).map((f) => <button key={f} className="btn" disabled={!!busy || !stats.count} onClick={() => doExport(f)}>{f === "geojson" ? "GeoJSON" : f.toUpperCase()}</button>)}
                </div>
                {download && <a className="dl" href={download.url} target="_blank" rel="noreferrer">↓ {download.url.split("/").pop()}</a>}
              </div>

              <div className="section list-sec">
                {pickedFeats.length > 0 && (
                  <div className="examples">
                    <span className="flabel">your example{pickedFeats.length > 1 ? "s" : ""}</span>
                    {pickedFeats.map((c) => <CellThumb key={c.properties.example} f={c} size={34} />)}
                    <span className="hint small">{mode === "refine" ? "re-ranked by your ✓ / ✗" : "✓ / ✗ any result to refine live"}</span>
                  </div>)}
                {nAcc > 0 && <div className="sec-title">Accepted · {nAcc}</div>}
                {acceptedFeats.map((f) => <ResultRow key={`a-${exampleOf(f)}`} f={f} o={outlines[exampleOf(f)]} sel={sel} state="accepted"
                  onFly={flyToFeature} onJudge={judge} busy={!!busy} thumbById={thumbById} cut={cut} change={compare === "on" ? changes[exampleOf(f)] : undefined} />)}
                <div className="sec-title">{nAcc > 0 ? "Other detected" : "Detected"} · {ranked.length}{topK ? " (top results, not exhaustive)" : ""}
                  {ranked.length > LIST_MAX ? ` (showing ${LIST_MAX})` : ""}
                  {rejected.length > 0 && <span className="muted"> · {rejected.length} ✗ by you</span>}</div>
                {ranked.slice(0, LIST_MAX).map((f) => <ResultRow key={exampleOf(f)} f={f} o={outlines[exampleOf(f)]} sel={sel}
                  state={passes(f) ? "hit" : "filtered"} onFly={flyToFeature} onJudge={judge} busy={!!busy}
                  thumbById={thumbById} cut={cut} change={compare === "on" ? changes[exampleOf(f)] : undefined} />)}
              </div>
            </>)}
        </aside>

        {/* ---------------- map ---------------- */}
        <section className="map-wrap">
          <div ref={mapEl} className="map" />
          <div className="map-top">
            <PlaceJump places={placeMap} onPick={jumpTo} />
            {(busy || streaming) && <div className="status-chip"><span className="spin" />{busy ?? `Outlining ${stats.outlinedAll}/${stats.total}…`}</div>}
          </div>
          <div className={`layers-card ${layersOpen ? "" : "closed"}`}>
            <button className="lc-head" onClick={() => setLayersOpen(!layersOpen)}>
              <span>Layers</span><span className="muted">{layersOpen ? "–" : "+"}</span>
            </button>
            {layersOpen && (
              <div className="lc-body">
                <div className="lc-row">
                  <span className="flabel">Imagery</span>
                  <div className="seg">{[2022, 2020].map((y) => (
                    <button key={y} className={y === year ? "on" : ""} disabled={!years.has(y)}
                      title={years.has(y) ? `NAIP ${y}` : `No ${y} index yet`} onClick={() => switchYear(y)}>{y}</button>))}</div>
                </div>
                {aois.length > 0 && (
                  <div className="lc-row">
                    <span className="flabel">Index</span>
                    <select value={aoi} onChange={(e) => { setAoi(e.target.value); resetResults(); setStage("ask"); }}>
                      {aois.map((i) => <option key={i.aoi} value={i.aoi}>{pretty(i.aoi)} · {i.chips.toLocaleString()} chips</option>)}
                    </select>
                  </div>)}
                <label className={`lc-toggle ${results.length ? "" : "disabled"}`}>
                  <input type="checkbox" disabled={!results.length || compare === "loading"} checked={compare !== "off"} onChange={(e) => toggleCompare(e.target.checked)} />
                  <span>New since 2020</span>{compare === "loading" && <span className="spin" />}
                </label>
                {compareNote && <div className="lc-note">{compareNote}</div>}
                {compare === "on" && (
                  <div className="legend">{(["new", "existing", "unsure"] as const).map((k) =>
                    <span key={k}><i style={{ background: COLORS[k] }} />{k}</span>)}</div>)}
                <div className="lc-sep" />
                {CONTEXT_LAYERS.map((c) => (
                  <label key={c.key} className="lc-toggle">
                    <input type="checkbox" checked={!!layersOn[c.key]} onChange={(e) => toggleLayer(c.key, e.target.checked)} />
                    <span className={`sw ${c.kind}`} style={{ background: c.kind === "fill" ? `${c.color}55` : c.color, borderColor: c.color }} />
                    <span>{c.label}</span>
                  </label>))}
                <div className="lc-sep" />
                <div className="legend">
                  <span><i style={{ background: COLORS.hit }} />outline</span>
                  <span><i className="dash" style={{ borderColor: COLORS.hit }} />cell (not outlined yet)</span>
                  <span><i style={{ background: COLORS.accepted }} />accepted</span>
                </div>
              </div>)}
          </div>
          <div className="badge">NAIP {year} · 0.6 m · {aoi ? `${pretty(aoi)}${isSlice ? " (slice index)" : ""}` : "no index"}</div>
          {dbg && <pre className="dbg">{dbg}</pre>}
          {err && <div className="toast" role="alert"><span>{err}</span><button onClick={() => setErr(null)}>×</button></div>}
        </section>

        {stage === "ask" && (
          <AskScreen query={query} setQuery={setQuery} onAsk={ask} busy={busy} places={placeMap} placeSource={placeSource}
            place={place} setPlace={setPlace} hl={hl} aoi={aoi} year={year} err={err}
            onBrowse={(p) => { setStage("results"); if (p && placeMap[p]) mv.current?.fitBbox(placeMap[p], { maxZoom: 15, duration: 0 }); }}
            onAgent={runAgent} />)}
        {stage === "pick" && (
          <PickScreen query={askedText} cands={cands} picks={picks} nots={nots} setPicks={setPicks} setNots={setNots} place={place}
            busy={busy} onBack={() => setStage("ask")} onFind={() => findAll(picks, negativesFor(cands, picks))} concept={concept} />)}
      </div>

      {/* ---------------- right: work log / agent ---------------- */}
      <aside className="panel right">
        <div className="tabs">
          <button className={tab === "log" ? "on" : ""} onClick={() => setTab("log")}>Work log</button>
          <button className={tab === "agent" ? "on" : ""} onClick={() => setTab("agent")}>Agent <span className="tag planner">rules planner</span></button>
        </div>
        <div className={`tabpane ${tab === "log" ? "" : "hidden"}`}><WorkLog /></div>
        <div className={`tabpane ${tab === "agent" ? "" : "hidden"}`}>
          <AgentPanel msgs={agentMsgs} running={agentRunning} onRun={runAgent} text={agentText} setText={setAgentText} />
        </div>
      </aside>
    </div>
  );
}

function defaultAoi(h: Health, y: number) {
  const idx = h.indexes.filter((i) => i.year === y);
  return idx.find((i) => i.aoi === "county")?.aoi ?? [...idx].sort((a, b) => b.chips - a.chips)[0]?.aoi ?? "";
}

// ---------------- result row ----------------
function ResultRow({ f, o, sel, state, onFly, onJudge, busy, thumbById, cut, change }: {
  f: Feature; o?: Outline; sel: string | null; state: "hit" | "filtered" | "accepted"; onFly: (f: Feature) => void;
  onJudge: (f: Feature, ok: boolean) => void; busy: boolean; thumbById: Record<string, string>; cut: number; change?: string;
}) {
  const ex = exampleOf(f);
  const conf = confOf(f);
  const why = f.properties.why as { nearest_accepted: string; similarity: number } | undefined;
  const whyThumb = why ? thumbById[why.nearest_accepted] ?? `/api/v1/chips/${why.nearest_accepted.split("#")[0]}.jpg` : null;
  return (
    <div id={`res-${ex}`} className={`res ${state} ${sel === ex ? "sel" : ""}`} onClick={() => onFly(f)}>
      <CellThumb f={f} size={56} />
      <div className="res-body">
        <div className="res-top">
          <span className="rank">#{f.properties.rank ?? "✓"}</span>
          <span className="conf" title={`confidence ${conf.toFixed(3)} (heuristic, not a probability); cut ${cut}`}>
            <i style={{ width: `${Math.min(100, conf * 100)}%` }} className={conf >= cut ? "" : "lo"} />
          </span>
          <span className="confn">{pct(conf)}</span>
          {change && <span className={`tag ch-${change}`}>{change}</span>}
        </div>
        <div className="res-sub">
          {o === undefined ? <span className="muted">outlining…</span>
            : o.geometry ? <>{fmtM2(o.area_m2)} · {fmtAcres(o.area_acres)} ac</>
            : <span className="muted">no outline (cell only)</span>}
          {state === "filtered" && <span className="muted"> · filtered out</span>}
        </div>
        {why && (
          <div className="why" title={`nearest accepted example ${why.nearest_accepted}`}>
            {whyThumb && <img src={thumb(whyThumb)} alt="" />}<span>why: like your ✓ <b>{why.similarity.toFixed(2)}</b> sim</span>
          </div>)}
      </div>
      <div className="acts" onClick={(e) => e.stopPropagation()}>
        <button className="ok" title="Yes — more like this" disabled={busy || state === "accepted"} onClick={() => onJudge(f, true)}>✓</button>
        <button className="no" title="No — not this" disabled={busy} onClick={() => onJudge(f, false)}>✗</button>
      </div>
    </div>
  );
}

// ---------------- neighbourhood jump ----------------
function PlaceJump({ places, onPick }: { places: Record<string, Bbox>; onPick: (name: string) => void }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const names = Object.keys(places);
  const hits = q ? names.filter((n) => n.toLowerCase().includes(q.toLowerCase())).slice(0, 7) : names.slice(0, 8);
  const go = (n: string | null) => { if (!n) return; onPick(n); setQ(n); setOpen(false); };
  return (
    <form className="jump" onSubmit={(e) => { e.preventDefault(); go(matchPlace(q, places)); }}>
      <span className="jump-ico" aria-hidden>⌕</span>
      <input value={q} placeholder="Jump to a neighbourhood…" onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }} />
      {open && hits.length > 0 && (
        <div className="jump-list">{hits.map((n) => <button type="button" key={n} onMouseDown={() => go(n)}>{n}</button>)}</div>)}
    </form>
  );
}

// ---------------- agent panel ----------------
function AgentPanel({ msgs, running, onRun, text, setText }: {
  msgs: AgentMsg[]; running: boolean; onRun: (text: string) => void; text: string; setText: (s: string) => void;
}) {
  const scalar = (v: unknown) => v == null || typeof v === "number" || typeof v === "boolean" || (typeof v === "string" && v.length <= 60);
  return (
    <div className="agent">
      <div className="agent-banner">
        <span className="tag planner">rules planner</span>
        <p>A deterministic rules planner on the server (no LLM): <code>POST /api/v1/agent/run</code> reads the request, runs the real
          tools (each step_N envelope is in the work log) and returns map actions that drive this UI. Requests about people are declined.</p>
      </div>
      <form className="agent-prompt" onSubmit={(e) => { e.preventDefault(); onRun(text); }}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. pools within 1 km of I-485 in Ballantyne" aria-label="Agent request" />
        <button className="btn primary" disabled={running || !text.trim()}>{running ? "Running…" : "Run"}</button>
      </form>
      <div className="chips agent-chips">
        {AGENT_CHIPS.map((c) => <button key={c} type="button" className="chip" disabled={running} onClick={() => { setText(c); onRun(c); }}>{c}</button>)}
      </div>
      <div className="agent-msgs">
        {msgs.map((m, i) => {
          switch (m.kind) {
            case "prompt": return <div key={i} className="am prompt"><span className="tag planner">{m.planner}</span> “{m.text}”</div>;
            case "status": return <div key={i} className="am act"><span className={`tag ${m.status}`}>{m.status}</span></div>;
            case "say": return <div key={i} className="am say">{m.text}</div>;
            case "fly": return <div key={i} className="am act">fly_to · {m.label}{m.approximate ? " (approximate bbox)" : ""}</div>;
            case "candidates": return (
              <div key={i} className="am act">show_candidates · {m.feats.length} senses
                <div className="am-thumbs">{m.feats.map((f, j) => (
                  <div key={`${exampleOf(f)}-${j}`} className={f.properties.no_clear_example ? "nce" : ""}
                    title={f.properties.no_clear_example ? `${f.properties.sense_label}: no clear example — ${f.properties.reason ?? ""}` : f.properties.sense_label}>
                    <CellThumb f={f} size={40} /><span>{f.properties.sense_label}{f.properties.no_clear_example ? " (no clear example)" : ""}</span>
                  </div>))}</div>
              </div>);
            case "results": return (
              <div key={i} className="am act">add_results · {m.detected} {m.exhaustive ? "detected" : "top results (not exhaustive)"} · {m.outlined} outlined
                {m.rejected > 0 ? ` · ${m.rejected} rejected by object check` : ""}</div>);
            case "layer": return <div key={i} className="am act">add_layer · {m.layer} · {m.n} features</div>;
            case "filter": return <div key={i} className="am act">set_filter · {m.n} of {m.of} kept</div>;
            case "metrics": return (
              <div key={i} className="am act">show_metrics
                <div className="am-metrics">{Object.entries(m.metrics).filter(([, v]) => scalar(v)).map(([k, v]) =>
                  <div key={k}><span>{pretty(k)}</span><b>{v == null ? "—" : String(v)}</b></div>)}</div>
              </div>);
            case "download": return <div key={i} className="am act">offer_download · <a href={m.url} target="_blank" rel="noreferrer">{m.format.toUpperCase()} ↓</a></div>;
            case "error": return <div key={i} className="am err">{m.text}</div>;
          }
        })}
        {running && <div className="am act"><span className="spin" /> rules planner working…</div>}
      </div>
    </div>
  );
}
