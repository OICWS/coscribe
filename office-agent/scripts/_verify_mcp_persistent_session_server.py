"""Standalone stateful MCP stdio server -- a helper process for
scripts/verify_mcp_persistent_session.py, not meant to be run directly.

Exposes one tool, increment(), that adds 1 to an in-process counter and
returns the new value. Whether repeated calls return an increasing
sequence (1, 2, 3, ...) or the same value (1, 1, 1, ...) every time
depends entirely on whether the calling MCP client keeps this process
alive across calls (a persistent session) or restarts it fresh each time
(a per-call session -- the bug runtime_lg/mcp.py's McpServerConnection
fixes; see that module's docstring).
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stateful-test-server")
_counter = 0


@mcp.tool()
def increment() -> int:
    """Add 1 to an in-process counter and return the new value."""
    global _counter
    _counter += 1
    return _counter


if __name__ == "__main__":
    mcp.run(transport="stdio")
