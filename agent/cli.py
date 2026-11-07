"""Print a bounded Groundtruth plan, execution record, and map actions."""

from __future__ import annotations

import json
import sys

from .actions import actions_for
from .executor import execute
from .planner import load_config, place_for, plan
from .policy import declined_reason


def run(request: str) -> dict:
    reason = declined_reason(request)
    if reason:
        return {"request": request, "status": "declined", "declined_reason": reason, "plan": [], "steps": [], "map_actions": [{"action": "say", "text": "Groundtruth does not provide data about people, property owners, or school districts."}]}
    config = load_config()
    steps = plan(request, config)
    executed = execute(steps)
    actions = actions_for(request, place_for(request, config), executed)
    return {"request": request, "status": "mock" if any("Mock mode" in limit for item in executed for limit in item.envelope["limits"]) else "ok", "plan": [{"tool": step.tool, "arguments": step.arguments} for step in steps], "steps": [{"ref": item.ref, "tool": item.tool, "ms": item.ms, "envelope": item.envelope} for item in executed], "map_actions": actions}


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python -m agent.cli "find pools"')
    try:
        print(json.dumps(run(" ".join(sys.argv[1:])), indent=2))
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
