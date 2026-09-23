"""Suspend/resume primitives (ROADMAP.md Phase 4): sleep_for/sleep_until,
wake_on_task, wake_on_subagent, wake_on_event -- let the model end a turn
saying "check back later" instead of the *user* having to schedule
anything. Distinct from tools/scheduled_tasks.py: a wake resumes *this*
conversation once; a scheduled task runs on its own schedule in fresh
conversations of its own.

This module holds the data model, on-disk storage (WakeStore/
SignalStore), and the model-callable tools -- all plain, synchronous state
mutations needing no live LLM client or checkpointer. The *resume* half --
actually waking a thread back up, which needs a live checkpointer and a
way to construct/reuse a ChatSessionLG -- lives in runtime_lg/selfwake.py.

Kinds of wake, deliberately scoped to backing that's real today, not
hypothetical:
- "timer": sleep_for/sleep_until -- wake at a wall-clock time.
- "task": wake_on_task(task_id) -- task_id is a tools/background_tasks.py
  BackgroundTask.task_id. Validated against that store up front, so a
  wake nothing could ever resolve is never registered.
- "subagent": wake_on_subagent(task_id) -- task_id is a tools/
  subagent_tasks.py SubAgentTask.task_id, a background spawn_agent_
  background run -- a separate id space from "task". Unlike a background script (only ever
  "running" or a terminal status), a sub-agent task can also be
  "paused" or "blocked_on_approval" -- both non-"running", so _is_due
  treats them as due too, same as any other terminal status (see
  runtime_lg/selfwake.py's _is_due for "subagent").
- "event": wake_on_event(event_key) -- resolved by a signal_event call
  (this thread, another thread, or -- once ROADMAP.md Phase 5c's Slack/
  webhook work lands -- an external event source calling the same
  SignalStore this module already provides). No webhook receiver exists
  yet; signal_event is the concrete extension point that future work hangs
  off of, not a webhook receiver built prematurely now.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..runtime.types import tool_metadata
from .background_tasks import BackgroundTaskStore
from .subagent_tasks import SubAgentTaskStore

VALID_KINDS = ("timer", "task", "subagent", "event")
VALID_STATUSES = ("pending", "woken", "cancelled")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp, got {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


@dataclass
class WakeRequest:
    wake_id: str
    thread_id: str
    kind: str  # one of VALID_KINDS -- plain str rather than Literal:
    # aisuite's schema inference has broken on exotic annotations before.
    reason: str
    created_at: str
    status: str = "pending"  # "pending" | "woken" | "cancelled"
    wake_at: str | None = None  # kind="timer": ISO timestamp to fire at
    task_id: str | None = None  # kind="task": a BackgroundTask.task_id
    subagent_task_id: str | None = None  # kind="subagent": a SubAgentTask.task_id
    event_key: str | None = None  # kind="event"
    woken_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "wake_id": self.wake_id,
            "thread_id": self.thread_id,
            "kind": self.kind,
            "reason": self.reason,
            "created_at": self.created_at,
            "status": self.status,
            "wake_at": self.wake_at,
            "task_id": self.task_id,
            "subagent_task_id": self.subagent_task_id,
            "event_key": self.event_key,
            "woken_at": self.woken_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WakeRequest:
        return cls(
            wake_id=data["wake_id"],
            thread_id=data["thread_id"],
            kind=data["kind"],
            reason=data["reason"],
            created_at=data["created_at"],
            status=data.get("status", "pending"),
            wake_at=data.get("wake_at"),
            task_id=data.get("task_id"),
            subagent_task_id=data.get("subagent_task_id"),
            event_key=data.get("event_key"),
            woken_at=data.get("woken_at"),
        )


class WakeStore:
    """One JSON file per wake *request* -- a wake is a single pending
    request, not a reusable named definition."""

    def __init__(self, state_dir: str | Path) -> None:
        self.root = Path(state_dir) / "wakes"

    def save(self, wake: WakeRequest) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path_for(wake.wake_id)
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(wake.to_dict(), handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

    def load(self, wake_id: str) -> WakeRequest | None:
        path = self._path_for(wake_id)
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as handle:
            return WakeRequest.from_dict(json.load(handle))

    def list_pending(self) -> list[WakeRequest]:
        """Every "pending" wake across *every* thread -- the poller needs
        this global view; list_wakes (the model-facing tool below) filters
        this down to one thread."""
        return [w for w in self._list_all() if w.status == "pending"]

    def _list_all(self) -> list[WakeRequest]:
        if not self.root.is_dir():
            return []
        return [WakeRequest.from_dict(json.loads(p.read_text(encoding="utf-8")))
                for p in sorted(self.root.glob("*.json"))]

    def _path_for(self, wake_id: str) -> Path:
        return self.root / f"{wake_id}.json"


class SignalStore:
    """One JSON file per event *key*, overwritten on every signal_event
    call -- only the most recent firing of a given key matters for
    resolving a pending wake_on_event."""

    def __init__(self, state_dir: str | Path) -> None:
        self.root = Path(state_dir) / "signals"

    def signal(self, event_key: str, payload: str) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        record = {"event_key": event_key, "signaled_at": _now_iso(), "payload": payload}
        path = self._path_for(event_key)
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return record

    def fired_since(self, event_key: str, since_iso: str) -> bool:
        path = self._path_for(event_key)
        if not path.exists():
            return False
        record = json.loads(path.read_text(encoding="utf-8"))
        return str(record["signaled_at"]) >= since_iso

    def _path_for(self, event_key: str) -> Path:
        return self.root / f"{quote(event_key, safe='')}.json"


def build_selfwake_tools(thread_id: str, state_dir: str | Path) -> list[Callable[..., Any]]:
    """Return the selfwake tool callables, bound to one conversation thread.

    Wired into coordinator.py next to build_task_tools.
    The actual resume -- reading these pending requests back and restarting
    a sleeping thread -- happens out-of-band in runtime_lg/selfwake.py's
    poll_due_wakes, driven by web/app.py's background poll loop (while the
    web server is running) or cli.py's --check-wakes flag (for an external
    cron/systemd setup) -- neither of which this module needs to know about.
    """
    wake_store = WakeStore(state_dir)
    signal_store = SignalStore(state_dir)
    task_store = BackgroundTaskStore(state_dir)
    subagent_task_store = SubAgentTaskStore(state_dir)

    def _create(kind: str, reason: str, **fields: Any) -> dict[str, Any]:
        wake = WakeRequest(
            wake_id=uuid.uuid4().hex[:12],
            thread_id=thread_id,
            kind=kind,
            reason=reason,
            created_at=_now_iso(),
            **fields,
        )
        wake_store.save(wake)
        return wake.to_dict()

    def sleep_until(wake_at: str, reason: str) -> dict[str, Any]:
        """Pause this conversation and automatically resume it at a specific
        wall-clock time -- for "check back tomorrow morning" style requests.
        Ends this turn; nothing further happens until the wake time arrives,
        at which point a new turn starts automatically with your `reason` as
        context. Requires either a running coscribe web server (polls for
        due wakes automatically) or `coscribe --check-wakes` scheduled via
        external cron/systemd.

        Args:
            wake_at: ISO-8601 timestamp to resume at, e.g.
                "2026-08-18T09:00:00+00:00". Must be in the future.
            reason: what to do or check when you wake up -- this is the only
                context you'll have; be specific.
        """
        parsed = _parse_iso(wake_at, field_name="wake_at")
        if parsed <= datetime.now(UTC):
            raise ValueError(f"wake_at must be in the future, got {wake_at!r}")
        # Normalize to UTC before storing: poll_due_wakes compares stored
        # wake_at strings against datetime.now(UTC).isoformat() lexically,
        # which only works if every stored timestamp uses the same "+00:00"
        # offset -- a wake_at given in a non-UTC offset (e.g. "+05:00")
        # would otherwise sort wrong against that comparison.
        return _create("timer", reason, wake_at=parsed.astimezone(UTC).isoformat())

    def sleep_for(seconds: int, reason: str) -> dict[str, Any]:
        """Pause this conversation and automatically resume it after a given
        delay -- the relative-time counterpart to sleep_until, for "check
        back in 2 hours" style requests rather than a specific clock time.

        Args:
            seconds: how long to wait before resuming, must be positive.
            reason: what to do or check when you wake up -- this is the only
                context you'll have; be specific.
        """
        if seconds <= 0:
            raise ValueError(f"seconds must be positive, got {seconds}")
        wake_at = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
        return _create("timer", reason, wake_at=wake_at)

    def wake_on_task(task_id: str, reason: str) -> dict[str, Any]:
        """Pause this conversation and automatically resume it once a
        background script started via run_background_script finishes
        (succeeds, fails, times out, or is killed) -- for "let me know
        when that's done" instead of calling check_background_task
        yourself over and over. task_id must be a real, currently-running
        background task id (see run_background_script's own return value,
        or list_background_tasks) -- there is no other kind of background
        task this can watch.

        Args:
            task_id: the task_id of an in-progress background script.
            reason: what to do or check when you wake up -- e.g. "read its
                output with check_background_task and summarize the
                result."
        """
        task = task_store.load(task_id)
        if task is None:
            raise ValueError(f"No background task with id {task_id!r}")
        return _create("task", reason, task_id=task_id)

    def wake_on_subagent(task_id: str, reason: str) -> dict[str, Any]:
        """Pause this conversation and automatically resume it once a
        background sub-agent started via spawn_agent_background finishes
        (succeeds, fails, is paused, or ends up blocked on an approval it
        can't ask for in the background) -- for "let me know when that
        sub-agent is done" instead of calling check_subagent_task
        yourself over and over. task_id must be a real, currently-running
        background sub-agent task id (see spawn_agent_background's own
        return value, or list_subagent_tasks).

        Args:
            task_id: the task_id of an in-progress background sub-agent.
            reason: what to do or check when you wake up -- e.g. "read
                its result with check_subagent_task and summarize it."
        """
        subagent_task = subagent_task_store.load(task_id)
        if subagent_task is None:
            raise ValueError(f"No sub-agent task with id {task_id!r}")
        return _create("subagent", reason, subagent_task_id=task_id)

    def wake_on_event(event_key: str, reason: str) -> dict[str, Any]:
        """Pause this conversation and automatically resume it once a named
        event fires -- for waiting on something external to this
        conversation (another thread finishing related work, a future
        webhook/connector signal). Today, the only way an event actually
        fires is a signal_event call (from this thread, another thread, or
        a future connector) using the same event_key -- there is no
        automatic webhook wiring yet, so this only helps if something is
        actually going to call signal_event(event_key) later.

        Args:
            event_key: an arbitrary name both sides agree on in advance,
                e.g. "deploy-finished" or "invoice-2026-08-approved".
            reason: what to do or check when you wake up.
        """
        return _create("event", reason, event_key=event_key)

    def signal_event(event_key: str, payload: str = "") -> dict[str, Any]:
        """Fire a named event, resolving any pending wake_on_event(event_key)
        call -- anywhere, not just in this thread. Use this to tell another,
        possibly-sleeping conversation that something it's waiting on has
        happened.

        Args:
            event_key: must match the event_key a wake_on_event call used.
            payload: optional short note about what happened, surfaced to
                the woken thread's context.
        """
        return signal_store.signal(event_key, payload)

    def list_wakes() -> list[dict[str, Any]]:
        """List this conversation's own pending sleep/wake requests -- use this to check what you're
        currently waiting on."""
        return [
            w.to_dict()
            for w in wake_store.list_pending()
            if w.thread_id == thread_id
        ]

    def cancel_wake(wake_id: str) -> dict[str, Any]:
        """Cancel one of this conversation's own pending wakes -- e.g. if
        the thing you were going to check on is no longer relevant.

        Args:
            wake_id: id of the wake to cancel, as returned by sleep_until/
                a sleep/wake tool or list_wakes.
        """
        wake = wake_store.load(wake_id)
        if wake is None or wake.thread_id != thread_id:
            raise KeyError(f"No pending wake with id {wake_id!r} in this conversation")
        if wake.status != "pending":
            raise ValueError(f"Wake {wake_id!r} is already {wake.status!r}, not pending")
        wake.status = "cancelled"
        wake_store.save(wake)
        return wake.to_dict()

    return [
        # WRITE_LOCAL: each of these persists a durable record that changes
        # this thread's future unattended behavior -- same reasoning
        # memory.py's remember got in the Phase 4 risk-taxonomy pass, not a
        # disposable, thread-scoped bookkeeping tool like task_create.
        tool_metadata(sleep_until, risk_category="WRITE_LOCAL", category="selfwake"),
        tool_metadata(sleep_for, risk_category="WRITE_LOCAL", category="selfwake"),
        tool_metadata(wake_on_task, risk_category="WRITE_LOCAL", category="selfwake"),
        tool_metadata(wake_on_subagent, risk_category="WRITE_LOCAL", category="selfwake"),
        tool_metadata(wake_on_event, risk_category="WRITE_LOCAL", category="selfwake"),
        tool_metadata(signal_event, risk_category="WRITE_LOCAL", category="selfwake"),
        tool_metadata(list_wakes, risk_category="READ", category="selfwake"),
        tool_metadata(cancel_wake, risk_category="WRITE_LOCAL", category="selfwake"),
    ]
