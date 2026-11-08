// The composer (T1.3), the same component on the landing and in the chat panel: auto-growing textarea (≤ 6 lines) and a
// quiet control row: AREA chip (search places, saved areas, draw on map) · YEAR chip · MODE chip · Send / Stop.
// Slash commands: /area /year /compare /draw. Esc closes popovers.
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { ArrowUp, Calendar, ChevronDown, MapPin, PenLine, Search, Square, SquareDashed, Sparkles } from "lucide-react";
import type { Area, Mode, SavedArea, Year } from "./store";

export type DrawKind = "rectangle" | "polygon";
type Props = {
  variant: "landing" | "panel"; value: string; onChange: (v: string) => void; onSend: () => void;
  busy: boolean; onStop: () => void; placeholder: string; autoFocus?: boolean; inputId: string;
  area: Area; onArea: (a: Area) => void; year: Year; onYear: (y: Year) => void; mode: Mode; onMode: (m: Mode) => void;
  places: Record<string, number[]>; saved: SavedArea[]; onDraw: (k: DrawKind) => void; onSaveArea?: () => void;
};
export type ComposerHandle = { focus: () => void; open: (p: Pop) => void };
type Pop = "area" | "year" | "mode" | null;
type Hit = { label: string; sub: string; area: Area };

const I = { size: 14, strokeWidth: 1.5 } as const;
const YEARS: [Year, string, string][] = [["2022", "2022", "NAIP 2022"], ["2020", "2020", "NAIP 2020"],
  ["compare", "Compare 2020→2022", "what changed between the two"]];
const MODES: [Mode, string, string][] = [["find", "Find", "find and outline things"], ["measure", "Measure", "lead with numbers and areas"],
  ["compare", "Compare", "what changed since 2020"]];

