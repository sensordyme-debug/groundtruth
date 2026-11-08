import { useState } from "react";
import { thumb } from "./api";
import type { Bbox, Feature, Health } from "./api";
import { AGENT_DEMO_PROMPT, DEFAULT_PLACES, EXAMPLE_QUERIES, LANDING_CHIPS, matchPlace, parseQuery, pretty } from "./config";

/** Chip thumbnail with the matching 16.8 m cell outlined. */
export function CellThumb({ f, size, big }: { f: Feature; size?: number; big?: boolean }) {
  if (!f.properties.chip_bbox || !f.properties.cell_bbox)
    return <div className={`cthumb empty ${big ? "big" : ""}`} style={size ? { width: size, height: size } : undefined} aria-hidden />;
  const [w, s, e, n] = f.properties.chip_bbox, [cw, cs, ce, cn] = f.properties.cell_bbox;
  const st = { left: `${((cw - w) / (e - w)) * 100}%`, top: `${((n - cn) / (n - s)) * 100}%`,
    width: `${((ce - cw) / (e - w)) * 100}%`, height: `${((cn - cs) / (n - s)) * 100}%` };
  return (
    <div className={`cthumb ${big ? "big" : ""}`} style={size ? { width: size, height: size } : undefined}>
      <img src={thumb(f.properties.thumb_url)} alt="" />
      <div className="cellbox" style={st} />
    </div>
  );
}

export function AskScreen(p: {
  query: string; setQuery: (s: string) => void; onAsk: (q: string) => void; busy: string | null; err: string | null;
  places: Record<string, Bbox>; placeSource: string; place: string | null; setPlace: (p: string | null) => void;
  hl: Health | null; aoi: string; year: number; onBrowse: (place: string | null) => void; onAgent: (prompt: string) => void;
}) {
  const [pq, setPq] = useState("");
  const names = Object.keys(p.places);
  const sugg = pq ? names.filter((n) => n.toLowerCase().includes(pq.toLowerCase())).slice(0, 8)
    : DEFAULT_PLACES.filter((n) => p.places[n]);
  const parsed = p.query ? parseQuery(p.query, p.places) : null;
  const idx = p.hl?.indexes.filter((i) => i.year === p.year) ?? [];
  const choosePlace = (n: string | null) => { if (!n) return; p.setPlace(n); setPq(""); };
  return (
    <div className="screen ask-screen">
      <div className="screen-brand"><span className="logo" aria-hidden>◎</span>Groundtruth<span className="brand-sub">teach the map anything · Mecklenburg County, NC</span></div>
      <div className="hero">
        <h1>What are you looking for?</h1>
        <form className="ask-big" onSubmit={(e) => { e.preventDefault(); p.onAsk(p.query); }}>
          <input autoFocus value={p.query} onChange={(e) => p.setQuery(e.target.value)} placeholder="swimming pools, parking lots, solar panels…" />
          {p.place && !parsed?.place && <span className="pill in-input">in {p.place}<button type="button" onClick={() => p.setPlace(null)} aria-label="clear place">×</button></span>}
          <button className="btn primary lg" disabled={!!p.busy || !p.aoi || !p.query.trim()}>{p.busy ? "Looking…" : "Search"}</button>
        </form>
        {parsed?.place && <div className="hint parsed">Searching for <b>{parsed.text}</b> in <b>{parsed.place}</b></div>}
        {p.busy && <div className="hint parsed"><span className="spin" /> {p.busy}</div>}
        {p.err && <div className="err parsed">{p.err}</div>}
        <div className="chips">
          {LANDING_CHIPS.map((q) => <button key={q} className="chip" onClick={() => p.onAgent(q)}>{q}</button>)}
        </div>
        <div className="chips">
          {EXAMPLE_QUERIES.map((q) => <button key={q} className="chip ghost" onClick={() => { p.setQuery(q); p.onAsk(q); }}>pick: {q}</button>)}
        </div>

        <div className="hood">
          <div className="hood-head">
            <span className="flabel">Neighbourhood</span>
            <form className="hood-input" onSubmit={(e) => { e.preventDefault(); choosePlace(matchPlace(pq, p.places)); }}>
              <input value={pq} onChange={(e) => setPq(e.target.value)} placeholder="Ballantyne, NoDa, South End…" />
            </form>
            {p.place && <button className="btn ghost" onClick={() => p.onBrowse(p.place)}>View {p.place} on the map →</button>}
          </div>
          <div className="chips">
            {sugg.map((n) => <button key={n} className={`chip ${p.place === n ? "on" : ""}`} onClick={() => choosePlace(n)}>{n}</button>)}
            {pq && !sugg.length && <span className="hint small">No neighbourhood called “{pq}”.</span>}
          </div>
        </div>

        <div className="ask-foot">
          <div>
            <span className="flabel">Indexed now</span>{" "}
            {idx.length ? idx.map((i) => `${pretty(i.aoi)} (${i.chips.toLocaleString()} chips)`).join(" · ") : "loading…"} · NAIP {p.year}, 0.6 m
            {p.aoi && p.aoi !== "county" && <> — slice indexes only, so neighbourhoods outside them return nothing yet.</>}
          </div>
          <button className="agent-link" onClick={() => p.onAgent(AGENT_DEMO_PROMPT)}>
            <span className="tag planner">rules planner</span> run “{AGENT_DEMO_PROMPT}” →
          </button>
          {p.placeSource && <div className="src">Places: {p.placeSource}</div>}
        </div>
      </div>
    </div>
  );
}

