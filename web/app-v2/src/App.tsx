// Groundtruth v2: landing → workspace (chat panel + one map). Structure, copy and motion follow
// docs/design/target/Main.dc.html; every number and image is real (planner envelopes, local NAIP).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgentResponse, Context, cropUrl, exportSet, Feature, agentRun } from "./api";
import { GtMap, focusGeom, COUNTY } from "./map";
import { Card, StepLine, bboxAround, cardFor, centroid, currentEnv, featId, fmt, pillsFor, sayText, stepLines } from "./turns";
import * as I from "./icons";

type Tile = { id: string; label: string; img: string };
type Msg =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "working" }
  | { id: string; role: "assistant"; text: string; steps: StepLine[]; secs: string; card: Card | null; pills: string[];
      pick?: Tile[]; query?: string; showDetails?: boolean };

const LANDING_BG = cropUrl([-80.8830, 35.4955, -80.8710, 35.5030], 2022, 640);  // Lake Norman shore, real NAIP
let uid = 0;
const nextId = (p: string) => `${p}${++uid}`;

export default function App() {
  const [stage, setStage] = useState<"landing" | "work">("landing");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [ctx, setCtx] = useState<Context>({});
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [nots, setNots] = useState<Record<string, boolean>>({});
  const [pickDone, setPickDone] = useState(false);
  const [openSteps, setOpenSteps] = useState<Record<string, boolean>>({});
  const [menu, setMenu] = useState<string | null>(null);
  const [sel, setSel] = useState<Feature | null>(null);
  const [selN, setSelN] = useState(0);
  const [selFrom, setSelFrom] = useState<{ msgId: string; query: string } | null>(null);
  const [swipe, setSwipe] = useState(55);
  const [fb, setFb] = useState<Record<string, "yes" | "no">>({});
  const [year, setYear] = useState(2022);
  const [layersOpen, setLayersOpen] = useState(false);
  const [lyr, setLyr] = useState({ flood: false, hwy: false, bldg3d: false });
  const [legend, setLegend] = useState<{ on: boolean; docks: boolean; label: string }>({ on: false, docks: false, label: "Pools" });
  const mapEl = useRef<HTMLDivElement>(null);
  const msgsEl = useRef<HTMLDivElement>(null);
  const gm = useRef<GtMap | null>(null);
  const shown = useRef<{ ids: Set<string>; features: Feature[]; docks: Feature[]; links: Feature[] } | null>(null);
  const lastResp = useRef<AgentResponse | null>(null);
  const t0 = useRef(0);

  useEffect(() => {
    if (!mapEl.current || gm.current) return;
    gm.current = new GtMap(mapEl.current);
    (window as any).__gt = { ready: true };
  }, []);
  useEffect(() => { if (msgsEl.current) msgsEl.current.scrollTop = msgsEl.current.scrollHeight; }, [msgs]);
  useEffect(() => { gm.current?.setYear(year); }, [year]);
  useEffect(() => { gm.current?.select(sel ? featId(sel) : null); }, [sel]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setSel(null); setMenu(null); setLayersOpen(false); } };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => { const t = setTimeout(() => gm.current?.resize(), 650); return () => clearTimeout(t); }, [stage]);

  // ---------------- map transformation for one reply ----------------
  const applyToMap = useCallback((r: AgentResponse) => {
    const m = gm.current;
    if (!m) return;
    const env = currentEnv(r);
    const all = env?.result?.geojson.features ?? [];
    const feats = all.filter((f) => !["dock", "dock_link"].includes(f.properties?.kind) && f.geometry);
    const docks = all.filter((f) => f.properties?.kind === "dock");
    const links = all.filter((f) => f.properties?.kind === "dock_link");
    const heat = r.actions.some((a) => a.action === "heatmap");
    if (r.status === "declined" || r.ask === "pick") return;
    if (heat) {
      shown.current = null;
      m.setResults({ features: feats, docks: [], links: [], look: "glow" });
      m.flyToBbox(COUNTY, 24, 11);
      setLegend({ on: true, docks: false, label: legendLabel(r) });
      return;
    }
    if (!feats.length) return; // nothing new to draw (e.g. no docks added since 2020): keep the last picture
    const ids = new Set(feats.map(featId));
    const prev = shown.current;
    const subset = prev && feats.every((f) => prev.ids.has(featId(f))) && ids.size < prev.ids.size;
    if (subset && prev) {
      // a filter: keep the previous set on screen, fade the non-matches to .16 and pulse the matches twice
      m.setResults({ features: prev.features, docks, links, look: "rings", matches: ids, pulse: true });
      shown.current = { ...prev, docks, links };
      m.flyToCenter(densest(feats), 14.6);
    } else {
      m.setResults({ features: feats, docks, links, look: "rings", pulse: false });
      shown.current = { ids, features: feats, docks, links };
      if (feats.length > 12) m.flyToCenter(densest(feats), 14.3); else m.flyToBbox(bboxOf(feats), 80, 16.2);
    }
    setLegend({ on: true, docks: docks.length > 0, label: legendLabel(r) });
  }, []);

  // ---------------- one turn ----------------
  const run = useCallback(async (text: string, c: Context, replaceId: string | null = null) => {
    t0.current = performance.now();
    try {
      const r = await agentRun(text, c);
      lastResp.current = r;
      const { lines, secs } = stepLines(r, performance.now() - t0.current);
      let pick: Tile[] | undefined;
      if (r.ask === "pick") {
        const pc = r.steps.find((s) => s.tool === "pick_candidates")?.envelope;
        pick = (pc?.result?.geojson.features ?? []).filter((f) => !f.properties.no_clear_example).map((f) => ({
          id: f.properties.sense_id, label: f.properties.sense_label, img: f.properties.thumb_url }));
      }
      const msg: Msg = { id: nextId("a"), role: "assistant", text: sayText(r), steps: lines, secs, card: cardFor(r),
        pills: pillsFor(r), pick, query: r.plan?.effective_text ?? text };
      (window as any).__gtLast = { ms: Math.round(performance.now() - t0.current), status: r.status, count: msg.card?.count ?? null,
        brain: r.brain, ask: r.ask ?? null };
      setMsgs((ms) => replaceId ? replaceAssistant(ms, replaceId, msg) : ms.filter((x) => x.role !== "working").concat([msg]));
      if (!replaceId) setCtx((o) => ({ ...o, place: r.plan?.place ?? o.place ?? null, last_text: r.plan?.effective_text ?? text }));
      applyToMap(r);
    } catch {
      setMsgs((ms) => ms.filter((x) => x.role !== "working").concat([{ id: nextId("a"), role: "assistant",
        text: "Something went wrong on my side. Try again in a moment.", steps: [], secs: "0.0", card: null, pills: [] }]));
    } finally {
      setBusy(false);
    }
  }, [applyToMap]);

  const send = useCallback((raw: string) => {
    const text = raw.trim();
    if (!text || busy) return;
    if (stage === "landing") setStage("work");
    setInput(""); setBusy(true); setSel(null); setMenu(null); setLayersOpen(false);
    setMsgs((ms) => ms.concat([{ id: nextId("u"), role: "user", text }, { id: "working", role: "working" }]));
    const c: Context = { ...ctx, picked: pickedList(picked), not: pickedList(nots) };
    if (stage === "landing") { c.place = null; c.last_text = null; }
    void run(text, c);
    setTimeout(() => void gm.current?.startImagery(), 150);  // after the chat request has a connection
  }, [busy, stage, ctx, picked, nots, run]);

  const continuePick = useCallback((query: string) => {
    setPickDone(true); setBusy(true);
    setMsgs((ms) => ms.concat([{ id: "working", role: "working" }]));
    void run(query, { place: null, last_text: null, picked: pickedList(picked), not: pickedList(nots) });
  }, [picked, nots, run]);

  const reset = () => {
    setStage("landing"); setMsgs([]); setInput(""); setBusy(false); setCtx({}); setPicked({}); setNots({}); setPickDone(false);
    setOpenSteps({}); setMenu(null); setSel(null); setFb({}); setYear(2022); setLayersOpen(false);
    setLegend({ on: false, docks: false, label: "Pools" });
    shown.current = null; gm.current?.setResults(null); gm.current?.flyToBbox(COUNTY, 20, 11);
  };

  const openResult = useCallback((f: Feature, n: number, from: { msgId: string; query: string } | null = null) => {
    setSel(f); setSelN(n); setSwipe(55); setMenu(null);
    if (from) setSelFrom(from);
    gm.current?.flyToFeature(f);
  }, []);
  useEffect(() => {
    if (!gm.current) return;
    gm.current.onOpen = (id) => {
      const f = shown.current?.features.find((x) => featId(x) === id);
      if (f) openResult(f, (shown.current?.features.indexOf(f) ?? 0) + 1);
    };
  }, [openResult]);

  // "Not this" feeds the refine: the house leaves the set and the count updates (re-run with the rejection)
  const feedback = useCallback((v: "yes" | "no") => {
    if (!sel) return;
    const id = featId(sel);
    setFb((o) => ({ ...o, [id]: v }));
    if (v === "no") {
      const c: Context = { ...ctx, rejected: [...(ctx.rejected ?? []), id], accepted: ctx.accepted,
        last_text: null, picked: pickedList(picked), not: pickedList(nots) };
      setCtx((o) => ({ ...o, rejected: c.rejected }));
      if (shown.current) shown.current = { ...shown.current, ids: new Set([...shown.current.ids].filter((x) => x !== id)),
        features: shown.current.features.filter((x) => featId(x) !== id) };
      // re-run the request whose card this result came from, and update that card in place
      if (selFrom?.query) { setBusy(true); void run(selFrom.query, c, selFrom.msgId); }
    } else {
      setCtx((o) => ({ ...o, accepted: [...(o.accepted ?? []), id] }));
    }
  }, [sel, selFrom, ctx, picked, nots, run]);

  const doExport = useCallback(async (card: Card, format: "csv" | "geojson") => {
    setMenu(null);
    const url = await exportSet({ type: "FeatureCollection", features: card.features }, format);
    if (!url) return;
    const a = document.createElement("a");
    a.href = url; a.download = url.split("/").pop() ?? `groundtruth.${format}`;
    document.body.appendChild(a); a.click(); a.remove();
    (window as any).__gtExport = { url, rows: card.features.length, count: card.count };
  }, []);

  const lastA = useMemo(() => { let k = -1; msgs.forEach((m, i) => { if (m.role === "assistant") k = i; }); return k; }, [msgs]);
  const nPicked = Object.values(picked).filter(Boolean).length;
  const landing = stage === "landing";

  const sheet = sel ? sheetFor(sel, selN) : null;
  const selBox = sel ? sheetBbox(sel) : null;

  return (
    <div style={{ position: "relative", height: "100vh", minHeight: 560, width: "100%", overflow: "hidden", background: "#f4f4f2" }}>
      <div className="gt-ws" style={{ position: "absolute", inset: 0, display: "flex" }}>
        <aside className="gt-panel" aria-label="Conversation" style={{ width: 400, flex: "none", height: "100%", background: "#ffffff",
          borderRight: "1px solid #e7e6e3", display: "flex", flexDirection: "column", zIndex: 2, opacity: landing ? 0 : 1,
          transform: `translateX(${landing ? -24 : 0}px)`,
          transition: "opacity .5s ease .05s, transform .6s cubic-bezier(.2,.8,.2,1) .05s" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 14px 8px 22px" }}>
            <button className="gt-ghost" onClick={reset} style={{ fontSize: 15, fontWeight: 500, color: "#191919", marginLeft: -7 }}>Groundtruth</button>
            <button className="gt-icon" onClick={reset} aria-label="New search"><I.Plus /></button>
          </div>

          <div ref={msgsEl} data-testid="msgs" style={{ flex: 1, overflowY: "auto", padding: "8px 22px 24px", display: "flex", flexDirection: "column", gap: 16 }}>
            {msgs.map((m, i) => {
              if (m.role === "user") return (
                <div key={m.id} className="gt-in" style={{ display: "flex", justifyContent: "flex-end" }}>
                  <div data-testid="user-msg" style={{ maxWidth: "82%", background: "#ecebe8", borderRadius: 16, padding: "9px 14px", fontSize: 14, lineHeight: 1.45 }}>{m.text}</div>
                </div>);
              if (m.role === "working") return (
                <div key={m.id} className="gt-dots gt-in" role="status" aria-label="Working" style={{ padding: "6px 0" }}><span /><span /><span /></div>);
              const open = !!openSteps[m.id];
              const isPick = !!m.pick && !pickDone;
              return (
                <div key={m.id} className="gt-in" data-testid="assistant-msg" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  <p data-testid="reply" style={{ margin: 0, fontSize: 15, lineHeight: 1.5, color: "#191919" }}>{m.text}</p>
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
                          <div style={{ fontSize: 13, color: "#6b6b68", marginTop: 3, lineHeight: 1.4 }}>{m.card.sub}</div>
                        </div>
                        <button className="gt-icon" aria-label="More options" aria-expanded={menu === m.id}
                          onClick={() => setMenu((x) => (x === m.id ? null : m.id))} style={{ margin: "-8px -8px 0 0" }}><I.More /></button>
                      </div>
                      {menu === m.id && (
                        <div className="gt-menu gt-in" role="menu">
                          <button className="gt-menuitem" role="menuitem" onClick={() => doExport(m.card!, "csv")}>Export CSV</button>
                          <button className="gt-menuitem" role="menuitem" onClick={() => doExport(m.card!, "geojson")}>Export GeoJSON</button>
                          <button className="gt-menuitem" role="menuitem" onClick={() => { setMenu(null);
                            setMsgs((ms) => ms.map((x) => x.id === m.id && x.role === "assistant" ? { ...x, showDetails: !x.showDetails } : x)); }}>
                            Details: how this was found</button>
                        </div>)}
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 6, marginTop: 12 }}>
                        {m.card.features.slice(0, 4).map((f, k) => (
                          <button key={featId(f)} className="gt-thumb" aria-label="Open this result" onClick={() => openResult(f, k + 1, { msgId: m.id, query: m.query ?? "" })}>
                            <img src={cropUrl(bboxAround({ ...f, geometry: focusGeom(f) }, 46), 2022, 160)} alt="" loading="lazy" />
                          </button>))}
                      </div>
                      {m.showDetails && (
                        <div className="gt-in" style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
                          {m.card.details.map((d, k) => <p key={k} style={{ margin: 0, fontSize: 12, lineHeight: 1.45, color: "#6b6b68" }}>{d}</p>)}
                        </div>)}
                      <button className="gt-ghost" onClick={() => { setSel(null); if (shown.current) gm.current?.flyToBbox(bboxOf(m.card!.features), 56, 16.2); }}
                        style={{ margin: "10px 0 -4px -7px", color: "#191919" }}>View on map</button>
                    </div>)}

                  {i === lastA && m.pills.length > 0 && !busy && (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {m.pills.map((p) => <button key={p} className="gt-pill" onClick={() => send(p)}>{p}</button>)}
                    </div>)}
                </div>);
            })}
          </div>

          <div style={{ padding: "10px 16px 16px" }}>
            <div style={{ background: "#ffffff", border: "1px solid #e7e6e3", borderRadius: 16, boxShadow: "var(--shadow-composer)",
              padding: "12px 10px 10px 14px", display: "flex", alignItems: "flex-end", gap: 10 }}>
              <label className="gt-sr" htmlFor="gt-ask-2">Ask a follow-up</label>
              <textarea id="gt-ask-2" className="gt-ta" rows={1} placeholder="Ask a follow-up…" value={landing ? "" : input}
                onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
                style={{ minHeight: 24, maxHeight: 120 }} />
              <button className="gt-send" onClick={() => send(input)} disabled={busy || !input.trim()} aria-label="Send"><I.Up /></button>
            </div>
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
          </div>
          {layersOpen && (
            <div className="gt-frost gt-in" style={{ position: "absolute", top: 62, left: 16, borderRadius: 14, padding: 6, width: 220, zIndex: 3 }}>
              {([["flood", "Flood zones"], ["hwy", "Highways"], ["bldg3d", "3D buildings"]] as const).map(([k, label]) => (
                <label key={k} className="gt-check"><input type="checkbox" checked={lyr[k]}
                  onChange={() => { const on = !lyr[k]; setLyr((o) => ({ ...o, [k]: on })); void gm.current?.setLayer(k, on); }} />{label}</label>))}
            </div>)}

          <div className="gt-frost" role="status" style={{ position: "absolute", top: 16, left: "50%", transform: "translateX(-50%)", borderRadius: 999,
            padding: "9px 16px", fontSize: 13, color: "#191919", display: "flex", alignItems: "center", gap: 8, zIndex: 3,
            opacity: busy && !landing ? 1 : 0, transition: "opacity .3s ease", pointerEvents: "none" }}>
            <span className="gt-dots" aria-hidden="true"><span /><span /><span /></span>Working
          </div>

          <div className="gt-frost" style={{ position: "absolute", left: 16, bottom: 16, borderRadius: 999, padding: "8px 14px", display: "flex", gap: 14,
            fontSize: 12, color: "#191919", zIndex: 3, opacity: legend.on ? 1 : 0, transition: "opacity .4s ease" }}>
            <span style={{ display: "flex", alignItems: "center", gap: 7 }}><span style={{ width: 9, height: 9, borderRadius: "50%", background: "var(--accent)", display: "inline-block" }} />{legend.label}</span>
            {legend.docks && <span style={{ display: "flex", alignItems: "center", gap: 7 }}><span style={{ width: 14, height: 4, borderRadius: 2, background: "var(--dock)", display: "inline-block" }} />Docks</span>}
          </div>

          <div className="gt-frost gt-zoom" style={{ position: "absolute", right: 16, bottom: 16, borderRadius: 14, display: "flex", flexDirection: "column", padding: 3, zIndex: 3 }}>
            <button className="gt-icon" onClick={() => gm.current?.zoom(1)} aria-label="Zoom in"><I.Plus14 /></button>
            <div style={{ height: 1, background: "#e7e6e3", margin: "0 8px" }} />
            <button className="gt-icon" onClick={() => gm.current?.zoom(-1)} aria-label="Zoom out"><I.Minus14 /></button>
          </div>

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
                <span style={{ position: "absolute", left: 10, top: 10, fontSize: 11, fontWeight: 500, color: "#191919", background: "rgba(255,255,255,.88)", borderRadius: 999, padding: "3px 8px" }}>2020</span>
                <span style={{ position: "absolute", right: 10, top: 10, fontSize: 11, fontWeight: 500, color: "#191919", background: "rgba(255,255,255,.88)", borderRadius: 999, padding: "3px 8px" }}>2022</span>
              </div>
              <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: "#6b6b68" }}>
                Compare 2020 and 2022
                <input className="gt-range" type="range" min={0} max={100} value={swipe} onChange={(e) => setSwipe(Number(e.target.value))} />
              </label>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {sheet.facts.map((f, k) => <p key={k} style={{ margin: 0, fontSize: 14, lineHeight: 1.5, color: "#191919" }}>{f}</p>)}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: "auto" }}>
                <button className="gt-pill" style={{ color: "#191919" }} onClick={() => feedback("yes")}>Looks right</button>
                <button className="gt-pill" onClick={() => feedback("no")} disabled={busy}>Not this</button>
                <span style={{ fontSize: 12, color: "#6b6b68" }}>{fb[featId(sel)] === "yes" ? "Thanks. I’ll find more like this." : fb[featId(sel)] === "no" ? "Removed. I’ll show fewer like this." : ""}</span>
              </div>
            </>)}
          </div>
        </main>
      </div>

      <div data-testid="landing" style={{ position: "absolute", inset: 0, zIndex: 5, opacity: landing ? 1 : 0, visibility: landing ? "visible" : "hidden",
        transition: "opacity .55s ease, visibility .55s" }}>
        <div aria-hidden="true" style={{ position: "absolute", inset: 0, overflow: "hidden", background: "#7b925e" }}>
          <div style={{ position: "absolute", inset: "-8%", backgroundImage: `url(${LANDING_BG})`, backgroundSize: "cover", backgroundPosition: "center",
            transform: "scale(1.35)", filter: "blur(16px) saturate(.6) brightness(1.15)" }} />
          <div style={{ position: "absolute", inset: 0, background: "rgba(244,244,242,.68)" }} />
        </div>
        <div style={{ position: "relative", height: "100%", display: "flex", flexDirection: "column" }}>
          <div style={{ padding: "22px 28px", fontSize: 15, fontWeight: 500 }}>Groundtruth</div>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "0 16px 12vh", gap: 22 }}>
            <h1 style={{ margin: 0, fontSize: 30, fontWeight: 400, letterSpacing: "-0.02em", lineHeight: 1.2, textAlign: "center" }}>What do you want to find?</h1>
            <div style={{ width: "100%", maxWidth: 640, background: "#ffffff", border: "1px solid #e7e6e3", borderRadius: 20, boxShadow: "var(--shadow-float)", padding: "16px 12px 12px 18px" }}>
              <label className="gt-sr" htmlFor="gt-ask-1">What do you want to find?</label>
              <textarea id="gt-ask-1" className="gt-ta" rows={2} placeholder="Houses with pools near Lake Norman…" value={landing ? input : ""} autoFocus
                onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }} />
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginTop: 8 }}>
                <span style={{ fontSize: 12, color: "#6b6b68" }}>Mecklenburg County · NAIP 2022</span>
                <button className="gt-send" onClick={() => send(input)} disabled={busy || !input.trim()} aria-label="Search"><I.Up /></button>
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "center" }}>
              <button className="gt-pill" onClick={() => send("Houses with pools")}>Houses with pools</button>
              <button className="gt-pill" onClick={() => send("Swimming pools")}>Swimming pools</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const pickedList = (o: Record<string, boolean>) => Object.keys(o).filter((k) => o[k]);

