// Planner response → what the chat shows: one or two plain sentences, "Worked X s · N steps", one result card,
// follow-up pills. Every number comes from the envelopes. Never shows internal words (see the banned list in the docs).
import { AgentResponse, Envelope, Feature } from "./api";

export type StepLine = { label: string; detail: string; ms: string };
export type Card = { title: string; sub: string; count: number | null; features: Feature[]; ref: string; kind: string;
  details: string[] };

const SCRUB: [RegExp, string][] = [[/\bchips?\b/gi, "tiles"], [/\bcells?\b/gi, "patches"], [/\benvelopes?\b/gi, "results"],
  [/\bsenses?\b/gi, "meanings"], [/\(step_\d+\)/g, ""], [/\s+([.;,])/g, "$1"]];

export const scrub = (s: string) => SCRUB.reduce((a, [re, to]) => a.replace(re, to), s).replace(/\s{2,}/g, " ").trim();

export const fmt = (n: number) => n.toLocaleString("en-US");

/** The final set: the step the planner marked current (never an emptied intermediate step). */
export function currentEnv(r: AgentResponse): Envelope | null {
  const s = r.steps.find((x) => x.ref === r.current_ref) ?? r.steps[r.steps.length - 1];
  return s?.envelope ?? null;
}

export function countOf(env: Envelope | null): number | null {
  const m = env?.result?.metrics ?? {};
  const n = m.count_detected ?? m.count ?? m.count_out;
  return typeof n === "number" ? n : null;
}

/** Counts are shown only when exhaustive inside a place (else: "top matches"). */
export function isExhaustive(env: Envelope | null, r: AgentResponse): boolean {
  const m = env?.result?.metrics ?? {};
  if (env?.tool === "find_derived") return true;
  if (m.mode === "exhaustive_scan") return true;
  if (["find_buildings", "find_place"].includes(env?.tool ?? "") && r.plan?.place) return true;
  if (env?.tool === "filter") return !!r.plan?.place;
  return false;
}

export function sayText(r: AgentResponse): string {
  const says = r.actions.filter((a) => a.action === "say").map((a) => a.text ?? "");
  return scrub(says[says.length - 1] ?? "");
}

const LABEL: Record<string, (e: Envelope, r: AgentResponse) => [string, string]> = {
  pick_candidates: (e) => ["Read the request", `found ${e.result?.metrics?.clear_examples ?? 6} kinds that look alike`],
  find_all: (e, r) => {
    const m = e.result?.metrics ?? {};
    if (m.mode === "exhaustive_scan")
      return ["Found every match in the area", m.dense ? "dense scan" : `${fmt(m.chips_in_box ?? 0)} tiles`];
    return ["Scored every tile", r.plan?.place ? "" : "across the county"];
  },
  find_derived: (e) => {
    const m = e.result?.metrics ?? {};
    const req: string[] = m.require ?? [];
    if (m.docks_new_since) return ["Compared each dock", "2020 vs 2022"];
    if (req.includes("has_dock")) return ["Matched docks to houses", "one dock per house"];
    if (req.includes("lakefront")) return ["Kept lakefront houses", "within 100 m, no road between"];
    return ["Matched pools to houses", `${fmt(m.pools_in_place ?? 0)} pools checked`];
  },
  compare_years: () => ["Compared with 2020", ""],
  filter: (e) => ["Kept the ones that fit", `${fmt(e.result?.metrics?.count_out ?? 0)} of ${fmt(e.result?.metrics?.count_in ?? 0)}`],
  add_layer: () => ["Loaded the reference layer", ""],
  measure: () => ["Measured them", ""],
  export: () => ["Prepared the file", ""],
  isochrone: () => ["Mapped travel time", ""],
  find_place: () => ["Looked up places", ""],
  find_buildings: () => ["Looked up buildings", "footprints + heights"],
  find_anything: (e) => ["Looked everywhere for it", `${fmt(e.result?.metrics?.chips_searched ?? 0)} tiles`],
  canopy: () => ["Read tree heights", ""],
};

