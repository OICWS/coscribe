"""Task tracking: a lightweight, per-thread todo list the Coordinator can use
to plan and track progress on multi-step requests.

This is Coordinator-native behavior (see ARCHITECTURE.md's "Task 追踪工具",
modeled on claude-code's TaskCreate/TaskUpdate tools), not something borrowed
via MCP/Skill, so it ships as a built-in tool family alongside the file tools.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..runtime.types import tool_metadata
from ._file_locks import file_lock

VALID_STATUSES = ("pending", "in_progress", "completed")


class TaskToolkit:
    """A todo list scoped to one conversation thread.

    Persisted to its own JSON file, thread-id-derived like the checkpointer's
    own storage, but kept as a separate file: tasks aren't part of the
    message history the checkpointer manages, and are read/written
    synchronously on every mutation rather than only at the end of a run.
    """

    def __init__(self, thread_id: str, state_dir: str | Path) -> None:
        self._path = Path(state_dir) / f"{quote(thread_id, safe='')}.tasks.json"
        self._tasks: list[dict[str, Any]] = self._load()
        self._next_id = max((int(task["id"]) for task in self._tasks), default=0) + 1

    def _load(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        with self._path.open(encoding="utf-8") as handle:
            return list(json.load(handle))

    def _save(self) -> None:
        # Live-hit bug, same class as write_xlsx's (see tools/_file_locks.py):
        # task_create is risk_category="READ" (deliberately ungated, see
        # build_task_tools' docstring below), so a real model's several
        # task_create calls in one AIMessage don't even get the light
        # serialization approval-gated tools get from
        # _resolve_pending_approvals -- LangGraph dispatches them fully
        # concurrently. Two threads both computing the same fixed
        # `.with_suffix(".tmp")` name and racing os.replace() means
        # whichever thread's replace() loses finds its own .tmp already
        # consumed by the other's -- confirmed live:
        # "FileNotFoundError: [Errno 2] No such file or directory:
        # '....tasks.tmp' -> '....tasks.json'". Locking here doesn't need
        # to cover create()/update()'s in-memory self._tasks mutation
        # (list.append()/dict item assignment are already safe under the
        # GIL, and self._tasks is loaded once at __init__, not re-read per
        # call) -- only the tmp-file-then-rename sequence itself races.
        with file_lock(self._path.resolve()):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._path.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(self._tasks, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)

    def create(self, content: str) -> dict[str, Any]:
        task = {"id": str(self._next_id), "content": content, "status": "pending"}
        self._next_id += 1
        self._tasks.append(task)
        self._save()
        return task

    def update(self, task_id: str, status: str) -> dict[str, Any]:
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")
        for task in self._tasks:
            if task["id"] == task_id:
                task["status"] = status
                self._save()
                return task
        raise KeyError(f"No task with id {task_id!r}")

    def list_tasks(self) -> list[dict[str, Any]]:
        return list(self._tasks)


def build_task_tools(thread_id: str, state_dir: str | Path) -> list[Callable[..., Any]]:
    """Return the task-tracking tool callables, bound to one conversation thread."""
    toolkit = TaskToolkit(thread_id, state_dir)

    def task_create(content: str) -> dict[str, Any]:
        """Add a new task to the plan for this conversation.

        Args:
            content: short imperative description of the task, e.g. "Read config.py"
        """
        return toolkit.create(content)

    def task_update(task_id: str, status: str) -> dict[str, Any]:
        """Update a task's status as you make progress. Mark each task completed
        as soon as it's done; don't batch updates.

        Args:
            task_id: id of the task to update, as returned by task_create/task_list
            status: one of "pending", "in_progress", "completed"
        """
        return toolkit.update(task_id, status)

    def task_list() -> list[dict[str, Any]]:
        """List all tasks in the current plan, in creation order."""
        return toolkit.list_tasks()

    return [
        # READ, not WRITE_LOCAL, despite writing a state file -- deliberately
        # kept ungated (preserving current behavior): task tracking is
        # thread-scoped, disposable bookkeeping the model uses constantly as
        # part of ordinary multi-step work (visible throughout this
        # project's own live-verification sessions as routine
        # task_create/task_update calls), unlike memory.py's remember,
        # which persists a durable, cross-session, system-prompt-injected
        # fact -- gating this the same way would make approval-fatigue the
        # normal state of using the tool at all, for something with no
        # real externally-visible consequence.
        tool_metadata(task_create, risk_category="READ", category="tasks"),
        tool_metadata(task_update, risk_category="READ", category="tasks"),
        tool_metadata(task_list, risk_category="READ", category="tasks"),
    ]
