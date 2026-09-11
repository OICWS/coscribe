"""Workflow save/replay for runtime_lg -- the LangGraph-native counterpart
to tools/workflows.py.

Storage (`Workflow`/`WorkflowStep`/`WorkflowStore`/`WorkflowRun`/
`WorkflowStepStatus`/`WorkflowRunStore`/`reconcile_interrupted_runs`) is
reused directly from tools/workflows.py, imported below rather than
duplicated -- none of it depends on runtime/'s Agent/Runner/RunState, only
on `state_dir` and plain dicts, so there's nothing runtime_lg-specific to
port there. `WORKFLOW_ASSERTION_INSTRUCTIONS`/`WORKFLOW_CURATOR_INSTRUCTIONS`
(plain prompt strings) are reused unmodified too.

What *does* need a LangGraph-native replacement is the model-call-driven
and replay-driving logic, since tools/workflows.py's versions are built on
`runtime.types.Agent`/`runtime.runner.Runner`/`RunState`:

- `infer_step_assertions_lg`/`propose_workflow_save_lg` replace
  `tools/workflows.py`'s `_infer_step_assertions`/`propose_workflow_save`'s
  one-off `Agent` + `Runner.run_sync` calls with a direct
  `model.ainvoke([...])` -- the same pattern web/session.py's own
  `_handle_compact` already uses for its one-off summary call.
- `recorded_tool_call_steps_lg` replaces `recorded_tool_call_steps`, which
  reads `RunState.steps` -- a shape runtime_lg's checkpointer-backed
  threads don't have (conversation state here is a list of `BaseMessage`
  from `agent.aget_state(config)`, not a `FileStateStore`-persisted
  `RunState`).
- `record_chain_workflow_lg`/`record_agent_workflow_lg` replace their
  identically-named tools/workflows.py counterparts, built on the above.
- Chain-mode *replay* (`run_chain_lg`) replaces `_run_chain`, which drives
  `Runner.execute_tool_call` under a `ToolPolicy`. runtime_lg has no
  synchronous "call a tool under an approval policy" primitive outside of
  a compiled graph's own `HumanInTheLoopMiddleware` -- but web/session.py's
  `/runworkflow` is deliberately NOT a model-callable *tool* (see that
  module's docstring for the scope cut this implies and why), so replay
  here never runs inside a graph's own tool node in the first place. That
  sidesteps needing an `interrupt()`-bridging trick the way
  runtime_lg/subagents.py's spawn_agent needs one for its own nested
  approvals: each step's approval decision is made by the caller (see
  `decide` below, which ChatSessionLG wires to its own
  `_decide_action_request` -- the exact function a normal top-level turn
  already uses to decide a pending interrupt's action_requests), and each
  step is then invoked directly against the live tool object by the
  caller too (`invoke_tool` below) -- no nested graph involved at all.
- Agent-mode replay has no dedicated function here: ChatSessionLG builds a
  fresh compiled graph (same `build_langgraph_agent` call shape as
  spawn_agent's own sub_agent) and drives it with its own
  `_stream_turn`/`_resolve_pending_approvals` -- the exact machinery a
  normal top-level turn already uses, just pointed at a temporary
  agent/config pair. See web/session.py's `_run_workflow_agent_mode`.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from ..tools.workflows import (
    WORKFLOW_ASSERTION_INSTRUCTIONS,
    WORKFLOW_CURATOR_INSTRUCTIONS,
    Workflow,
    WorkflowSaveProposal,
    WorkflowStep,
    WorkflowStore,
)
from .messages import extract_text, render_transcript_lg, tool_result_value

__all__ = [
    "infer_step_assertions_lg",
    "propose_workflow_save_lg",
    "record_agent_workflow_lg",
    "record_chain_workflow_lg",
    "recorded_tool_call_steps_lg",
    "run_chain_lg",
]


def _extract_json_array(text: str) -> str:
    """Same idea as tools/workflows.py's private helper of the same name --
    duplicated rather than imported (a 4-line pure-string helper, not
    worth a cross-module private import) so this module doesn't reach into
    another module's underscore-prefixed names."""
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start : end + 1]


def _extract_json_object(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start : end + 1]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def recorded_tool_call_steps_lg(messages: list[Any]) -> list[tuple[str, dict[str, Any], Any]]:
    """(tool_name, arguments, result) triples for every tool call in a
    LangGraph-checkpointed message list, in order -- the runtime_lg
    counterpart to tools/workflows.py's recorded_tool_call_steps, which
    reads RunState.steps."""
    results_by_id: dict[str, Any] = {}
    for message in messages:
        if isinstance(message, ToolMessage) and message.tool_call_id:
            results_by_id[message.tool_call_id] = tool_result_value(message.content)
    steps: list[tuple[str, dict[str, Any], Any]] = []
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            if call["id"] not in results_by_id:
                # No matching ToolMessage yet -- this call hasn't actually
                # returned. Most often this is list_recorded_steps reading
                # its own not-yet-completed call from checkpointed state
                # (a real, live-reported intermittent test failure): the
                # AIMessage proposing this exact call can already be
                # visible in state before the tool node finishes, so
                # without this check that in-flight call would sometimes
                # show up as a phantom "recorded" step. Nothing was
                # actually recorded yet, so skip it.
                continue
            steps.append((call["name"], dict(call["args"]), results_by_id[call["id"]]))
    return steps


