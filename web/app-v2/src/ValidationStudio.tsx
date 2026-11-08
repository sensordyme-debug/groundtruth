// Validation Studio: human review of AI detections on the live map (real NAIP, zoom/pan). The prediction is drawn as a
// dashed outline; the reviewer accepts, rejects, marks uncertain / needs review, or draws / corrects the polygon. Reviews
// go to the versioned validation store (api/validation_api.py), never to data/labels/. Keys: A R U N, D draw, J/K next/prev.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Download, HelpCircle, PenLine, Flag, X } from "lucide-react";
import type { GtMap } from "./map";
import { owner } from "./store";

export type Sample = { id: string; class: string; region: string; holdout_group?: string; geometry: any; centroid?: number[];
  source_tool?: string; model?: string; score?: number | null; imagery?: Record<string, any>; in_label_cluster?: boolean;
  review?: { decision: string; polygon?: any; t?: number; note?: string } | null; [k: string]: any };

const CLASSES = ["", "pool", "building", "parking", "dock", "solar"];
const DECISIONS: [string, string, JSX.Element][] = [["accept", "Accept", <Check size={14} />], ["reject", "Reject", <X size={14} />],
  ["uncertain", "Uncertain", <HelpCircle size={14} />], ["needs_review", "Needs review", <Flag size={14} />]];