/** Steps of this answer. A cached answer shows the time it took now (wallMs), its steps without the original timings. */
export function stepLines(r: AgentResponse, wallMs = 0): { lines: StepLine[]; secs: string } {
  let total = 0;
  const lines: StepLine[] = r.steps.map((s) => {
    const ms = (s.envelope.steps ?? []).reduce((a, x) => a + (x.ms ?? 0), 0);
    total += ms;
    const [label, detail] = (LABEL[s.tool] ?? (() => [s.tool.replace(/_/g, " "), ""]))(s.envelope, r);
    return { label, detail: scrub(detail), ms: ms >= 1 && !r.cached ? `${Math.round(ms)} ms` : "" };
  });
  const brain = r.brain?.startsWith("local") ? "Planned by the local model" : "Planned by built-in rules";
  lines.push({ label: brain, detail: r.cached ? "from cache" : "", ms: "" });
  return { lines, secs: Math.max(0.1, (r.cached ? wallMs : total) / 1000).toFixed(1) };
}

const TITLES: Record<string, string> = { swimming_pool: "pools", parking_lot: "parking lots", solar_installation: "solar arrays",
  warehouse: "warehouses", construction_site: "construction sites" };

export function cardFor(r: AgentResponse): Card | null {
  const env = currentEnv(r);
  if (!env?.result || r.ask) return null;
  const feats = env.result.geojson.features.filter((f) => !["dock", "dock_link"].includes(f.properties?.kind));
  if (!feats.length && !countOf(env)) return null;
  const p = r.plan ?? {};
  const exhaustive = isExhaustive(env, r);
  const n = exhaustive ? countOf(env) : null;
  const m = env.result.metrics ?? {};
  let title = "", sub = "";
  const place = p.place ? `${p.place} · NAIP 2022` : "Mecklenburg County · NAIP 2022";
  if (env.tool === "find_derived") {
    const req: string[] = m.require ?? [];
    if (m.docks_new_since) { title = `${fmt(n ?? 0)} docks added since ${m.docks_new_since.year}`; sub = `compared with NAIP ${m.docks_new_since.year}`; }
    else if (req.includes("has_dock")) { title = `${fmt(n ?? 0)} lakefront houses with a pool and a dock`; sub = "within 100 m of the shore · dock in front of the house"; }
    else if (req.includes("lakefront")) { title = `${fmt(n ?? 0)} lakefront houses with a pool`; sub = "within 100 m of the shore"; }
    else { title = `${fmt(n ?? 0)} houses with a pool`; sub = place; }
    if (m.sort === "pool_area") sub = "biggest pools first";
  } else {
    const what = p.target_kind === "concept" ? (TITLES[p.concept] ?? String(p.concept).replace(/_/g, " ")) : String(p.target ?? "results");
    title = n !== null ? `${fmt(n)} ${what}` : `Top matches: ${what}`;
    sub = place;
  }
  const details = [scrub(env.method ?? ""), ...(env.sources ?? []).map((s) => scrub(`${s.name}${s.licence ? ` (${s.licence})` : ""}`))]
    .filter(Boolean);
  return { title, sub, count: n, features: feats, ref: r.current_ref ?? "", kind: env.tool, details };
}

export function pillsFor(r: AgentResponse): string[] {
  const out: string[] = [];
  for (const a of r.actions) {
    if (a.action === "suggest_places") out.push(...(a.places ?? []).map((p) => `in ${p}`));
    if (a.action === "suggest") out.push(...(a.pills ?? []));
  }
  return out;
}

/** A square bbox (EPSG:4326) ~size_m across around a feature's centroid. */
export function bboxAround(f: Feature, sizeM = 70): number[] {
  const c = centroid(f.geometry);
  const dLat = sizeM / 2 / 111320, dLon = sizeM / 2 / (111320 * Math.cos((c[1] * Math.PI) / 180));
  return [c[0] - dLon, c[1] - dLat, c[0] + dLon, c[1] + dLat];
}

export function centroid(g: any): [number, number] {
  const pts: number[][] = [];
  const walk = (a: any) => { if (typeof a[0] === "number") pts.push(a); else a.forEach(walk); };
  walk(g.type === "Point" ? [g.coordinates] : g.coordinates);
  if (!pts.length) return [0, 0];
  // relative to the first vertex (exact enough at house scale, no lon/lat skew)
  const [x0, y0] = pts[0];
  let sx = 0, sy = 0;
  for (const [x, y] of pts) { sx += x - x0; sy += y - y0; }
  return [x0 + sx / pts.length, y0 + sy / pts.length];
}

export const featId = (f: Feature) => String(f.properties?.id ?? f.properties?.example ?? f.id ?? "");