const Composer = forwardRef<ComposerHandle, Props>(function Composer(p, ref) {
  const ta = useRef<HTMLTextAreaElement>(null);
  const [pop, setPop] = useState<Pop>(null);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [hi, setHi] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  useImperativeHandle(ref, () => ({ focus: () => ta.current?.focus(), open: (x) => setPop(x) }));

  // auto-grow up to 6 lines (15 px × 1.5)
  useEffect(() => {
    const t = ta.current;
    if (!t) return;
    t.style.height = "auto";
    t.style.height = `${Math.min(t.scrollHeight, 6 * 22.5)}px`;
  }, [p.value]);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) setPop(null); };
    addEventListener("mousedown", close);
    return () => removeEventListener("mousedown", close);
  }, []);

  // area search: configured neighbourhoods first, then Overture places / landmarks by name (find_place)
  const local = useMemo(() => {
    const s = q.trim().toLowerCase();
    return Object.entries(p.places).filter(([n]) => !s || n.toLowerCase().includes(s)).slice(0, 8)
      .map(([n, b]) => ({ label: n, sub: "Neighbourhood", area: { label: n, place: n, bbox: b } as Area }));
  }, [q, p.places]);
  useEffect(() => {
    const s = q.trim();
    if (pop !== "area" || s.length < 3) { setHits([]); return; }
    const ctl = new AbortController();
    const t = setTimeout(() => {
      fetch("/api/v1/tools/find_place", { method: "POST", headers: { "Content-Type": "application/json" }, signal: ctl.signal,
        body: JSON.stringify({ query: s, limit: 6 }) }).then((r) => r.json()).then((env) => {
        const fs = env?.result?.geojson?.features ?? [];
        setHits(fs.filter((f: any) => f.properties?.name).slice(0, 6).map((f: any) => {
          const [x, y] = f.geometry.type === "Point" ? f.geometry.coordinates : f.geometry.coordinates[0][0];
          const d = 0.012; // ~1.3 km around a landmark
          return { label: f.properties.name, sub: String(f.properties.category ?? "Place").replace(/_/g, " "),
            area: { label: f.properties.name, bbox: [x - d, y - d * 0.8, x + d, y + d * 0.8] } };
        }));
      }).catch(() => {});
    }, 180);
    return () => { clearTimeout(t); ctl.abort(); };
  }, [q, pop]);
  const options: Hit[] = pop === "area" ? [...local, ...hits] : [];
  useEffect(() => setHi(0), [q, pop]);

  const choose = (a: Area) => { p.onArea(a); setPop(null); setQ(""); ta.current?.focus(); };
  const slash = (v: string) => {
    const m = v.match(/(^|\s)\/(area|year|compare|draw)(\s|$)/);
    if (!m) return false;
    p.onChange(v.replace(m[0], m[1]).trimStart());
    if (m[2] === "area") setPop("area");
    else if (m[2] === "year") setPop("year");
    else if (m[2] === "compare") { p.onYear("compare"); p.onMode("compare"); }
    else if (m[2] === "draw") p.onDraw("rectangle");
    return true;
  };

  const yearLabel = YEARS.find((y) => y[0] === p.year)![1];
  const modeLabel = MODES.find((m) => m[0] === p.mode)![1];
  const landing = p.variant === "landing";
  return (
    <div ref={root} className={`gt-composer${landing ? " gt-composer-landing" : ""}`} data-testid={`composer-${p.variant}`}>
      <label className="gt-sr" htmlFor={p.inputId}>{landing ? "What do you want to find?" : "Ask a follow-up"}</label>
      <textarea id={p.inputId} ref={ta} className="gt-ta" rows={landing ? 2 : 1} placeholder={p.placeholder} value={p.value}
        autoFocus={p.autoFocus}
        onChange={(e) => { if (!slash(e.target.value)) p.onChange(e.target.value); }}
        onKeyDown={(e) => {
          if (e.key === "Escape") { setPop(null); return; }
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (slash(p.value + " ")) return;
            if (!p.busy) p.onSend();
          }
        }} />
      <div className="gt-ctlrow">
        <div className="gt-chipwrap">
          <button className={`gt-chip${p.area ? " gt-chip-on" : ""}`} aria-haspopup="dialog" aria-expanded={pop === "area"} data-testid="chip-area"
            onClick={() => setPop(pop === "area" ? null : "area")}>
            <MapPin {...I} /><span className="gt-chiplabel">{p.area?.label ?? "Anywhere in Mecklenburg"}</span><ChevronDown {...I} size={12} />
          </button>
          {pop === "area" && (
            <div className="gt-pop gt-pop-area" role="dialog" aria-label="Choose an area">
              <div className="gt-popsearch">
                <Search {...I} />
                <input autoFocus placeholder="Search a neighbourhood or place…" value={q} aria-label="Search areas"
                  onChange={(e) => setQ(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "ArrowDown") { e.preventDefault(); setHi((h) => Math.min(h + 1, options.length - 1)); }
                    if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)); }
                    if (e.key === "Enter" && options[hi]) { e.preventDefault(); choose(options[hi].area); }
                    if (e.key === "Escape") { setPop(null); ta.current?.focus(); }
                  }} />
              </div>
              <div className="gt-poplist" role="listbox">
                {p.area && <button className="gt-menuitem" onClick={() => choose(null)}>Anywhere in Mecklenburg</button>}
                {options.map((o, i) => (
                  <button key={`${o.label}-${i}`} role="option" aria-selected={i === hi} className={`gt-menuitem${i === hi ? " gt-menuitem-hi" : ""}`}
                    onMouseEnter={() => setHi(i)} onClick={() => choose(o.area)}>
                    <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{o.label}</span>
                    <span className="gt-menusub">{o.sub}</span>
                  </button>))}
                {p.saved.length > 0 && <div className="gt-poplabel">Saved areas</div>}
                {p.saved.map((a) => (
                  <button key={a.id} className="gt-menuitem" onClick={() => choose({ label: a.name, geojson: a.geometry })}>
                    <MapPin {...I} />{a.name}</button>))}
                <div className="gt-poplabel">Draw on map</div>
                <div style={{ display: "flex", gap: 4 }}>
                  <button className="gt-menuitem" onClick={() => { setPop(null); p.onDraw("rectangle"); }}><SquareDashed {...I} />Rectangle</button>
                  <button className="gt-menuitem" onClick={() => { setPop(null); p.onDraw("polygon"); }}><PenLine {...I} />Polygon</button>
                </div>
                {p.onSaveArea && p.area && !p.area.place && (
                  <button className="gt-menuitem" onClick={() => { setPop(null); p.onSaveArea?.(); }}>Save this area…</button>)}
              </div>
            </div>)}
        </div>
        <div className="gt-chipwrap">
          <button className={`gt-chip${p.year !== "2022" ? " gt-chip-on" : ""}`} aria-haspopup="listbox" aria-expanded={pop === "year"} data-testid="chip-year"
            onClick={() => setPop(pop === "year" ? null : "year")}><Calendar {...I} /><span className="gt-chiplabel">{yearLabel}</span><ChevronDown {...I} size={12} /></button>
          {pop === "year" && (
            <div className="gt-pop gt-pop-small" role="listbox" aria-label="Imagery year">
              {YEARS.map(([v, l, s]) => (
                <button key={v} role="option" aria-selected={p.year === v} className={`gt-menuitem${p.year === v ? " gt-menuitem-on" : ""}`}
                  onClick={() => { p.onYear(v); if (v === "compare") p.onMode("compare"); setPop(null); ta.current?.focus(); }}>
                  <span style={{ flex: 1 }}>{l}</span><span className="gt-menusub">{s}</span></button>))}
            </div>)}
        </div>
        <div className="gt-chipwrap">
          <button className={`gt-chip${p.mode !== "find" ? " gt-chip-on" : ""}`} aria-haspopup="listbox" aria-expanded={pop === "mode"} data-testid="chip-mode"
            onClick={() => setPop(pop === "mode" ? null : "mode")}><Sparkles {...I} /><span className="gt-chiplabel">{modeLabel}</span><ChevronDown {...I} size={12} /></button>
          {pop === "mode" && (
            <div className="gt-pop gt-pop-small" role="listbox" aria-label="Mode">
              {MODES.map(([v, l, s]) => (
                <button key={v} role="option" aria-selected={p.mode === v} className={`gt-menuitem${p.mode === v ? " gt-menuitem-on" : ""}`}
                  onClick={() => { p.onMode(v); setPop(null); ta.current?.focus(); }}>
                  <span style={{ flex: 1 }}>{l}</span><span className="gt-menusub">{s}</span></button>))}
            </div>)}
        </div>
        <span style={{ flex: 1 }} />
        {p.busy
          ? <button className="gt-send gt-stop" onClick={p.onStop} aria-label="Stop"><Square size={11} fill="currentColor" strokeWidth={0} /></button>
          : <button className="gt-send" onClick={p.onSend} disabled={!p.value.trim()} aria-label={landing ? "Search" : "Send"}><ArrowUp size={15} strokeWidth={1.8} /></button>}
      </div>
    </div>
  );
});
export default Composer;

/** The chip state shown under a user message ("Lake Norman · 2022 · Measure"). */
export function tagsFor(area: Area, year: Year, mode: Mode): string[] {
  const t: string[] = [area?.label ?? "", year === "compare" ? "2020→2022" : year, mode === "find" ? "" : mode[0].toUpperCase() + mode.slice(1)];
  return t.filter(Boolean);
}
