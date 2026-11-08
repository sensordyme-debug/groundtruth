// Left sidebar (T1.1): wordmark + collapse, New search (Ctrl K), Recent sessions, Saved areas, Pinned results, footer
// with settings. 240 px open, 56 px icon rail when collapsed, an overlay drawer below 900 px.
import { useEffect, useRef, useState } from "react";
import { MapPin, MessageSquare, MoreHorizontal, PanelLeft, PanelLeftClose, Pencil, Pin as PinIcon, Plus, Settings, Trash2 } from "lucide-react";
import type { Pin, Prefs, SavedArea, SessionRow } from "./store";
import { relTime } from "./store";

type Props = {
  mode: "open" | "rail"; narrow: boolean; drawerOpen: boolean; onDrawer: (o: boolean) => void;
  onToggle: () => void; onNew: () => void;
  sessions: SessionRow[]; total: number; activeId: string | null; onOpen: (id: string) => void;
  onRename: (id: string, title: string) => void; onDelete: (id: string) => void; onShowAll: () => void;
  areas: SavedArea[]; onArea: (a: SavedArea) => void; onDeleteArea: (id: string) => void;
  pins: Pin[]; onPin: (p: Pin) => void; onUnpin: (id: string) => void;
  prefs: Prefs; onPrefs: (p: Prefs) => void;
};

const I = { size: 16, strokeWidth: 1.5 } as const;

