"""Standalone (not pytest) live verification of runtime_lg/mcp.py's
persistent-session fix (see McpServerConnection's own docstring for the
full write-up of the bug it fixes): MultiServerMCPClient.get_tools() opens
a *fresh* session -- a fresh subprocess -- for every single tool call,
which silently broke any stateful MCP tool. Most visibly reported live:
Playwright browser automation, where a page opened by one
playwright_browser_navigate call was gone by the very next tool call,
because the whole browser process backing it had already been torn down
the instant the first call returned.

Uses a tiny custom stateful MCP stdio server
(_verify_mcp_persistent_session_server.py, next to this script) instead of
a real npm/uvx-installed one -- no network/package-manager dependency, and
the counter it exposes makes "did the same process handle both calls"
directly observable: an increasing sequence (1, 2, 3) proves persistence
across calls; the same value every time proves a fresh process per call.

Run with: python scripts/verify_mcp_persistent_session.py
No LLM API key needed -- doesn't touch any provider, only real MCP
subprocess I/O.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from coscribe.runtime_lg.mcp import McpServerConnection

SERVER_SCRIPT = Path(__file__).parent / "_verify_mcp_persistent_session_server.py"


def _extract_int(tool_result: Any) -> int:
    """A LangChain tool's .ainvoke() returns a list of content blocks
    (LangChain's standard multimodal shape), not the bare return value --
    increment()'s int comes back as [{"type": "text", "text": "1", ...}]."""
    return int(tool_result[0]["text"])


async def call_increment_via_get_tools_per_call(times: int) -> list[int]:
    """The old/buggy path: MultiServerMCPClient.get_tools(), which (per its
    own docstring: "A new session will be created for each tool call")
    opens a fresh session -- a fresh subprocess -- for every single tool
    call. Kept here as a live, side-by-side demonstration of the bug this
    pass fixed, not just asserted from memory."""
    client = MultiServerMCPClient(
        {
            "stateful": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(SERVER_SCRIPT)],
            }
        }
    )
    results = []
    for _ in range(times):
        tools = await client.get_tools(server_name="stateful")
        increment = next(t for t in tools if t.name.endswith("increment"))
        results.append(_extract_int(await increment.ainvoke({})))
    return results


async def call_increment_via_persistent_connection(times: int) -> list[int]:
    """The fixed path: McpServerConnection, one persistent session/
    subprocess held open for as long as the connection stays open --
    exactly what web/app.py now keeps alive in mcp_connections for as
    long as a server is "connected"."""
    connection = McpServerConnection(
        "stateful", {"command": sys.executable, "args": [str(SERVER_SCRIPT)]}
    )
    tools = await connection.connect()
    increment = next(t for t in tools if t.name.endswith("increment"))
    results = [_extract_int(await increment.ainvoke({})) for _ in range(times)]
    await connection.close()
    return results


async def main() -> None:
    checks: list[str] = []

    per_call = await call_increment_via_get_tools_per_call(2)
    if per_call == [1, 1]:
        checks.append(
            "PASS (confirms the bug this pass fixed, for reference): "
            f"get_tools()'s per-call session restarts the server every time -- "
            f"two calls both returned 1, got {per_call}"
        )
    else:
        checks.append(f"FAIL: expected [1, 1] from the per-call path, got {per_call}")

    persistent = await call_increment_via_persistent_connection(3)
    if persistent == [1, 2, 3]:
        checks.append(
            "PASS: McpServerConnection's persistent session keeps the same server "
            f"process alive across calls -- got the increasing sequence {persistent}"
        )
    else:
        checks.append(f"FAIL: expected [1, 2, 3] from the persistent path, got {persistent}")

    print("\n".join(checks))
    if any(check.startswith("FAIL") for check in checks):
        raise SystemExit(1)
    print("\nAll checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
