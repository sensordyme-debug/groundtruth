// Devin-style work log: every tool call (UI or rules-planner agent step) with its envelope: status, confidence, steps (phase · ms),
// method, assumptions and limits. Limits are shown as text, never hidden behind a click, for the latest calls.
import { useEffect, useState } from "react";
import { CallEvent, TimedEnvelope, onCall } from "./api";

type Entry = {
  id: number; tool: string; origin: "ui" | "agent"; phase: CallEvent["phase"]; env?: TimedEnvelope; error?: string;
  t: number; repeats: number; totalMs: number; summary: string;
};

function summarise(tool: string, body: Record<string, any>): string {
  if (body._ref) return `agent ${body._ref}`;
  if (body.query) return `“${body.query}”`;
  if (tool === "outline") return `${(body.examples ?? []).length} cells`;
  if (body.name) return String(body.name);
  if (tool === "find_all" || tool === "refine") {
    const bits = [];
    if (body.examples) bits.push(`${body.examples.length} example${body.examples.length === 1 ? "" : "s"}`);
    if (body.accept) bits.push(`+${body.accept.length}`);
    if (body.reject?.length || body.exclude?.length) bits.push(`−${(body.reject ?? body.exclude).length}`);
    if (body.place) bits.push(`in ${body.place}`);
    return bits.join(" · ");
  }
  if (tool === "filter") return JSON.stringify(body.where ?? {}).replace(/"/g, "").slice(0, 80);
  if (tool === "export") return String(body.format ?? "");
  if (tool === "measure") return (body.to_layers ?? []).join(", ");
  return "";
}

const STATUS_TEXT: Record<string, string> = { ok: "ok", partial: "partial", declined: "declined" };

export default function WorkLog({ compact }: { compact?: boolean }) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [open, setOpen] = useState<Set<number>>(new Set());

  useEffect(() => onCall((e) => setEntries((list) => {
    const idx = list.findIndex((x) => x.id === e.id);
    if (idx >= 0) {
      const cur = list[idx];
      const next: Entry = { ...cur, phase: e.phase, env: e.env, error: e.error, totalMs: cur.totalMs + (e.env?.wallMs ?? 0) };
      // coalesce repeated outline polls into the previous outline entry
      const prev = list[idx + 1];
      if (e.phase === "done" && cur.tool === "outline" && prev && prev.tool === "outline" && prev.phase === "done" && prev.origin === cur.origin) {
        const merged: Entry = { ...prev, env: e.env, repeats: prev.repeats + 1, totalMs: prev.totalMs + (e.env?.wallMs ?? 0), t: e.t };
        return [merged, ...list.filter((x) => x.id !== cur.id && x.id !== prev.id)].slice(0, 80);
      }
      const copy = list.slice(); copy[idx] = next; return copy;
    }
    return [{ id: e.id, tool: e.tool, origin: e.origin, phase: e.phase, env: e.env, error: e.error, t: e.t, repeats: 1,
      totalMs: e.env?.wallMs ?? 0,
      summary: summarise(e.tool, e.body) }, ...list].slice(0, 80);
  })), []);

  if (!entries.length)
    return <div className="log-empty">Every tool call lands here with its steps, timings, method and limits — nothing is hidden.</div>;

  return (
    <div className={`log ${compact ? "compact" : ""}`}>
      {entries.map((l, i) => {
        const env = l.env;
        const expanded = i < 3 || open.has(l.id);
        return (
          <div className={`entry ${l.phase}`} key={l.id}>
            <button className="entry-head" onClick={() => setOpen((s) => { const n = new Set(s); n.has(l.id) ? n.delete(l.id) : n.add(l.id); return n; })}>
              <span className="tname">{l.tool}{l.repeats > 1 && <span className="rep"> ×{l.repeats}</span>}</span>
              {l.origin === "agent" && <span className="tag planner">rules planner</span>}
              {l.phase === "start" && <span className="tag running">running</span>}
              {l.phase === "error" && <span className="tag declined">error</span>}
              {env && <span className={`tag ${env.status}`}>{STATUS_TEXT[env.status]}</span>}
              {env && <span className="tag envconf" title="envelope confidence (per-tool heuristic, explained in method)">
                {env.confidence == null ? "conf —" : `conf ${env.confidence.toFixed(2)}`}</span>}
              <span className="ms">{l.phase === "start" ? "…" : `${l.repeats > 1 ? l.totalMs : env?.wallMs ?? 0} ms`}</span>
            </button>
            {l.summary && <div className="entry-sum">{l.summary}</div>}
            {l.error && <div className="entry-err">{l.error}</div>}
            {env?.declined_reason && <div className="entry-err">Declined: {env.declined_reason}</div>}
            {env && expanded && (
              <>
                {env.steps.length > 0 && (
                  <div className="steps">
                    {env.steps.map((s, j) => (
                      <div className="step" key={j} title={s.detail}>
                        <span className="ph">{s.phase}</span><span className="dt">{s.detail}</span><span className="sm">{Math.round(s.ms)} ms</span>
                      </div>))}
                  </div>)}
                <div className="kv">
                  <span>confidence</span><b>{env.confidence == null ? "not stated" : env.confidence.toFixed(2)}</b>
                </div>
                {env.method && <div className="method"><span>method</span>{env.method}</div>}
                {env.assumptions.length > 0 && <div className="method"><span>assumes</span>{env.assumptions.join(" · ")}</div>}
                {env.limits.length > 0 && (
                  <ul className="limits">{env.limits.map((x, j) => <li key={j}>{x}</li>)}</ul>)}
              </>)}
            {env && !expanded && env.limits.length > 0 && <div className="collapsed-note">{env.steps.length} step{env.steps.length === 1 ? "" : "s"} · {env.limits.length} limit{env.limits.length === 1 ? "" : "s"} — tap to show</div>}
          </div>);
      })}
    </div>
  );
}
