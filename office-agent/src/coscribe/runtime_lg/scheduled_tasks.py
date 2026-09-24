"""Scheduled Tasks run side for runtime_lg -- the LangGraph-native
counterpart to tools/scheduled_tasks.py, same split runtime_lg/selfwake.py
already uses: storage + model-callable tools live in tools/scheduled_tasks.py
(no live client/checkpointer needed there); actually executing a run does
need one, so it lives here.

Every run gets its own fresh conversation (ScheduledRun.thread_id); what
carries over between runs is the task's notes, injected into each run's
prompt (see build_run_prompt) and updated by the run itself through the
update_task_notes tool.

Same get_session callback shape as runtime_lg/selfwake.py's poll_due_wakes
-- web/app.py's background poll loop and cli.py's --check-wakes flag call
both poll functions in the same pass, so both take an identical
`Callable[[str], Awaitable[Any]]`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from ..tools.scheduled_tasks import (
    ScheduledRun,
    ScheduledTrigger,
    ScheduledTriggerStore,
    compute_next_run_at,
)

logger = logging.getLogger(__name__)

RUN_PROMPT_PREFIX = "[Scheduled run of "

_NOTES_INSTRUCTIONS = (
    "Before you finish, call update_task_notes with the complete updated "
    "notes -- what the next run needs to know: progress markers, where you "
    "left off, anything that changed, pitfalls worth remembering. Keep them "
    "short and drop anything stale."
)


class _RelaySocket:
    """Stands in for a websocket during a background run: every event goes
    to whichever browser tab currently has this run's thread open (the
    session's _live_websocket, set by web/app.py's ws_endpoint), so a run
    can be watched live -- or to nobody, harmlessly.

    can_resolve_approvals is False even when someone is watching: a run
    must never block on a human (the poller awaits it), so a gated call
    parks durably in the checkpointer instead, and that same tab's
    resume_after_reconnect path offers the approval on the next open."""

    can_resolve_approvals = False

    def __init__(self, session: Any) -> None:
        self._session = session
        self.error: str | None = None

    async def send_json(self, data: dict[str, Any]) -> None:
        if data.get("type") == "error":
            self.error = str(data.get("message", ""))
        websocket = getattr(self._session, "_live_websocket", None)
        if websocket is None:
            return
        try:
            await websocket.send_json(data)
        except Exception:  # noqa: BLE001 -- a tab closing mid-run must not fail the run
            logger.debug("scheduled_tasks: live tab went away mid-run", exc_info=True)


def _format_started(started_at: str) -> str:
    return datetime.fromisoformat(started_at).strftime("%Y-%m-%d %H:%M")


def build_run_prompt(trigger: ScheduledTrigger, run: ScheduledRun, notes: str) -> str:
    header = (
        f'{RUN_PROMPT_PREFIX}"{trigger.name}" · '
        f"{'started manually' if run.source == 'manual' else 'on schedule'} · "
        f"{_format_started(run.started_at)}. This is a fresh conversation; "
        "no one may be watching, so work through the task on your own.]"
    )
    parts = [header, trigger.prompt.strip()]
    if trigger.notes_enabled:
        parts.append(
            "---\nNotes from earlier runs of this task:\n"
            + (notes.strip() or "(none yet -- this is the first run with notes)")
            + "\n\n"
            + _NOTES_INSTRUCTIONS
        )
    return "\n\n".join(parts)


async def _has_pending_interrupt(session: Any) -> bool:
    state = await session.lg_agent.aget_state(session.config)
    return any(task.interrupts for task in state.tasks)


async def _run_in_session(
    trigger: ScheduledTrigger, run: ScheduledRun, notes: str, session: Any
) -> tuple[str, str | None]:
    """Run one prompt against the run's own session, returning (status,
    error).

    Applies trigger.model to the session before firing (a one-way switch:
    this thread belongs to this run alone). The approval tier is set only
    for the run itself (see ChatSessionLG._auto_approves): a person who
    keeps chatting in this thread afterwards gets ordinary approvals. Any
    gated call the tier doesn't cover parks durably for a person to
    approve later ("needs_approval") -- the relay socket never blocks on
    one."""
    socket = _RelaySocket(session)
    current_model = getattr(session, "_model_string", None)
    if trigger.model is not None and trigger.model != current_model:
        await session.switch_model(trigger.model, socket)

    prompt = build_run_prompt(trigger, run, notes)
    session.run_approval_mode = trigger.approval_mode
    session.active_run_prompt = prompt
    try:
        await socket.send_json({"type": "scheduled_run_started", "text": prompt})
        await session.handle_user_message(prompt, socket)
    finally:
        session.run_approval_mode = None
        session.active_run_prompt = None

    if socket.error is not None:
        return "failed", socket.error
    if getattr(session, "_stop_requested", False):
        return "stopped", None
    if await _has_pending_interrupt(session):
        return "needs_approval", None
    return "completed", None


async def execute_run(
    state_dir: str | Path,
    trigger_id: str,
    run_id: str,
    get_session: Callable[[str], Awaitable[Any]],
) -> ScheduledRun | None:
    """Execute an already-recorded ("running") run and record how it
    ended. Never raises for a run's own failure -- that's recorded as
    status "failed" on the run itself, which is what every caller (the
    poller, Run now's background task) needs."""
    store = ScheduledTriggerStore(state_dir)
    trigger = store.load(trigger_id)
    run = trigger.find_run(run_id) if trigger is not None else None
    if trigger is None or run is None:
        return None
    try:
        session = await get_session(run.thread_id)
        status, error = await _run_in_session(trigger, run, store.read_notes(trigger_id), session)
    except asyncio.CancelledError:
        store.finish_run(trigger_id, run_id, "stopped")
        raise
    except Exception as exc:  # noqa: BLE001 -- recorded on the run, not swallowed
        logger.exception(
            "scheduled_tasks: run %s of trigger %s (%r) failed", run_id, trigger_id, trigger.name
        )
        status, error = "failed", str(exc)
    updated = store.finish_run(trigger_id, run_id, status, error)
    if status == "needs_approval":
        # Only after finish_run: resolving the approval re-records the run
        # (see ChatSessionLG._record_resumed_run_status).
        session.offer_pending_approval_to_live_tab()
    return updated.find_run(run_id) if updated is not None else None


async def poll_due_scheduled_tasks(
    state_dir: str | Path,
    get_session: Callable[[str], Awaitable[Any]],
    on_pruned: Callable[[list[ScheduledRun]], Awaitable[None]] | None = None,
) -> list[ScheduledTrigger]:
    """Start a run for every due ScheduledTrigger, advance each schedule
    (or disable a one-time trigger), then wait for all of them to finish.
    Returns the triggers that fired, for a caller to log/print.

    The schedule advances when the run *starts*, not when it ends, so a
    run that takes longer than the poll interval is never started twice,
    and a run that fails isn't retried every poll -- its failure is on
    the run record instead. Due runs execute concurrently: each has its
    own conversation, so there's nothing for them to contend over."""
    store = ScheduledTriggerStore(state_dir)
    now = datetime.now()

    fired: list[ScheduledTrigger] = []
    executions = []
    for due in store.list_due(now):
        trigger, run, pruned = store.start_run(due.trigger_id, "scheduled")
        if trigger.schedule.kind == "once":
            trigger.enabled = False
            trigger.next_run_at = None
        else:
            trigger.next_run_at = compute_next_run_at(trigger.schedule, datetime.now())
        store.save(trigger)
        if pruned and on_pruned is not None:
            await on_pruned(pruned)
        fired.append(trigger)
        executions.append(execute_run(state_dir, trigger.trigger_id, run.run_id, get_session))
    await asyncio.gather(*executions)
    return [store.load(t.trigger_id) or t for t in fired]


async def fire_trigger_now(
    state_dir: str | Path,
    trigger_id: str,
    get_session: Callable[[str], Awaitable[Any]],
) -> ScheduledRun | None:
    """Start a manual run and wait for it -- for callers with nothing else
    to do meanwhile (the CLI, tests). web/app.py's Run now endpoint
    instead records the run and executes it in the background, so the
    browser can open the run's conversation and watch it live. Never
    touches the trigger's regular schedule: running a daily 9am task by
    hand at 2pm must not make it skip tomorrow's real 9am fire, and a
    paused task stays paused."""
    store = ScheduledTriggerStore(state_dir)
    _, run, _ = store.start_run(trigger_id, "manual")
    return await execute_run(state_dir, trigger_id, run.run_id, get_session)


def reconcile_interrupted_runs(state_dir: str | Path) -> int:
    """Mark every run still "running" as failed -- called at startup, when
    no run can genuinely still be in progress (a previous process exited
    mid-run). Returns how many were fixed up."""
    store = ScheduledTriggerStore(state_dir)
    fixed = 0
    for trigger in store.list_all():
        for run in trigger.runs:
            if run.status == "running":
                store.finish_run(
                    trigger.trigger_id,
                    run.run_id,
                    "failed",
                    "Interrupted -- coscribe closed while this run was in progress.",
                )
                fixed += 1
    return fixed