async function j(url: string, init?: RequestInit) {
  const r = await fetch(url, { ...init, headers: { "Content-Type": "application/json", "X-GT-Owner": owner(), ...(init?.headers ?? {}) } });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

export default function ValidationStudio({ map, onToast }: { map: GtMap | null; onToast: (t: string) => void }) {
  const [cls, setCls] = useState("pool");
  const [region, setRegion] = useState("");
  const [status, setStatus] = useState("unreviewed");
  const [items, setItems] = useState<Sample[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [i, setI] = useState(0);
  const [progress, setProgress] = useState<any>(null);
  const [metrics, setMetrics] = useState<any>(null);
  const [drawn, setDrawn] = useState<any | null>(null);
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setErr("");
    try {
      const q = new URLSearchParams({ status, limit: "200", ...(cls ? { cls } : {}), ...(region ? { region } : {}) });
      const d = await j(`/api/v1/validation/samples?${q}`);
      const rows: Sample[] = d.samples ?? d.items ?? d;
      setItems(Array.isArray(rows) ? rows : []); setTotal(d.total ?? (Array.isArray(rows) ? rows.length : 0)); setI(0);
      setProgress(await j("/api/v1/validation/progress").catch(() => null));
      setMetrics(await j("/api/v1/validation/metrics").catch(() => null));
    } catch (e: any) {
      setErr(e?.message === "404" ? "The validation service isn't available on this server yet." : "Couldn't load the review queue.");
      setItems([]); setTotal(0);
    }
  }, [cls, region, status]);
  useEffect(() => { void load(); }, [load]);

  const cur = items[i];
  const regions = useMemo(() => {
    const p = progress?.by_region ?? progress?.regions ?? null;
    return p ? Object.keys(p) : Array.from(new Set(items.map((x) => x.region))).sort();
  }, [progress, items]);

  // show the current sample on the map: prediction dashed, the human polygon (drawn or saved) solid white
  useEffect(() => {
    if (!map) return;
    setDrawn(null); setNote(cur?.review?.note ?? "");
    void map.showReview(cur?.geometry ?? null, cur?.review?.polygon ?? null);
  }, [map, cur?.id]);  // eslint-disable-line react-hooks/exhaustive-deps

  const decide = useCallback(async (decision: string, polygon?: any) => {
    if (!cur || busy) return;
    setBusy(true);
    try {
      await j("/api/v1/validation/reviews", { method: "POST", body: JSON.stringify({ sample_id: cur.id, decision, ...(polygon ? { polygon } : {}), ...(note ? { note } : {}) }) });
      const rev = { decision, polygon: polygon ?? null, t: Date.now() / 1000, note };
      setItems((xs) => xs.map((x) => (x.id === cur.id ? { ...x, review: rev } : x)));
      setProgress(await j("/api/v1/validation/progress").catch(() => progress));
      onToast(`Saved: ${decision.replace("_", " ")}`);
      setI((k) => Math.min(k + 1, items.length - 1));
    } catch { onToast("Couldn't save that review"); }
    setBusy(false);
  }, [cur, busy, note, items.length, progress, onToast]);

  const drawPolygon = useCallback(async () => {
    if (!map || !cur) return;
    onToast("Click the corners, double-click to finish");
    const g = await map.draw("polygon");
    if (g) { setDrawn(g); void map.showReview(cur.geometry, g); }
  }, [map, cur, onToast]);

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (/^(INPUT|TEXTAREA|SELECT)$/.test((e.target as HTMLElement)?.tagName ?? "")) return;
      const m: Record<string, () => void> = { a: () => void decide("accept"), r: () => void decide("reject"), u: () => void decide("uncertain"),
        n: () => void decide("needs_review"), d: () => void drawPolygon(), j: () => setI((x) => Math.min(x + 1, items.length - 1)),
        k: () => setI((x) => Math.max(x - 1, 0)) };
      const f = m[e.key.toLowerCase()];
      if (f && !e.ctrlKey && !e.metaKey) { e.preventDefault(); f(); }
    };
    addEventListener("keydown", k);
    return () => removeEventListener("keydown", k);
  }, [decide, drawPolygon, items.length]);

  const reviewed = progress?.reviewed ?? progress?.total_reviewed;
  const totalAll = progress?.total ?? progress?.samples;
  return (
    <div className="gt-vs" data-testid="validation-studio">
      <div className="gt-vs-head">
        <div style={{ fontSize: 15, fontWeight: 500 }}>Validation Studio</div>
        <div className="gt-srcline">Human review of AI detections · stored separately from training labels</div>
      </div>
      <div className="gt-vs-filters">
        <select value={cls} onChange={(e) => setCls(e.target.value)} aria-label="Class" data-testid="vs-class">
          {CLASSES.map((c) => <option key={c} value={c}>{c ? c[0].toUpperCase() + c.slice(1) : "All classes"}</option>)}</select>
        <select value={region} onChange={(e) => setRegion(e.target.value)} aria-label="Region">
          <option value="">All regions</option>{regions.map((r) => <option key={r} value={r}>{r}</option>)}</select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          <option value="unreviewed">To review</option><option value="reviewed">Reviewed</option><option value="all">All</option></select>
      </div>
      {typeof reviewed === "number" && typeof totalAll === "number" && (
        <div className="gt-vs-progress" data-testid="vs-progress"><div style={{ width: `${totalAll ? (100 * reviewed) / totalAll : 0}%` }} />
          <span>{reviewed} of {totalAll} reviewed</span></div>)}
      {err && <div className="gt-vs-empty">{err}</div>}
      {!err && total === 0 && <div className="gt-vs-empty">Nothing to review with these filters.</div>}
      {cur && (
        <div className="gt-vs-card" data-testid="vs-card" data-sample={cur.id}>
          <div className="gt-vs-nav">
            <button className="gt-icon gt-icon-sm" aria-label="Previous (K)" onClick={() => setI((x) => Math.max(x - 1, 0))} disabled={i === 0}><ChevronLeft size={16} /></button>
            <span>{i + 1} of {items.length}{total && total > items.length ? ` (of ${total})` : ""}</span>
            <button className="gt-icon gt-icon-sm" aria-label="Next (J)" onClick={() => setI((x) => Math.min(x + 1, items.length - 1))} disabled={i >= items.length - 1}><ChevronRight size={16} /></button>
          </div>
          <div className="gt-evgrid">
            <span>Class</span><span>{cur.class}</span>
            <span>Region</span><span>{cur.region}{cur.holdout_group ? ` · ${cur.holdout_group}` : ""}{cur.in_label_cluster ? " · in the training-label cluster" : ""}</span>
            <span>Detector</span><span>{cur.source_tool ?? "—"}{cur.model ? ` · ${cur.model}` : ""}</span>
            <span>Score</span><span>{cur.score != null ? `${Number(cur.score).toFixed(2)} (uncalibrated)` : "—"}</span>
            <span>Imagery</span><span>NAIP 2022{cur.acquisition_date ? ` · flown ${cur.acquisition_date}` : ""}{cur.scene_id ? ` · ${cur.scene_id}` : ""}</span>
            {cur.geometry_kind && <><span>Prediction</span><span>{cur.geometry_kind === "box" ? "a box (no outline yet)" : "an outline"}</span></>}
            {cur.review && <><span>Your review</span><span data-testid="vs-current">{cur.review.decision.replace("_", " ")}{cur.review.polygon ? " · polygon" : ""}</span></>}
          </div>
          <div className="gt-vs-legend"><span className="gt-vs-pred" />AI prediction<span className="gt-vs-human" />Your polygon</div>
          <div className="gt-vs-actions">
            {DECISIONS.map(([d, l, ic]) => (
              <button key={d} className={`gt-pill gt-vs-btn${cur.review?.decision === d ? " gt-vs-on" : ""}`} onClick={() => void decide(d)} disabled={busy}
                data-testid={`vs-${d}`} title={`${l} (${d[0].toUpperCase()})`}>{ic}{l}</button>))}
          </div>
          <div className="gt-vs-actions">
            <button className="gt-pill gt-vs-btn" onClick={() => void drawPolygon()} data-testid="vs-draw" title="Draw or correct the outline (D)"><PenLine size={14} />{drawn ? "Redraw" : "Draw / correct outline"}</button>
            {drawn && <button className="gt-pill gt-dark gt-vs-btn" onClick={() => void decide("corrected", drawn)} data-testid="vs-save-polygon">Save corrected outline</button>}
          </div>
          <input className="gt-input" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" />
          <div className="gt-srcline">Keys: A accept · R reject · U uncertain · N needs review · D draw · J/K next/previous</div>
        </div>)}
      {metrics && (
        <div className="gt-vs-metrics" data-testid="vs-metrics">
          <div style={{ fontWeight: 500, fontSize: 13 }}>Measured from human reviews</div>
          <MetricsTable m={metrics} />
          <div style={{ fontWeight: 500, fontSize: 13 }}>By geographic holdout group</div>
          <HoldoutTable m={metrics} />
          <a className="gt-pill gt-pill-a" href="/api/v1/validation/export?format=geojson" download><Download size={13} />Export labels (GeoJSON)</a>
          <a className="gt-pill gt-pill-a" href="/api/v1/validation/export?format=csv" download><Download size={13} />CSV</a>
        </div>)}
    </div>
  );
}

