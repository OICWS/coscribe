"""Runs a Workflow as a LangGraph StateGraph, one node per step, over the
same checkpointer as conversations -- so a run that stops (a failed step,
a pending approval, a crash) picks up exactly where it stopped:

- A branch is a node whose outgoing edge depends on its condition; a loop
  is a start node, a next-item node and a collect node the body cycles
  through, with each pass's position in state. So everything below holds
  inside a loop too, for the pass that stopped.

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

from .refs import evaluate, render_text, render_value, resolve
from .spec import (
    ApprovalStep,
    BranchStep,
    CheckStep,
    LLMStep,
    LoopStep,
    OutputField,
    Placed,
    ScriptStep,
    Step,
    ToolStep,
    Workflow,
    step_output,
    walk,
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
    # Which pass of each loop around the step this is, outermost first --
    # empty outside loops. A step's record is one per pass.
    iteration: list[int] = field(default_factory=list)

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
    # Per loop id: {"index", "items", "results", "started_at"} of its
    # current pass.
    loops: Annotated[dict[str, Any], _merge]


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


_NO_STEPS = "This workflow has no steps yet -- add some on its task page."


def preflight(workflow: Workflow, ctx: StepContext) -> None:
    """Refuse a workflow that can't run here, before any step does."""
    if not workflow.steps:
        raise WorkflowNotRunnable(_NO_STEPS)
    for placed in walk(workflow.steps):
        step, where = placed.step, f"step {placed.number}"
        if isinstance(step, ToolStep):
            if step.tool in UNAVAILABLE_TOOLS:
                raise WorkflowNotRunnable(f"{where}: {step.tool} can't be used in a workflow")
            if step.tool not in ctx.tools:
                raise WorkflowNotRunnable(f"{where}: there's no tool called {step.tool!r}")
        if isinstance(step, LoopStep) and not step.steps:
            raise WorkflowNotRunnable(f"{where}: the loop has no steps to repeat yet")
        if isinstance(step, BranchStep) and not (step.then or step.otherwise):
            raise WorkflowNotRunnable(f"{where}: the branch has no steps on either side yet")