def _save_workflow_record_lg(
    name: str,
    *,
    mode: str,
    summary: str,
    steps: list[WorkflowStep],
    thread_id: str,
    state_dir: Any,
) -> dict[str, Any]:
    store = WorkflowStore(state_dir)
    existing = store.load(name)
    now = _now_iso()
    workflow = Workflow(
        name=name,
        mode=mode,
        summary=summary,
        steps=steps,
        source_thread_id=thread_id,
        created_at=existing.created_at if existing else now,
        updated_at=now,
        last_run_at=existing.last_run_at if existing else None,
        last_run_status=existing.last_run_status if existing else None,
    )
    store.save(workflow)
    return workflow.to_dict()


async def infer_step_assertions_lg(
    steps_with_results: list[tuple[WorkflowStep, Any]], *, model: Any
) -> list[str | None]:
    """Async, model.ainvoke-based counterpart to tools/workflows.py's
    _infer_step_assertions (a one-off Agent + Runner.run_sync call there)
    -- same one-off safety-net pass over a freshly recorded chain, same
    fail-open-to-no-assertions behavior on any error (a bad/unparseable
    response or a provider failure must not block a save)."""
    if not steps_with_results:
        return []
    transcript = "\n".join(
        f"{index}. {step.tool_name}({step.arguments}) -> result: {str(result)[:400]!r}"
        for index, (step, result) in enumerate(steps_with_results)
    )
    try:
        response = await model.ainvoke(
            [
                SystemMessage(content=WORKFLOW_ASSERTION_INSTRUCTIONS),
                HumanMessage(content=transcript),
            ]
        )
        parsed = json.loads(_extract_json_array(extract_text(response.content) or ""))
    except Exception:  # noqa: BLE001 -- fail open, never block a save
        return [None] * len(steps_with_results)
    if not isinstance(parsed, list) or len(parsed) != len(steps_with_results):
        return [None] * len(steps_with_results)
    return [entry.strip() if isinstance(entry, str) and entry.strip() else None for entry in parsed]


async def record_chain_workflow_lg(
    name: str,
    summary: str,
    *,
    messages: list[Any],
    state_dir: Any,
    thread_id: str,
    start_index: int,
    model: Any,
) -> dict[str, Any]:
    """runtime_lg counterpart to tools/workflows.py's record_chain_workflow
    -- same start_index-scoped capture (only steps recorded from
    /startworkflow onward, never the thread's earlier history), same
    auto-populated expect_contains safety net."""
    tool_steps = recorded_tool_call_steps_lg(messages)
    if start_index >= len(tool_steps):
        raise ValueError(
            "No tool calls were recorded between /startworkflow and /endworkflow -- "
            "perform the actions to record first, then /endworkflow."
        )
    captured = tool_steps[start_index:]
    chain_steps = [WorkflowStep(tool_name=n, arguments=a) for n, a, _ in captured]
    assertions = await infer_step_assertions_lg(
        list(zip(chain_steps, (result for _, _, result in captured), strict=True)), model=model
    )
    for chain_step, assertion in zip(chain_steps, assertions, strict=True):
        chain_step.expect_contains = assertion
    return _save_workflow_record_lg(
        name,
        mode="chain",
        summary=summary,
        steps=chain_steps,
        thread_id=thread_id,
        state_dir=state_dir,
    )


def record_agent_workflow_lg(
    name: str, summary: str, *, thread_id: str, state_dir: Any
) -> dict[str, Any]:
    return _save_workflow_record_lg(
        name, mode="agent", summary=summary, steps=[], thread_id=thread_id, state_dir=state_dir
    )


