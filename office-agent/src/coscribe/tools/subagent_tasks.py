"""Background sub-agent runs: the spawn_agent counterpart to
tools/background_tasks.py's run_background_script -- storage, the live
in-process supervisor, and the model-callable read-only tools all live
here (no live LLM client needed for any of that); the piece that *does*
need one -- actually building and driving the sub-agent's own compiled
graph -- lives in runtime_lg/subagents.py's build_spawn_agent_background_tool
instead, same tools/ vs runtime_lg/ split tools/selfwake.py's own
docstring already explains for the identical reason.

**Why background spawn_agent can't reuse spawn_agent's own nested-interrupt
bridge.** runtime_lg/subagents.py's synchronous spawn_agent bridges a
child's pending approval up to the user by calling `interrupt()` itself,
*inside the parent's own tool-node execution* -- that only works because
the parent turn is still on the stack, synchronously blocked, at the
moment the child pauses. A background run has already returned a
task_id and control to the parent by the time its child might hit
HumanInTheLoopMiddleware; there is no parent turn left to bridge into.
So a child that pauses on approval here is left exactly where
runtime_lg/selfwake.py's `_SilentSocket` already leaves an unattended
top-level turn: paused, durable in its own checkpointer, discoverable
-- reported as status="blocked_on_approval" (see `_run_supervised`)
rather than silently bridged or silently dropped. A real, accepted v1
scope cut, not an oversight: resolving it would mean either giving a
background run some way to interrupt() a *live* connected browser tab
outside of any tool-node execution (a substantially different, riskier
mechanism) or restricting spawn_agent_background to approval-free tool
sets only -- neither justified before anyone has actually hit the gap.

**Pause is cooperative, not a hard cancel.** Checked between LangGraph
superstep boundaries (`stream_mode="values"` yields once per completed
step), so a pause request never tears down a tool call mid-flight, only
ever stops the child *between* steps -- the already-completed steps'
checkpoints are untouched, and resuming just continues the same
checkpointed thread. Verified against a real precedent before building
this: `claude-code-best/claude-code` (a from-scratch, MIT, non-Anthropic
reimplementation, read live on GitHub for this) implements its own
`/goal` pause/resume the identical way -- `pauseGoal`/`resumeGoal` just
flip a status flag on a plain in-memory record, no event emitter, no
cancel/abort; a driving loop checks the flag at its own next natural
checkpoint. No engineering reason to build something heavier here.

**Known v1 limitation, not fixed (same posture as background_tasks.py's
own documented one for run_background_script)**: `_LIVE`/`_TRANSCRIPTS`
below only exist in this coscribe-web process's own memory. If the
server restarts while a background sub-agent is mid-run, its on-disk
SubAgentTask record is orphaned at status="running" forever, and its
transcript (only ever held by the live compiled graph object, never
serialized) is lost -- the same "the desktop app is expected to stay
running for the life of one background thing" trade-off already made
for scripts, not re-litigated here.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..runtime.types import tool_metadata

VALID_STATUSES = ("running", "paused", "blocked_on_approval", "succeeded", "failed")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class SubAgentTask:
    task_id: str
    thread_id: str
    instructions: str
    prompt: str
    tool_names: str  # comma-separated, as given to spawn_agent_background
    description: str
    status: str  # one of VALID_STATUSES
    started_at: str
    finished_at: str | None = None
    result: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "thread_id": self.thread_id,
            "instructions": self.instructions,
            "prompt": self.prompt,
            "tool_names": self.tool_names,
            "description": self.description,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubAgentTask:
        return cls(
            task_id=data["task_id"],
            thread_id=data["thread_id"],
            instructions=data["instructions"],
            prompt=data["prompt"],
            tool_names=data.get("tool_names", ""),
            description=data["description"],
            status=data.get("status", "running"),
            started_at=data["started_at"],
            finished_at=data.get("finished_at"),
            result=data.get("result"),
            error=data.get("error"),
        )


class SubAgentTaskStore:
    """One JSON file per task, same shape as background_tasks.py's
    BackgroundTaskStore -- no separate log file, since a sub-agent's
    step-by-step transcript is read live off its own checkpointer
    (_TRANSCRIPTS below), not accumulated as unstructured text."""

    def __init__(self, state_dir: str | Path) -> None:
        self.root = Path(state_dir) / "subagent_tasks"

    def save(self, task: SubAgentTask) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path_for(task.task_id)
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(task.to_dict(), handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

    def load(self, task_id: str) -> SubAgentTask | None:
        path = self._path_for(task_id)
        if not path.exists():
            return None
        return SubAgentTask.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_for_thread(self, thread_id: str) -> list[SubAgentTask]:
        if not self.root.is_dir():
            return []
        tasks = [
            SubAgentTask.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(self.root.glob("*.json"))
        ]
        return [t for t in tasks if t.thread_id == thread_id]

    def _path_for(self, task_id: str) -> Path:
        return self.root / f"{task_id}.json"


@dataclass
class _LiveHandle:
    """Per-running-task pause control -- `pause_requested` is the flag
    `_run_supervised` polls between superstep boundaries;
    `resume_event` is what a paused supervisor actually awaits on
    (cleared while paused, set by resume_subagent_task)."""

    pause_requested: bool = False
    resume_event: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        self.resume_event.set()


# task_id -> pause control, for every task currently supervised by *this*
# process. Popped once the supervisor coroutine finishes (succeeded/
# failed/blocked_on_approval) -- a paused task stays in here (it isn't
# "finished"), which is exactly what lets resume_subagent_task find it.
_LIVE: dict[str, _LiveHandle] = {}

# task_id -> (the live compiled sub-agent graph, its child thread config)
# -- kept for the *life of the process*, not popped on completion, unlike
# _LIVE: get_subagent_transcript needs to keep reading a finished task's
# full step history (that's the whole point of the Sub Agents panel's
# "view what it did"), and the only place that history exists is this
# live graph object's own checkpointer -- nothing else serializes it.
# Same "process-lifetime cache, no explicit eviction" trade-off web/
# app.py's own `sessions` dict already makes for the main conversation.
_TRANSCRIPTS: dict[str, tuple[Any, dict[str, Any]]] = {}

# Same purpose as background_tasks.py's identical set -- keeps every
# in-flight supervisor coroutine referenced so asyncio can't silently
# garbage-collect it mid-run.
_RUNNING_SUPERVISORS: set[asyncio.Task[None]] = set()


def register_live_subagent(
    task_id: str, sub_agent: Any, child_config: dict[str, Any]
) -> _LiveHandle:
    """Called by runtime_lg/subagents.py's build_spawn_agent_background_tool
    right after starting a task -- registers both the pause-control handle
    and the transcript lookup in one place, so that module doesn't need to
    know about `_LIVE`/`_TRANSCRIPTS` as two separate dicts."""
    handle = _LiveHandle()
    _LIVE[task_id] = handle
    _TRANSCRIPTS[task_id] = (sub_agent, child_config)
    return handle


def track_supervisor_task(supervisor: asyncio.Task[None]) -> None:
    """Keeps a strong reference to a just-created run_supervised_subagent
    asyncio.Task so it can't be garbage-collected mid-run (see
    `_RUNNING_SUPERVISORS`'s own comment) -- a thin setter so callers
    outside this module (runtime_lg/subagents.py) don't need to reach
    into the underscore-prefixed set directly."""
    _RUNNING_SUPERVISORS.add(supervisor)
    supervisor.add_done_callback(_RUNNING_SUPERVISORS.discard)


async def run_supervised_subagent(
    store: SubAgentTaskStore, task: SubAgentTask, sub_agent: Any, child_config: dict[str, Any]
) -> None:
    """Owns one background sub-agent run end to end -- see this module's
    own docstring for why approvals aren't bridged and pause is
    cooperative. Runs as its own asyncio.Task, independent of whatever
    tool call started it."""
    from ..runtime_lg.messages import extract_text  # local: avoid import cycle at module load

    handle = _LIVE[task.task_id]
    prompt_sent = False
    try:
        while True:
            if handle.pause_requested:
                task.status = "paused"
                store.save(task)
                handle.resume_event.clear()
                await handle.resume_event.wait()
                task.status = "running"
                store.save(task)
                continue

            state = await sub_agent.aget_state(child_config)
            if not prompt_sent and not state.values:
                stream = sub_agent.astream(
                    {"messages": [{"role": "user", "content": task.prompt}]},
                    config=child_config,
                    stream_mode="values",
                )
                prompt_sent = True
            elif state.next:
                stream = sub_agent.astream(None, config=child_config, stream_mode="values")
            else:
                break  # nothing left to run -- graph reached its end

            paused_mid_stream = False
            async for _ in stream:
                if handle.pause_requested:
                    paused_mid_stream = True
                    break
            if paused_mid_stream:
                continue  # loop back to the top -- the pause branch handles it

            state = await sub_agent.aget_state(child_config)
            if state.next:
                # The stream ended on its own with steps still pending --
                # a real interrupt() (pending approval), not our own
                # pause request (already ruled out above).
                task.status = "blocked_on_approval"
                store.save(task)
                break
    except Exception as exc:
        task.status = "failed"
        task.error = str(exc)[:2000]
        task.finished_at = _now_iso()
        store.save(task)
        _LIVE.pop(task.task_id, None)
        return

    _LIVE.pop(task.task_id, None)
    if task.status == "blocked_on_approval":
        return

    final_state = await sub_agent.aget_state(child_config)
    messages = final_state.values.get("messages", [])
    last = messages[-1] if messages else None
    content = extract_text(getattr(last, "content", None)) if last is not None else ""
    task.result = content or "(sub-agent produced no text reply)"
    task.status = "succeeded"
    task.finished_at = _now_iso()
    store.save(task)


def pause_subagent_task(state_dir: str | Path, task_id: str) -> dict[str, Any]:
    """Request a running background sub-agent to pause at its next
    checkpoint -- called from the Sub Agents panel's pause button (a
    direct REST action, not a model tool: this is the human pausing it,
    same "not something the model decides for itself" reasoning
    kill_background_task's own approval gate reflects for a different
    action)."""
    store = SubAgentTaskStore(state_dir)
    task = store.load(task_id)
    if task is None:
        raise KeyError(f"No sub-agent task with id {task_id!r}")
    if task.status != "running":
        raise ValueError(f"Task {task_id!r} is {task.status!r}, not running")
    handle = _LIVE.get(task_id)
    if handle is None:
        raise RuntimeError(
            f"No live handle for sub-agent task {task_id!r} in this process "
            "(it may have started before the server last restarted)"
        )
    handle.pause_requested = True
    return task.to_dict()


def resume_subagent_task(state_dir: str | Path, task_id: str) -> dict[str, Any]:
    """Resume a paused background sub-agent from wherever its checkpointer
    left it."""
    store = SubAgentTaskStore(state_dir)
    task = store.load(task_id)
    if task is None:
        raise KeyError(f"No sub-agent task with id {task_id!r}")
    if task.status != "paused":
        raise ValueError(f"Task {task_id!r} is {task.status!r}, not paused")
    handle = _LIVE.get(task_id)
    if handle is None:
        raise RuntimeError(
            f"No live handle for sub-agent task {task_id!r} in this process "
            "(it may have started before the server last restarted)"
        )
    handle.pause_requested = False
    handle.resume_event.set()
    return task.to_dict()


def get_subagent_transcript(task_id: str) -> dict[str, Any] | None:
    """Full step-by-step history of a background sub-agent's own
    conversation -- what it did (tool calls/results) and what it said --
    read straight off its live checkpointer via the same serializer the
    main conversation's own history replay uses (runtime_lg/messages.py's
    serialize_history_for_ws_lg), so the Sub Agents panel can render a
    sub-agent's transcript with the identical shape/visual language as
    the main chat log. None if this process has no live record of
    task_id (never started here, or lost across a server restart -- see
    this module's own docstring)."""
    from ..runtime_lg.messages import serialize_history_for_ws_lg

    live = _TRANSCRIPTS.get(task_id)
    if live is None:
        return None
    sub_agent, child_config = live
    state = sub_agent.get_state(child_config)
    messages = list(state.values.get("messages", [])) if state.values else []
    return {"entries": serialize_history_for_ws_lg(messages)}


def build_subagent_task_tools(thread_id: str, state_dir: str | Path) -> list[Callable[..., Any]]:
    """Read-only, model-callable tools -- mirrors background_tasks.py's
    check_background_task/list_background_tasks shape exactly. Starting
    a background sub-agent and pausing/resuming one are deliberately
    NOT here: starting needs `model`/`available_tools`, so it's built in
    runtime_lg/subagents.py's build_spawn_agent_background_tool instead
    (same reason spawn_agent itself isn't built here); pausing/resuming
    is a human action from the panel, not a model tool (see
    pause_subagent_task's own docstring)."""
    store = SubAgentTaskStore(state_dir)

    def list_subagent_tasks() -> list[dict[str, Any]]:
        """List this conversation's own background sub-agents (running or
        finished) started via spawn_agent_background -- use this to check
        what's currently in flight without needing a specific task_id."""
        return [t.to_dict() for t in store.list_for_thread(thread_id)]

    def check_subagent_task(task_id: str) -> dict[str, Any]:
        """Check a background sub-agent's status -- its result once it
        succeeds, or its error if it failed. Call this to poll instead of
        using wake_on_subagent when you'd rather keep working on
        something else in the meantime and check back yourself.

        Args:
            task_id: id returned by spawn_agent_background.
        """
        task = store.load(task_id)
        if task is None or task.thread_id != thread_id:
            raise KeyError(f"No sub-agent task with id {task_id!r} in this conversation")
        return task.to_dict()

    return [
        tool_metadata(list_subagent_tasks, risk_category="READ", category="subagent_tasks"),
        tool_metadata(check_subagent_task, risk_category="READ", category="subagent_tasks"),
    ]