def resolve_inputs(workflow: Workflow, given: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for spec in workflow.inputs:
        value = given.get(spec.name, spec.default)
        if value is None or value == "":
            raise WorkflowNotRunnable(f"input {spec.name!r} needs a value")
        values[spec.name] = value
    return values


def _supersteps(steps: list[Step]) -> int:
    """The most graph steps `steps` can take -- LangGraph stops a run that
    goes past its recursion limit, and a loop legitimately goes round."""
    total = 0
    for step in steps:
        if isinstance(step, BranchStep):
            total += 1 + max(_supersteps(step.then), _supersteps(step.otherwise))
        elif isinstance(step, LoopStep):
            total += 2 + step.max_items * (_supersteps(step.steps) + 2)
        else:
            total += 1
    return total


_ITEM_LABEL_CHARS = 60
_LABEL_KEYS = ("name", "title", "path", "file", "id", "text")


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _ITEM_LABEL_CHARS else text[: _ITEM_LABEL_CHARS - 1] + "…"


def _item_labels(items: list[Any]) -> list[str]:
    """A few words naming each of a loop's items, for picking a pass in the
    run view -- the items themselves can be large. For records, the first
    field that tells them apart (search hits all share their `path`)."""
    records = [item for item in items if isinstance(item, dict)]
    key = None
    if records and len(records) == len(items):
        present = [k for k in _LABEL_KEYS if all(isinstance(r.get(k), str) for r in records)]
        key = next((k for k in present if len({r[k] for r in records}) == len(records)), None)
        key = key or next(iter(present), None)
    labels = []
    for item in items:
        if key is not None:
            labels.append(_short(item[key]))
        elif isinstance(item, str):
            labels.append(_short(item))
        else:
            labels.append(_short(json.dumps(item, ensure_ascii=False, default=str)))
    return labels


def _loop_progress(loop: dict[str, Any]) -> dict[str, Any]:
    return {"done": loop["index"], "total": len(loop["items"]), "items": loop["labels"]}


def build_graph(workflow: Workflow, ctx: StepContext, on_step: OnStep, checkpointer: Any) -> Any:
    placed = {p.step.id: p for p in walk(workflow.steps)}

    async def emit(record: StepRecord) -> None:
        result = on_step(record)
        if inspect.isawaitable(result):
            await result

    def iteration(state: _State, step: Step) -> list[int]:
        loops = state.get("loops") or {}
        return [loops[loop_id]["index"] for loop_id in placed[step.id].loops]

    async def fail(step: Step, it: list[int], started: str, exc: Exception) -> StepFailed:
        if isinstance(exc, StepFailed):
            failure = exc
        else:
            failure = StepFailed(str(exc) or type(exc).__name__)
        failure.step_id = step.id
        await emit(
            StepRecord(
                step.id,
                "failed",
                started,
                _now(),
                error=str(failure),
                checks=failure.checks,
                iteration=it,
            )
        )
        return failure

    def make_node(step: Step) -> Callable[[_State], Awaitable[dict[str, Any]]]:
        async def run(state: _State) -> dict[str, Any]:
            values = state["values"]
            it = iteration(state, step)
            started = _now()
            if isinstance(step, ApprovalStep):
                return await _approval(step, values, started, it)
            await emit(StepRecord(step.id, "running", started_at=started, iteration=it))
            checks = None
            output: Any = None
            try:
                if isinstance(step, ToolStep):
                    output = await _run_tool(step, values, ctx)
                elif isinstance(step, ScriptStep):
                    output = await _run_script(step, values, ctx)
                elif isinstance(step, LLMStep):
                    output = await _run_llm(step, values, ctx)
                elif isinstance(step, CheckStep):
                    checks = _run_check(step, values)
                elif isinstance(step, BranchStep):
                    held, left, right = evaluate(step.condition, values)
                    checks = [{"held": held, "left": preview(left), "right": preview(right)}]
                    output = {"arm": "then" if held else "otherwise"}
            except Exception as exc:  # noqa: BLE001 -- any step error stops the run, recorded
                failure = await fail(step, it, started, exc)
                if failure is exc:
                    raise
                raise failure from exc
            await emit(
                StepRecord(
                    step.id,
                    "done",
                    started,
                    _now(),
                    output=preview(output),
                    checks=checks,
                    iteration=it,
                )
            )
            save_as = None if isinstance(step, BranchStep) else step_output(step)
            return {"values": {save_as: output}} if save_as else {"values": {}}

        return run

    async def _approval(
        step: ApprovalStep, values: dict[str, Any], started: str, it: list[int]
    ) -> dict[str, Any]:
        checks = None
        if step.when is not None:
            held, left, right = evaluate(step.when, values)
            checks = [{"held": held, "left": preview(left), "right": preview(right)}]
            if not held:
                await emit(
                    StepRecord(step.id, "skipped", started, _now(), checks=checks, iteration=it)
                )
                return {"values": {}}
        message = render_text(step.message, values)
        await emit(
            StepRecord(step.id, "waiting", started, output=message, checks=checks, iteration=it)
        )
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
                    iteration=it,
                )
            )
            raise StepFailed(error, step_id=step.id)
        await emit(
            StepRecord(
                step.id,
                "done",
                started,
                _now(),
                output=message,
                note=note or None,
                iteration=it,
            )
        )
        return {"values": {}}

    def loop_start(step: LoopStep) -> Callable[[_State], Awaitable[dict[str, Any]]]:
        async def run(state: _State) -> dict[str, Any]:
            it = iteration(state, step)
            started = _now()
            try:
                items = resolve(step.over, state["values"])
                if not isinstance(items, list):
                    raise StepFailed(
                        f"{step.over} is {type(items).__name__}, not a list to go through"
                    )
                if len(items) > step.max_items:
                    raise StepFailed(
                        f"{step.over} has {len(items)} items, more than this loop's limit of "
                        f"{step.max_items}"
                    )
            except Exception as exc:  # noqa: BLE001 -- recorded like any step's failure
                failure = await fail(step, it, started, exc)
                if failure is exc:
                    raise
                raise failure from exc
            loop = {
                "index": 0,
                "items": items,
                "labels": _item_labels(items),
                "results": [],
                "started_at": started,
            }
            await emit(
                StepRecord(step.id, "running", started, output=_loop_progress(loop), iteration=it)
            )
            return {"loops": {step.id: loop}}

        return run

    def loop_next(step: LoopStep) -> Callable[[_State], Awaitable[dict[str, Any]]]:
        async def run(state: _State) -> dict[str, Any]:
            loop = state["loops"][step.id]
            if loop["index"] < len(loop["items"]):
                return {"values": {step.item: loop["items"][loop["index"]]}}
            output = {**_loop_progress(loop), "collected": preview(loop["results"])}
            await emit(
                StepRecord(
                    step.id,
                    "done",
                    loop["started_at"],
                    _now(),
                    output=output,
                    iteration=iteration(state, step),
                )
            )
            return {"values": {step.save_as: loop["results"]}} if step.save_as else {"values": {}}

        return run

    def loop_collect(step: LoopStep) -> Callable[[_State], Awaitable[dict[str, Any]]]:
        async def run(state: _State) -> dict[str, Any]:
            loop = state["loops"][step.id]
            it = iteration(state, step)
            try:
                value = resolve(step.collect, state["values"]) if step.collect else None
            except Exception as exc:  # noqa: BLE001 -- recorded like any step's failure
                failure = await fail(step, it, loop["started_at"], exc)
                if failure is exc:
                    raise
                raise failure from exc
            loop = {**loop, "index": loop["index"] + 1, "results": [*loop["results"], value]}
            await emit(
                StepRecord(
                    step.id,
                    "running",
                    loop["started_at"],
                    output=_loop_progress(loop),
                    iteration=it,
                )
            )
            return {"loops": {step.id: loop}}

        return run

    graph = StateGraph(_State)

    def wire(steps: list[Step], after: str) -> None:
        for index, step in enumerate(steps):
            following = _node_name(steps[index + 1]) if index + 1 < len(steps) else after
            node = _node_name(step)
            if isinstance(step, BranchStep):
                graph.add_node(node, cast(Any, make_node(step)))
                graph.add_conditional_edges(
                    node,
                    _branch_router(step),
                    {
                        "then": _node_name(step.then[0]) if step.then else following,
                        "otherwise": _node_name(step.otherwise[0]) if step.otherwise else following,
                    },
                )
                wire(step.then, following)
                wire(step.otherwise, following)
            elif isinstance(step, LoopStep):
                next_node, collect_node = f"next__{step.id}", f"collect__{step.id}"
                graph.add_node(node, cast(Any, loop_start(step)))
                graph.add_node(next_node, cast(Any, loop_next(step)))
                graph.add_node(collect_node, cast(Any, loop_collect(step)))
                graph.add_edge(node, next_node)
                graph.add_conditional_edges(
                    next_node,
                    _loop_router(step),
                    {
                        "body": _node_name(step.steps[0]) if step.steps else collect_node,
                        "done": following,
                    },
                )
                wire(step.steps, collect_node)
                graph.add_edge(collect_node, next_node)
            else:
                graph.add_node(node, cast(Any, make_node(step)))
                graph.add_edge(node, following)

    wire(workflow.steps, END)
    graph.add_edge(START, _node_name(workflow.steps[0]))
    return graph.compile(checkpointer=checkpointer)


