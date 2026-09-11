"""Standalone (not pytest) live verification of runtime_lg's spawn_agent
nested-interrupt bridge (see runtime_lg/subagents.py's module docstring for
the full write-up of what this proves).

Two scenarios against real Gemini:
1. single-round: parent asks a sub-agent to write one file. The sub-agent's
   own write_file call interrupts; spawn_agent must bridge that up to the
   PARENT's own interrupt so the top-level caller sees it (and can approve
   it) without ever knowing a sub-agent is involved.
2. two-round: parent asks a sub-agent to write two files with a genuine
   dependency between them (read the first back before writing the second),
   forcing two *sequential* rounds of nested approval within one
   spawn_agent call -- decided APPROVE then REJECT specifically to rule out
   round 2 silently reusing round 1's cached decision. This was expected to
   fail (see subagents.py's module docstring for why, in detail) and didn't
   -- both rounds resolved correctly.

Run with: python scripts/verify_nested_interrupt.py
Needs GEMINI_API_KEY in the environment.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from langgraph.types import Command

from coscribe.runtime_lg import build_langgraph_agent, resolve_chat_model
from coscribe.runtime_lg.subagents import build_spawn_agent_tool
from coscribe.tools import build_file_tools

PARENT_INSTRUCTIONS = (
    "You are a coordinator. For any file-writing request, delegate it to "
    "spawn_agent (give it the write_file tool) instead of doing it yourself. "
    "Don't ask for permission in words, just call the tool."
)
SUBAGENT_INSTRUCTIONS = (
    "You write files as instructed, exactly as asked, one write_file call "
    "per requested file. Don't ask for permission in words, just call the tool."
)


def run_single_round(model_string: str, workdir: Path) -> list[str]:
    checks: list[str] = []
    model = resolve_chat_model(model_string)
    file_tools = build_file_tools(workdir)
    spawn_agent = build_spawn_agent_tool(model, file_tools, max_rounds=1)
    parent = build_langgraph_agent(model, [spawn_agent], PARENT_INSTRUCTIONS)
    config = {"configurable": {"thread_id": "nested-single-round"}}

    parent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Delegate this to spawn_agent with tool_names='write_file' and "
                        f"instructions={SUBAGENT_INSTRUCTIONS!r}: write a file called "
                        "note.txt containing exactly: hello"
                    ),
                }
            ]
        },
        config=config,
    )
    state = parent.get_state(config)
    interrupted = bool(state.next)
    checks.append(f"[{'PASS' if interrupted else 'FAIL'}] nested interrupt reached the PARENT")
    if interrupted:
        parent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config=config)
    written = (workdir / "note.txt").read_text() if (workdir / "note.txt").is_file() else None
    checks.append(
        f"[{'PASS' if written == 'hello' else 'FAIL'}] approving at the PARENT actually ran "
        f"the child's write_file (got {written!r})"
    )
    return checks


def run_two_round(model_string: str, workdir: Path) -> list[str]:
    """Forces the sub-agent through two sequential approval-gated calls in
    one spawn_agent invocation, decided APPROVE then REJECT, to rule out
    round 2 silently reusing round 1's cached decision (see
    runtime_lg/subagents.py's module docstring for why that was a real
    worry, and why it turned out not to happen)."""
    checks: list[str] = []
    model = resolve_chat_model(model_string)
    file_tools = build_file_tools(workdir)
    spawn_agent = build_spawn_agent_tool(model, file_tools, max_rounds=2)
    parent = build_langgraph_agent(model, [spawn_agent], PARENT_INSTRUCTIONS)
    config = {"configurable": {"thread_id": "nested-two-round"}}

    parent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Delegate this to spawn_agent with tool_names='write_file,read_file' "
                        f"and instructions={SUBAGENT_INSTRUCTIONS!r}: write a file called "
                        "a.txt containing exactly: alpha -- then, only after that call's "
                        "result comes back, read a.txt back and write a second file called "
                        "b.txt containing exactly: beta. These must be two separate, "
                        "sequential write_file calls -- do not call write_file twice in the "
                        "same turn."
                    ),
                }
            ]
        },
        config=config,
    )
    # Decisive test for the "stale decision silently reused" worry: approve
    # round 1, REJECT round 2. If each round genuinely gets its own resume
    # value (not round 1's cached one reapplied), a.txt must exist and
    # b.txt must NOT.
    round_num = 0
    while parent.get_state(config).next and round_num < 4:
        round_num += 1
        pending_state = parent.get_state(config)
        num_actions = len(pending_state.tasks[0].interrupts[0].value["action_requests"])
        decision_type = "approve" if round_num == 1 else "reject"
        decisions = [{"type": decision_type}] * num_actions
        checks.append(
            f"round {round_num}: parent interrupt bundles {num_actions} action(s), "
            f"deciding {decision_type!r}"
        )
        parent.invoke(Command(resume={"decisions": decisions}), config=config)
    a_ok = (workdir / "a.txt").is_file() and (workdir / "a.txt").read_text() == "alpha"
    b_rejected = not (workdir / "b.txt").is_file()
    checks.append(
        f"[{'PASS' if (a_ok and b_rejected) else 'FAIL'}] round 1's approve and round 2's "
        f"reject were each applied to the correct round (not one stale decision reused) -- "
        f"a.txt written={a_ok}, b.txt correctly NOT written={b_rejected}"
    )
    return checks


def main() -> int:
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        print("SKIP: no GEMINI_API_KEY")
        return 0

    all_checks: list[str] = []
    model_string = "gemini:gemini-3.1-flash-lite"

    for label, fn in (("single-round", run_single_round), ("two-round", run_two_round)):
        print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
        workdir = Path(tempfile.mkdtemp(prefix=f"nested_interrupt_{label}_"))
        try:
            checks = fn(model_string, workdir)
            all_checks += checks
            for line in checks:
                print(line)
        except Exception as exc:  # noqa: BLE001 -- report, don't let one scenario kill the run
            print(f"[FAIL] {label} errored: {exc!r}")
            all_checks.append(f"[FAIL] {label} errored: {exc!r}")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    for line in all_checks:
        print(line)

    failed = [c for c in all_checks if c.startswith("[FAIL]")]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
