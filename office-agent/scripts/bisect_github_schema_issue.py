"""Pinpoint exactly which github tool's schema google-genai rejects, by
binary search: connect to github for real, split its tool list in half,
make a real (cheap, tiny) Gemini call with each half bound, and recurse
into whichever half fails -- ~6 real API calls to narrow 44 tools down to
the exact one, using the real API's own validation instead of guessing at
schema patterns (which came back clean twice already).

Run from the office-agent directory, with your venv activated and
GEMINI_API_KEY set:

    python scripts/bisect_github_schema_issue.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv

from coscribe.cli import _dotenv_path
from coscribe.runtime_lg import resolve_chat_model
from coscribe.runtime_lg.mcp import connect_one_mcp_server_lg
from coscribe.tools.mcp import load_mcp_server_configs

# Same dotenv loading coscribe-web/coscribe do at startup (cli.py's own
# _load_settings) -- without it, GEMINI_API_KEY only exists in .env, never
# makes it into os.environ, and resolve_chat_model's ChatGoogleGenerativeAI
# falls through to looking for Google Cloud Application Default
# Credentials instead, failing with a confusing, unrelated error.
load_dotenv(_dotenv_path())


async def try_bind(model, tools: list) -> tuple[bool, str]:  # type: ignore[no-untyped-def]
    """Returns (ok, error_message). A real, tiny API call -- not just
    bind_tools(), which doesn't itself hit the network; the 400 only
    happens when a real GenerateContentRequest is sent."""
    try:
        bound = model.bind_tools(tools)
        await bound.ainvoke("say hi")
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:300]


async def main() -> None:
    config_path = Path("mcp.json")
    if not config_path.is_file():
        print("No mcp.json found in this directory.")
        return
    configs = load_mcp_server_configs(config_path)
    if "github" not in configs:
        print("No 'github' entry in mcp.json.")
        return

    print("Connecting to github...")
    tools, connection = await connect_one_mcp_server_lg("github", configs["github"])
    model = resolve_chat_model("gemini:gemini-3.1-flash-lite")

    try:
        print(f"{len(tools)} tools. Confirming the full set reproduces the crash...")
        ok, err = await try_bind(model, tools)
        if ok:
            print("Full set succeeded?! The problem may be intermittent, or tied to a")
            print("specific *combination* with other connectors, not github alone.")
            return
        print(f"Reproduced: {err}\n")

        current = list(tools)
        path: list[str] = []
        while len(current) > 1:
            mid = len(current) // 2
            first_half, second_half = current[:mid], current[mid:]
            ok_first, _ = await try_bind(model, first_half)
            if not ok_first:
                current = first_half
                path.append(f"first half ({len(first_half)} tools)")
            else:
                current = second_half
                path.append(f"second half ({len(second_half)} tools)")
            print(f"-> narrowed to {path[-1]}")

        culprit = current[0]
        print(f"\nCULPRIT TOOL: {culprit.name}")
        print(f"args_schema:\n{culprit.args_schema}")
    finally:
        if connection is not None:
            await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
