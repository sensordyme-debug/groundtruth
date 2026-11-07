# Groundtruth map agent

The agent plans up to eight Groundtruth tool calls, executes them against the HTTP API or local mock fixtures, and emits map actions as JSON. Its mock results are illustrative and are labelled as such in the output. Places and approximate bounding boxes live in `config/mecklenburg.yaml`; planner logic has no Mecklenburg-specific place names.

Run from the repository root:

```bash
GROUNDTRUTH_MOCK=1 AGENT_PLANNER=rules python3 -m agent.cli "find pools"
GROUNDTRUTH_MOCK=1 AGENT_PLANNER=rules python3 -m agent.cli "find pools near I-485 in Ballantyne"
GROUNDTRUTH_MOCK=1 AGENT_PLANNER=rules python3 -m agent.cli "how many acres of parking lots in Uptown"
GROUNDTRUTH_MOCK=1 AGENT_PLANNER=rules python3 -m agent.cli "export them as CSV"
```

`AGENT_PLANNER=auto` (default) uses an OpenAI-compatible chat-completions API when `AGENT_LLM_API_URL` is set, otherwise rules. Set `AGENT_LLM_MODEL`, `AGENT_LLM_API_KEY`, and `AGENT_LLM_TIMEOUT_S` as needed. `AGENT_PLANNER=llm` requires the LLM URL. The LLM adapter requests a JSON plan at temperature 0. Tool names and argument names follow `docs/CONTRACT.md`; HTTP routes remain unconfirmed.

`GROUNDTRUTH_MOCK=1` reads `mcp/mock_fixtures`. For live execution set `GROUNDTRUTH_API_URL`; the provisional route is `/{tool_name}` and must be confirmed before use. The agent declines people-data and school-district requests before planning. The mock agent's follow-up export uses an empty placeholder collection; live result handoff is still pending.

Run the offline tests from the repository root with `PYTHONPATH=. mcp/.venv/bin/pytest tests/mcp tests/agent -q`. Live dispatch requires endpoint and result handoff work before a real demo.
