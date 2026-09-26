"""Delegated sub-agent runs: the task record, its store, and the
in-process registry of live runs. Driving a run (it needs a model and a
compiled graph) lives in runtime_lg/subagents.py; this module stays free
of LLM clients, the same tools/ vs runtime_lg/ split tools/selfwake.py
describes.

A run's transcript exists only in its live graph's in-memory
checkpointer, so a server restart loses it and leaves a "running" record
behind -- the same trade-off background_tasks.py makes for scripts.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..runtime.types import tool_metadata

VALID_STATUSES = ("running", "needs_approval", "succeeded", "failed", "stopped")
FINISHED_STATUSES = ("succeeded", "failed", "stopped")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class SubAgentTask:
    task_id: str
    thread_id: str
    instructions: str
    prompt: str
    tool_names: str  # comma-separated as the parent gave it; empty is "the parent's tools"
    description: str
    status: str  # one of VALID_STATUSES
    started_at: str
    finished_at: str | None = None
    result: str | None = None
    error: str | None = None
    model: str = ""
    tokens: int = 0
    tool_uses: int = 0
    # The latest call, {"tool_name", "arguments"}, for "what is it doing".
    last_tool: dict[str, Any] | None = None
    # The approval_required payload it's waiting on, if any.
    pending_approval: dict[str, Any] | None = None
    # Whether the parent is waiting on the result (spawn_agent) or went
    # on without it (spawn_agent_background).
    background: bool = False

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
            "model": self.model,
            "tokens": self.tokens,
            "tool_uses": self.tool_uses,
            "last_tool": self.last_tool,
            "pending_approval": self.pending_approval,
            "background": self.background,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubAgentTask:
        status = data.get("status", "running")
        # Records from before stop replaced pause, and before approvals
        # could be answered, read as the nearest current state.
        status = {"paused": "stopped", "blocked_on_approval": "stopped"}.get(status, status)
        return cls(
            task_id=data["task_id"],
            thread_id=data["thread_id"],
            instructions=data["instructions"],
            prompt=data["prompt"],
            tool_names=data.get("tool_names", ""),
            description=data["description"],
            status=status,
            started_at=data["started_at"],
            finished_at=data.get("finished_at"),
            result=data.get("result"),
            error=data.get("error"),
            model=data.get("model", ""),
            tokens=data.get("tokens", 0),
            tool_uses=data.get("tool_uses", 0),
            last_tool=data.get("last_tool"),
            pending_approval=data.get("pending_approval"),
            background=data.get("background", False),
        )


class SubAgentTaskStore:
    """One JSON file per task."""

    def __init__(self, state_dir: str | Path) -> None:
        self.root = Path(state_dir) / "subagent_tasks"

    def save(self, task: SubAgentTask) -> None:
        with _WRITE_LOCK:
            self._write(task)

    def _write(self, task: SubAgentTask) -> None:
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
        task = SubAgentTask.from_dict(json.loads(path.read_text(encoding="utf-8")))
        return self._settle_if_orphaned(task)

    def list_for_thread(self, thread_id: str) -> list[SubAgentTask]:
        if not self.root.is_dir():
            return []
        tasks = [
            SubAgentTask.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in self.root.glob("*.json")
        ]
        mine = sorted((t for t in tasks if t.thread_id == thread_id), key=lambda t: t.started_at)
        return [self._settle_if_orphaned(task) for task in mine]

    def _settle_if_orphaned(self, task: SubAgentTask) -> SubAgentTask:
        """A run this process isn't driving was cut off by a restart; left as
        "running" it would show as working forever. Re-read under the write
        lock: the run may have saved its final state and left _RUNNING since
        `task` was read."""
        if task.status in FINISHED_STATUSES or task.task_id in _RUNNING:
            return task
        with _WRITE_LOCK:
            path = self._path_for(task.task_id)
            if not path.exists():
                return task
            fresh = SubAgentTask.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if fresh.status in FINISHED_STATUSES or fresh.task_id in _RUNNING:
                return fresh
            fresh.status = "stopped"
            fresh.pending_approval = None
            fresh.error = fresh.error or "The app restarted while it was running."
            fresh.finished_at = fresh.finished_at or _now_iso()
            self._write(fresh)
            return fresh

    def delete(self, task_id: str) -> None:
        self._path_for(task_id).unlink(missing_ok=True)

    def _path_for(self, task_id: str) -> Path:
        return self.root / f"{task_id}.json"


# Store writes come from the event loop and, for reads that settle an
# orphaned record, from any thread that reads the store.
_WRITE_LOCK = threading.Lock()

# task_id -> the asyncio.Task driving it, while it runs in this process.
# Holding it here also keeps it from being garbage-collected mid-run.
_RUNNING: dict[str, asyncio.Task[Any]] = {}

# task_id -> (its compiled graph, its child thread config), kept after it
# finishes: the only copy of what it did lives in that graph's checkpointer.
_TRANSCRIPTS: dict[str, tuple[Any, dict[str, Any]]] = {}


def register_subagent_run(
    task_id: str, runner: asyncio.Task[Any], sub_agent: Any, child_config: dict[str, Any]
) -> None:
    _RUNNING[task_id] = runner
    _TRANSCRIPTS[task_id] = (sub_agent, child_config)
    runner.add_done_callback(lambda _: _RUNNING.pop(task_id, None))


def running_subagent(task_id: str) -> asyncio.Task[Any] | None:
    return _RUNNING.get(task_id)


def stop_subagent_task(state_dir: str | Path, task_id: str) -> dict[str, Any]:
    """Cancel a running sub-agent. Its runner records the "stopped" status
    as it unwinds, so the returned record may still read as running."""
    task = SubAgentTaskStore(state_dir).load(task_id)
    if task is None:
        raise KeyError(f"No sub-agent task with id {task_id!r}")
    runner = _RUNNING.get(task_id)
    if task.status in FINISHED_STATUSES or runner is None:
        raise ValueError(f"Task {task_id!r} isn't running")
    runner.cancel()
    return task.to_dict()


def forget_finished_subagents(state_dir: str | Path, thread_id: str) -> int:
    store = SubAgentTaskStore(state_dir)
    removed = 0
    for task in store.list_for_thread(thread_id):
        if task.status in FINISHED_STATUSES:
            store.delete(task.task_id)
            _TRANSCRIPTS.pop(task.task_id, None)
            removed += 1
    return removed


def get_subagent_transcript(task_id: str) -> dict[str, Any] | None:
    """The run's own conversation, in the same shape the main chat's
    history uses, so the panel renders it with the same components. None
    when this process has no record of it (see the module docstring)."""
    from ..runtime_lg.messages import serialize_history_for_ws_lg

    live = _TRANSCRIPTS.get(task_id)
    if live is None:
        return None
    sub_agent, child_config = live
    state = sub_agent.get_state(child_config)
    messages = list(state.values.get("messages", [])) if state.values else []
    return {"entries": serialize_history_for_ws_lg(messages)}


def build_subagent_task_tools(thread_id: str, state_dir: str | Path) -> list[Callable[..., Any]]:
    """Read-only tools for checking on delegated runs. Starting one needs a
    model, so it lives in runtime_lg/subagents.py; stopping one is the
    user's call, from the panel."""
    store = SubAgentTaskStore(state_dir)

    def list_subagent_tasks() -> list[dict[str, Any]]:
        """List this conversation's sub-agents, running or finished."""
        return [t.to_dict() for t in store.list_for_thread(thread_id)]

    def check_subagent_task(task_id: str) -> dict[str, Any]:
        """Check a background sub-agent's status -- its result once it
        succeeds, or its error if it failed. Call this to poll instead of
        using wake_on_subagent when you'd rather keep working on
        something else in the meantime and check back yourself.

        Args:
            task_id: id returned by spawn_agent_background (or spawn_agent).
        """
        task = store.load(task_id)
        if task is None or task.thread_id != thread_id:
            raise KeyError(f"No sub-agent task with id {task_id!r} in this conversation")
        return task.to_dict()

    return [
        tool_metadata(list_subagent_tasks, risk_category="READ", category="subagent_tasks"),
        tool_metadata(check_subagent_task, risk_category="READ", category="subagent_tasks"),
    ]
