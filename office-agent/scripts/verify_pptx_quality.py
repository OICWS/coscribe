"""Standalone verification for a real, live-reported complaint: "PPTX
output looks like a blank page with a few words." NOT a pytest test --
drives the real Coordinator agent (build_coordinator_agent, same as
web/session.py's ChatSessionLG) with the built-in pptx Skill explicitly
enabled, against a real Gemini model, exactly the way a real user's
enabled-skill thread would. Auto-approves every tool call (same
Command(resume=...) pattern scripts/verify_runtime_lg.py already
established) so the run completes unattended.

What this actually caught, live: with the skill and coordinator
instructions as they were before this script's own first run, the model
never called load_skill("pptx") at all and went straight to write_pptx --
producing exactly the reported bare-template look for 3 of 4 slides (only
the one slide getting a background image looked designed). Fixed by
coordinator.py's INSTRUCTIONS now explicitly telling the model to
load_skill("pptx") before writing any deck when that skill is available,
and by builtin_skills/pptx/SKILL.md reframing run_node_script as the
default for anything the user will actually look at, not a fallback for
special cases -- re-running this same script afterward showed
load_skill + run_node_script actually being called, and the resulting
.pptx built from real composed shapes (verified via python-pptx) rather
than stock placeholder layouts. Worth re-running after any future change
to either file, to catch a regression the same way.

Needs GEMINI_API_KEY in the environment. Run with:

    python scripts/verify_pptx_quality.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from langgraph.types import Command

from coscribe.config import Settings
from coscribe.coordinator import build_coordinator_agent
from coscribe.runtime.types import get_tool_metadata
from coscribe.runtime_lg import build_langgraph_agent, resolve_chat_model


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="coscribe_pptx_verify_"))
    state_dir = workdir / "state"
    settings = Settings(
        default_model="gemini:gemini-3.1-flash-lite",
        workspace_root=workdir / "workspace",
        state_dir=state_dir,
        skills_dir=workdir / "skills",  # empty -- only the builtin pptx skill matters here
        memory_path=workdir / "MEMORY.md",
    )
    settings.workspace_root.mkdir(parents=True, exist_ok=True)

    agent_spec = build_coordinator_agent(settings, "verify-pptx", skill_names={"PPTX Slides"})
    model = resolve_chat_model(agent_spec.model)
    approval_names = [
        t.name if hasattr(t, "name") else t.__name__
        for t in agent_spec.tools
        if get_tool_metadata(t).requires_approval
    ]
    agent = build_langgraph_agent(
        model, agent_spec.tools, agent_spec.instructions, extra_interrupt_tool_names=approval_names
    )
    config = {"configurable": {"thread_id": "verify-pptx"}}

    prompt = (
        "Create a 4-slide deck (deck.pptx) pitching a fictional product called "
        "'Aurora' -- a smart home energy monitor. Slide 1: title. Slide 2: the "
        "problem (rising energy bills, no visibility into usage). Slide 3: how "
        "Aurora solves it (real-time per-device tracking, phone app, AI "
        "recommendations). Slide 4: call to action. Make it look genuinely "
        "professional, not a plain bullet-point template."
    )
    print(f"Prompt: {prompt}\n{'=' * 70}")
    tool_calls_seen: list[str] = []

    result = agent.invoke({"messages": [{"role": "user", "content": prompt}]}, config=config)
    turns = 0
    while agent.get_state(config).next and turns < 20:
        turns += 1
        for msg in result.get("messages", []):
            for tc in getattr(msg, "tool_calls", None) or []:
                tool_calls_seen.append(tc["name"])
        print(f"[turn {turns}] interrupted for approval -- auto-approving")
        result = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config=config)

    for msg in result.get("messages", []):
        for tc in getattr(msg, "tool_calls", None) or []:
            tool_calls_seen.append(tc["name"])

    print(f"\nTool calls made: {tool_calls_seen}")
    final_text = result["messages"][-1].content if result.get("messages") else ""
    print(f"\nFinal agent message:\n{final_text}\n")

    deck_path = settings.workspace_root / "deck.pptx"
    if not deck_path.is_file():
        candidates = list(settings.workspace_root.glob("*.pptx"))
        deck_path = candidates[0] if candidates else deck_path
    if not deck_path.is_file():
        print(f"NO .pptx FILE FOUND under {settings.workspace_root}")
        sys.exit(1)
    print(f"Deck written to: {deck_path}")

    soffice = shutil.which("soffice")
    if soffice is None:
        print("soffice not found -- skipping visual QA render")
        return
    import subprocess

    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(workdir), str(deck_path)],
        capture_output=True,
        timeout=60,
    )
    pdf_path = workdir / (deck_path.stem + ".pdf")
    if not pdf_path.is_file():
        print("soffice conversion failed -- no PDF produced")
        return
    subprocess.run(
        ["pdftoppm", "-jpeg", "-r", "150", str(pdf_path), str(workdir / "slide")],
        capture_output=True,
        timeout=60,
    )
    images = sorted(workdir.glob("slide-*.jpg"))
    print(f"Rendered {len(images)} slide image(s):")
    for img in images:
        print(f"  {img}")


if __name__ == "__main__":
    main()
