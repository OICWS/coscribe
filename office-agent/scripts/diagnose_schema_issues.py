"""Diagnostic: find every tool schema property that would crash Gemini's
Schema conversion, across the *exact* combined tool list a real session
sends it (built-in/skill tools first, then every configured MCP
connector, same order web/session.py's _build_lg_tools uses) -- not just
one connector in isolation. Broader detection than the first pass: also
catches an `items` key that's *present* but itself underspecified (no
`type`/`$ref`/`anyOf`/`oneOf`/`allOf`/`enum`), not just a missing `items`
key outright -- either shape produces the same google-genai error.

Prints only tool names and property paths, never your token or full
schema values.

Run from the office-agent directory, with your venv activated:

    python scripts/diagnose_schema_issues.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from coscribe.config import Settings
from coscribe.coordinator import build_coordinator_agent
from coscribe.runtime_lg.mcp import connect_mcp_tools_lg


def find_issues(schema: Any, path: str = "$") -> list[str]:
    issues: list[str] = []
    if isinstance(schema, dict):
        schema_type = schema.get("type")
        is_array = schema_type == "array" or (
            isinstance(schema_type, list) and "array" in schema_type
        )
        if is_array:
            items = schema.get("items")
            if items is None:
                issues.append(f"{path}: array with no 'items' key at all")
            elif isinstance(items, dict) and not items:
                issues.append(f"{path}: 'items' is an empty dict")
            elif isinstance(items, dict):
                has_type = "type" in items
                has_alt = any(k in items for k in ("$ref", "anyOf", "oneOf", "allOf", "enum"))
                if not has_type and not has_alt:
                    issues.append(f"{path}: 'items' present but has no type/$ref/anyOf/oneOf/enum")
        enum = schema.get("enum")
        if isinstance(enum, list) and any(isinstance(v, bool) for v in enum):
            issues.append(f"{path}: enum contains boolean value(s)")
        for key, value in schema.items():
            issues.extend(find_issues(value, f"{path}.{key}"))
    elif isinstance(schema, list):
        for i, item in enumerate(schema):
            issues.extend(find_issues(item, f"{path}[{i}]"))
    return issues


def _schema_of(tool: Any) -> Any:
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is None:
        return None
    if isinstance(args_schema, dict):
        return args_schema
    if hasattr(args_schema, "model_json_schema"):
        return args_schema.model_json_schema()
    return None


async def main() -> None:
    config_path = Path("mcp.json")
    settings = Settings(
        default_model="gemini:gemini-3.1-flash-lite",
        mcp_config_path=config_path if config_path.is_file() else None,
    )

    # skill_names=None splices every skill -- broadest possible check,
    # since we don't know which skills a given real session had enabled.
    agent_spec = build_coordinator_agent(settings, "diagnose", skill_names=None)
    print(f"{len(agent_spec.tools)} built-in/skill tool(s).")

    mcp_tools: list[Any] = []
    connections: dict[str, Any] = {}
    if settings.mcp_config_path is not None:
        print("Connecting to every configured MCP server (this can take a moment)...")
        mcp_tools, connections = await connect_mcp_tools_lg(settings.mcp_config_path)
        print(f"{len(mcp_tools)} MCP tool(s) across {len(connections)} server(s).")
    else:
        print("No mcp.json found -- only checking built-in/skill tools.")

    try:
        all_tools = [*agent_spec.tools, *mcp_tools]
        print(f"\n{len(all_tools)} tool(s) total -- scanning...\n")
        any_found = False
        for i, tool in enumerate(all_tools):
            name = getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
            issues = find_issues(_schema_of(tool))
            if issues:
                any_found = True
                print(f"[{i}] {name}")
                for issue in issues:
                    print(f"    {issue}")
        if not any_found:
            print("No issues found in any tool's schema.")
    finally:
        for connection in connections.values():
            await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
