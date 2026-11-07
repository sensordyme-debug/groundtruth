"""Small protocol-level client smoke test, also usable outside pytest."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    project = Path(__file__).resolve().parents[2]
    mcp_dir = project / "mcp"
    env = {**os.environ, "GROUNDTRUTH_MOCK": "1"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"], env=env, cwd=mcp_dir)
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {item.name for item in listed.tools}
            expected = {"search_text", "pick_candidates", "find_similar", "outline", "refine", "add_layer", "filter", "measure", "export"}
            assert expected <= names, f"missing tools: {expected - names}"
            cases = {
                "search_text": {"query": "swimming pools"},
                "pick_candidates": {"query": "swimming pools"},
                "find_similar": {"chip_ids": ["pool-1"]},
                "outline": {"chip_ids": ["pool-1"], "concept": "pool"},
                "refine": {"session_id": "demo", "accept_ids": ["pool-1"]},
                "add_layer": {"name": "osm_roads"},
                "filter": {"features": {"type": "FeatureCollection", "features": []}, "where": {}},
                "measure": {"features": {"type": "FeatureCollection", "features": []}, "what": "count"},
                "export": {"features": {"type": "FeatureCollection", "features": []}, "format": "csv"},
            }
            for name, arguments in cases.items():
                result = await session.call_tool(name, arguments)
                assert not result.isError, f"{name}: {result.content}"
                assert result.structuredContent["tool"] == name
                assert result.structuredContent["status"] in {"ok", "partial", "declined"}
                assert result.content[0].text.startswith("status=")
            resources = await session.list_resources()
            assert any(str(item.uri) == "groundtruth://about" for item in resources.resources)
            print(f"OK: {len(cases)} tools called; groundtruth://about listed")


if __name__ == "__main__":
    asyncio.run(main())
