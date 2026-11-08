// Groundtruth: sidebar (sessions, saved areas, pins) + landing / workspace (chat panel + one map).
// Structure, copy and motion follow docs/design/target; every number and image is real (planner envelopes, local NAIP).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Crosshair, Link2, Menu, MoreHorizontal, Pencil, Pin as PinIcon, Trash2 } from "lucide-react";
import { AgentResponse, Context, cropUrl, exportSet, Feature, agentRun, Envelope } from "./api";
import { GtMap, focusGeom, COUNTY, geomBbox } from "./map";
import { Card, StepLine, bboxAround, cardFor, centroid, currentEnv, featId, fmt, pillsFor, sayText, stepLines, scrub } from "./turns";
import Sidebar, { ReportRow, Seg } from "./Sidebar";
import NavRail, { View } from "./NavRail";
import InvestigationBlock, { Inv, InvEvidence, Bars } from "./Investigation";
import ValidationStudio from "./ValidationStudio";
import Composer, { ComposerHandle, DrawKind, tagsFor } from "./Composer";
import { Area, Mode, Pin, Prefs, SavedArea, SessionRow, Year, api, loadPrefs, owner as ownerFn, ruleTitle, savePrefs, units } from "./store";
import * as I from "./icons";

type Tile = { id: string; label: string; img: string };
type PointItem = { lon: number; lat: number } | { bbox: number[] } | { id: string };
type Req = { kind: "agent"; text: string; ctx: Context } | { kind: "point"; positives: PointItem[]; negatives: PointItem[]; area: any; year: number };
type UserMsg = { id: string; role: "user"; text: string; tags?: string[]; thumb?: string };
type AsstMsg = { id: string; role: "assistant"; text: string; steps: StepLine[]; secs: string; card: Card | null; pills: string[];
  pick?: Tile[]; query?: string; showDetails?: boolean; req?: Req; learn?: string; nameable?: boolean; named?: string;
  limits?: string[]; insights?: string[]; pointIds?: { examples: string[]; negatives: string[] }; inv?: Inv };
type Msg = UserMsg | { id: string; role: "working" } | AsstMsg;
type Shown = { ids: Set<string>; features: Feature[]; docks: Feature[]; links: Feature[] };

const LANDING_BG = "/v2assets/landing.webp"; // NAIP 2022, Lake Norman shore, pre-rendered (scripts/build_landing_bg.py)
const STARTERS = [
  { img: "/v2assets/card_flood.webp", title: "Flood exposure of homes with pools", sub: "Lake Norman · FEMA zones, lidar, evidence",
    run: { text: "Find residential properties near Lake Norman with swimming pools that intersect mapped 100-year flood zones. Rank the strongest candidates and tell me where data is missing." } },
  { img: "/v2assets/card_point.webp", title: "Click anything to find all like it", sub: "No labels: it learns from your clicks",
    run: { point: true } },
  { img: "/v2assets/card_lakefront.webp", title: "Lakefront homes with pools and docks", sub: "Lake Norman, every home checked",
    run: { text: "Lakefront homes with pools and docks in Lake Norman" } },
] as const;
const LN_BBOX = [-80.958699, 35.427332, -80.844396, 35.514785];
let uid = 0;
const nextId = (p: string) => `${p}${Date.now().toString(36)}${++uid}`;

