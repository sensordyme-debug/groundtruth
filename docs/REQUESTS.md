# Requests for Monish

## 2026-11-07 15:20 ET — Publish HTTP routes and fixtures

What: Add the API base URL, POST paths, request schemas, and one canonical fixture per implemented tool to `docs/CONTRACT.md` and `docs/fixtures/`.

Why: The MCP server and agent now follow the listed tool argument names, but the contract does not define HTTP routes. Phase 2 live calls cannot be verified against the current repository.

Workaround meanwhile: Use `GROUNDTRUTH_MOCK=1` and local illustrative fixtures under `mcp/mock_fixtures/`.

## 2026-11-07 15:20 ET — Define compare years

What: Specify a versioned compare-years tool if year comparison remains in the demo scope.

Why: The agent cannot plan “new since 2020” because no such tool appears in the contract.

Workaround meanwhile: The agent reports that intent as unsupported.
