"""Chat brain for /agent/run: policy → plan (local LLM, else rules) → deterministic executor → reply (LLM, number-guarded).

The response says which brain answered (``brain``) for the UI footnote: "local LLM (Qwen3-4B, llama.cpp)" or
"rules planner (deterministic)". LOCAL_LLM=0 disables the LLM (rules only).
"""

from __future__ import annotations

import os
import re
from typing import Any

from groundtruth.planner import EXPORT_RE, PLANNER, run as rules_run
from groundtruth.tools import _people


def chat(engine_for, text: str, context: dict[str, Any] | None) -> dict[str, Any]:
    context = dict(context or {})
    # 1. policy BEFORE any model call: people data never reaches the LLM
    # the previous request (client-supplied) is checked too: people text never reaches the model through last_text
    if context.get("last_text") and _people(str(context["last_text"])):
        context["last_text"] = None
    if _people(text) or EXPORT_RE.search(text) or os.environ.get("LOCAL_LLM", "1") != "1":
        out = rules_run(engine_for, text, context)
        out["brain"] = PLANNER
        return out
    from api.planner.llm import local

    plan, pinfo = local.llm_plan(text, context.get("last_text"))
    if plan is None:  # unreachable / timeout / invalid → rules (stated in the footnote)
        out = rules_run(engine_for, text, context)
        out["brain"] = PLANNER
        out["brain_fallback"] = pinfo.get("error") or "invalid plan"
        out["llm_plan_ms"] = pinfo.get("ms")
        return out
    p, hnotes = local.hybrid_plan(plan, text, context.get("last_text"))
    if not p.target_kind and context.get("last_text"):
        # the model saw no target: treat as a follow-up of the previous request through the rules path
        out = rules_run(engine_for, text, context)
        out["brain"] = PLANNER
        out["brain_fallback"] = "no target in the LLM plan"
        return out
    out = rules_run(engine_for, text, context, plan_override=p, follow_up=bool(plan.get("follow_up")))
    out["brain"] = local.BRAIN
    out["llm_plan"] = plan
    out["hybrid_notes"] = hnotes
    out["llm_plan_ms"] = pinfo.get("ms")
    out["think_leak"] = bool(pinfo.get("think_leak"))
    # 4. reply: rewrite the template answer; the number guard keeps every number tied to an envelope
    says = [a for a in out["actions"] if a.get("action") == "say"]
    # the hero chain (derived) and questions back to the user keep their exact template wording
    if says and out.get("status") == "ok" and not out.get("cached_reply") and not out.get("ask") \
            and (out.get("plan") or {}).get("target_kind") != "derived":
        tmpl = says[-1]["text"]
        reply, rinfo = local.llm_reply(tmpl, out["steps"])
        refs = " ".join(sorted(set(re.findall(r"\(step_\d+\)", tmpl))))
        says[-1]["text"] = reply if reply == tmpl else (reply + (f" {refs}" if refs else ""))
        out["llm_reply_ms"] = rinfo.get("ms")
        out["reply_guard"] = rinfo.get("guard") or rinfo.get("error") or "ok"
        out["think_leak"] = out["think_leak"] or bool(rinfo.get("think_leak"))
    return out
