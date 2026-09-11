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
            if trigger.workflow_name is not None:
                result = await session.run_saved_workflow(trigger.workflow_name, _SilentSocket())
                status = str(result.get("status", "completed"))
            else:
                await session.handle_user_message(trigger.prompt, _SilentSocket())
                status = "completed"
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
