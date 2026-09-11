"""Phase 1 exit-criteria verification for runtime_lg (see the migration
plan). NOT a pytest test -- a standalone script exercising real,
*unmodified* coscribe tools (build_file_tools, build_memory_tools)
against real LLM providers through runtime_lg.build_langgraph_agent.

Requires the langgraph_spike extras in a venv where they can coexist with
coscribe's own deps (pyproject.toml's langgraph_spike extra documents
why that's not this project's own .venv today). Run with:

    python scripts/verify_runtime_lg.py

Needs ANTHROPIC_API_KEY / GEMINI_API_KEY / DEEPSEEK_API_KEY / GLM_API_KEY
in the environment; skips whichever provider has no key rather than
failing. DeepSeek/GLM go through the same custom-OpenAI-compatible-provider
path (resolve_chat_model's custom_providers arg) coscribe's own
web/app.py PROVIDER_CATALOG already documents base_url/default_model for --
reused verbatim here, not re-guessed.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from langgraph.types import Command

from coscribe.runtime_lg import build_langgraph_agent, resolve_chat_model
from coscribe.tools import build_file_tools, build_memory_tools

INSTRUCTIONS = (
    "You are a local office assistant with access to a small workspace. "
    "Use the tools you're given; don't ask for permission in words, just call them."
)


def run_scenario(
    provider_label: str,
    model_string: str,
    workdir: Path,
    custom_providers: dict[str, dict[str, str]] | None = None,
) -> list[str]:
    """Returns a list of human-readable check results (pass/fail lines)."""
    checks: list[str] = []
    model = resolve_chat_model(model_string, custom_providers)
    tools = build_file_tools(workdir) + build_memory_tools(workdir / "MEMORY.md")
    agent = build_langgraph_agent(model, tools, INSTRUCTIONS)
    config = {"configurable": {"thread_id": f"verify-{provider_label}"}}

    print(f"\n{'=' * 70}\n{provider_label} ({model_string})\n{'=' * 70}")

    # -- turn 1: streaming + a low-risk tool (no interrupt expected) -------
    saw_stream_chunk = False
    for _mode, _chunk in agent.stream(
        {"messages": [{"role": "user", "content": "List the files in the workspace."}]},
        config=config,
        stream_mode=["messages"],
    ):
        saw_stream_chunk = True
    checks.append(f"[{'PASS' if saw_stream_chunk else 'FAIL'}] streaming produced chunks (turn 1)")

    state = agent.get_state(config)
    checks.append(
        f"[{'PASS' if not state.next else 'FAIL'}] no interrupt on a read-only tool call"
    )

    # -- turn 2: approval-gated write_file, APPROVE path --------------------
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "Write a file called note.txt containing exactly: hello",
                }
            ]
        },
        config=config,
    )
    state = agent.get_state(config)
    interrupted = bool(state.next)
    checks.append(f"[{'PASS' if interrupted else 'FAIL'}] interrupt fired for write_file")
    if interrupted:
        result = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config=config)
    written = (workdir / "note.txt").read_text() if (workdir / "note.txt").is_file() else None
    checks.append(
        f"[{'PASS' if written == 'hello' else 'FAIL'}] approved write actually happened "
        f"(got {written!r})"
    )

    # -- turn 3, SAME thread: the exact failure mode that broke the old ----
    # -- Gemini adapter three times -- replaying prior function-call/     --
    # -- signature history, then REJECTING a second write.                --
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "Now overwrite note.txt with the word goodbye instead.",
                }
            ]
        },
        config=config,
    )
    state = agent.get_state(config)
    interrupted = bool(state.next)
    checks.append(
        f"[{'PASS' if interrupted else 'FAIL'}] turn-3 (multi-turn history replay) interrupt fired"
    )
    if interrupted:
        result = agent.invoke(Command(resume={"decisions": [{"type": "reject"}]}), config=config)
    still_hello = (workdir / "note.txt").read_text() == "hello"
    checks.append(f"[{'PASS' if still_hello else 'FAIL'}] rejected write did NOT happen")

    last = result["messages"][-1]
    checks.append(f"final message (informational): {getattr(last, 'content', last)!r}")
    return checks


# (label, env var to require, model string, custom_providers-builder or None)
# DeepSeek/GLM go through the same custom-OpenAI-compatible-provider path
# coscribe's own Settings/LLMClient use for user-added providers,
# base_url values copied verbatim from web/app.py's PROVIDER_CATALOG
# (already verified against each vendor's docs there, not re-guessed here).
SCENARIOS: list[tuple[str, str, str, Any]] = [
    ("Gemini", "GEMINI_API_KEY", "gemini:gemini-3.1-flash-lite", None),
    ("Anthropic", "ANTHROPIC_API_KEY", "anthropic:claude-sonnet-4-5-20250929", None),
    (
        "DeepSeek",
        "DEEPSEEK_API_KEY",
        "deepseek:deepseek-v4-pro",
        lambda key: {"deepseek": {"base_url": "https://api.deepseek.com/v1", "api_key": key}},
    ),
    (
        "GLM",
        "GLM_API_KEY",
        "glm:glm-4.6v",
        lambda key: {
            "glm": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key": key}
        },
    ),
]


def main() -> int:
    all_checks: list[str] = []

    for label, env_var, model_string, custom_providers_fn in SCENARIOS:
        key = os.getenv(env_var) or (
            os.getenv("GOOGLE_API_KEY") if env_var == "GEMINI_API_KEY" else None
        )
        if not key:
            print(f"SKIP {label}: no {env_var}")
            continue
        custom_providers = custom_providers_fn(key) if custom_providers_fn else None
        workdir = Path(tempfile.mkdtemp(prefix=f"runtime_lg_verify_{label.lower()}_"))
        try:
            all_checks += run_scenario(label, model_string, workdir, custom_providers)
        except Exception as exc:  # noqa: BLE001 -- report, don't let one provider kill the run
            print(f"\n{'=' * 70}\n{label} ({model_string}) -- ERRORED\n{'=' * 70}")
            all_checks.append(f"[FAIL] {label} errored before completing: {exc!r}")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    for line in all_checks:
        print(line)

    failed = [c for c in all_checks if c.startswith("[FAIL]")]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
