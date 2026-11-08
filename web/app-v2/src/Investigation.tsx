// One investigation in the conversation: what it understood → the plan (real step states from the backend, polled)
// → findings graded by their weakest evidence, each opening its evidence → cross-checks → next steps grounded in
// what the data supports. Nothing here is generated in the browser: every line comes from the investigation record.
import { useState } from "react";
import { AlertTriangle, Check, ChevronRight, CircleDashed, Download, Loader2, MapPin, Minus, X } from "lucide-react";
import type { Feature } from "./api";

export type InvStep = { id: string; op: string; label: string; why: string; status: string; ms: number | null; detail: string;
  error: string | null; count: number | null; depends_on: string[] };
export type InvEvidence = { id: string; step_id: string; kind: string; statement: string; source: Record<string, string>; method: string;
  measurement: Record<string, any>; feature_ids: string[]; strength: string; confidence_note: string; limitations: string[] };
export type InvFinding = { id: string; statement: string; nature: string; strength: string; why_strength: string; evidence_ids: string[];
  feature_ids: string[] };
export type Inv = { id: string; status: string; objective: string; question: string | null; suggestions: string[]; summary: string;
  plan: { scope: Record<string, any> | null; targets: string[]; constraints: string[]; steps: InvStep[]; limitations: string[];
    questions: string[]; planner: string } | null;
  events: { t: number; step: string | null; kind: string; text: string }[]; evidence: InvEvidence[]; findings: InvFinding[];
  contradictions: string[]; result: { features: Feature[]; metrics: Record<string, any>; count?: number } | null;
  declined_reason: string | null; created: number; updated: number };

const STRENGTH: Record<string, [string, string]> = {
  strong: ["Strong", "computed from a published layer or a measured source"],
  moderate: ["Moderate", "measured, with stated limits"],
  weak: ["Weak", "relies on a detector whose precision isn't measured for this data"],
  insufficient: ["Gap", "the data needed is missing"],
};
const NATURE: Record<string, string> = { computed: "Computed", model_prediction: "Model prediction", observed: "Observed",
  inference: "Inference", gap: "Data gap" };
const I = { size: 14, strokeWidth: 1.75 } as const;

export function StepIcon({ s }: { s: string }) {
  if (s === "running") return <Loader2 {...I} className="gt-spin" aria-label="Running" />;
  if (s === "done") return <Check {...I} aria-label="Done" />;
  if (s === "failed") return <X {...I} aria-label="Failed" />;
  if (s === "blocked" || s === "skipped") return <Minus {...I} aria-label={s === "blocked" ? "Blocked" : "Skipped"} />;
  return <CircleDashed {...I} aria-label="Queued" />;
}

export function Bars({ s }: { s: string }) {
  const n = s === "strong" ? 3 : s === "moderate" ? 2 : s === "weak" ? 1 : 0;
  return <span className={`gt-bars gt-bars-${s}`} aria-hidden="true"><i className={n >= 1 ? "on" : ""} /><i className={n >= 2 ? "on" : ""} /><i className={n >= 3 ? "on" : ""} /></span>;
}

type Props = { inv: Inv; latest: boolean; busy: boolean; fmtU: (s: string) => string;
  onShow: (ids: string[], label: string) => void; onFollow: (text: string) => void; onOpenEvidence: (e: InvEvidence) => void };