export default function Sidebar(p: Props) {
  const rail = p.mode === "rail" && !p.narrow;
  const [rowMenu, setRowMenu] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [settings, setSettings] = useState(false);
  const setRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest?.("[data-keep-open]")) { setRowMenu(null); setSettings(false); }
    };
    addEventListener("mousedown", close);
    return () => removeEventListener("mousedown", close);
  }, []);
  if (p.narrow && !p.drawerOpen) return null;
  const width = rail ? 56 : 240;
  return (
    <>
      {p.narrow && <div className="gt-scrim" onClick={() => p.onDrawer(false)} />}
      <nav className={`gt-side${p.narrow ? " gt-drawer" : ""}`} aria-label="Sidebar" style={{ width }} data-testid="sidebar" data-mode={rail ? "rail" : "open"}>
        <div className="gt-side-top" style={{ justifyContent: rail ? "center" : "space-between" }}>
          {!rail && <span style={{ fontSize: 15, fontWeight: 500, paddingLeft: 6 }}>Groundtruth</span>}
          <button className="gt-icon gt-icon-sm" onClick={p.narrow ? () => p.onDrawer(false) : p.onToggle}
            aria-label={rail ? "Expand sidebar" : "Collapse sidebar"} title={rail ? "Expand sidebar" : "Collapse sidebar"}>
            {rail ? <PanelLeft {...I} /> : <PanelLeftClose {...I} />}
          </button>
        </div>
        <div style={{ padding: rail ? "4px 8px" : "4px 12px 8px" }}>
          <button className="gt-newbtn" onClick={p.onNew} aria-label="New search" title="New search (Ctrl K)"
            style={rail ? { width: 40, padding: 0, justifyContent: "center" } : undefined}>
            <Plus {...I} />{!rail && <><span style={{ flex: 1, textAlign: "left" }}>New search</span><span className="gt-kbd">Ctrl K</span></>}
          </button>
        </div>
        {!rail && (
          <div className="gt-side-scroll">
            <Section title="Recent">
              {p.sessions.length === 0 && <div className="gt-side-empty">Your searches show up here.</div>}
              {p.sessions.map((s) => (
                <div key={s.id} className={`gt-row${s.id === p.activeId ? " gt-row-on" : ""}`} data-testid="session-row" data-id={s.id}>
                  {editing === s.id ? (
                    <input className="gt-rowedit" autoFocus defaultValue={s.title} aria-label="Rename search"
                      onKeyDown={(e) => {
                        if (e.key === "Enter") { p.onRename(s.id, (e.target as HTMLInputElement).value); setEditing(null); }
                        if (e.key === "Escape") setEditing(null);
                      }}
                      onBlur={(e) => { p.onRename(s.id, e.target.value); setEditing(null); }} />
                  ) : (
                    <button className="gt-rowbtn" onClick={() => p.onOpen(s.id)} title={s.title}>
                      <span className="gt-rowtitle">{s.title}</span>
                      <span className="gt-rowtime">{relTime(s.updated)}</span>
                    </button>
                  )}
                  <button className="gt-rowmore" data-keep-open aria-label={`Options for ${s.title}`}
                    onClick={() => setRowMenu((m) => (m === s.id ? null : s.id))}><MoreHorizontal {...I} /></button>
                  {rowMenu === s.id && (
                    <div className="gt-pop gt-rowmenu" data-keep-open role="menu">
                      <button className="gt-menuitem" role="menuitem" onClick={() => { setEditing(s.id); setRowMenu(null); }}><Pencil {...I} size={14} />Rename</button>
                      <button className="gt-menuitem" role="menuitem" onClick={() => { setRowMenu(null); p.onDelete(s.id); }}><Trash2 {...I} size={14} />Delete</button>
                    </div>)}
                </div>))}
              {p.total > p.sessions.length && <button className="gt-ghost" style={{ marginLeft: 2 }} onClick={p.onShowAll}>Show all ({p.total})</button>}
            </Section>
            <Section title="Saved areas">
              {p.areas.length === 0 && <div className="gt-side-empty">Save an area from the map's area chip.</div>}
              {p.areas.map((a) => (
                <div key={a.id} className="gt-row">
                  <button className="gt-rowbtn" onClick={() => p.onArea(a)} title={a.name}>
                    <MapPin {...I} size={14} style={{ flex: "none", color: "#6b6b68" }} /><span className="gt-rowtitle">{a.name}</span>
                  </button>
                  <button className="gt-rowmore" aria-label={`Delete ${a.name}`} onClick={() => p.onDeleteArea(a.id)}><Trash2 {...I} size={14} /></button>
                </div>))}
            </Section>
            <Section title="Pinned results">
              {p.pins.length === 0 && <div className="gt-side-empty">Pin a result from its ⋯ menu.</div>}
              {p.pins.map((x) => (
                <div key={x.id} className="gt-row">
                  <button className="gt-rowbtn" onClick={() => p.onPin(x)} title={x.title}>
                    <PinIcon {...I} size={14} style={{ flex: "none", color: "#6b6b68" }} /><span className="gt-rowtitle">{x.title}</span>
                  </button>
                  <button className="gt-rowmore" aria-label={`Unpin ${x.title}`} onClick={() => p.onUnpin(x.id)}><Trash2 {...I} size={14} /></button>
                </div>))}
            </Section>
          </div>)}
        {rail && (
          <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4, paddingTop: 8 }}>
            <button className="gt-icon gt-icon-sm" aria-label="Recent searches" title="Recent searches" onClick={p.onToggle}><MessageSquare {...I} /></button>
            <button className="gt-icon gt-icon-sm" aria-label="Saved areas" title="Saved areas" onClick={p.onToggle}><MapPin {...I} /></button>
            <button className="gt-icon gt-icon-sm" aria-label="Pinned results" title="Pinned results" onClick={p.onToggle}><PinIcon {...I} /></button>
          </div>)}
        <div className="gt-side-foot" style={{ justifyContent: rail ? "center" : "space-between", position: "relative" }} ref={setRef}>
          {!rail && <span style={{ fontSize: 12, color: "#6b6b68" }}>Mecklenburg County · NAIP 2022/2020</span>}
          <button className="gt-icon gt-icon-sm" data-keep-open aria-label="Settings" aria-expanded={settings} onClick={() => setSettings((s) => !s)}><Settings {...I} /></button>
          {settings && (
            <div className="gt-pop gt-settings" data-keep-open role="dialog" aria-label="Settings">
              <div className="gt-poplabel">Imagery year</div>
              <Seg value={String(p.prefs.year)} options={[["2022", "2022"], ["2020", "2020"]]} onChange={(v) => p.onPrefs({ ...p.prefs, year: Number(v) as 2022 | 2020 })} />
              <div className="gt-poplabel">Units</div>
              <Seg value={p.prefs.units} options={[["m", "Metres"], ["ft", "Feet"]]} onChange={(v) => p.onPrefs({ ...p.prefs, units: v as "m" | "ft" })} />
              <label className="gt-check" style={{ marginTop: 6 }}><input type="checkbox" checked={p.prefs.reduceMotion}
                onChange={() => p.onPrefs({ ...p.prefs, reduceMotion: !p.prefs.reduceMotion })} />Reduce motion</label>
            </div>)}
        </div>
      </nav>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="gt-side-sec">
      <h2 className="gt-side-h">{title}</h2>
      {children}
    </section>
  );
}

export function Seg({ value, options, onChange }: { value: string; options: [string, string][]; onChange: (v: string) => void }) {
  return (
    <div className="gt-seg" role="radiogroup">
      {options.map(([v, l]) => (
        <button key={v} role="radio" aria-checked={value === v} className={value === v ? "gt-seg-on" : ""} onClick={() => onChange(v)}>{l}</button>))}
    </div>
  );
}
