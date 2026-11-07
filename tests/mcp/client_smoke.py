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
            result = await session.call_tool("search_text", {"query": "swimming pools"})
            assert result.structuredContent["tool"] == "search_text"
            assert result.structuredContent["status"] == "ok"
            assert result.content[0].text.startswith("status=ok; count=3")
            print(f"OK: {len(expected)} tools listed; search_text returned {result.structuredContent['status']}")


if __name__ == "__main__":
    asyncio.run(main())