export default function InvestigationBlock({ inv, latest, busy, fmtU, onShow, onFollow, onOpenEvidence }: Props) {
  const running = inv.status === "running" || inv.status === "planning";
  const [planOpen, setPlanOpen] = useState<boolean | null>(null);
  const [openF, setOpenF] = useState<string | null>(null);
  const steps = inv.plan?.steps ?? [];
  const doneMs = steps.reduce((a, s) => a + (s.ms ?? 0), 0);
  const showPlan = planOpen ?? running;
  const evMap = Object.fromEntries(inv.evidence.map((e) => [e.id, e]));
  const cov = inv.evidence.find((e) => e.kind === "coverage");
  const next = nextSteps(inv);

  if (inv.status === "declined") return <p className="gt-reply" data-testid="reply">{inv.declined_reason}</p>;
  if (inv.status === "needs_input") return (
    <div className="gt-invblock" data-testid="investigation">
      <p className="gt-reply" data-testid="reply">{inv.question}</p>
      {latest && !busy && inv.suggestions.length > 0 && (
        <div className="gt-pillrow">{inv.suggestions.map((s) => <button key={s} className="gt-pill" onClick={() => onFollow(`${inv.objective} ${s}`)}>{s}</button>)}</div>)}
    </div>);

  return (
    <div className="gt-invblock" data-testid="investigation" data-status={inv.status}>
      {inv.plan && (
        <div className="gt-understood">
          <Row k="Area" v={inv.plan.scope?.label ?? "Previous results"} />
          {inv.plan.targets.length > 0 && <Row k="Looking for" v={inv.plan.targets.join(", ")} />}
          {inv.plan.constraints.length > 0 && <Row k="Conditions" v={inv.plan.constraints.join("; ")} />}
          {cov && <Row k="Data here" v={Object.entries(cov.measurement).map(([k, v]) => `${k} ${v ? "✓" : "✕"}`).join(" · ")} />}
        </div>)}

      <div className="gt-plan" data-testid="plan">
        <button className="gt-planhead" onClick={() => setPlanOpen(!showPlan)} aria-expanded={showPlan}>
          <ChevronRight size={13} strokeWidth={1.75} style={{ transform: `rotate(${showPlan ? 90 : 0}deg)`, transition: "transform .18s ease" }} />
          {running ? <span>Investigating · step {Math.min(steps.filter((s) => s.status !== "queued").length, steps.length)} of {steps.length}</span>
            : <span>Ran {steps.filter((s) => s.status === "done").length} of {steps.length} steps · {(doneMs / 1000).toFixed(1)} s</span>}
        </button>
        {showPlan && (
          <ol className="gt-steps">
            {steps.map((s) => (
              <li key={s.id} className={`gt-step gt-step-${s.status}`} data-testid="plan-step" data-status={s.status}>
                <span className="gt-step-ic"><StepIcon s={s.status} /></span>
                <span className="gt-step-body">
                  <span className="gt-step-label">{s.label}</span>
                  <span className="gt-step-detail">{fmtU(s.error ?? s.detail) || s.why}</span>
                </span>
                <span className="gt-step-ms">{s.ms != null && s.status !== "queued" ? `${Math.round(s.ms)} ms` : ""}</span>
              </li>))}
          </ol>)}
      </div>

      {!running && inv.summary && <p className="gt-reply" data-testid="reply">{fmtU(inv.summary)}</p>}

      {!running && inv.findings.length > 0 && (
        <div className="gt-findings" data-testid="findings">
          {inv.findings.map((f) => {
            const open = openF === f.id;
            return (
              <div key={f.id} className={`gt-finding gt-finding-${f.strength}`} data-testid="finding">
                <button className="gt-finding-head" onClick={() => setOpenF(open ? null : f.id)} aria-expanded={open}>
                  <Bars s={f.strength} />
                  <span className="gt-finding-text">{fmtU(f.statement)}</span>
                  <ChevronRight size={13} strokeWidth={1.75} style={{ flex: "none", color: "#8b8b88", transform: `rotate(${open ? 90 : 0}deg)`, transition: "transform .18s ease" }} />
                </button>
                {open && (
                  <div className="gt-finding-body gt-in">
                    <div className="gt-finding-meta">{NATURE[f.nature] ?? f.nature} · {STRENGTH[f.strength]?.[0]}: {f.why_strength}</div>
                    {f.evidence_ids.map((id) => evMap[id]).filter(Boolean).map((e) => (
                      <button key={e.id} className="gt-evrow" onClick={() => onOpenEvidence(e)} data-testid="evidence-row">
                        <span className="gt-evid">{e.id}</span>
                        <span className="gt-evtext">{fmtU(e.statement)}<span className="gt-evsrc">{e.source?.name}{e.source?.date ? ` · ${e.source.date}` : ""}</span></span>
                      </button>))}
                    {f.feature_ids.length > 0 && (
                      <button className="gt-ghost" style={{ marginLeft: -7, color: "#191919", display: "flex", alignItems: "center", gap: 6 }}
                        onClick={() => onShow(f.feature_ids, f.statement)}><MapPin size={13} strokeWidth={1.75} />Show these {f.feature_ids.length} on the map</button>)}
                  </div>)}
              </div>);
          })}
        </div>)}

      {!running && inv.contradictions.length > 0 && (
        <div className="gt-cross" data-testid="crosschecks">
          {inv.contradictions.map((c, k) => <div key={k}><AlertTriangle size={13} strokeWidth={1.75} />{c}</div>)}
        </div>)}

      {!running && (
        <div className="gt-pillrow">
          {latest && !busy && next.map((n) => <button key={n} className="gt-pill" onClick={() => onFollow(n)}>{n}</button>)}
          {inv.plan && <a className="gt-pill gt-pill-a" href={`/api/v1/investigations/${inv.id}/report.md`} download data-testid="report-link">
            <Download size={13} strokeWidth={1.75} />Report</a>}
        </div>)}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return <div className="gt-urow"><span>{k}</span><span>{v}</span></div>;
}

/** Follow-ups the data supports right now (no generic suggestions). */
function nextSteps(inv: Inv): string[] {
  const out: string[] = [];
  const cons = (inv.plan?.constraints ?? []).join(" ");
  const ev = inv.evidence;
  const elev = ev.find((e) => e.measurement && e.measurement.known !== undefined);
  const cov = ev.find((e) => e.kind === "coverage")?.measurement ?? {};
  const n = inv.result?.count ?? inv.result?.features?.length ?? 0;
  if (!n) return out;
  if (!/flood zone/.test(cons) && cov["flood maps"]) out.push("which of those are in the 100-year flood zone");
  if (elev && elev.measurement.known > 0 && !/base flood elevation/.test(cons)) out.push("which of those are less than 1 m above the flood elevation");
  if (!/shore/.test(cons) && ev.some((e) => e.measurement?.median_shore_dist_m !== undefined)) out.push("which of those are within 30 m of the shore");
  return out.slice(0, 3);
}
