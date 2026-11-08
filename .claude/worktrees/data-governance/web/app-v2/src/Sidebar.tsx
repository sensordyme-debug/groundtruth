// Investigation sidebar (next to the rail): New investigation (Ctrl K), search, investigations grouped by date with
// their real status, plus the Reports and Saved views. 264 px; hidden when closed; an overlay drawer below 900 px.
import { useEffect, useMemo, useRef, useState } from "react";
import { Download, MapPin, MoreHorizontal, Pencil, Pin as PinIcon, Plus, Search, Trash2, X } from "lucide-react";
import type { Pin, SavedArea, SessionRow } from "./store";
import { relTime } from "./store";
import type { View } from "./NavRail";

export type ReportRow = { id: string; objective: string; status: string; updated: number; scope?: string };
type Props = {
  view: View; narrow: boolean; open: boolean; onClose: () => void; onNew: () => void;
  sessions: SessionRow[]; total: number; activeId: string | null; onOpen: (id: string) => void; query: string; onQuery: (q: string) => void;
  onRename: (id: string, title: string) => void; onDelete: (id: string) => void; onShowAll: () => void;
  areas: SavedArea[]; onArea: (a: SavedArea) => void; onDeleteArea: (id: string) => void;
  pins: Pin[]; onPin: (p: Pin) => void; onUnpin: (id: string) => void; reports: ReportRow[];
};

const I = { size: 16, strokeWidth: 1.5 } as const;
const STATUS: Record<string, string> = { running: "Running", planning: "Running", done: "Completed", partial: "Needs review",
  failed: "Failed", declined: "Declined", needs_input: "Needs input", cancelled: "Stopped", search: "" };

function group(t: number): string {
  const d = new Date(t * 1000), now = new Date();
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86400000);
  return diff <= 0 ? "Today" : diff === 1 ? "Yesterday" : diff <= 7 ? "Previous 7 days" : "Older";
}

