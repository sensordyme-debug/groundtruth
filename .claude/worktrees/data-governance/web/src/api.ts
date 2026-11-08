// Thin client for the Groundtruth API (docs/CONTRACT.md). Every tool returns an Envelope.
// Every tool call is published on a tiny bus so the work log sees UI calls and agent (rules planner) steps alike.

export type Step = { phase: string; detail: string; ms: number };
export type Feature = {
  type: "Feature";
  id?: string | number;
  geometry: GeoJSON.Geometry | null;
  properties: Record<string, any>;
};
export type FC = { type: "FeatureCollection"; features: Feature[] };
export type Envelope = {
  tool: string;
  version: string;
  status: "ok" | "partial" | "declined";
  result: { geojson: FC; metrics: Record<string, any> };
  confidence: number | null;
  method: string;
  assumptions: string[];
  limits: string[];
  sources: { name: string; url: string; date: string; licence: string }[];
  steps: Step[];
  declined_reason: string | null;
};
export type TimedEnvelope = Envelope & { wallMs: number };
export type IndexInfo = { aoi: string; year: number; chips: number; index_version: string };
export type Health = {
  ok: boolean;
  contract: string;
  engine_version: string;
  primary_year: number;
  indexes: IndexInfo[];
};
export type Bbox = [number, number, number, number];
export type Places = { source: string; places: Record<string, Bbox> };

const BASE = "/api/v1";

export async function health(): Promise<Health> {
  const r = await fetch(`${BASE}/health`);
  if (!r.ok) throw new Error(`health ${r.status}`);
  return r.json();
}

export async function places(): Promise<Places> {
  const r = await fetch(`${BASE}/places`);
  if (!r.ok) throw new Error(`places ${r.status}`);
  return r.json();
}

// ---------------- call bus (work log) ----------------
export type CallEvent = {
  id: number;
  tool: string;
  origin: "ui" | "agent";
  body: Record<string, unknown>;
  phase: "start" | "done" | "error";
  env?: TimedEnvelope;
  error?: string;
  t: number;
};
type Listener = (e: CallEvent) => void;
const listeners = new Set<Listener>();
let nextId = 0;
export function onCall(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
const emit = (e: CallEvent) => listeners.forEach((l) => l(e));

export async function tool(name: string, body: Record<string, unknown>, origin: "ui" | "agent" = "ui"): Promise<TimedEnvelope> {
  const id = ++nextId;
  const t0 = performance.now();
  emit({ id, tool: name, origin, body, phase: "start", t: Date.now() });
  try {
    const r = await fetch(`${BASE}/tools/${name}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${name}: HTTP ${r.status} ${(await r.text()).slice(0, 240)}`);
    const env = (await r.json()) as Envelope;
    const timed = { ...env, wallMs: Math.round(performance.now() - t0) };
    emit({ id, tool: name, origin, body, phase: "done", env: timed, t: Date.now() });
    return timed;
  } catch (e) {
    emit({ id, tool: name, origin, body, phase: "error", error: String(e), t: Date.now() });
    throw e;
  }
}

export const exampleOf = (f: Feature): string =>
  (f.properties.example as string) ?? `${f.properties.chip_id ?? f.id}#${f.properties.cell}`;

export function boxPoly(b: number[]): GeoJSON.Polygon {
  const [w, s, e, n] = b;
  return { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] };
}

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// thumb_url from the API has no ?aoi=, and /chips/{id}.jpg falls back to the default (largest) index, so chips from
// another slice 404. Append the active aoi client-side until the API includes it.
let thumbAoi = "";
export const setThumbAoi = (a: string) => { thumbAoi = a; };
export function thumb(url: string | undefined): string {
  if (!url) return "";
  if (!thumbAoi || url.includes("aoi=")) return url;
  return `${url}${url.includes("?") ? "&" : "?"}aoi=${encodeURIComponent(thumbAoi)}`;
}

/** Publish an envelope produced elsewhere (e.g. an agent step) on the call bus so the work log shows it. */
export function publishEnvelope(name: string, env: Envelope, origin: "ui" | "agent", body: Record<string, unknown> = {}) {
  const wallMs = Math.round(env.steps.reduce((s, x) => s + (Number(x.ms) || 0), 0));
  emit({ id: ++nextId, tool: name, origin, body, phase: "done", env: { ...env, wallMs }, t: Date.now() });
}

// ---------------- agent (rules planner) ----------------
export type AgentStep = { ref: string; tool: string; envelope: Envelope };
export type AgentResponse = {
  planner: string;
  plan: Record<string, any>;
  status: "ok" | "partial" | "declined";
  steps: AgentStep[];
  actions: Record<string, any>[];
};

/** POST /api/v1/agent/run: the server-side deterministic rules planner (no LLM). */
export async function agentRun(text: string, aoi?: string, context?: { place?: string | null; last_text?: string | null }): Promise<AgentResponse> {
  const r = await fetch(`${BASE}/agent/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, ...(aoi ? { aoi } : {}), ...(context ? { context } : {}) }),
  });
  if (!r.ok) throw new Error(`agent/run: HTTP ${r.status} ${(await r.text()).slice(0, 240)}`);
  return r.json();
}
