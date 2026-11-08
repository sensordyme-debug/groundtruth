# agent/ (owner: Donovan)

The AI agent that acts on the map (add layers, filter, measure, export) through the HTTP API (`docs/CONTRACT.md`).
The UI reserves an SSE slot at `/api/v1/events` for its stream. Tests go in `tests/agent/`. See `AGENTS.md`.
