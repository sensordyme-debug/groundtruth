// Groundtruth API client (same engine API as the classic UI).
export type Feature = { type: "Feature"; id?: string | number; geometry: any; properties: Record<string, any> };
export type FC = { type: "FeatureCollection"; features: Feature[] };
export type Envelope = {
  tool: string; status: string; result?: { geojson: FC; metrics: Record<string, any> };
  confidence?: number; method?: string; assumptions?: string[]; limits?: string[];
  sources?: { name: string; url?: string; date?: string; licence?: string }[];
  steps?: { phase: string; detail?: string; ms?: number }[]; declined_reason?: string;
};
export type Step = { ref: string; tool: string; envelope: Envelope };
export type Action = { action: string; envelope_ref?: string; bbox?: number[]; text?: string; places?: string[];
  pills?: string[]; layer_id?: string; format?: string; label?: string };
export type AgentResponse = {
  plan: Record<string, any>; steps: Step[]; actions: Action[]; status: string; brain?: string;
  current_ref?: string; ask?: "pick" | "where"; cached?: boolean; brain_fallback?: string;
};
export type Context = { place?: string | null; last_text?: string | null; picked?: string[]; not?: string[];
  rejected?: string[]; accepted?: string[] };

export async function agentRun(text: string, context: Context, signal?: AbortSignal): Promise<AgentResponse> {
  const r = await fetch("/api/v1/agent/run", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, context }), signal,
  });
  if (!r.ok) throw new Error(`agent: HTTP ${r.status}`);
  return r.json();
}

export async function exportSet(geojson: FC, format: "csv" | "geojson"): Promise<string | null> {
  const r = await fetch("/api/v1/tools/export", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ geojson, format }),
  });
  const env: Envelope = await r.json();
  return env.result?.metrics?.download ?? null;
}

/** A NAIP crop (local imagery) around a feature: a square ~size_m across, centred on the feature. */
export function cropUrl(bbox: number[], year = 2022, px = 256): string {
  return `/api/v1/crop?bbox=${bbox.map((v) => v.toFixed(6)).join(",")}&year=${year}&px=${px}`;
}
