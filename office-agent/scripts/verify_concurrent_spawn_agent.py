"""Standalone (not pytest) live verification of runtime_lg's spawn_agent
under *concurrent* calls -- see runtime_lg/subagents.py's module docstring
for the full write-up of what this proves and the bug it caught.

Scenario: the parent proposes TWO spawn_agent calls in one AIMessage.
LangGraph's Pregel executor runs same-superstep tool-node tasks
concurrently, so both spawn_agent invocations run on separate OS threads,
each independently bridging its own child's approval-gated write_file call
via interrupt(). This surfaced two real, previously-unverified questions:

1. Does LangGraph's Command(resume=...) correctly resolve *multiple*
   simultaneously-pending interrupts (one per concurrent spawn_agent call),
   or does a single shared resume value only work for one at a time?
   Answer: the latter -- a single shared value raises "When there are
   multiple pending interrupts, you must specify the interrupt id when
   resuming" the moment two are pending at once. Fixed in
   web/session.py's _resolve_pending_approvals by resuming via
   Command(resume={interrupt_id: value, ...}), keyed per task.

2. Does a child sub-agent's own progress survive the *parent* tool node's
   own interrupt/resume replay (interrupt()'s documented "resumes from the
   start of the node, re-executing all logic")? Answer: not by default --
   build_spawn_agent_tool used to build a *fresh* InMemorySaver on every
   call, including on every replay, so a resumed spawn_agent silently
   re-asked its child from scratch and applied the human's decision to
   whatever new question that produced instead of the original one. Fixed
   by sharing one InMemorySaver across every call a given
   build_spawn_agent_tool closure makes.

Run with: python scripts/verify_concurrent_spawn_agent.py
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

INSTRUCTIONS = (
    "You are a helpful assistant with file tools and spawn_agent for delegation."
)


def run(model_string: str, workdir: Path) -> list[str]:
    checks: list[str] = []
    model = resolve_chat_model(model_string)
    file_tools = build_file_tools(workdir)
    spawn_agent = build_spawn_agent_tool(model, file_tools)
    agent = build_langgraph_agent(model, [*file_tools, spawn_agent], INSTRUCTIONS)
    config = {"configurable": {"thread_id": "verify-concurrent-spawn"}}

    prompt = (
        "Call spawn_agent TWICE in this same turn (two separate, parallel tool "
        "calls, not sequential): "
        "1) instructions='write files', prompt='write a file called a.txt "
        "containing exactly: AAA', tool_names='write_file' "
        "2) instructions='write files', prompt='write a file called b.txt "
        "containing exactly: BBB', tool_names='write_file' "
        "You must call spawn_agent twice in one response."
    )
    agent.invoke({"messages": [{"role": "user", "content": prompt}]}, config=config)
    state = agent.get_state(config)
    checks.append(
        f"[{'PASS' if len(state.tasks) == 2 else 'FAIL'}] two concurrent tasks pending "
        f"(got {len(state.tasks)})"
    )

    # Resume each task's interrupt individually, by id -- a single shared
    # resume value is what used to crash here (see module docstring).
    resume_map = {}
    for task in state.tasks:
        for intr in task.interrupts:
            req = intr.value["action_requests"][0]
            decision = "approve" if req["args"]["path"] == "a.txt" else "reject"
            resume_map[intr.id] = {"decisions": [{"type": decision}]}

    try:
        agent.invoke(Command(resume=resume_map), config=config)
        resumed_ok = True
    except RuntimeError as exc:
        checks.append(f"[FAIL] resuming both interrupts by id raised: {exc!r}")
        resumed_ok = False

    if resumed_ok:
        state = agent.get_state(config)
        checks.append(
            f"[{'PASS' if not state.next else 'FAIL'}] no lingering/phantom interrupts "
            f"after one resume (state.next={state.next!r})"
        )
        a_ok = (workdir / "a.txt").is_file() and (workdir / "a.txt").read_text() == "AAA"
        b_rejected = not (workdir / "b.txt").is_file()
        checks.append(
            f"[{'PASS' if a_ok else 'FAIL'}] approved call actually wrote a.txt "
            f"(exists={a_ok})"
        )
        checks.append(
            f"[{'PASS' if b_rejected else 'FAIL'}] rejected call did NOT write b.txt "
            f"(correctly absent={b_rejected})"
        )
    return checks


def main() -> int:
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        print("SKIP: no GEMINI_API_KEY")
        return 0

    workdir = Path(tempfile.mkdtemp(prefix="verify_concurrent_spawn_"))
    try:
        checks = run("gemini:gemini-3.1-flash-lite", workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    for line in checks:
        print(line)

    failed = [c for c in checks if c.startswith("[FAIL]")]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