async def propose_workflow_save_lg(
    name: str,
    *,
    messages: list[Any],
    model: Any,
    clarification_history: list[tuple[str, str]],
) -> WorkflowSaveProposal:
    """runtime_lg counterpart to tools/workflows.py's propose_workflow_save
    -- same curator judgment call (clarify vs. propose a chain/agent-mode
    save), same fail-open-to-a-clarifying-question behavior."""
    tool_steps = recorded_tool_call_steps_lg(messages)
    steps_listing = "\n".join(
        f"{index}. {tool_name_}({args})" for index, (tool_name_, args, _) in enumerate(tool_steps)
    )
    clarification_block = ""
    if clarification_history:
        clarification_block = "\n\nPrevious clarification exchange:\n" + "\n".join(
            f"Q: {question}\nA: {answer}" for question, answer in clarification_history
        )
    prompt = (
        f'Proposed workflow name: "{name}"\n\n'
        f"Conversation transcript:\n{render_transcript_lg(messages)}\n\n"
        f"Numbered tool calls so far (0-based):\n{steps_listing or '(none yet)'}"
        f"{clarification_block}"
    )
    fallback = WorkflowSaveProposal(
        decision="clarify",
        question=(
            "I couldn't tell what to save from this conversation -- what would you "
            "like this workflow to do?"
        ),
    )
    try:
        response = await model.ainvoke(
            [SystemMessage(content=WORKFLOW_CURATOR_INSTRUCTIONS), HumanMessage(content=prompt)]
        )
        data = json.loads(_extract_json_object(extract_text(response.content) or ""))
    except Exception:  # noqa: BLE001 -- fail open to a question, never crash
        return fallback
    if not isinstance(data, dict):
        return fallback

    decision = data.get("decision")
    if decision == "clarify":
        question = data.get("question")
        if isinstance(question, str) and question.strip():
            return WorkflowSaveProposal(decision="clarify", question=question.strip())
        return fallback

    if decision == "propose":
        mode = data.get("mode")
        if mode == "chain":
            start_index = data.get("start_index")
            if isinstance(start_index, int) and 0 <= start_index < len(tool_steps):
                summary = data.get("summary")
                return WorkflowSaveProposal(
                    decision="propose",
                    mode="chain",
                    start_index=start_index,
                    summary=summary.strip() if isinstance(summary, str) else "",
                )
        elif mode == "agent":
            summary = data.get("summary")
            if isinstance(summary, str) and summary.strip():
                return WorkflowSaveProposal(
                    decision="propose", mode="agent", summary=summary.strip()
                )

    return fallback


async def run_chain_lg(
    workflow: Workflow,
    *,
    tools_by_name: dict[str, Any],
    decide: Callable[[str, dict[str, Any]], Awaitable[tuple[bool, str | None]]],
    invoke_tool: Callable[[Any, dict[str, Any]], Awaitable[Any]],
    start_step: int = 0,
    on_step: Callable[[int, str, str, str | None], Awaitable[None]] | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """runtime_lg counterpart to tools/workflows.py's _run_chain --
    deterministic replay, halting on the first denied/failed/mismatched
    step, same `start_step`-scoped resume support.

    Unlike _run_chain, has no Runner/ToolPolicy/Tools-registry of its own:
    `decide(tool_name, arguments) -> (approved, reason)` and
    `invoke_tool(tool, arguments) -> result` are both supplied by the
    caller (ChatSessionLG), which already owns the equivalent machinery
    (`_decide_action_request` for the former, direct tool invocation for
    the latter) for a normal turn's own gated tool calls -- see this
    module's own docstring for why no nested-interrupt bridging is needed
    here."""
    if not 0 <= start_step < len(workflow.steps):
        raise ValueError(
            f"start_step must be between 0 and {len(workflow.steps) - 1}, got {start_step}"
        )

    results: list[dict[str, Any]] = []
    for index, step in enumerate(workflow.steps):
        if index < start_step:
            continue
        if stop_requested is not None and stop_requested():
            if on_step is not None:
                await on_step(index, step.tool_name, "stopped", "stopped by user")
            return {
                "status": "stopped",
                "error": f"stopped by user before step {index}",
                "completed_steps": index,
                "results": results,
            }
        if on_step is not None:
            await on_step(index, step.tool_name, "running", None)
        tool = tools_by_name.get(step.tool_name)
        if tool is None:
            detail = f"tool {step.tool_name!r} no longer exists"
            if on_step is not None:
                await on_step(index, step.tool_name, "failed", detail)
            return {
                "status": "failed",
                "error": f"step {index}: {detail}",
                "completed_steps": index,
                "results": results,
            }
        approved, reason = await decide(step.tool_name, step.arguments)
        if not approved:
            detail = f"denied: {reason}"
            if on_step is not None:
                await on_step(index, step.tool_name, "failed", detail)
            return {
                "status": "failed",
                "error": f"step {index} ({step.tool_name}) {detail}",
                "completed_steps": index,
                "results": results,
            }
        try:
            result = await invoke_tool(tool, step.arguments)
        except Exception as exc:  # noqa: BLE001 -- stored args are less trustworthy than a
            # fresh model-generated call (the tool's signature may have changed since
            # save time); report which step failed rather than crashing the run.
            if on_step is not None:
                await on_step(index, step.tool_name, "failed", f"raised: {exc}")
            return {
                "status": "failed",
                "error": f"step {index} ({step.tool_name}) raised: {exc}",
                "completed_steps": index,
                "results": results,
            }
        if step.expect_contains and step.expect_contains.lower() not in str(result).lower():
            detail = (
                f"expected result to mention {step.expect_contains!r}, but it didn't -- "
                "this step may not have actually done what it looks like it did"
            )
            if on_step is not None:
                await on_step(index, step.tool_name, "failed", detail)
            return {
                "status": "failed",
                "error": f"step {index} ({step.tool_name}): {detail}",
                "completed_steps": index,
                "results": results,
            }
        if on_step is not None:
            await on_step(index, step.tool_name, "done", None)
        results.append({"tool_name": step.tool_name, "result": result})

    return {"status": "completed", "completed_steps": len(workflow.steps), "results": results}