export function PickScreen(p: {
  query: string; cands: Feature[]; picks: string[]; nots: string[]; setPicks: (x: string[]) => void; setNots: (x: string[]) => void;
  place: string | null; busy: string | null; onBack: () => void; onFind: () => void; concept: string | null;
}) {
  const toggle = (list: string[], x: string) => (list.includes(x) ? list.filter((y) => y !== x) : [...list, x]);
  return (
    <div className="screen pick-screen">
      <div className="pick-head">
        <button className="btn ghost" onClick={p.onBack}>← Back</button>
        <div>
          <h2>Which “{p.query}” do you mean?</h2>
          <p className="hint">Select one or more tiles as examples. Every tile you don't pick becomes a negative (“not this”). The highlighted square is the 17 m cell the match is about.</p>
        </div>
      </div>
      <div className="tiles">
        {p.cands.map((c, i) => {
          const ex = c.properties.example as string;
          if (c.properties.no_clear_example || !c.geometry) return (
            <div key={ex ?? `nce-${i}`} className="tile no-clear" aria-disabled title="No clear example in the index — not selectable">
              <div className="tile-img nce-img"><span>no clear example</span></div>
              <div className="tile-meta">
                <div>
                  <div className="sense">{c.properties.sense_label ?? pretty(String(c.properties.sense_id ?? "sense"))}</div>
                  <div className="hint small">{c.properties.reason ?? "no tile clearly shows this meaning"}</div>
                </div>
              </div>
            </div>);
          const picked = p.picks.includes(ex), not = p.nots.includes(ex);
          return (
            <div key={ex} className={`tile ${picked ? "picked" : ""} ${not ? "not" : ""}`}
              onClick={() => { p.setPicks(toggle(p.picks, ex)); if (not) p.setNots(p.nots.filter((y) => y !== ex)); }}>
              <div className="tile-img">
                <CellThumb f={c} big />
                {picked && <span className="check">✓</span>}
              </div>
              <div className="tile-meta">
                <div>
                  <div className="sense">{c.properties.sense_label}</div>
                  <div className="hint small">match {Number(c.properties.score).toFixed(2)}</div>
                </div>
                <button className={`not-btn ${not ? "on" : ""}`} onClick={(e) => { e.stopPropagation();
                  p.setNots(toggle(p.nots, ex)); if (picked) p.setPicks(p.picks.filter((y) => y !== ex)); }}>
                  {not ? "✗ not this" : "not this"}
                </button>
              </div>
            </div>);
        })}
      </div>
      <div className="pick-foot">
        <span className="hint">
          {p.picks.length ? `${p.picks.length} example${p.picks.length > 1 ? "s" : ""}` : "Pick at least one example"}
          {p.nots.length > 0 && ` · ${p.nots.length} not this`}
          {p.place && ` · in ${p.place}`}
          {p.concept && ` · concept ${pretty(p.concept)}`}
        </span>
        <button className="btn primary lg" disabled={!p.picks.length || !!p.busy} onClick={p.onFind}>
          {p.busy ? p.busy : "Find all"}
        </button>
      </div>
    </div>
  );
}
