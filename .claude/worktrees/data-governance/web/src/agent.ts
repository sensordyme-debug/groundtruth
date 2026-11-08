// Agent slot. The server's deterministic rules planner (POST /api/v1/agent/run, no LLM) runs the tool calls and returns
// `steps` (step_N envelopes) plus map actions per this schema; the UI applies them through `AgentRun.apply`.
import { AgentResponse, Bbox, Envelope, agentRun, publishEnvelope } from "./api";

export type MapAction =
  | { action: "fly_to"; bbox: Bbox; label?: string; approximate?: boolean }
  | { action: "add_layer"; layer_id: string; source?: "envelope"; envelope_ref: string; style?: Record<string, any> }
  | { action: "show_candidates" | "show_metrics" | "offer_download"; envelope_ref: string; format?: string }
  | { action: "add_results"; layer_id: string; envelope_ref: string; style?: Record<string, any> }
  | { action: "set_filter"; layer_id: string; envelope_ref: string }
  | { action: "say"; text: string };

/** The UI surface the adapter drives. App.tsx implements it. */
export interface AgentTarget {
  flyTo(bbox: Bbox, label?: string, approximate?: boolean): void;
  addLayer(layerId: string, env: Envelope, style?: Record<string, any>): void;
  showCandidates(env: Envelope): void;
  showMetrics(env: Envelope): void;
  offerDownload(env: Envelope, format?: string): void;
  addResults(layerId: string, env: Envelope, style?: Record<string, any>): void;
  setFilter(layerId: string, env: Envelope): void;
  say(text: string): void;
}

export class AgentRun {
  envelopes: Record<string, Envelope> = {};
  constructor(private target: AgentTarget) {}

  ref(r: string | undefined): Envelope {
    const env = r ? this.envelopes[r] : undefined;
    if (!env) throw new Error(`unknown envelope_ref "${r}" (have ${Object.keys(this.envelopes).join(", ") || "no steps"})`);
    return env;
  }

  apply(a: MapAction) {
    const t = this.target;
    switch (a.action) {
      case "fly_to": return t.flyTo(a.bbox, a.label, a.approximate);
      case "add_layer": return t.addLayer(a.layer_id, this.ref(a.envelope_ref), a.style);
      case "show_candidates": return t.showCandidates(this.ref(a.envelope_ref));
      case "show_metrics": return t.showMetrics(this.ref(a.envelope_ref));
      case "offer_download": return t.offerDownload(this.ref(a.envelope_ref), a.format);
      case "add_results": return t.addResults(a.layer_id, this.ref(a.envelope_ref), a.style);
      case "set_filter": return t.setFilter(a.layer_id, this.ref(a.envelope_ref));
      case "say": return t.say(a.text);
      default: throw new Error(`unknown action ${(a as any).action}`);
    }
  }
}

/** Run the rules planner for `text`, publish every step envelope to the work log (origin "agent"), then apply the
 *  actions in order. Returns the raw response; per-action errors go to `onError` and do not stop later actions. */
export async function runRulesPlanner(text: string, target: AgentTarget,
  opts: { aoi?: string; context?: { place?: string | null; last_text?: string | null }; onError?: (msg: string) => void;
          onResponse?: (res: AgentResponse) => void } = {}): Promise<AgentResponse> {
  const res = await agentRun(text, opts.aoi, opts.context);
  opts.onResponse?.(res);
  const run = new AgentRun(target);
  for (const s of res.steps ?? []) {
    run.envelopes[s.ref] = s.envelope;
    publishEnvelope(s.tool, s.envelope, "agent", { _ref: s.ref });
  }
  for (const a of res.actions ?? []) {
    try { run.apply(a as MapAction); }
    catch (e) { opts.onError?.(String(e)); }
  }
  return res;
}
