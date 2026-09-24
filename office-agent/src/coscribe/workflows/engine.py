"""Runs a Workflow as a LangGraph StateGraph, one node per step, over the
same checkpointer as conversations -- so a run that stops (a failed step,
a pending approval, a crash) picks up exactly where it stopped:

- After a failure, resume() re-runs only the failed step: LangGraph keeps
  that step as the checkpoint's next node, and earlier steps' results in
  state. retry_from() forks from an earlier step instead (a check failing
  because a model step returned bad data needs that model step re-run).
- An approval step pauses with interrupt(). LangGraph re-executes the whole
  node on resume, so an approval step does nothing else -- a side effect
  placed before an interrupt() would happen twice.

Every step reports its progress through on_step as it goes, for the run's
record and a watching tab; the full values stay in the checkpoint."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict, cast

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .refs import evaluate, render_text, render_value
from .spec import (
    ApprovalStep,
    CheckStep,
    LLMStep,
    OutputField,
    ScriptStep,
    Step,
    ToolStep,
    Workflow,
    step_output,
)

logger = logging.getLogger(__name__)

StepStatus = Literal["pending", "running", "done", "failed", "skipped", "waiting"]
RunOutcomeStatus = Literal["completed", "failed", "waiting"]

# Tools that need a person in the loop mid-call; a workflow asks through an
# approval step instead.
UNAVAILABLE_TOOLS = frozenset({"ask_user_question", "create_scheduled_task"})
SCRIPT_TIMEOUT_SECONDS = 300.0
_PREVIEW_TEXT_CHARS = 2000
_PREVIEW_LIST_ITEMS = 50

LLM_STEP_INSTRUCTIONS = (
    "You are one step of a fixed workflow, not a chat. Work only from the "
    "information given in the message. Return exactly the requested fields, "
    "with the requested types -- nothing else."
)


class StepFailed(Exception):
    def __init__(
        self, message: str, checks: list[dict[str, Any]] | None = None, step_id: str | None = None
    ) -> None:
        super().__init__(message)
        self.checks = checks
        self.step_id = step_id


class WorkflowNotRunnable(ValueError):
    pass


@dataclass
class StepRecord:
    step_id: str
    status: StepStatus
    started_at: str | None = None
    finished_at: str | None = None
    output: Any = None
    error: str | None = None
    # One entry per condition of a check (or an approval's `when`):
    # {"held", "left", "right"}, so a failure shows what it compared.
    checks: list[dict[str, Any]] | None = None
    # What the person who answered an approval step wrote, if anything.
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StepContext:
    tools: dict[str, Callable[..., Any]]
    workspace_root: Path
    state_dir: Path
    # A LangChain chat model for a "provider:model" string, or the
    # default model for None.
    make_model: Callable[[str | None], Any]
    run_script: Callable[[Path, Path, str, float], dict[str, object]] | None = None


@dataclass
class RunOutcome:
    status: RunOutcomeStatus
    step_id: str | None = None
    error: str | None = None
    # For "waiting": what the approval step asks.
    request: dict[str, Any] | None = None
    values: dict[str, Any] = field(default_factory=dict)


OnStep = Callable[[StepRecord], Awaitable[None] | None]


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {**left, **right}


class _State(TypedDict):
    values: Annotated[dict[str, Any], _merge]


def _node_name(step: Step) -> str:
    # A node can't share a name with a state key ("values").
    return f"step__{step.id}"


def preview(value: Any, depth: int = 0) -> Any:
    """A bounded, JSON-safe copy for a run record."""
    if isinstance(value, str):
        if len(value) <= _PREVIEW_TEXT_CHARS:
            return value
        return (
            value[:_PREVIEW_TEXT_CHARS]
            + f"... [{len(value) - _PREVIEW_TEXT_CHARS} more characters]"
        )
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        return {str(k): preview(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        items = [preview(v, depth + 1) for v in value[:_PREVIEW_LIST_ITEMS]]
        if len(value) > _PREVIEW_LIST_ITEMS:
            items.append(f"... [{len(value) - _PREVIEW_LIST_ITEMS} more items]")
        return items
    return str(value)


# -- Step executors -----------------------------------------------------------


async def _run_tool(step: ToolStep, values: dict[str, Any], ctx: StepContext) -> Any:
    tool = ctx.tools[step.tool]
    args = render_value(step.args, values)
    if inspect.iscoroutinefunction(tool):
        return await tool(**args)
    return await asyncio.to_thread(tool, **args)


def _default_run_script(
    workspace_root: Path, state_dir: Path, script: str, timeout: float
) -> dict[str, object]:
    from ..tools.scripts import _run_python_script

    return _run_python_script(workspace_root, state_dir, script, timeout)


async def _run_script(step: ScriptStep, values: dict[str, Any], ctx: StepContext) -> Any:
    inputs = render_value(step.inputs, values)
    # repr() of the JSON text is a valid Python string literal, so input
    # values can never break out into the script's code.
    prelude = f"inputs = __import__('json').loads({json.dumps(inputs, ensure_ascii=False)!r})\n"
    run_script = ctx.run_script or _default_run_script
    result = await asyncio.to_thread(
        run_script, ctx.workspace_root, ctx.state_dir, prelude + step.code, SCRIPT_TIMEOUT_SECONDS
    )
    stderr = str(result.get("stderr") or "").strip()
    if result.get("timed_out"):
        raise StepFailed(
            f"The script ran longer than {SCRIPT_TIMEOUT_SECONDS:.0f}s and was stopped."
        )
    if result.get("exit_code") != 0:
        raise StepFailed(
            f"The script failed (exit code {result.get('exit_code')}): {stderr[-1500:]}"
        )
    lines = [line for line in str(result.get("stdout") or "").splitlines() if line.strip()]
    if not lines:
        raise StepFailed(
            "The script printed nothing; it must print its result as JSON on its last line."
        )
    try:
        return json.loads(lines[-1])
    except ValueError as exc:
        raise StepFailed(f"The script's last line isn't JSON: {lines[-1][:200]!r}") from exc


_JSON_TYPES: dict[str, dict[str, Any]] = {
    "text": {"type": "string"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
    "list": {"type": "array", "items": {}},
}


def fields_schema(fields: list[OutputField]) -> dict[str, Any]:
    return {
        "title": "step_result",
        "description": "The result of this workflow step.",
        "type": "object",
        "properties": {
            f.name: {**_JSON_TYPES[f.type], "description": f.description} for f in fields
        },
        "required": [f.name for f in fields],
        "additionalProperties": False,
    }


def _field_problems(result: Any, fields: list[OutputField]) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(result, dict):
        return {}, [f"expected an object with {', '.join(f.name for f in fields)}"]
    cleaned: dict[str, Any] = {}
    problems: list[str] = []
    for f in fields:
        if f.name not in result:
            problems.append(f"{f.name} is missing")
            continue
        value = result[f.name]
        if f.type == "number" and isinstance(value, str):
            try:
                value = float(value) if "." in value else int(value)
            except ValueError:
                pass
        valid = {
            "text": isinstance(value, str),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "list": isinstance(value, list),
        }[f.type]
        if valid:
            cleaned[f.name] = value
        else:
            problems.append(f"{f.name} must be {f.type}, got {type(value).__name__}")
    return cleaned, problems


def _json_object(text: str) -> Any:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        raise ValueError("no JSON object in the reply")
    return json.loads(match.group(0))


async def _ask_model(model: Any, messages: list[Any], fields: list[OutputField]) -> Any:
    """Structured output through a forced tool call where the provider
    allows one; otherwise ask for bare JSON. Some refuse forced tool calls
    outright (DeepSeek's thinking mode: "does not support this
    tool_choice"), so any failure of the first route falls back to the
    second, whose own error is what gets reported."""
    try:
        structured = model.with_structured_output(fields_schema(fields), method="function_calling")
        return await structured.ainvoke(messages)
    except Exception:  # noqa: BLE001 -- the JSON route below is the fallback for any refusal
        logger.info("workflow: structured output unavailable, asking for JSON", exc_info=True)
    keys = ", ".join(f"{f.name} ({f.type})" for f in fields)
    reply = await model.ainvoke(
        [*messages, HumanMessage(f"Reply with only a JSON object with keys: {keys}.")]
    )
    return _json_object(str(reply.content))


async def _run_llm(step: LLMStep, values: dict[str, Any], ctx: StepContext) -> Any:
    model = ctx.make_model(step.model)
    if "temperature" in getattr(type(model), "model_fields", {}):
        model = model.model_copy(update={"temperature": 0})
    field_lines = "\n".join(f"- {f.name} ({f.type}): {f.description}" for f in step.fields)
    messages: list[Any] = [
        SystemMessage(LLM_STEP_INSTRUCTIONS),
        HumanMessage(f"{render_text(step.prompt, values)}\n\nReturn:\n{field_lines}"),
    ]
    problems: list[str] = []
    for _attempt in range(2):
        try:
            result = await _ask_model(model, messages, step.fields)
        except ValueError as exc:
            problems = [str(exc)]
        else:
            cleaned, problems = _field_problems(result, step.fields)
            if not problems:
                return cleaned
        messages.append(
            HumanMessage("That result was rejected: " + "; ".join(problems) + ". Try again.")
        )
    raise StepFailed("The model didn't return the required fields: " + "; ".join(problems))


def _run_check(step: CheckStep, values: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for condition in step.conditions:
        held, left, right = evaluate(condition, values)
        results.append({"held": held, "left": preview(left), "right": preview(right)})
    failed = [r for r in results if not r["held"]]
    if failed:
        raise StepFailed(f"{len(failed)} of {len(results)} checks failed", checks=results)
    return results


# -- The graph ---------------------------------------------------------------


def preflight(workflow: Workflow, ctx: StepContext) -> None:
    """Refuse a workflow that can't run here, before any step does."""
    for index, step in enumerate(workflow.steps, start=1):
        if isinstance(step, ToolStep):
            if step.tool in UNAVAILABLE_TOOLS:
                raise WorkflowNotRunnable(f"step {index}: {step.tool} can't be used in a workflow")
            if step.tool not in ctx.tools:
                raise WorkflowNotRunnable(f"step {index}: there's no tool called {step.tool!r}")


def resolve_inputs(workflow: Workflow, given: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for spec in workflow.inputs:
        value = given.get(spec.name, spec.default)
        if value is None or value == "":
            raise WorkflowNotRunnable(f"input {spec.name!r} needs a value")
        values[spec.name] = value
    return values


def build_graph(workflow: Workflow, ctx: StepContext, on_step: OnStep, checkpointer: Any) -> Any:
    async def emit(record: StepRecord) -> None:
        result = on_step(record)
        if inspect.isawaitable(result):
            await result

    def make_node(step: Step) -> Callable[[_State], Awaitable[dict[str, Any]]]:
        async def run(state: _State) -> dict[str, Any]:
            values = state["values"]
            started = _now()
            if isinstance(step, ApprovalStep):
                return await _approval(step, values, started)
            await emit(StepRecord(step.id, "running", started_at=started))
            checks = None
            try:
                if isinstance(step, ToolStep):
                    output = await _run_tool(step, values, ctx)
                elif isinstance(step, ScriptStep):
                    output = await _run_script(step, values, ctx)
                elif isinstance(step, LLMStep):
                    output = await _run_llm(step, values, ctx)
                else:
                    checks = _run_check(step, values)
                    output = None
            except StepFailed as exc:
                exc.step_id = step.id
                await emit(
                    StepRecord(
                        step.id, "failed", started, _now(), error=str(exc), checks=exc.checks
                    )
                )
                raise
            except Exception as exc:  # noqa: BLE001 -- any step error stops the run, recorded
                message = str(exc) or type(exc).__name__
                await emit(StepRecord(step.id, "failed", started, _now(), error=message))
                raise StepFailed(message, step_id=step.id) from exc
            await emit(
                StepRecord(step.id, "done", started, _now(), output=preview(output), checks=checks)
            )
            save_as = step_output(step)
            return {"values": {save_as: output}} if save_as else {"values": {}}

        return run

    async def _approval(step: ApprovalStep, values: dict[str, Any], started: str) -> dict[str, Any]:
        checks = None
        if step.when is not None:
            held, left, right = evaluate(step.when, values)
            checks = [{"held": held, "left": preview(left), "right": preview(right)}]
            if not held:
                await emit(StepRecord(step.id, "skipped", started, _now(), checks=checks))
                return {"values": {}}
        message = render_text(step.message, values)
        await emit(StepRecord(step.id, "waiting", started, output=message, checks=checks))
        answer = interrupt({"step_id": step.id, "title": step.title, "message": message})
        approved = isinstance(answer, dict) and answer.get("approved") is True
        note = str(answer.get("note") or "") if isinstance(answer, dict) else ""
        if not approved:
            error = "Not approved" + (f": {note}" if note else "")
            await emit(
                StepRecord(
                    step.id,
                    "failed",
                    started,
                    _now(),
                    output=message,
                    error=error,
                    note=note or None,
                )
            )
            raise StepFailed(error, step_id=step.id)
        await emit(StepRecord(step.id, "done", started, _now(), output=message, note=note or None))
        return {"values": {}}

    graph = StateGraph(_State)
    previous = START
    for step in workflow.steps:
        graph.add_node(_node_name(step), cast(Any, make_node(step)))
        graph.add_edge(previous, _node_name(step))
        previous = _node_name(step)
    graph.add_edge(previous, END)
    return graph.compile(checkpointer=checkpointer)


class WorkflowRun:
    """One run of a workflow, on its own checkpoint thread."""

    def __init__(
        self,
        workflow: Workflow,
        ctx: StepContext,
        checkpointer: Any,
        thread_id: str,
        on_step: OnStep,
    ) -> None:
        self.workflow = workflow
        self.ctx = ctx
        self.config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        self._graph = build_graph(workflow, ctx, on_step, checkpointer)

    async def start(self, inputs: dict[str, Any]) -> RunOutcome:
        preflight(self.workflow, self.ctx)
        return await self._drive({"values": resolve_inputs(self.workflow, inputs)})

    async def resume(self) -> RunOutcome:
        """Carry on after a crash or a failed step's cause being fixed. A
        declined approval stays declined here -- LangGraph replays the
        recorded answer -- so asking again is retry_from() that step."""
        return await self._drive(None)

    async def answer(self, approved: bool, note: str = "") -> RunOutcome:
        return await self._drive(Command(resume={"approved": approved, "note": note}))

    async def retry_from(self, step_id: str) -> RunOutcome:
        """Re-run from an earlier step, discarding what came after it."""
        target = f"step__{step_id}"
        async for snapshot in self._graph.aget_state_history(self.config):
            if snapshot.next == (target,):
                return await self._drive(None, snapshot.config)
        raise WorkflowNotRunnable(f"step {step_id!r} hasn't been reached in this run")

    async def _drive(self, payload: Any, config: dict[str, Any] | None = None) -> RunOutcome:
        try:
            await self._graph.ainvoke(payload, config or self.config)
        except StepFailed as exc:
            state = await self._graph.aget_state(self.config)
            return RunOutcome(
                "failed", step_id=exc.step_id, error=str(exc), values=state.values.get("values", {})
            )
        state = await self._graph.aget_state(self.config)
        values = state.values.get("values", {})
        for task in state.tasks:
            for pending in task.interrupts:
                request = pending.value if isinstance(pending.value, dict) else {}
                return RunOutcome(
                    "waiting", step_id=request.get("step_id"), request=request, values=values
                )
        return RunOutcome("completed", values=values)
