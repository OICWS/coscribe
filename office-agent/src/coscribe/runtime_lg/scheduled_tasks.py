"""Scheduled Tasks resume side for runtime_lg -- the LangGraph-native
counterpart to tools/scheduled_tasks.py, same split runtime_lg/selfwake.py
and runtime_lg/workflows.py already use: storage + model-callable tools
live in tools/scheduled_tasks.py (no live client/checkpointer needed
there); actually firing a due trigger does need one, so it lives here.

Same get_session callback shape as runtime_lg/selfwake.py's poll_due_wakes
(reused, not reinvented) -- web/app.py's background poll loop and cli.py's
--check-wakes flag call both poll functions in the same pass, so both take
an identical `Callable[[str], Awaitable[Any]]`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from ..tools.scheduled_tasks import ScheduledTrigger, ScheduledTriggerStore, compute_next_run_at
from .selfwake import _SilentSocket

logger = logging.getLogger(__name__)


async def _fire_trigger_once(trigger: ScheduledTrigger, session: Any) -> str:
    """Actually run one trigger's workflow/prompt against an already-
    constructed session, returning its status string. Raises on failure
    -- callers decide for themselves whether that should be swallowed
    (poll_due_scheduled_tasks, so one broken trigger doesn't block every
    other due one) or surfaced (fire_trigger_now, a live REST caller
    that wants to know a manual run actually failed, not have it
    silently logged and skipped).

    Applies trigger.model to the session before firing (a one-way switch,
    not restored after -- this thread is this trigger's own dedicated
    conversation, per this module's docstring, so trigger.model *is* its
    real model, not a temporary override of something else). approval_mode
    IS restored after, since accept_edits is a broader safety toggle and
    the cost of restoring it is a single attribute set, not a graph
    rebuild. "manual" needs no session change at all: _SilentSocket
    already makes any gated call durably park in the checkpointer rather
    than block on an approval nobody's there to give, which is exactly
    what "manual" means for an unattended fire. "auto"/"skip" both map to
    accept_edits today -- this codebase has no third gating tier between
    "ask a human" and "auto-approve like accept-edits does"; a hook
    veto/exec-policy-forbidden/plan_mode rejection still applies in
    either case, unchanged, since those are categorical safety rails
    independent of accept_edits."""
    current_model = getattr(session, "_model_string", None)
    if trigger.model is not None and trigger.model != current_model:
        await session.switch_model(trigger.model, _SilentSocket())

    previous_accept_edits = session.accept_edits
    if trigger.approval_mode in ("auto", "skip"):
        session.accept_edits = True
    try:
        if trigger.workflow_name is not None:
            result = await session.run_saved_workflow(trigger.workflow_name, _SilentSocket())
            return str(result.get("status", "completed"))
        await session.handle_user_message(trigger.prompt, _SilentSocket())
        return "completed"
    finally:
        session.accept_edits = previous_accept_edits


async def poll_due_scheduled_tasks(
    state_dir: str | Path,
    get_session: Callable[[str], Awaitable[Any]],
) -> list[ScheduledTrigger]:
    """Find every due ScheduledTrigger, fire it (a saved workflow via
    run_saved_workflow, or a freeform prompt via handle_user_message),
    record the result, and advance its schedule (or disable it, for a
    one-time trigger). Returns what fired, for a caller to log/print.

    A trigger whose session fails to construct or whose turn raises is
    logged and left untouched (not marked run, not rescheduled) so it
    stays due and is retried on the next poll -- same defensive posture
    runtime_lg/selfwake.py's poll_due_wakes already takes, not a new
    policy invented here.
    """
    store = ScheduledTriggerStore(state_dir)
    now = datetime.now()

    fired: list[ScheduledTrigger] = []
    for trigger in store.list_due(now):
        try:
            session = await get_session(trigger.thread_id)
            status = await _fire_trigger_once(trigger, session)
        except Exception:
            logger.exception(
                "scheduled_tasks: failed to fire trigger %s (%r)",
                trigger.trigger_id,
                trigger.name,
            )
            continue

        trigger.last_run_at = datetime.now().isoformat()
        trigger.last_run_status = status
        if trigger.schedule.kind == "once":
            trigger.enabled = False
            trigger.next_run_at = None
        else:
            trigger.next_run_at = compute_next_run_at(trigger.schedule, datetime.now())
        store.save(trigger)
        fired.append(trigger)
    return fired


async def fire_trigger_now(
    state_dir: str | Path,
    trigger_id: str,
    get_session: Callable[[str], Awaitable[Any]],
) -> ScheduledTrigger:
    """The Scheduled Tasks detail page's "Run now" action -- an explicit,
    out-of-band execution that never touches the trigger's regular
    schedule (`next_run_at`/`enabled` untouched, only `last_run_at`/
    `last_run_status` recorded): running a daily 9am task by hand at 2pm
    must not make it skip tomorrow's real 9am fire, and a paused task
    stays paused afterward. Unlike poll_due_scheduled_tasks, a firing
    failure here is *not* swallowed -- a live REST caller explicitly
    asking to run it now wants to know it failed, not have that silently
    logged and skipped like an unattended poll would."""
    store = ScheduledTriggerStore(state_dir)
    trigger = store.load(trigger_id)
    if trigger is None:
        raise KeyError(f"No scheduled task with id {trigger_id!r}")

    session = await get_session(trigger.thread_id)
    status = await _fire_trigger_once(trigger, session)

    trigger.last_run_at = datetime.now().isoformat()
    trigger.last_run_status = status
    store.save(trigger)
    return trigger
