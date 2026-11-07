"""Turn cited envelopes into idempotent map actions."""

from __future__ import annotations

from typing import Any

from .executor import ExecutedStep


def actions_for(request: str, place: tuple[str, dict[str, Any]] | None, results: list[ExecutedStep]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if place:
        actions.append({"action": "fly_to", "bbox": place[1]["bbox"], "label": place[0], "approximate": place[1].get("approximate", False)})
    for item in results:
        ref = item.ref
        if item.envelope["status"] == "declined":
            actions.append({"action": "say", "text": f"Request declined: {item.envelope['declined_reason']}."})
            break
        if item.tool == "pick_candidates":
            actions.append({"action": "show_candidates", "envelope_ref": ref})
        elif item.tool == "add_layer":
            actions.append({"action": "add_layer", "layer_id": item.arguments["name"], "source": "envelope", "envelope_ref": ref, "style": "line"})
        elif item.tool == "search_text":
            actions.append({"action": "add_results", "layer_id": "search_results", "envelope_ref": ref, "style": "outline"})
        elif item.tool == "filter":
            actions.append({"action": "set_filter", "layer_id": "search_results", "envelope_ref": ref})
        elif item.tool == "measure":
            actions.append({"action": "show_metrics", "envelope_ref": ref})
        elif item.tool == "export":
            if (item.envelope.get("result") or {}).get("download_url"):
                actions.append({"action": "offer_download", "envelope_ref": ref, "format": item.arguments["format"]})
            else:
                actions.append({"action": "say", "text": "Export is unavailable in mock mode."})
    if results and results[0].tool == "pick_candidates":
        actions.append({"action": "say", "text": "Choose the image that matches what you mean. Results are illustrative mock data."})
    elif results:
        metric_step = next((item for item in reversed(results) if (item.envelope.get("result") or {}).get("metrics")), None)
        if metric_step:
            metrics = metric_step.envelope["result"]["metrics"]
            parts = []
            for field in ("count", "area_acres"):
                if field in metrics:
                    parts.append(f"{field}={metrics[field]}")
            if parts:
                prefix = "Mock result: " if any("Mock mode" in limit for limit in metric_step.envelope["limits"]) else "Result: "
                actions.append({"action": "say", "text": prefix + ", ".join(parts) + f" (from {metric_step.ref})."})
    return actions