export default function Sidebar(p: Props) {
  const [rowMenu, setRowMenu] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const search = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (!(e.target as HTMLElement).closest?.("[data-keep-open]")) setRowMenu(null); };
    addEventListener("mousedown", close);
    return () => removeEventListener("mousedown", close);
  }, []);
  const groups = useMemo(() => {
    const g: [string, SessionRow[]][] = [];
    for (const s of p.sessions) {
      const k = group(s.updated);
      const last = g[g.length - 1];
      if (last && last[0] === k) last[1].push(s); else g.push([k, [s]]);
    }
    return g;
  }, [p.sessions]);
  if (!p.open) return null;
  return (
    <>
      {p.narrow && <div className="gt-scrim" onClick={p.onClose} />}
      <aside className={`gt-side${p.narrow ? " gt-drawer" : ""}`} aria-label={p.view === "reports" ? "Reports" : p.view === "saved" ? "Saved" : "Investigations"} data-testid="sidebar">
        <div className="gt-side-top">
          <span className="gt-side-title">{p.view === "reports" ? "Reports" : p.view === "saved" ? "Saved" : "Investigations"}</span>
          <button className="gt-icon gt-icon-sm" onClick={p.onClose} aria-label="Close sidebar" title="Close sidebar"><X {...I} /></button>
        </div>
        {p.view === "investigations" && (<>
          <div style={{ padding: "0 12px 8px", display: "flex", flexDirection: "column", gap: 8 }}>
            <button className="gt-newbtn" onClick={p.onNew} title="New investigation (Ctrl K)" data-testid="new-investigation">
              <Plus {...I} /><span style={{ flex: 1, textAlign: "left" }}>New investigation</span><span className="gt-kbd">Ctrl K</span>
            </button>
            <label className="gt-sidesearch">
              <Search {...I} size={14} />
              <input ref={search} value={p.query} onChange={(e) => p.onQuery(e.target.value)} placeholder="Search investigations"
                aria-label="Search investigations" data-testid="session-search" />
            </label>
          </div>
          <div className="gt-side-scroll">
            {p.sessions.length === 0 && <div className="gt-side-empty">{p.query ? "Nothing matches." : "Your investigations show up here."}</div>}
            {groups.map(([g, rows]) => (
              <section key={g} className="gt-side-sec">
                <h2 className="gt-side-h">{g}</h2>
                {rows.map((s) => (
                  <div key={s.id} className={`gt-row${s.id === p.activeId ? " gt-row-on" : ""}`} data-testid="session-row" data-id={s.id}>
                    {editing === s.id ? (
                      <input className="gt-rowedit" autoFocus defaultValue={s.title} aria-label="Rename investigation"
                        onKeyDown={(e) => {
                          if (e.key === "Enter") { p.onRename(s.id, (e.target as HTMLInputElement).value); setEditing(null); }
                          if (e.key === "Escape") setEditing(null);
                        }}
                        onBlur={(e) => { p.onRename(s.id, e.target.value); setEditing(null); }} />
                    ) : (
                      <button className="gt-rowbtn gt-rowbtn2" onClick={() => p.onOpen(s.id)} title={s.title}>
                        <span className="gt-rowtitle">{s.title}</span>
                        <span className="gt-rowmeta">
                          {s.status && STATUS[s.status] ? <span className={`gt-stat gt-stat-${s.status}`}>{STATUS[s.status]}</span> : null}
                          <span>{s.scope ? `${s.scope} · ` : ""}{relTime(s.updated)}</span>
                        </span>
                      </button>
                    )}
                    <button className="gt-rowmore" data-keep-open aria-label={`Options for ${s.title}`} aria-expanded={rowMenu === s.id}
                      onClick={() => setRowMenu((m) => (m === s.id ? null : s.id))}><MoreHorizontal {...I} /></button>
                    {rowMenu === s.id && (
                      <div className="gt-pop gt-rowmenu" data-keep-open role="menu">
                        <button className="gt-menuitem" role="menuitem" onClick={() => { setEditing(s.id); setRowMenu(null); }}><Pencil {...I} size={14} />Rename</button>
                        <button className="gt-menuitem" role="menuitem" onClick={() => { setRowMenu(null); p.onDelete(s.id); }}><Trash2 {...I} size={14} />Delete</button>
                      </div>)}
                  </div>))}
              </section>))}
            {p.total > p.sessions.length && <button className="gt-ghost" style={{ margin: "6px 0 0 6px" }} onClick={p.onShowAll}>Show all ({p.total})</button>}
          </div>
        </>)}
        {p.view === "reports" && (
          <div className="gt-side-scroll">
            {p.reports.length === 0 && <div className="gt-side-empty">Completed investigations show their reports here.</div>}
            {p.reports.map((r) => (
              <div key={r.id} className="gt-row">
                <a className="gt-rowbtn gt-rowbtn2" href={`/api/v1/investigations/${r.id}/report.md`} download title={r.objective} data-testid="report-row">
                  <span className="gt-rowtitle">{r.objective}</span>
                  <span className="gt-rowmeta"><span className={`gt-stat gt-stat-${r.status}`}>{STATUS[r.status] ?? r.status}</span>
                    <span>{r.scope ? `${r.scope} · ` : ""}{relTime(r.updated)}</span></span>
                </a>
                <span className="gt-rowmore" style={{ opacity: 1 }} aria-hidden="true"><Download {...I} size={14} /></span>
              </div>))}
          </div>)}
        {p.view === "saved" && (
          <div className="gt-side-scroll">
            <section className="gt-side-sec" style={{ marginTop: 4 }}>
              <h2 className="gt-side-h">Saved areas</h2>
              {p.areas.length === 0 && <div className="gt-side-empty">Save an area from the composer's area chip.</div>}
              {p.areas.map((a) => (
                <div key={a.id} className="gt-row">
                  <button className="gt-rowbtn" onClick={() => p.onArea(a)} title={a.name}><MapPin {...I} size={14} style={{ flex: "none", color: "#6b6b68" }} /><span className="gt-rowtitle">{a.name}</span></button>
                  <button className="gt-rowmore" aria-label={`Delete ${a.name}`} onClick={() => p.onDeleteArea(a.id)}><Trash2 {...I} size={14} /></button>
                </div>))}
            </section>
            <section className="gt-side-sec">
              <h2 className="gt-side-h">Pinned results</h2>
              {p.pins.length === 0 && <div className="gt-side-empty">Pin a result from its ⋯ menu.</div>}
              {p.pins.map((x) => (
                <div key={x.id} className="gt-row">
                  <button className="gt-rowbtn" onClick={() => p.onPin(x)} title={x.title}><PinIcon {...I} size={14} style={{ flex: "none", color: "#6b6b68" }} /><span className="gt-rowtitle">{x.title}</span></button>
                  <button className="gt-rowmore" aria-label={`Unpin ${x.title}`} onClick={() => p.onUnpin(x.id)}><Trash2 {...I} size={14} /></button>
                </div>))}
            </section>
          </div>)}
        <div className="gt-side-foot"><span style={{ fontSize: 12, color: "#6b6b68" }}>Mecklenburg County · NAIP 2022/2020</span></div>
      </aside>
    </>
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