/** Swap in a re-run's card (and steps) on the message it came from; its text and pills stay as they were. */
function replaceAssistant(ms: Msg[], id: string, msg: Msg): Msg[] {
  return ms.filter((x) => x.role !== "working").map((x) => (x.id === id && x.role === "assistant" && msg.role === "assistant"
    ? { ...x, card: msg.card, steps: msg.steps, secs: msg.secs } : x));
}

function legendLabel(r: AgentResponse): string {
  const p = r.plan ?? {};
  if (p.target_kind === "derived" || p.concept === "swimming_pool") return "Pools";
  return String(p.concept_query ?? p.target ?? "Results").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
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

/** The detail sheet's crop: 328 × 200 aspect (≈ 82 × 50 m) around the result, shifted toward its dock when it has one. */
function sheetBbox(f: Feature): number[] {
  const [x, y] = centroid(focusGeom(f));
  const wM = 82, hM = 50;
  const dLon = wM / 2 / (111320 * Math.cos((y * Math.PI) / 180)), dLat = hM / 2 / 111320;
  return [x - dLon, y - dLat, x + dLon, y + dLat];
}

function sheetFor(f: Feature, n: number): { title: string; sub: string; facts: string[] } {
  const p = f.properties ?? {};
  const facts: string[] = [];
  if (p.kind === "house_with_pool") {
    const a = Math.round(Number(p.pool_area_m2 ?? 0));
    facts.push(`A pool of about ${fmt(a)} m² next to the house.`);
    if (p.lakefront) facts.push(`About ${fmt(Math.round(Number(p.dist_shore_m ?? 0)))} m from the Lake Norman shoreline.`);
    else facts.push("Not on the water.");
    const chg: any[] = p.dock_new_2020 ?? [];
    if (p.has_dock) facts.push(chg.some((c) => c?.new) ? "Its own dock, new since 2020." : "Its own dock, already there in 2020.");
    else if (p.lakefront) facts.push("No dock.");
    return { title: p.lakefront ? "Lakefront house" : "House with a pool", sub: `Result ${n} · NAIP 2022`, facts };
  }
  const area = p.area_m2 ?? p.footprint_m2;
  if (area) facts.push(`About ${fmt(Math.round(Number(area)))} m².`);
  if (p.height_m) facts.push(`About ${Number(p.height_m).toFixed(1)} m tall.`);
  if (p.change === "new") facts.push("New since 2020.");
  return { title: "Result", sub: `Result ${n} · NAIP 2022`, facts };
}
