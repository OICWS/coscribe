"""Selfwake resume side for runtime_lg -- the LangGraph-native counterpart
to tools/selfwake.py, same split tools/workflows.py / runtime_lg/workflows.py
already use: storage + model-callable tools live in tools/selfwake.py (no
live client/checkpointer needed there); actually resuming a sleeping thread
does need one, so it lives here instead.

Deliberately does NOT import web.session.ChatSessionLG -- web/session.py
already imports *from* runtime_lg (see runtime_lg/__init__.py), so importing
it back here would be circular. Instead, poll_due_wakes takes a
`get_session` callback (thread_id -> an object with an async
handle_user_message(text, socket) method) -- web/app.py passes its own
_get_session (wrapped async, reusing its in-memory session cache and
skill sidecar resolution as-is), cli.py's --check-wakes builds a
fresh one-shot session per call. Neither caller needs a new shared
session-bootstrap helper; both existing construction paths are reused
unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..tools.background_tasks import BackgroundTaskStore
from ..tools.selfwake import SignalStore, WakeRequest, WakeStore
from ..tools.workflows import WorkflowRunStore

logger = logging.getLogger(__name__)


class _SilentSocket:
    """Duck-typed WebSocket stand-in for a wake-triggered turn -- nobody is
    watching live, so every message is just discarded (debug-logged, not
    printed/streamed anywhere). In particular, unlike cli.py's _CliSocket,
    an "approval_required" message here does NOT prompt or auto-resolve --
    it's left pending, identical to today's documented behavior for an
    unattended cron run made without --accept-edits (see README.md's
    "Scheduled / unattended runs"): the interrupt is durably held by the
    checkpointer, and the user discovers it next time they open that
    thread.

    `can_resolve_approvals = False` is the actual mechanism behind that
    last sentence -- web/session.py's `_can_resolve_approvals()` checks
    for this attribute (duck-typed, not an isinstance check, since
    session.py can't import this module back without a circular import)
    and skips calling `_resolve_pending_approvals` entirely when it's
    False. Skipping is load-bearing, not an optimization: that method
    would otherwise create a real `asyncio.Future` and `await` it,
    resolved only by a genuine incoming WS message nobody is ever going to
    send here -- verified live, this hung the calling coroutine
    indefinitely, which for a poll-loop caller (poll_due_wakes/
    poll_due_scheduled_tasks) means the *entire* background poller wedges
    on the first unattended run that happens to touch a gated tool, not
    just that one thread."""

    can_resolve_approvals = False

    async def send_json(self, data: dict[str, Any]) -> None:
        logger.debug("selfwake: discarding %s message from a wake-triggered turn", data.get("type"))


def _is_due(
    wake: WakeRequest,
    *,
    now: datetime,
    run_store: WorkflowRunStore,
    task_store: BackgroundTaskStore,
    signal_store: SignalStore,
) -> bool:
    if wake.kind == "timer":
        if wake.wake_at is None:
            return False
        wake_at = datetime.fromisoformat(wake.wake_at)
        if wake_at.tzinfo is None:
            wake_at = wake_at.replace(tzinfo=UTC)
        return wake_at <= now
    if wake.kind == "job":
        if wake.job_id is None:
            return False
        run = run_store.load(wake.job_id)
        # A run that's vanished (deleted) can never resolve any other way,
        # so treat that as due too rather than leaving the wake stuck
        # pending forever.
        return run is None or run.status != "running"
    if wake.kind == "task":
        if wake.task_id is None:
            return False
        task = task_store.load(wake.task_id)
        # Same "vanished counts as due" reasoning as "job" above.
        return task is None or task.status != "running"
    if wake.kind == "event":
        if wake.event_key is None:
            return False
        return signal_store.fired_since(wake.event_key, wake.created_at)
    return False


async def poll_due_wakes(
    state_dir: str | Path,
    get_session: Callable[[str], Awaitable[Any]],
) -> list[WakeRequest]:
    """Find every due WakeRequest, resume its thread with a synthetic
    message, mark it woken. Returns what fired, for a caller to log/print.

    One bad wake (a session that fails to construct, a turn that raises)
    is logged and skipped rather than aborting the whole poll -- the same
    defensive posture the rest of this codebase's background-ish work
    takes (see _CatchToolErrorsMiddleware's docstring, reconcile_interrupted_runs).
    """
    wake_store = WakeStore(state_dir)
    signal_store = SignalStore(state_dir)
    run_store = WorkflowRunStore(state_dir)
    task_store = BackgroundTaskStore(state_dir)
    now = datetime.now(UTC)

    fired: list[WakeRequest] = []
    for wake in wake_store.list_pending():
        if not _is_due(
            wake, now=now, run_store=run_store, task_store=task_store, signal_store=signal_store
        ):
            continue
        try:
            session = await get_session(wake.thread_id)
            message = f"[Scheduled wake-up reached ({wake.kind}) -- {wake.reason}]"
            await session.handle_user_message(message, _SilentSocket())
        except Exception:
            logger.exception(
                "selfwake: failed to resume thread %s for wake %s", wake.thread_id, wake.wake_id
            )
            continue
        wake.status = "woken"
        wake.woken_at = datetime.now(UTC).isoformat()
        wake_store.save(wake)
        fired.append(wake)
    return fired