function MetricsTable({ m }: { m: any }) {
  const rows: [string, any][] = Object.entries(m.classes ?? {});
  if (!rows.length || rows.every(([, v]) => !v.n_reviewed)) return <div className="gt-srcline">No reviews yet: precision appears once samples are reviewed.</div>;
  return (
    <table className="gt-vs-table"><thead><tr><th>Class</th><th>Reviewed</th><th>Precision</th><th>95 % CI</th><th>Recall</th></tr></thead>
      <tbody>{rows.map(([k, v]) => (
        <tr key={k}><td>{k}</td><td>{v.n_reviewed ?? 0} / {v.n_samples ?? "—"}</td><td>{v.precision != null ? Number(v.precision).toFixed(2) : "—"}</td>
          <td>{Array.isArray(v.precision_ci95) ? v.precision_ci95.map((x: number) => Number(x).toFixed(2)).join("–") : "—"}</td>
          <td>{v.recall?.status === "measured" ? Number(v.recall.recall).toFixed(2) : "not measurable"}</td></tr>))}</tbody></table>
  );
}

/** Precision per geographic holdout group: does the detector hold up away from where it was tuned? */
function HoldoutTable({ m }: { m: any }) {
  const rows = Object.entries(m.classes ?? {}).flatMap(([cls, v]: [string, any]) =>
    Object.entries(v.by_holdout_group ?? {}).map(([g, b]: [string, any]) => ({ cls, g, b })));
  if (!rows.length) return null;
  return (
    <table className="gt-vs-table" data-testid="vs-holdout"><thead><tr><th>Class</th><th>Holdout group</th><th>Reviewed</th><th>Precision</th><th>95 % CI</th></tr></thead>
      <tbody>{rows.map(({ cls, g, b }) => (
        <tr key={cls + g}><td>{cls}</td><td>{g === "None" ? "—" : g}</td><td>{b.n_reviewed ?? 0} / {b.n_samples ?? "—"}</td>
          <td>{b.precision != null ? Number(b.precision).toFixed(2) : "not measured"}</td>
          <td>{Array.isArray(b.precision_ci95) ? b.precision_ci95.map((x: number) => Number(x).toFixed(2)).join("–") : "—"}</td></tr>))}</tbody></table>
  );
}