def _branch_router(step: BranchStep) -> Callable[[_State], str]:
    # Re-evaluating is safe: the branch node already evaluated the same
    # values without error, and nothing has changed them since.
    def route(state: _State) -> str:
        held, _left, _right = evaluate(step.condition, state["values"])
        return "then" if held else "otherwise"

    return route


def _loop_router(step: LoopStep) -> Callable[[_State], str]:
    def route(state: _State) -> str:
        loop = state["loops"][step.id]
        return "body" if loop["index"] < len(loop["items"]) else "done"

    return route


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
        self.on_step = on_step
        self._placed: dict[str, Placed] = {p.step.id: p for p in walk(workflow.steps)}
        self.config: dict[str, Any] = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": _supersteps(workflow.steps) + 10,
        }
        self._graph = build_graph(workflow, ctx, on_step, checkpointer) if workflow.steps else None

    async def start(self, inputs: dict[str, Any]) -> RunOutcome:
        preflight(self.workflow, self.ctx)
        return await self._drive({"values": resolve_inputs(self.workflow, inputs), "loops": {}})

    async def resume(self) -> RunOutcome:
        """Carry on after a crash or a failed step's cause being fixed. A
        declined approval stays declined here -- LangGraph replays the
        recorded answer -- so asking again is retry_from() that step."""
        return await self._drive(None)

    async def answer(self, approved: bool, note: str = "") -> RunOutcome:
        return await self._drive(Command(resume={"approved": approved, "note": note}))

    async def retry_from(self, step_id: str) -> RunOutcome:
        """Re-run from an earlier step, discarding what came after it --
        inside a loop, from that step's most recent pass."""
        target = f"step__{step_id}"
        async for snapshot in self._compiled.aget_state_history(self.config):
            if snapshot.next == (target,):
                config = {**snapshot.config, "recursion_limit": self.config["recursion_limit"]}
                return await self._drive(None, config)
        raise WorkflowNotRunnable(f"step {step_id!r} hasn't been reached in this run")

    @property
    def _compiled(self) -> Any:
        if self._graph is None:
            raise WorkflowNotRunnable(_NO_STEPS)
        return self._graph

    async def _emit(self, record: StepRecord) -> None:
        result = self.on_step(record)
        if inspect.isawaitable(result):
            await result

    def _loops_around(self, node: str) -> tuple[str, ...]:
        kind, _, step_id = node.partition("__")
        placed = self._placed.get(step_id)
        if placed is None:
            return ()
        # A loop's own next-item and collect nodes are inside it.
        return (*placed.loops, step_id) if kind in ("next", "collect") else placed.loops

    async def _mark_loops(
        self,
        node: str,
        status: StepStatus,
        error: str | None,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Record the loops around a stopped (or resuming) step as stopped
        (or running) at that pass, so a loop's own row says where it is."""
        around = self._loops_around(node)
        if not around:
            return
        state = await self._compiled.aget_state(config or self.config)
        loops = (state.values or {}).get("loops") or {}
        for depth, loop_id in enumerate(around):
            loop = loops.get(loop_id)
            if loop is None:
                continue
            await self._emit(
                StepRecord(
                    loop_id,
                    status,
                    loop["started_at"],
                    output=_loop_progress(loop),
                    error=error and f"Stopped at item {loop['index'] + 1}: {error}",
                    iteration=[loops[outer]["index"] for outer in around[:depth]],
                )
            )

    async def _drive(self, payload: Any, config: dict[str, Any] | None = None) -> RunOutcome:
        graph = self._compiled
        if payload is None or isinstance(payload, Command):
            state = await graph.aget_state(config or self.config)
            for node in state.next:
                await self._mark_loops(node, "running", None, config)
        try:
            await graph.ainvoke(payload, config or self.config)
        except StepFailed as exc:
            await self._mark_loops(f"step__{exc.step_id}", "failed", str(exc))
            state = await graph.aget_state(self.config)
            return RunOutcome(
                "failed", step_id=exc.step_id, error=str(exc), values=state.values.get("values", {})
            )
        state = await graph.aget_state(self.config)
        values = state.values.get("values", {})
        for task in state.tasks:
            for pending in task.interrupts:
                request = pending.value if isinstance(pending.value, dict) else {}
                await self._mark_loops(f"step__{request.get('step_id')}", "waiting", None)
                return RunOutcome(
                    "waiting", step_id=request.get("step_id"), request=request, values=values
                )
        return RunOutcome("completed", values=values)