export default function App() {
  // ---------- shell ----------
  const [prefs, setPrefsState] = useState<Prefs>(loadPrefs);
  const setPrefs = (p: Prefs) => { setPrefsState(p); savePrefs(p); };
  const [narrow, setNarrow] = useState(() => innerWidth < 900);
  const [drawer, setDrawer] = useState(false);
  const [view, setView] = useState<View>("investigations");
  const [validating, setValidating] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [query, setQuery] = useState("");
  const [evOpen, setEvOpen] = useState<InvEvidence | null>(null);
  const [selEv, setSelEv] = useState<any | null>(null);
  const pollRef = useRef<{ id: string; stop: boolean } | null>(null);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [total, setTotal] = useState(0);
  const [limit, setLimit] = useState(20);
  const [areas, setAreas] = useState<SavedArea[]>([]);
  const [pins, setPins] = useState<Pin[]>([]);
  const [places, setPlaces] = useState<Record<string, number[]>>({});
  const [sid, setSid] = useState<string | null>(null);
  const [title, setTitle] = useState("New search");
  const [editTitle, setEditTitle] = useState(false);
  const [hdrMenu, setHdrMenu] = useState(false);
  const [toast, setToast] = useState("");
  const [nameDlg, setNameDlg] = useState<{ title: string; placeholder: string; onSave: (n: string) => void } | null>(null);
  // ---------- conversation ----------
  const [stage, setStage] = useState<"landing" | "work">("landing");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [ctx, setCtx] = useState<Context & Record<string, any>>({});
  const [area, setArea] = useState<Area>(null);
  const [cyear, setCyear] = useState<Year>(String(prefs.year) as Year);
  const [mode, setMode] = useState<Mode>("find");
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [nots, setNots] = useState<Record<string, boolean>>({});
  const [pickDone, setPickDone] = useState(false);
  const [openSteps, setOpenSteps] = useState<Record<string, boolean>>({});
  const [menu, setMenu] = useState<string | null>(null);
  // ---------- map ----------
  const [sel, setSel] = useState<Feature | null>(null);
  const [selN, setSelN] = useState(0);
  const [selFrom, setSelFrom] = useState<{ msgId: string } | null>(null);
  const [swipe, setSwipe] = useState(55);
  const [fb, setFb] = useState<Record<string, "yes" | "no">>({});
  const [year, setYear] = useState<number>(prefs.year);
  const [layersOpen, setLayersOpen] = useState(false);
  const [attrOpen, setAttrOpen] = useState(false);
  const [srcs, setSrcs] = useState<any[]>([]);
  const [lyr, setLyr] = useState({ flood: false, hwy: false, bldg3d: false });
  const [legend, setLegend] = useState<{ on: boolean; docks: boolean; label: string }>({ on: false, docks: false, label: "Pools" });
  const [pointOn, setPointOn] = useState(false);
  const [hint, setHint] = useState("");
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const mapEl = useRef<HTMLDivElement>(null);
  const msgsEl = useRef<HTMLDivElement>(null);
  const comp1 = useRef<ComposerHandle>(null);
  const comp2 = useRef<ComposerHandle>(null);
  const gm = useRef<GtMap | null>(null);
  const shown = useRef<Shown | null>(null);
  const abort = useRef<AbortController | null>(null);
  const restoring = useRef(false);
  const point = useRef<{ positives: PointItem[]; negatives: PointItem[]; msgId: string | null }>({ positives: [], negatives: [], msgId: null });
  const t0 = useRef(0);
  const landing = stage === "landing";

  // ---------- boot ----------
  const refreshLists = useCallback(() => {
    api.sessions(limit, query).then((r) => { setSessions(r.sessions); setTotal(r.total); }).catch(() => {});
    api.investigations().then((r) => setReports(r.investigations.filter((x: ReportRow) => x.status !== "needs_input"))).catch(() => {});
    api.areas().then((r) => setAreas(r.areas)).catch(() => {});
    api.pins().then((r) => setPins(r.pins)).catch(() => {});
  }, [limit, query]);
  useEffect(() => { const t = setTimeout(refreshLists, 150); return () => clearTimeout(t); }, [refreshLists]);
  useEffect(() => {
    fetch("/api/v1/places").then((r) => r.json()).then((d) => setPlaces(d.places ?? {})).catch(() => {});
    fetch("/api/v1/sources").then((r) => r.json()).then((d) => setSrcs(Array.isArray(d.sources) ? d.sources : [])).catch(() => {});
    if (!mapEl.current || gm.current) return;
    gm.current = new GtMap(mapEl.current);
    (window as any).__gt = { ready: true };
    const m = location.pathname.match(/^\/s\/([A-Za-z0-9]+)/);
    if (m) void openSession(m[1], false);
    const onPop = () => {
      const q = location.pathname.match(/^\/s\/([A-Za-z0-9]+)/);
      if (q) void openSession(q[1], false); else resetAll(false);
    };
    addEventListener("popstate", onPop);
    const onResize = () => setNarrow(innerWidth < 900);
    addEventListener("resize", onResize);
    return () => { removeEventListener("popstate", onPop); removeEventListener("resize", onResize); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { (window as any).__gtReduce = prefs.reduceMotion; document.documentElement.classList.toggle("gt-reduce", prefs.reduceMotion); }, [prefs.reduceMotion]);
  useEffect(() => { if (msgsEl.current) msgsEl.current.scrollTop = msgsEl.current.scrollHeight; }, [msgs]);
  useEffect(() => { gm.current?.setYear(year); }, [year]);
  useEffect(() => { gm.current?.select(sel ? featId(sel) : null); }, [sel]);
  useEffect(() => { const t = setTimeout(() => gm.current?.resize(), 650); return () => clearTimeout(t); }, [stage, prefs.sidebar, narrow]);
  useEffect(() => { if (toast) { const t = setTimeout(() => setToast(""), 2200); return () => clearTimeout(t); } }, [toast]);

  // keyboard: Ctrl/⌘K new search · F find like this · Esc closes things
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target as HTMLElement)?.tagName ?? "");
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); resetAll(true); return; }
      if (e.key === "Escape") { setSel(null); setMenu(null); setLayersOpen(false); setHdrMenu(false); setAttrOpen(false); setNameDlg(null); if (pointOn) togglePoint(false); }
      if (!typing && !e.ctrlKey && !e.metaKey && !e.altKey && e.key.toLowerCase() === "f" && stage === "work") { e.preventDefault(); togglePoint(!pointOn); }
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  });

  // ---------- map transformation for one result ----------
  const applyToMap = useCallback((r: { status: string; ask?: string | null; actions?: any[]; env: Envelope | null; plan?: any }, opts: { fly?: boolean } = {}) => {
    const m = gm.current;
    if (!m) return;
    const fly = opts.fly ?? true;
    const all = r.env?.result?.geojson.features ?? [];
    const feats = all.filter((f) => !["dock", "dock_link"].includes(f.properties?.kind) && f.geometry);
    const docks = all.filter((f) => f.properties?.kind === "dock");
    const links = all.filter((f) => f.properties?.kind === "dock_link");
    const acts = r.actions ?? [];
    if (acts.some((a) => a.action === "show_layer" && a.layer === "flood")) { setLyr((o) => ({ ...o, flood: true })); void m.setLayer("flood", true); }
    if (r.status === "declined" || r.ask === "pick") return;
    if (acts.some((a) => a.action === "heatmap")) {
      shown.current = null;
      m.setResults({ features: feats, docks: [], links: [], look: "glow" });
      if (fly) m.flyToBbox(COUNTY, 24, 11);
      setLegend({ on: true, docks: false, label: legendLabel(r.plan) });
      return;
    }
    if (!feats.length) return; // nothing new to draw: keep the last picture
    const ids = new Set(feats.map(featId));
    const prev = shown.current;
    const subset = prev && feats.every((f) => prev.ids.has(featId(f))) && ids.size < prev.ids.size;
    if (subset && prev) {
      m.setResults({ features: prev.features, docks, links, look: "rings", matches: ids, pulse: true });
      shown.current = { ...prev, docks, links };
      if (fly) m.flyToCenter(densest(feats), 14.6);
    } else {
      m.setResults({ features: feats, docks, links, look: "rings", pulse: false });
      shown.current = { ids, features: feats, docks, links };
      if (fly) { if (feats.length > 12) m.flyToCenter(densest(feats), 14.3); else m.flyToBbox(bboxOf(feats), 80, 16.2); }
    }
    setLegend({ on: true, docks: docks.length > 0, label: legendLabel(r.plan) });
  }, []);

  // ---------- context the planner sees ----------
  const areaCtx = (a: Area) => (a ? (a.place ? { place: a.place } : a.geojson ? { geojson: a.geojson } : { bbox: a.bbox }) : null);
  const fullCtx = useCallback((extra: Partial<Context> = {}): Context & Record<string, any> => ({
    ...ctx, picked: pickedList(picked), not: pickedList(nots), area: areaCtx(area), year: cyear === "compare" ? "compare" : Number(cyear),
    mode, ...extra,
  }), [ctx, picked, nots, area, cyear, mode]);

  // ---------- one agent turn ----------
  const finish = (msg: AsstMsg, replaceId: string | null) => {
    setMsgs((ms) => replaceId ? replaceAssistant(ms, replaceId, msg) : ms.filter((x) => x.role !== "working").concat([msg]));
  };
  const runAgent = useCallback(async (text: string, c: Context, replaceId: string | null = null, opts: { fly?: boolean; quiet?: boolean } = {}) => {
    t0.current = performance.now();
    const ctl = new AbortController();
    abort.current = ctl;
    try {
      const r = await agentRun(text, c, ctl.signal);
      const { lines, secs } = stepLines(r, performance.now() - t0.current);
      let pick: Tile[] | undefined;
      if (r.ask === "pick") {
        const pc = r.steps.find((s) => s.tool === "pick_candidates")?.envelope;
        pick = (pc?.result?.geojson.features ?? []).filter((f) => !f.properties.no_clear_example).map((f) => ({
          id: f.properties.sense_id, label: f.properties.sense_label, img: f.properties.thumb_url }));
      }
      const env = currentEnv(r);
      const msg: AsstMsg = { id: replaceId ?? nextId("a"), role: "assistant", text: sayText(r) || fallbackText(r), steps: lines, secs,
        card: cardFor(r), pills: pillsFor(r), pick, query: r.plan?.effective_text ?? text, req: { kind: "agent", text, ctx: c },
        limits: env?.limits, insights: env?.result?.metrics?.insights };
      (window as any).__gtLast = { ms: Math.round(performance.now() - t0.current), status: r.status, count: msg.card?.count ?? null,
        brain: r.brain, ask: r.ask ?? null };
      if (!opts.quiet) finish(msg, replaceId);
      else setMsgs((ms) => ms.map((x) => (x.id === replaceId && x.role === "assistant" ? { ...x, card: msg.card ? { ...msg.card } : x.card } : x)));
      if (!replaceId) {
        setCtx((o) => ({ ...o, place: r.plan?.place ?? o.place ?? null, last_text: r.plan?.effective_text ?? text }));
        if (title === "New search" && r.status !== "declined") retitle(ruleTitle(text, r.plan, area));
      }
      applyToMap({ status: r.status, ask: r.ask, actions: r.actions, env, plan: r.plan }, opts);
      return r;
    } catch (e: any) {
      if (e?.name === "AbortError") return null;
      finish({ id: replaceId ?? nextId("a"), role: "assistant", text: "I couldn't finish that. Try again, or try a nearby area?", steps: [], secs: "0.0",
        card: null, pills: [] }, replaceId);
      return null;
    } finally {
      if (abort.current === ctl) abort.current = null;
      setBusy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applyToMap, title, area]);

  // ---------- one "find like this" turn (T2) ----------
  const runPoint = useCallback(async (req: Extract<Req, { kind: "point" }>, replaceId: string | null, opts: { fly?: boolean; quiet?: boolean } = {}) => {
    t0.current = performance.now();
    const ctl = new AbortController();
    abort.current = ctl;
    try {
      const r = await fetch("/api/v1/point/find", { method: "POST", headers: { "Content-Type": "application/json" }, signal: ctl.signal,
        body: JSON.stringify({ positives: req.positives, negatives: req.negatives, area: req.area, year: req.year }) });
      const env: Envelope = await r.json();
      const ms = Math.round(performance.now() - t0.current);
      const m = env.result?.metrics ?? {};
      const feats = (env.result?.geojson.features ?? []).filter((f) => f.geometry);
      const exhaustive = !!m.exhaustive;
      const where = req.area?.place ? `around ${req.area.place}` : req.area ? "in this area" : "across the county";
      const n = exhaustive ? (m.count_detected ?? m.count ?? feats.length) : null;
      const nPos = (m.examples ?? []).length, nNeg = (m.negatives ?? []).length;
      const text = env.status === "declined" ? scrub(env.declined_reason ?? "I couldn't use that spot. Try a spot inside the county.")
        : !feats.length ? "I couldn't find anything like that here. Try another example or a bigger area?"
        : exhaustive ? `Found ${fmt(n!)} like this ${where}.` : `Here are the top matches like this ${where}. Pick an area for a full count.`;
      const card: Card | null = feats.length ? { title: n !== null ? `${fmt(n)} like this` : "Top matches like this",
        sub: `${req.area?.place ?? (req.area ? "Your area" : "Mecklenburg County")} · NAIP ${req.year}`, count: n, features: feats,
        ref: "point", kind: "point_find", details: [scrub(env.method ?? ""), ...(env.sources ?? []).map((s) => scrub(s.name))].filter(Boolean) } : null;
      const learn = `Learning from ${nPos} example${nPos === 1 ? "" : "s"}${nNeg ? ` · ${nNeg} excluded` : ""}`;
      const msg: AsstMsg = { id: replaceId ?? nextId("a"), role: "assistant", text, card, pills: [], learn, nameable: !!feats.length,
        steps: [{ label: "Read your example", detail: `${nPos} spot${nPos === 1 ? "" : "s"} on the map`, ms: "" },
          { label: exhaustive ? "Compared every patch in the area" : "Compared every patch in the county", detail: "", ms: `${m.point_find_ms ?? ms} ms` }],
        secs: (ms / 1000).toFixed(1), req, limits: env.limits, pointIds: { examples: m.examples ?? [], negatives: m.negatives ?? [] } };
      (window as any).__gtLast = { ms, status: env.status, count: card?.count ?? null, kind: "point", examples: nPos, negatives: nNeg };
      if (opts.quiet && replaceId) setMsgs((x) => x.map((y) => (y.id === replaceId && y.role === "assistant" ? { ...msg, named: y.named } : y)));
      else finish(msg, replaceId);
      point.current.msgId = msg.id;
      applyToMap({ status: env.status, env, plan: { target: "like this" } }, opts);
      return env;
    } catch (e: any) {
      if (e?.name === "AbortError") return null;
      finish({ id: replaceId ?? nextId("a"), role: "assistant", text: "I couldn't finish that. Try again in a moment.", steps: [], secs: "0.0", card: null, pills: [] }, replaceId);
      return null;
    } finally {
      if (abort.current === ctl) abort.current = null;
      setBusy(false);
    }
  }, [applyToMap]);

  // ---------- sessions ----------
  const ensureSession = useCallback(async (firstText: string) => {
    if (sid) return sid;
    try {
      const s = await api.create("New search", {});
      setSid(s.id);
      history.pushState({}, "", `/s/${s.id}`);
      refreshLists();
      return s.id;
    } catch { setTitle("New search"); void firstText; return null; }
  }, [sid, refreshLists]);
  const retitle = (t: string) => { setTitle(t); };
  // autosave: chat (without result features), map state, filters, examples
  useEffect(() => {
    if (!sid || restoring.current) return;
    const t = setTimeout(() => {
      const data = {
        v: 2, msgs: msgs.filter((m) => m.role !== "working").map(slimMsg), ctx, area, cyear, mode, picked, nots, pickDone,
        status: lastInv(msgs)?.status ?? "search", scope: lastInv(msgs)?.plan?.scope?.label ?? area?.label ?? (ctx.place || null),
        point: point.current, map: { camera: gm.current?.camera(), year, layers: lyr, sel: sel ? featId(sel) : null },
      };
      api.patch(sid, { title, data }).then(refreshLists).catch(() => {});
    }, 700);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid, msgs, ctx, area, cyear, mode, pickDone, year, lyr, sel, title]);

  const openSession = useCallback(async (id: string, push = true) => {
    let s;
    try { s = await api.session(id); } catch { setToast("That search isn't available."); history.replaceState({}, "", "/"); return; }
    restoring.current = true;
    abort.current?.abort();
    const d = s.data ?? {};
    setSid(s.id); setTitle(s.title); if (push) history.pushState({}, "", `/s/${s.id}`);
    setStage("work"); setDrawer(false); setSel(null); setMenu(null); setBusy(false); setPointOn(false); gm.current?.pointMode(false, () => {});
    setCtx(d.ctx ?? {}); setArea(d.area ?? null); setCyear(d.cyear ?? "2022"); setMode(d.mode ?? "find");
    setPicked(d.picked ?? {}); setNots(d.nots ?? {}); setPickDone(!!d.pickDone);
    point.current = d.point ?? { positives: [], negatives: [], msgId: null };
    const ms: Msg[] = d.msgs ?? [];
    setMsgs(ms);
    const mp = d.map ?? {};
    setYear(mp.year ?? 2022); setLyr(mp.layers ?? { flood: false, hwy: false, bldg3d: false });
    shown.current = null;
    void gm.current?.startImagery();
    void gm.current?.setResults(null);
    if (d.area?.geojson || d.area?.bbox) void gm.current?.showArea(d.area.geojson ?? bboxPoly(d.area.bbox), false);
    else void gm.current?.showArea(null);
    for (const k of ["flood", "hwy", "bldg3d"] as const) void gm.current?.setLayer(k, !!(mp.layers ?? {})[k]);
    // re-hydrate result sets from the engine caches: re-run each stored request, in order, without camera moves
    for (const m of ms) {
      if (m.role === "assistant" && m.inv) {
        const inv = await fetchInv(m.inv.id, true);
        if (inv) { setMsgs((x) => x.map((y) => (y.id === m.id && y.role === "assistant" ? { ...y, inv, card: invCard(inv) } : y))); applyInv(inv, false); }
        continue;
      }
      if (m.role !== "assistant" || !m.req || (!m.card && !m.pick)) continue;
      if (m.req.kind === "agent") await runAgent(m.req.text, m.req.ctx, m.id, { fly: false, quiet: true });
      else await runPoint(m.req, m.id, { fly: false, quiet: true });
    }
    if (mp.camera) gm.current?.restoreCamera(mp.camera);
    const sh = shown.current as Shown | null; // set by the re-runs above (TS can't see through the awaits)
    if (mp.sel && sh) {
      const f = sh.features.find((x) => featId(x) === mp.sel);
      if (f) { setSel(f); setSelN(sh.features.indexOf(f) + 1); }
    }
    restoring.current = false;
    (window as any).__gtRestored = { id: s.id, msgs: ms.length };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runAgent, runPoint]);

  const resetAll = (push = true) => {
    abort.current?.abort();
    setStage("landing"); setMsgs([]); setInput(""); setBusy(false); setCtx({}); setPicked({}); setNots({}); setPickDone(false);
    setOpenSteps({}); setMenu(null); setSel(null); setFb({}); setYear(prefs.year); setLayersOpen(false); setArea(null);
    setCyear(String(prefs.year) as Year); setMode("find"); setSid(null); setTitle("New search"); setDrawer(false);
    setLegend({ on: false, docks: false, label: "Pools" }); setPointOn(false); setHint("");
    point.current = { positives: [], negatives: [], msgId: null };
    gm.current?.pointMode(false, () => {});
    shown.current = null; void gm.current?.setResults(null); void gm.current?.showArea(null, false); gm.current?.flyToBbox(COUNTY, 20, 11);
    if (push) history.pushState({}, "", "/");
    setTimeout(() => comp1.current?.focus(), 60);
  };

  // ---------- sending ----------
  const send = useCallback(async (raw: string) => {
    const text = raw.trim();
    if (!text || busy) return;
    const first = stage === "landing";
    if (first) setStage("work");
    setInput(""); setBusy(true); setSel(null); setMenu(null); setLayersOpen(false);
    setMsgs((ms) => ms.concat([{ id: nextId("u"), role: "user", text, tags: tagsFor(area, cyear, mode) }, { id: "working", role: "working" }]));
    const c = fullCtx(first ? { place: null, last_text: null } : {});
    setTimeout(() => void gm.current?.startImagery(), 150); // after the chat request has a connection
    void ensureSession(text);
    const prev = first ? null : lastInv(msgs);
    const started = await startInvestigation(text, { area: c.area, year: c.year, mode: c.mode, rejected: c.rejected ?? [],
      previous: prev && ["done", "partial"].includes(prev.status) ? prev.id : undefined });
    if (started === "chat") await runAgent(text, c);
  }, [busy, stage, area, cyear, mode, fullCtx, runAgent, ensureSession, msgs]);

  // ---------- investigations: start, then poll the real record until it finishes ----------
  const startInvestigation = useCallback(async (objective: string, context: Record<string, any>): Promise<"chat" | "inv" | "error"> => {
    t0.current = performance.now();
    let r: any;
    try {
      r = await fetch("/api/v1/investigations", { method: "POST", headers: { "Content-Type": "application/json", "X-GT-Owner": ownerId() },
        body: JSON.stringify({ objective, context }) }).then((x) => x.json());
    } catch { return "chat"; }
    if (r.fallback === "chat") return "chat";
    if (!r.id) return "chat";
    const msgId = nextId("a");
    const inv: Inv = r;
    setMsgs((ms) => ms.filter((x) => x.role !== "working").concat([{ id: msgId, role: "assistant", text: "", steps: [], secs: "0.0",
      card: null, pills: [], inv }]));
    if (title === "New search" && inv.status !== "declined") retitle(invTitle(objective, inv));
    if (["running", "planning"].includes(inv.status)) await pollInv(inv.id, msgId);
    else { setBusy(false); (window as any).__gtLast = { ms: Math.round(performance.now() - t0.current), status: inv.status, kind: "investigation", id: inv.id }; }
    return "inv";
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title]);

  const pollInv = useCallback(async (id: string, msgId: string) => {
    const me = { id, stop: false };
    pollRef.current = me;
    let firstFeatures = false;
    for (;;) {
      await new Promise((res) => setTimeout(res, 300));
      if (me.stop) break;
      const inv = await fetchInv(id, false);
      if (!inv) continue;
      setMsgs((ms) => ms.map((x) => (x.id === msgId && x.role === "assistant" ? { ...x, inv: { ...inv, result: x.inv?.result ?? inv.result } } : x)));
      if (!firstFeatures && inv.plan?.steps.some((s) => s.op.startsWith("find") && s.status === "done")) {
        firstFeatures = true;
        (window as any).__gtFirst = Math.round(performance.now() - t0.current);
      }
      if (!["running", "planning"].includes(inv.status)) {
        const full = await fetchInv(id, true);
        if (full) {
          setMsgs((ms) => ms.map((x) => (x.id === msgId && x.role === "assistant" ? { ...x, inv: full, card: invCard(full) } : x)));
          applyInv(full, true);
          (window as any).__gtLast = { ms: Math.round(performance.now() - t0.current), first_ms: (window as any).__gtFirst ?? null,
            status: full.status, kind: "investigation", id, count: invCard(full)?.count ?? null };
        }
        break;
      }
    }
    if (pollRef.current === me) pollRef.current = null;
    setBusy(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const applyInv = (inv: Inv, fly: boolean) => {
    if (!inv.result?.features?.length) return;
    const env: any = { result: { geojson: { type: "FeatureCollection", features: inv.result.features }, metrics: inv.result.metrics } };
    const flood = (inv.plan?.constraints ?? []).some((c) => /flood/.test(c)) || inv.evidence.some((e) => e.source?.name?.includes("FEMA"));
    shown.current = null;
    applyToMap({ status: "ok", env, actions: flood ? [{ action: "show_layer", layer: "flood" }] : [], plan: { target_kind: "derived" } }, { fly });
  };

  // a finding → its features on the map (the rest fade), camera to them
  const showFeatures = (ids: string[], label: string) => {
    const sh = shown.current;
    if (!sh) return;
    const set = new Set(ids);
    const fs = sh.features.filter((f) => set.has(featId(f)));
    if (!fs.length) { setToast("Those results aren't on the map"); return; }
    void gm.current?.setResults({ features: sh.features, docks: sh.docks, links: sh.links, look: "rings", matches: set, pulse: true });
    gm.current?.flyToBbox(bboxOf(fs), 80, 16.5);
    setHint(""); setToast(label.length > 60 ? `${label.slice(0, 57)}…` : label);
  };

  const stop = () => {
    abort.current?.abort();
    if (pollRef.current) {
      const id = pollRef.current.id;
      void fetch(`/api/v1/investigations/${id}/cancel`, { method: "POST" });
      pollRef.current.stop = true;
      // keep polling once more so the record shows which steps were skipped
      setTimeout(async () => { const inv = await fetchInv(id, true);
        if (inv) setMsgs((ms) => ms.map((x) => (x.role === "assistant" && x.inv?.id === id ? { ...x, inv, card: invCard(inv) } : x))); }, 1500);
    }
    setBusy(false);
    setMsgs((ms) => ms.filter((x) => x.role !== "working").concat([{ id: nextId("a"), role: "assistant", text: "Stopped.", steps: [], secs: "0.0", card: null, pills: [] }]));
  };

  const continuePick = useCallback((query: string) => {
    setPickDone(true); setBusy(true);
    setMsgs((ms) => ms.concat([{ id: "working", role: "working" }]));
    void runAgent(query, fullCtx({ place: null, last_text: null }));
  }, [fullCtx, runAgent]);

  // ---------- area chip ----------
  const chooseArea = (a: Area) => {
    setArea(a);
    if (a?.geojson || a?.bbox) void gm.current?.showArea(a.geojson ?? bboxPoly(a.bbox!), stage === "work");
    else void gm.current?.showArea(null);
  };
  const drawArea = async (k: DrawKind) => {
    if (stage === "landing") { setStage("work"); void gm.current?.startImagery(); gm.current?.flyToBbox(area?.bbox ?? LN_BBOX, 30, 14); }
    setHint(k === "rectangle" ? "Drag a rectangle on the map. Esc cancels." : "Click the corners, double-click to finish. Esc cancels.");
    const g = await gm.current?.draw(k);
    setHint("");
    if (g) chooseArea({ label: "Drawn area", geojson: g });
  };
  const saveArea = (geometry: any, suggested = "My area") => {
    setNameDlg({ title: "Save this area", placeholder: suggested, onSave: (name) => {
      api.addArea(name || suggested, geometry).then(() => { refreshLists(); setToast("Area saved"); }).catch(() => setToast("Couldn't save the area"));
      if (area && !area.place) setArea({ ...area, label: name || suggested });
    } });
  };

  // ---------- find like this (T2) ----------
  const togglePoint = (on: boolean) => {
    setPointOn(on);
    setHint(on ? "Click any object to find all like it. Shift-click adds examples · Alt-drag draws a box." : "");
    gm.current?.pointMode(on, (p, add) => void pointPick(p, add));
  };
  const pointPick = async (p: PointItem, add: boolean) => {
    if (busy) return;
    if (!add || !point.current.positives.length) point.current = { positives: [p], negatives: [], msgId: null };
    else point.current = { ...point.current, positives: [...point.current.positives, p] };
    const thumbBox = "bbox" in p ? p.bbox : "lon" in p ? bboxAround({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] }, properties: {} }, 40) : null;
    setBusy(true); setSel(null);
    const replaceId = add ? point.current.msgId : null;
    setMsgs((ms) => ms.concat(replaceId ? [{ id: "working", role: "working" }] : [
      { id: nextId("u"), role: "user", text: add ? "Add this example" : "Find more like this", tags: tagsFor(area, cyear, mode),
        thumb: thumbBox ? cropUrl(thumbBox, 2022, 96) : undefined }, { id: "working", role: "working" }]));
    void ensureSession("Find like this");
    if (title === "New search") retitle(`${area?.label ?? "County"} · find like this`);
    await runPoint({ kind: "point", positives: point.current.positives, negatives: point.current.negatives, area: areaCtx(area), year: 2022 }, replaceId);
  };
  // "More like this" / "Not this" on any result: live refine of the point set, or of an agent set (re-run with the rejection)
  const refine = useCallback(async (f: Feature, v: "yes" | "no") => {
    const id = featId(f);
    setFb((o) => ({ ...o, [id]: v }));
    const from = selFrom?.msgId ?? point.current.msgId;
    const fromMsg = msgs.find((x) => x.id === from && x.role === "assistant") as AsstMsg | undefined;
    const ex = f.properties?.example ?? (f.properties?.pool_examples ?? [])[0];
    if (fromMsg?.req?.kind === "point" && ex) {
      const req = fromMsg.req;
      const next = { ...req, positives: v === "yes" ? [...req.positives, { id: ex }] : req.positives,
        negatives: v === "no" ? [...req.negatives, { id: ex }] : req.negatives };
      point.current = { positives: next.positives, negatives: next.negatives, msgId: fromMsg.id };
      setBusy(true);
      await runPoint(next, fromMsg.id, { fly: false, quiet: true });
      return;
    }
    if (v === "no" && fromMsg?.req?.kind === "agent") {
      const c = { ...fromMsg.req.ctx, rejected: [...(ctx.rejected ?? []), id], last_text: null };
      setCtx((o) => ({ ...o, rejected: c.rejected }));
      if (shown.current) shown.current = { ...shown.current, ids: new Set([...shown.current.ids].filter((x) => x !== id)),
        features: shown.current.features.filter((x) => featId(x) !== id) };
      setBusy(true);
      await runAgent(fromMsg.query ?? fromMsg.req.text, c, fromMsg.id, { fly: false, quiet: true });
    } else if (v === "yes") setCtx((o) => ({ ...o, accepted: [...(o.accepted ?? []), id] }));
  }, [selFrom, msgs, ctx, runPoint, runAgent]);

  const nameConcept = (msgId: string, name: string) => {
    const m = msgs.find((x) => x.id === msgId) as AsstMsg | undefined;
    if (!m?.req || m.req.kind !== "point" || !name.trim()) return;
    const n = name.trim().toLowerCase();
    // the planner's contract: {name: {examples: ["<chip>#<cell>"], negatives: [...]}} (the ids the clicks resolved to)
    setCtx((o) => ({ ...o, named_concepts: { ...(o.named_concepts ?? {}), [n]: { examples: m.pointIds?.examples ?? [],
      negatives: m.pointIds?.negatives ?? [] } } }));
    setMsgs((ms) => ms.map((x) => (x.id === msgId && x.role === "assistant" ? { ...x, named: n, nameable: false } : x))
      .concat([{ id: nextId("a"), role: "assistant", text: `Got it: “${n}”. You can ask about ${n} in this search, e.g. “${n} in flood zones”.`, steps: [], secs: "0.0", card: null,
        pills: [`${n} in flood zones`, `${n} around Lake Norman`] }]));
  };

  // ---------- results ----------
  const openResult = useCallback((f: Feature, n: number, from: { msgId: string } | null = null) => {
    setSel(f); setSelN(n); setSwipe(55); setMenu(null); setEvOpen(null); setSelEv(null);
    if (from) setSelFrom(from);
    const inv = lastInvWith(msgs, featId(f));
    if (inv) fetch(`/api/v1/investigations/${inv.id}/features/${encodeURIComponent(featId(f))}/evidence`).then((r) => r.json())
      .then((d) => setSelEv(d.found ? { ...d, inv: inv.id } : null)).catch(() => {});
    gm.current?.flyToFeature(f);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [msgs]);
  useEffect(() => {
    if (!gm.current) return;
    gm.current.onOpen = (id) => {
      const f = shown.current?.features.find((x) => featId(x) === id);
      if (f) openResult(f, (shown.current?.features.indexOf(f) ?? 0) + 1);
    };
    gm.current.onHover = (id, x, y) => {
      if (!id) { setTip(null); return; }
      const f = shown.current?.features.find((q) => featId(q) === id);
      if (f) setTip({ x, y, text: units(whyOf(f), prefs.units) });
    };
  }, [openResult, prefs.units]);

  const verdict = async (invId: string, fid: string, v: "confirm" | "reject") => {
    try {
      await fetch(`/api/v1/investigations/${invId}/feedback`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feature_id: fid, verdict: v }) });
      setSelEv((o: any) => (o ? { ...o, verdict: v } : o));
      setToast(v === "confirm" ? "Confirmed. Stored as evidence." : "Rejected. Stored as evidence.");
    } catch { setToast("Couldn't record that"); }
  };

  const doExport = useCallback(async (card: Card, format: "csv" | "geojson") => {
    setMenu(null);
    const url = await exportSet({ type: "FeatureCollection", features: card.features }, format);
    if (!url) { setToast("Export failed"); return; }
    const a = document.createElement("a");
    a.href = url; a.download = url.split("/").pop() ?? `groundtruth.${format}`;
    document.body.appendChild(a); a.click(); a.remove();
    (window as any).__gtExport = { url, rows: card.features.length, count: card.count };
  }, []);
  const pinCard = (m: AsstMsg) => {
    setMenu(null);
    if (!sid || !m.card) return;
    api.addPin(sid, m.id, m.card.title).then(() => { refreshLists(); setToast("Pinned"); }).catch(() => setToast("Couldn't pin"));
  };
  const openPin = async (p: Pin) => {
    if (p.session_id !== sid) await openSession(p.session_id);
    setTimeout(() => document.querySelector(`[data-msg="${p.msg_id}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" }), 400);
  };
  const renameSession = (id: string, t: string) => {
    const tt = t.trim(); if (!tt) return;
    if (id === sid) setTitle(tt);
    api.patch(id, { title: tt }).then(refreshLists).catch(() => {});
  };
  const deleteSession = (id: string) => {
    api.remove(id).then(() => { refreshLists(); if (id === sid) resetAll(true); setToast("Deleted"); }).catch(() => setToast("Couldn't delete"));
  };

  const lastA = useMemo(() => { let k = -1; msgs.forEach((m, i) => { if (m.role === "assistant") k = i; }); return k; }, [msgs]);
  const nPicked = Object.values(picked).filter(Boolean).length;
  const sheet = sel ? sheetFor(sel, selN, prefs.units) : null;
  const selBox = sel ? sheetBbox(sel) : null;
  const fromPoint = !!(sel && ((msgs.find((x) => x.id === (selFrom?.msgId ?? point.current.msgId)) as AsstMsg | undefined)?.req?.kind === "point"));
  const sideOpen = narrow ? drawer : prefs.sidebar === "open";
  const openSide = () => { if (narrow) setDrawer(true); else setPrefs({ ...prefs, sidebar: "open" }); };
  const closeSide = () => { if (narrow) setDrawer(false); else setPrefs({ ...prefs, sidebar: "rail" }); };

  const composerProps = { busy, onStop: stop, area, onArea: chooseArea, year: cyear, onYear: (y: Year) => { setCyear(y); if (y !== "compare") setYear(Number(y)); },
    mode, onMode: setMode, places, saved: areas, onDraw: (k: DrawKind) => void drawArea(k),
    onSaveArea: area && !area.place ? () => saveArea(area.geojson ?? bboxPoly(area.bbox!), area.label) : undefined };

  return (
    <div className="gt-app">
      <NavRail view={view} sidebarOpen={sideOpen} settingsOpen={settingsOpen} onSettings={() => setSettingsOpen((o) => !o)}
        validating={validating} onValidate={() => {
          const on = !validating;
          setValidating(on); setSel(null); setEvOpen(null);
          if (on) { setStage("work"); void gm.current?.startImagery(); void gm.current?.setResults(null); }
          else { void gm.current?.showReview(null, null); }
        }}
        onHome={() => { setValidating(false); void gm.current?.showReview(null, null); resetAll(true); }} onDiscover={() => { resetAll(true); setTimeout(() => document.querySelector(".gt-starters")?.scrollIntoView({ behavior: "smooth" }), 80); }}
        onView={(v) => { if (sideOpen && view === v) closeSide(); else { setView(v); openSide(); } }} />
      {settingsOpen && (
        <div className="gt-pop gt-settings gt-settings-rail" role="dialog" aria-label="Settings" onMouseLeave={() => setSettingsOpen(false)}>
          <div className="gt-poplabel">Imagery year</div>
          <Seg value={String(prefs.year)} options={[["2022", "2022"], ["2020", "2020"]]} onChange={(v) => { const p = { ...prefs, year: Number(v) as 2022 | 2020 }; setPrefs(p); if (stage === "landing") { setYear(p.year); setCyear(v as Year); } }} />
          <div className="gt-poplabel">Units</div>
          <Seg value={prefs.units} options={[["m", "Metres"], ["ft", "Feet"]]} onChange={(v) => setPrefs({ ...prefs, units: v as "m" | "ft" })} />
          <label className="gt-check" style={{ marginTop: 6 }}><input type="checkbox" checked={prefs.reduceMotion} onChange={() => setPrefs({ ...prefs, reduceMotion: !prefs.reduceMotion })} />Reduce motion</label>
        </div>)}
      <Sidebar view={view} narrow={narrow} open={sideOpen} onClose={closeSide} onNew={() => resetAll(true)} query={query} onQuery={setQuery}
        sessions={sessions} total={total} activeId={sid} onOpen={(id) => void openSession(id)} onRename={renameSession} onDelete={deleteSession} reports={reports}
        onShowAll={() => setLimit(200)} areas={areas} onArea={(a) => { chooseArea({ label: a.name, geojson: a.geometry }); if (stage === "work") gm.current?.flyToBbox(geomBbox(a.geometry), 48, 16); }}
        onDeleteArea={(id) => api.delArea(id).then(refreshLists)} pins={pins} onPin={(p) => void openPin(p)} onUnpin={(id) => api.delPin(id).then(refreshLists)} />

      <div className="gt-stage">
        <div className="gt-ws" style={{ position: "absolute", inset: 0, display: "flex" }}>
          <aside className="gt-panel" aria-label="Conversation" style={{ width: 420, flex: "none", height: "100%", background: "#ffffff",
            borderRight: "1px solid #e7e6e3", display: "flex", flexDirection: "column", zIndex: 2, opacity: landing ? 0 : 1,
            transform: `translateX(${landing ? -24 : 0}px)`, transition: "opacity .5s ease .05s, transform .6s cubic-bezier(.2,.8,.2,1) .05s" }}>
            <div className="gt-panelhead">
              {narrow && <button className="gt-icon gt-icon-sm" aria-label="Open sidebar" onClick={() => setDrawer(true)}><Menu size={16} strokeWidth={1.5} /></button>}
              {editTitle ? (
                <input className="gt-titleedit" autoFocus defaultValue={title} aria-label="Search title"
                  onKeyDown={(e) => { if (e.key === "Enter") { if (sid) renameSession(sid, (e.target as HTMLInputElement).value); else setTitle((e.target as HTMLInputElement).value); setEditTitle(false); } if (e.key === "Escape") setEditTitle(false); }}
                  onBlur={(e) => { if (sid) renameSession(sid, e.target.value); setEditTitle(false); }} />
              ) : (
                <button className="gt-titlebtn" onClick={() => setEditTitle(true)} title="Rename" data-testid="session-title">{title}</button>
              )}
              <div style={{ position: "relative" }}>
                <button className="gt-icon gt-icon-sm" aria-label="Search options" aria-expanded={hdrMenu} onClick={() => setHdrMenu((o) => !o)}><MoreHorizontal size={16} strokeWidth={1.5} /></button>
                {hdrMenu && (
                  <div className="gt-pop gt-hdrmenu" role="menu" onMouseLeave={() => setHdrMenu(false)}>
                    <button className="gt-menuitem" role="menuitem" onClick={() => { setHdrMenu(false); setEditTitle(true); }}><Pencil size={14} strokeWidth={1.5} />Rename</button>
                    <button className="gt-menuitem" role="menuitem" disabled={!sid} onClick={() => { setHdrMenu(false); const m = [...msgs].reverse().find((x) => x.role === "assistant" && (x as AsstMsg).card) as AsstMsg | undefined; if (m) pinCard(m); }}><PinIcon size={14} strokeWidth={1.5} />Pin latest result</button>
                    <button className="gt-menuitem" role="menuitem" disabled={!sid} onClick={() => { setHdrMenu(false); void navigator.clipboard?.writeText(location.href).then(() => setToast("Link copied")).catch(() => setToast(location.href)); }}><Link2 size={14} strokeWidth={1.5} />Copy link</button>
                    <button className="gt-menuitem" role="menuitem" disabled={!sid} onClick={() => { setHdrMenu(false); if (sid) deleteSession(sid); }}><Trash2 size={14} strokeWidth={1.5} />Delete</button>
                  </div>)}
              </div>
            </div>

            {validating && <ValidationStudio map={gm.current} onToast={setToast} />}
            <div ref={msgsEl} data-testid="msgs" hidden={validating} style={{ flex: 1, overflowY: "auto", padding: "8px 22px 24px", display: "flex", flexDirection: "column", gap: 16 }}>
              {msgs.map((m, i) => {
                if (m.role === "user") return (
                  <div key={m.id} className="gt-in" style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
                    <div data-testid="user-msg" className="gt-umsg">
                      {m.thumb && <img src={m.thumb} alt="" className="gt-umsg-thumb" />}<span>{m.text}</span></div>
                    {!!m.tags?.length && <div className="gt-tags">{m.tags.join(" · ")}</div>}
                  </div>);
                if (m.role === "working") return (
                  <div key={m.id} className="gt-in gt-skel" role="status" aria-label="Working"><span /><span /><span /></div>);
                const open = !!openSteps[m.id];
                const isPick = !!m.pick && !pickDone;
                return (
                  <div key={m.id} className="gt-in" data-testid="assistant-msg" data-msg={m.id} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    {m.inv ? (
                      <InvestigationBlock inv={m.inv} latest={i === lastA} busy={busy} fmtU={(x) => units(x, prefs.units)}
                        onShow={showFeatures} onFollow={(t) => void send(t)} onOpenEvidence={(e) => { setSel(null); setEvOpen(e); }} />
                    ) : <p data-testid="reply" style={{ margin: 0, fontSize: 15, lineHeight: 1.5, color: "#191919" }}>{units(m.text, prefs.units)}</p>}
                    {!!m.insights?.length && (
                      <div className="gt-insights" data-testid="insights">{m.insights.map((t, k) => <span key={k}>{units(t, prefs.units)}</span>)}</div>)}
                    {m.steps.length > 0 && (<>
                      <button className="gt-ghost" onClick={() => setOpenSteps((o) => ({ ...o, [m.id]: !o[m.id] }))} aria-expanded={open}
                        style={{ alignSelf: "flex-start", marginLeft: -7, display: "flex", alignItems: "center", gap: 6 }}>
                        Worked {m.secs} s · {m.steps.length} steps
                        <I.Chevron style={{ transform: `rotate(${open ? 90 : 0}deg)`, transition: "transform .18s ease" }} />
                      </button>
                      {open && (
                        <div className="gt-in" style={{ display: "flex", flexDirection: "column", gap: 7, padding: "0 0 2px 0" }}>
                          {m.steps.map((s, k) => (
                            <div key={k} style={{ display: "flex", gap: 10, fontSize: 13, lineHeight: 1.4 }}>
                              <span style={{ color: "#191919", flex: "none" }}>{s.label}</span>
                              <span style={{ color: "#6b6b68", flex: 1, minWidth: 0 }}>{s.detail}</span>
                              <span style={{ color: "#6b6b68", fontVariantNumeric: "tabular-nums", flex: "none" }}>{s.ms}</span>
                            </div>))}
                        </div>)}
                    </>)}

                    {isPick && m.pick && (<>
                      <div data-testid="pick-grid" style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 8 }}>
                        {m.pick.map((t) => {
                          const s = !!picked[t.id], no = !!nots[t.id];
                          return (
                            <div key={t.id} className="gt-tile" style={{ border: s ? "2px solid #191919" : "1px solid #e7e6e3", opacity: no ? 0.35 : 1 }}>
                              <button className="gt-tilebtn" aria-pressed={s} data-sense={t.id}
                                onClick={() => { setPicked((p) => ({ ...p, [t.id]: !p[t.id] })); setNots((n) => ({ ...n, [t.id]: false })); }}>
                                <img className="gt-tileimg" src={t.img} alt="" loading="lazy" />
                                <span style={{ display: "block", padding: "6px 8px 8px", fontSize: 12, lineHeight: 1.3, color: "#191919" }}>{t.label}</span>
                              </button>
                              <button className="gt-x" aria-label="Not this"
                                onClick={() => { setNots((n) => ({ ...n, [t.id]: !n[t.id] })); setPicked((p) => ({ ...p, [t.id]: false })); }}><I.X /></button>
                            </div>);
                        })}
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <button className="gt-pill gt-dark" disabled={nPicked === 0 || busy} onClick={() => continuePick(m.query ?? "")}>Continue</button>
                        <span style={{ fontSize: 12, color: "#6b6b68" }}>{nPicked === 0 ? "Pick at least one" : nPicked === 1 ? "1 picked" : `${nPicked} picked`}</span>
                      </div>
                    </>)}

                    {m.card && (
                      <div className="gt-card" data-testid="result-card">
                        <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div data-testid="card-title" style={{ fontSize: 15, fontWeight: 500, lineHeight: 1.35 }}>{m.card.title}</div>
                            <div style={{ fontSize: 13, color: "#6b6b68", marginTop: 3, lineHeight: 1.4 }}>{units(m.card.sub, prefs.units)}</div>
                          </div>
                          <button className="gt-icon" aria-label="More options" aria-expanded={menu === m.id}
                            onClick={() => setMenu((x) => (x === m.id ? null : m.id))} style={{ margin: "-8px -8px 0 0" }}><I.More /></button>
                        </div>
                        {menu === m.id && (
                          <div className="gt-menu gt-in" role="menu">
                            <button className="gt-menuitem" role="menuitem" disabled={!sid} onClick={() => pinCard(m)}>Pin result</button>
                            <button className="gt-menuitem" role="menuitem" onClick={() => doExport(m.card!, "csv")}>Export CSV</button>
                            <button className="gt-menuitem" role="menuitem" onClick={() => doExport(m.card!, "geojson")}>Export GeoJSON</button>
                            <button className="gt-menuitem" role="menuitem" onClick={() => { setMenu(null);
                              setMsgs((ms) => ms.map((x) => x.id === m.id && x.role === "assistant" ? { ...x, showDetails: !x.showDetails } : x)); }}>
                              Details: how this was found</button>
                            <button className="gt-menuitem" role="menuitem" disabled={!m.card.features.length}
                              onClick={() => { setMenu(null); saveArea(bboxPoly(bboxOf(m.card!.features)), m.card!.title); }}>Save area of these results</button>
                          </div>)}
                        {m.card.features.length > 0 ? (
                          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 6, marginTop: 12 }}>
                            {m.card.features.slice(0, 4).map((f, k) => (
                              <button key={featId(f)} className="gt-thumb" aria-label="Open this result" title={units(whyOf(f), prefs.units)}
                                onClick={() => openResult(f, k + 1, { msgId: m.id })}>
                                <img src={cropUrl(bboxAround({ ...f, geometry: focusGeom(f) }, 46), 2022, 160)} alt="" loading="lazy" />
                              </button>))}
                          </div>
                        ) : <div className="gt-thumbskel">{[0, 1, 2, 3].map((k) => <span key={k} />)}</div>}
                        {m.learn && <div className="gt-learn" data-testid="learn">{m.learn}</div>}
                        {m.showDetails && (
                          <div className="gt-in" style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
                            {[...m.card.details, ...(m.limits ?? []).map(scrub)].map((d, k) => <p key={k} style={{ margin: 0, fontSize: 12, lineHeight: 1.45, color: "#6b6b68" }}>{d}</p>)}
                          </div>)}
                        <button className="gt-ghost" onClick={() => { setSel(null); if (m.card!.features.length) gm.current?.flyToBbox(bboxOf(m.card!.features), 56, 16.2); }}
                          style={{ margin: "10px 0 -4px -7px", color: "#191919" }}>View on map</button>
                      </div>)}

                    {m.nameable && !m.named && i === lastA && (
                      <form className="gt-nameit" onSubmit={(e) => { e.preventDefault(); nameConcept(m.id, (e.currentTarget.elements.namedItem("n") as HTMLInputElement).value); }}>
                        <span>Name this?</span><input name="n" placeholder="e.g. boat lifts" aria-label="Name this" data-testid="name-input" />
                        <button className="gt-pill" type="submit">Save name</button>
                      </form>)}
                    {m.named && <div className="gt-learn">Saved as “{m.named}”</div>}

                    {i === lastA && m.pills.length > 0 && !busy && (
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                        {m.pills.map((p) => <button key={p} className="gt-pill" onClick={() => void send(p)}>{p}</button>)}
                      </div>)}
                  </div>);
              })}
            </div>

            <div style={{ padding: "10px 16px 16px", display: validating ? "none" : undefined }}>
              <Composer ref={comp2} variant="panel" inputId="gt-ask-2" placeholder="Ask a follow-up, or investigate further…" value={landing ? "" : input}
                onChange={setInput} onSend={() => void send(input)} {...composerProps} />
              <div style={{ fontSize: 12, color: "#6b6b68", marginTop: 8, paddingLeft: 4 }}>NAIP {year} · Mecklenburg County</div>
            </div>
          </aside>

          <main className="gt-map" aria-label="Map" style={{ flex: 1, minWidth: 0, position: "relative", overflow: "hidden", background: "#6f8a55",
            opacity: landing ? 0 : 1, transition: "opacity .7s ease .15s" }}>
            <div ref={mapEl} style={{ position: "absolute", inset: 0 }} />

            <div className="gt-frost" style={{ position: "absolute", top: 16, left: 16, borderRadius: 999, display: "flex", alignItems: "center", padding: 3, gap: 2, zIndex: 3 }}>
              <button className="gt-ghost" onClick={() => setYear((y) => (y === 2022 ? 2020 : 2022))} aria-label={`Imagery year ${year}`}
                style={{ padding: "8px 12px", borderRadius: 999, color: "#191919", display: "flex", alignItems: "center", gap: 5 }}>{year}<I.Down /></button>
              <button className="gt-icon" onClick={() => setLayersOpen((o) => !o)} aria-label="Layers and options" aria-expanded={layersOpen}
                style={{ width: 36, height: 36, borderRadius: 999 }}><I.Layers /></button>
              <button className={`gt-icon${pointOn ? " gt-icon-on" : ""}`} onClick={() => togglePoint(!pointOn)} aria-label="Find like this (F)" aria-pressed={pointOn}
                title="Find like this (F)" data-testid="find-like-this" style={{ width: 36, height: 36, borderRadius: 999 }}><Crosshair size={16} strokeWidth={1.5} /></button>
            </div>
            {layersOpen && (
              <div className="gt-frost gt-in" style={{ position: "absolute", top: 62, left: 16, borderRadius: 14, padding: 6, width: 220, zIndex: 3 }}>
                {([["flood", "Flood zones"], ["hwy", "Highways"], ["bldg3d", "3D buildings"]] as const).map(([k, label]) => (
                  <label key={k} className="gt-check" style={{ alignItems: "flex-start" }}><input type="checkbox" checked={lyr[k]} style={{ marginTop: 2 }}
                    onChange={() => { const on = !lyr[k]; setLyr((o) => ({ ...o, [k]: on })); void gm.current?.setLayer(k, on); }} />
                    <span style={{ display: "flex", flexDirection: "column" }}>{label}
                      <span className="gt-srcline" data-testid="layer-source">{srcLine(srcs, k === "flood" ? "fema" : k === "hwy" ? "openstreetmap" : "overture")}</span></span></label>))}
                <div className="gt-srcline" style={{ padding: "6px 10px 4px" }} data-testid="imagery-source">Imagery: {srcLine(srcs, year === 2020 ? "naip 2020" : "naip 2022")}</div>
              </div>)}

            <div className="gt-frost" role="status" style={{ position: "absolute", top: 16, left: "50%", transform: "translateX(-50%)", borderRadius: 999,
              padding: "9px 16px", fontSize: 13, color: "#191919", display: "flex", alignItems: "center", gap: 8, zIndex: 3,
              opacity: (busy && !landing) || hint ? 1 : 0, transition: "opacity .3s ease", pointerEvents: "none", maxWidth: "70%" }}>
              {busy && !landing ? <><span className="gt-dots" aria-hidden="true"><span /><span /><span /></span>Working</> : hint}
            </div>

            <div className="gt-frost" style={{ position: "absolute", left: 16, bottom: 16, borderRadius: 999, padding: "8px 14px", display: "flex", gap: 14,
              fontSize: 12, color: "#191919", zIndex: 3, opacity: legend.on || lyr.flood ? 1 : 0, transition: "opacity .4s ease" }}>
              {legend.on && <span style={{ display: "flex", alignItems: "center", gap: 7 }}><span style={{ width: 9, height: 9, borderRadius: "50%", background: "var(--accent)", display: "inline-block" }} />{legend.label}</span>}
              {legend.docks && <span style={{ display: "flex", alignItems: "center", gap: 7 }}><span style={{ width: 14, height: 4, borderRadius: 2, background: "var(--dock)", display: "inline-block" }} />Docks</span>}
              {lyr.flood && <span style={{ display: "flex", alignItems: "center", gap: 7 }}><span style={{ width: 12, height: 9, borderRadius: 2, background: "rgba(47,111,237,.35)", border: "1px solid #2F6FED", display: "inline-block" }} />Flood zone</span>}
            </div>

            {tip && <div className="gt-frost gt-tip" style={{ left: tip.x + 14, top: tip.y + 14 }}>{tip.text}</div>}

            <div style={{ position: "absolute", right: 72, bottom: 16, zIndex: 3, display: "flex", alignItems: "flex-end", gap: 8, flexDirection: "row-reverse" }}>
              <button className="gt-frost gt-attr" onClick={() => setAttrOpen((o) => !o)} aria-label="Map data sources" aria-expanded={attrOpen}>i</button>
              {attrOpen && <div className="gt-frost gt-in gt-attrpop" data-testid="sources-panel">
                <div style={{ fontWeight: 500, color: "#191919", marginBottom: 4 }}>Data sources</div>
                {srcs.length ? srcs.filter((x) => !String(x.provider ?? "").match(/foursquare|yelp|google/i) || x.freshness_status).slice(0, 14).map((x) => (
                  <div key={x.source_id ?? x.dataset_name}>{x.dataset_name}{x.acquisition_date ? ` · acquired ${acq(x.acquisition_date)}` : ""}{x.freshness_status ? ` · ${x.freshness_status}` : ""}{x.license ? ` · ${x.license}` : ""}</div>))
                  : <div>NAIP (USDA) · © OpenStreetMap contributors · FEMA NFHL · Overture Maps · USGS 3DEP · MapLibre</div>}
              </div>}
            </div>
            <div className="gt-frost gt-zoom" style={{ position: "absolute", right: 16, bottom: 16, borderRadius: 14, display: "flex", flexDirection: "column", padding: 3, zIndex: 3 }}>
              <button className="gt-icon" onClick={() => gm.current?.zoom(1)} aria-label="Zoom in"><I.Plus14 /></button>
              <div style={{ height: 1, background: "#e7e6e3", margin: "0 8px" }} />
              <button className="gt-icon" onClick={() => gm.current?.zoom(-1)} aria-label="Zoom out"><I.Minus14 /></button>
            </div>

            {evOpen && !sel && (
              <div className="gt-sheet gt-frost gt-in gt-evpanel" data-testid="evidence-panel">
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
                  <div>
                    <div className="gt-evpanel-k">Evidence {evOpen.id} · {evOpen.kind.replace(/_/g, " ")}</div>
                    <div style={{ fontSize: 15, fontWeight: 500, lineHeight: 1.4, marginTop: 4 }}>{units(evOpen.statement, prefs.units)}</div>
                  </div>
                  <button className="gt-icon" onClick={() => setEvOpen(null)} aria-label="Close" style={{ margin: "-8px -8px 0 0" }}><I.Close /></button>
                </div>
                <div className="gt-evgrid">
                  <span>Strength</span><span><Bars s={evOpen.strength} /> {evOpen.strength === "insufficient" ? "Gap" : evOpen.strength}</span>
                  {evOpen.source?.name && <><span>Source</span><span>{evOpen.source.name}{evOpen.source.date ? ` · ${evOpen.source.date}` : ""}{evOpen.source.licence ? ` · ${evOpen.source.licence}` : ""}</span></>}
                  {evOpen.method && <><span>Method</span><span>{scrub(evOpen.method)}</span></>}
                  {Object.entries(evOpen.measurement ?? {}).filter(([, v]) => typeof v !== "object").map(([k, v]) => (
                    <><span key={`k${k}`}>{k.replace(/_/g, " ")}</span><span key={`v${k}`} style={{ fontVariantNumeric: "tabular-nums" }}>{String(v)}</span></>))}
                  {evOpen.confidence_note && <><span>Confidence</span><span>{evOpen.confidence_note}</span></>}
                </div>
                {evOpen.limitations.length > 0 && <div className="gt-evlims">{evOpen.limitations.map((l, k) => <p key={k}>{scrub(l)}</p>)}</div>}
                {evOpen.feature_ids.length > 0 && <button className="gt-pill" style={{ alignSelf: "flex-start" }} onClick={() => showFeatures(evOpen.feature_ids, evOpen.statement)}>
                  Show {evOpen.feature_ids.length} on the map</button>}
              </div>)}
            <div className="gt-sheet gt-frost" aria-hidden={!sel} data-testid="sheet" style={{ position: "absolute", top: 16, right: 16, bottom: 16, width: 360, borderRadius: 20,
              display: "flex", flexDirection: "column", overflowY: "auto", zIndex: 4, padding: 16, gap: 14, opacity: sel ? 1 : 0,
              transform: `translateX(${sel ? 0 : 24}px)`, pointerEvents: sel ? "auto" : "none",
              transition: "opacity .3s ease, transform .45s cubic-bezier(.2,.8,.2,1)" }}>
              {sheet && selBox && sel && (<>
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
                  <div>
                    <div style={{ fontSize: 15, fontWeight: 500 }}>{sheet.title}</div>
                    <div style={{ fontSize: 13, color: "#6b6b68", marginTop: 2 }}>{sheet.sub}</div>
                  </div>
                  <button className="gt-icon" onClick={() => setSel(null)} aria-label="Close" style={{ margin: "-8px -8px 0 0" }}><I.Close /></button>
                </div>
                <div data-testid="swipe" style={{ position: "relative", width: 328, maxWidth: "100%", height: 200, borderRadius: 12, overflow: "hidden", flex: "none", background: "#7b925e" }}>
                  <img src={cropUrl(selBox, 2022, 656)} alt="2022" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }} />
                  <img src={cropUrl(selBox, 2020, 656)} alt="2020" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover",
                    clipPath: `inset(0 ${100 - swipe}% 0 0)` }} />
                  <div style={{ position: "absolute", top: 0, bottom: 0, left: `${swipe}%`, width: 2, marginLeft: -1, background: "#ffffff", boxShadow: "0 0 6px rgba(0,0,0,.35)" }} />
                  <span className="gt-yearbadge" style={{ left: 10 }}>2020</span>
                  <span className="gt-yearbadge" style={{ right: 10 }}>2022</span>
                </div>
                <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: "#6b6b68" }}>
                  Compare 2020 and 2022
                  <input className="gt-range" type="range" min={0} max={100} value={swipe} onChange={(e) => setSwipe(Number(e.target.value))} />
                </label>
                {selEv?.verdict === "confirm"
                  ? <span className="gt-provbadge gt-provbadge-human" data-testid="prov-badge">Human-verified</span>
                  : <span className="gt-provbadge" data-testid="prov-badge">{sel.properties?.provenance === "provider_reported" ? "Provider data · not verified here" : "AI prediction · not human-verified"}</span>}
                {sel.properties?.why && typeof sel.properties.why === "string" && <div className="gt-why" data-testid="why">{units(String(sel.properties.why), prefs.units)}</div>}
                {selEv && (
                  <div className="gt-selev" data-testid="feature-evidence">
                    <div className="gt-evpanel-k">Evidence for this result</div>
                    {selEv.evidence.filter((e: any) => e.feature_ids.length).slice(0, 6).map((e: any) => (
                      <button key={e.id} className="gt-evrow" onClick={() => { setSel(null); setEvOpen(e); }}>
                        <span className="gt-evid">{e.id}</span><span className="gt-evtext">{units(e.statement, prefs.units)}<span className="gt-evsrc">{e.source?.name}</span></span></button>))}
                    {selEv.missing.length > 0 && <div className="gt-evlims">{selEv.missing.map((x: string, k: number) => <p key={k}>Missing: {x}</p>)}</div>}
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <button className="gt-pill" onClick={() => void verdict(selEv.inv, featId(sel), "confirm")}>Confirm</button>
                      <button className="gt-pill" onClick={() => void verdict(selEv.inv, featId(sel), "reject")}>Reject</button>
                      <span style={{ fontSize: 12, color: "#6b6b68" }}>{selEv.verdict ? `Recorded as ${selEv.verdict === "confirm" ? "confirmed" : "rejected"}` : "Your review is stored as evidence"}</span>
                    </div>
                  </div>)}
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {sheet.facts.map((f, k) => <p key={k} style={{ margin: 0, fontSize: 14, lineHeight: 1.5, color: "#191919" }}>{f}</p>)}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: "auto", flexWrap: "wrap" }}>
                  <button className="gt-pill" style={{ color: "#191919" }} onClick={() => void refine(sel, "yes")} disabled={busy}>{fromPoint ? "More like this" : "Looks right"}</button>
                  <button className="gt-pill" onClick={() => void refine(sel, "no")} disabled={busy}>Not this</button>
                  <span style={{ fontSize: 12, color: "#6b6b68" }}>{fb[featId(sel)] === "yes" ? "Thanks. I’ll find more like this." : fb[featId(sel)] === "no" ? "Removed. I’ll show fewer like this." : ""}</span>
                </div>
              </>)}
            </div>

            {nameDlg && (
              <form className="gt-frost gt-in gt-dialog" role="dialog" aria-label={nameDlg.title}
                onSubmit={(e) => { e.preventDefault(); nameDlg.onSave((e.currentTarget.elements.namedItem("n") as HTMLInputElement).value.trim()); setNameDlg(null); }}>
                <div style={{ fontSize: 15, fontWeight: 500 }}>{nameDlg.title}</div>
                <input name="n" autoFocus defaultValue={nameDlg.placeholder} className="gt-input" aria-label="Name" />
                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                  <button type="button" className="gt-pill" onClick={() => setNameDlg(null)}>Cancel</button>
                  <button type="submit" className="gt-pill gt-dark">Save</button>
                </div>
              </form>)}
          </main>
        </div>

        <div data-testid="landing" className="gt-landing" style={{ opacity: landing ? 1 : 0, visibility: landing ? "visible" : "hidden" }}>
          <div aria-hidden="true" style={{ position: "absolute", inset: 0, overflow: "hidden", background: "#7b925e" }}>
            <div style={{ position: "absolute", inset: "-20px", backgroundImage: `url(${LANDING_BG})`, backgroundSize: "cover", backgroundPosition: "center", filter: "blur(9px)" }} />
            <div style={{ position: "absolute", inset: 0, background: "rgba(244,244,242,.55)" }} />
          </div>
          <div style={{ position: "relative", height: "100%", display: "flex", flexDirection: "column" }}>
            {narrow && <button className="gt-icon gt-icon-sm" style={{ margin: "14px 0 0 14px" }} aria-label="Open sidebar" onClick={() => setDrawer(true)}><Menu size={16} strokeWidth={1.5} /></button>}
            <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "0 16px 8vh", gap: 22 }}>
              <h1 style={{ margin: 0, fontSize: 30, fontWeight: 400, letterSpacing: "-0.02em", lineHeight: 1.2, textAlign: "center" }}>What should Groundtruth investigate?</h1>
              <div style={{ width: "100%", maxWidth: 640 }}>
                <Composer ref={comp1} variant="landing" inputId="gt-ask-1" placeholder="Ask Groundtruth to investigate an area…" value={landing ? input : ""}
                  autoFocus onChange={setInput} onSend={() => void send(input)} {...composerProps} />
              </div>
              <div className="gt-starters">
                {STARTERS.map((s) => (
                  <button key={s.title} className="gt-starter" data-testid="starter" onClick={() => {
                    if ("point" in s.run) {
                      setStage("work"); void gm.current?.startImagery(); gm.current?.flyToBbox([-80.886, 35.492, -80.866, 35.506], 20, 16.5);
                      if (title === "New search") setTitle("Find like this");
                      setMsgs([{ id: nextId("a"), role: "assistant", text: "Click any object on the map and I’ll find everything like it around here. Shift-click adds examples; Alt-drag draws a box around one object.", steps: [], secs: "0.0", card: null, pills: [] }]);
                      chooseArea({ label: "Lake Norman", place: "Lake Norman", bbox: LN_BBOX });
                      setTimeout(() => togglePoint(true), 50);
                    } else void send(s.run.text);
                  }}>
                    <img src={s.img} alt="" />
                    <span className="gt-starter-t">{s.title}</span>
                    <span className="gt-starter-s">{s.sub}</span>
                  </button>))}
              </div>
            </div>
          </div>
        </div>
      </div>
      {toast && <div className="gt-toast gt-in" role="status">{toast}</div>}
    </div>
  );
}

const pickedList = (o: Record<string, boolean>) => Object.keys(o).filter((k) => o[k]);

/** What a session stores for a message: everything except the result features (re-hydrated on open). */
function slimMsg(m: Msg): Msg {
  if (m.role !== "assistant" || !m.card) return m;
  return { ...m, card: { ...m.card, features: [], ids: m.card.features.slice(0, 2000).map(featId) } as any };
}

function replaceAssistant(ms: Msg[], id: string, msg: AsstMsg): Msg[] {
  return ms.filter((x) => x.role !== "working").map((x) => (x.id === id && x.role === "assistant"
    ? { ...x, card: msg.card, steps: msg.steps, secs: msg.secs, learn: msg.learn, req: msg.req, insights: msg.insights ?? x.insights,
      pointIds: msg.pointIds ?? x.pointIds } : x));
}

function fallbackText(r: AgentResponse): string {
  if (r.status === "declined") return "I can't help with that one. Ask about things visible from above.";
  return currentEnv(r)?.result ? "Here's what I found." : "I couldn't find that here. Try a nearby area?";
}

function legendLabel(plan: any): string {
  const p = plan ?? {};
  if (p.target_kind === "derived" || p.concept === "swimming_pool") return "Pools";
  return String(p.concept_query ?? p.target ?? "Results").replace(/_/g, " ").replace(/^\w/, (c: string) => c.toUpperCase());
}

/** The result with the most other results within ~700 m (ties → the first): where the camera goes. */
function densest(fs: Feature[]): [number, number] {
  const pts = fs.map((f) => centroid(focusGeom(f)));
  let best = pts[0], bn = -1;
  for (const p of pts) {
    const k = Math.cos((p[1] * Math.PI) / 180);
    let n = 0;
    for (const q of pts) if (((q[0] - p[0]) * k) ** 2 + (q[1] - p[1]) ** 2 < 0.0063 ** 2) n++;
    if (n > bn) { bn = n; best = p; }
  }
  return best;
}

function bboxOf(fs: Feature[]): number[] {
  let w = 180, s = 90, e = -180, n = -90;
  for (const f of fs) {
    const [x, y] = centroid(focusGeom(f));
    w = Math.min(w, x); s = Math.min(s, y); e = Math.max(e, x); n = Math.max(n, y);
  }
  const pad = 0.0015;
  return [w - pad, s - pad, e + pad, n + pad];
}

const bboxPoly = (b: number[]) => ({ type: "Polygon", coordinates: [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]] });

/** The detail sheet's crop: 328 × 200 aspect (≈ 82 × 50 m) around the result. */
function sheetBbox(f: Feature): number[] {
  const [x, y] = centroid(focusGeom(f));
  const wM = 82, hM = 50;
  const dLon = wM / 2 / (111320 * Math.cos((y * Math.PI) / 180)), dLat = hM / 2 / 111320;
  return [x - dLon, y - dLat, x + dLon, y + dLat];
}

/** One plain line about a result: the engine's WHY line when present, else its basic facts. */
function whyOf(f: Feature): string {
  const p = f.properties ?? {};
  if (p.why) return String(p.why);
  if (p.kind === "house_with_pool") return `House with a pool of ${Math.round(Number(p.pool_area_m2 ?? 0))} m²${p.lakefront ? ` · ${Math.round(Number(p.dist_shore_m ?? 0))} m from the shore` : ""}`;
  const a = p.area_m2 ?? p.footprint_m2;
  return a ? `About ${Math.round(Number(a))} m²` : "Open to see it in 2020 and 2022";
}

function sheetFor(f: Feature, n: number, u: "m" | "ft"): { title: string; sub: string; facts: string[] } {
  const p = f.properties ?? {};
  const facts: string[] = [];
  if (p.kind === "house_with_pool") {
    facts.push(`A pool of about ${fmt(Math.round(Number(p.pool_area_m2 ?? 0)))} m² next to the house.`);
    if (p.lakefront) facts.push(`About ${fmt(Math.round(Number(p.dist_shore_m ?? 0)))} m from the Lake Norman shoreline.`);
    else facts.push("Not on the water.");
    const chg: any[] = p.dock_new_2020 ?? [];
    if (p.has_dock) facts.push(chg.some((c) => c?.new) ? "Its own dock, new since 2020." : "Its own dock, already there in 2020.");
    else if (p.lakefront) facts.push("No dock.");
    return { title: p.lakefront ? "Lakefront house" : "House with a pool", sub: `Result ${n} · NAIP 2022`, facts: facts.map((x) => units(x, u)) };
  }
  const area = p.area_m2 ?? p.footprint_m2;
  if (area) facts.push(`About ${fmt(Math.round(Number(area)))} m².`);
  if (p.height_m) facts.push(`About ${Number(p.height_m).toFixed(1)} m tall.`);
  if (p.change === "new") facts.push("New since 2020.");
  return { title: p.kind === "dock" ? "Dock" : "Result", sub: `Result ${n} · NAIP 2022`, facts: facts.map((x) => units(x, u)) };
}


// ---------- investigations (helpers) ----------
function ownerId(): string { return ownerFn(); }

async function fetchInv(id: string, features: boolean): Promise<Inv | null> {
  try { return await fetch(`/api/v1/investigations/${id}${features ? "?features=1" : ""}`).then((r) => (r.ok ? r.json() : null)); }
  catch { return null; }
}

function lastInv(ms: Msg[]): Inv | null {
  for (let i = ms.length - 1; i >= 0; i--) { const m = ms[i]; if (m.role === "assistant" && m.inv) return m.inv; }
  return null;
}

function lastInvWith(ms: Msg[], fid: string): Inv | null {
  for (let i = ms.length - 1; i >= 0; i--) {
    const m = ms[i];
    if (m.role === "assistant" && m.inv?.result?.features?.some((f) => featId(f) === fid)) return m.inv;
  }
  return null;
}

/** The result card for an investigation: the final set, counted (the planner's exhaustive area scan). */
function invCard(inv: Inv): Card | null {
  const fs = (inv.result?.features ?? []).filter((f) => !["dock", "dock_link"].includes(f.properties?.kind));
  if (!fs.length) return null;
  const what = inv.plan?.targets?.[0] ?? "results";
  const cond = (inv.plan?.constraints ?? []).slice(-1)[0];
  return { title: `${fmt(fs.length)} ${what}${cond ? ` ${cond}` : ""}`, sub: `${inv.plan?.scope?.label ?? "Previous results"} · NAIP 2022`, count: fs.length,
    features: fs, ref: inv.id, kind: "investigation",
    details: inv.evidence.filter((e) => e.method).map((e) => `${e.id}: ${scrub(e.method)}`) };
}

function invTitle(objective: string, inv: Inv): string {
  const place = inv.plan?.scope?.label;
  const what = inv.plan?.targets?.[0];
  const cond = inv.plan?.constraints?.[0];
  if (place && what) return `${place} · ${what}${cond && /flood/.test(cond) ? " · flood" : ""}`;
  return objective.length > 60 ? `${objective.slice(0, 57)}…` : objective;
}


/** "FEMA NFHL · effective 2024-… · current" from the source registry (empty when the registry has no match). */
function srcLine(srcs: any[], key: string): string {
  const k = key.toLowerCase();
  const x = srcs.find((r) => `${r.source_id ?? ""} ${r.dataset_name ?? ""} ${r.provider ?? ""}`.toLowerCase().includes(k));
  if (!x) return "";
  return [shortProvider(x), x.acquisition_date ? `acquired ${acq(x.acquisition_date)}` : null, x.freshness_status].filter(Boolean).join(" · ");
}

/** An acquisition date that may be a string or {start, end, flights}: "2022-06-02 – 2022-09-27". */
function acq(d: any): string {
  if (!d) return "";
  if (typeof d === "string") return d;
  if (d.start && d.end) return d.start === d.end ? d.start : `${d.start} – ${d.end}`;
  return String(d.date ?? d.start ?? d.end ?? "");
}

function shortProvider(x: any): string {
  const n = String(x.dataset_name ?? x.provider ?? "");
  return n.length > 48 ? `${n.slice(0, 46)}…` : n;
}
