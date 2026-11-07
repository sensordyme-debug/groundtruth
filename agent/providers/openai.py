"""OpenAI-compatible chat completion adapter, configured entirely by environment."""

from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.request import Request, urlopen


def plan_with_openai(request: str, config: dict[str, Any], step_type: Callable[..., Any], tool_names: set[str]) -> list[Any]:
    base = os.environ["AGENT_LLM_API_URL"].rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError("AGENT_LLM_API_URL must be an HTTP URL")
    schema = {"type": "object", "properties": {"steps": {"type": "array", "maxItems": 8, "items": {"type": "object", "properties": {"tool": {"type": "string", "enum": sorted(tool_names)}, "arguments": {"type": "object"}}, "required": ["tool", "arguments"]}}}, "required": ["steps"]}
    body = {"model": os.getenv("AGENT_LLM_MODEL", "local"), "temperature": 0, "response_format": {"type": "json_schema", "json_schema": {"name": "groundtruth_plan", "strict": False, "schema": schema}}, "messages": [{"role": "system", "content": "Plan Groundtruth geospatial tool calls only. Use pick_candidates for ambiguous concepts. Never invent numbers. Max 8 steps. Return JSON with steps."}, {"role": "user", "content": json.dumps({"request": request, "coverage": config["county"], "tools": sorted(tool_names)})}]}
    headers = {"Content-Type": "application/json"}
    if os.getenv("AGENT_LLM_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['AGENT_LLM_API_KEY']}"
    req = Request(f"{base}/chat/completions", data=json.dumps(body).encode(), headers=headers, method="POST")
    with urlopen(req, timeout=float(os.getenv("AGENT_LLM_TIMEOUT_S", "20"))) as response:
        payload = json.load(response)
    content = payload["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return [step_type(tool=item["tool"], arguments=item["arguments"]) for item in parsed["steps"]]
