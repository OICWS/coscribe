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
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from ..tools.browser import BROWSER_HOST, BROWSER_RUNS, page_screenshot
from ..tools.scheduled_tasks import (
    ScheduledRun,
    ScheduledTrigger,
    ScheduledTriggerStore,
    compute_next_run_at,
)
from ..workflows.engine import RunOutcome, StepRecord, WorkflowNotRunnable, WorkflowRun
from ..workflows.permissions import sites_in
from ..workflows.spec import parse_workflow, walk
from ..workflows.testing import output_files, uses_browser
from .messages import extract_text, strip_mode_note

logger = logging.getLogger(__name__)

RUN_PROMPT_PREFIX = "[Scheduled run of "
INVESTIGATION_PREFIX = "[Look into a failed run of "
_PAGE_CHARS = 6000

# The task driving each run that's going now, so Stop can cancel it.
_RUNNING: dict[str, asyncio.Task[Any]] = {}


@contextlib.contextmanager
def _running(run_id: str) -> Any:
    task = asyncio.current_task()
    if task is not None:
        _RUNNING[run_id] = task
    try:
        yield
    finally:
        if _RUNNING.get(run_id) is task:
            del _RUNNING[run_id]


def stop_run(run_id: str, thread_id: str) -> bool:
    """Cancel a run that's going now -- it's recorded as stopped, not
    failed. False if it isn't running here."""
    task = _RUNNING.get(run_id)
    if task is None or task.done():
        return False
    # A browser step can be waiting on the desktop app for an hour.
    BROWSER_HOST.cancel(thread_id)
    task.cancel()
    return True


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


WorkflowAction = Callable[[WorkflowRun], Awaitable[RunOutcome]]


async def _run_workflow(
    store: ScheduledTriggerStore,
    trigger: ScheduledTrigger,
    run: ScheduledRun,
    session: Any,
    action: WorkflowAction,
) -> tuple[str, str | None]:
    """Drive a workflow run one action further (start, answer an approval,
    retry a step), recording each step on the run and relaying it to a
    watching tab. Returns (status, error) like _run_in_session."""
    if trigger.workflow is None:
        raise ValueError(f"Task {trigger.name!r} has no workflow")
    workflow = parse_workflow(trigger.workflow)
    socket = _RelaySocket(session)

    async def on_step(record: StepRecord) -> None:
        data = record.to_dict()
        store.record_step(trigger.trigger_id, run.run_id, data)
        await socket.send_json({"type": "workflow_step", "run_id": run.run_id, "record": data})

    ctx = session.workflow_context(trigger.model)
    # A run may stop for an answer; a test in a conversation just fails.
    ctx.ask_permission = True
    workflow_run = WorkflowRun(
        workflow,
        ctx,
        session.checkpointer,
        run.thread_id,
        on_step,
    )
    # Tasks due together, or runs caught up after the computer was off.
    async with BROWSER_RUNS if uses_browser(workflow) else contextlib.nullcontext():
        # Downloads from the run's tab, however late, are the run's.
        BROWSER_HOST.set_active(run.thread_id, True)
        try:
            outcome = await action(workflow_run)
            if outcome.status == "failed" and uses_browser(workflow):
                # Before another run takes the tab.
                await _record_failure_page(store, trigger, run, session)
        except WorkflowNotRunnable as exc:
            return "failed", str(exc)
        finally:
            BROWSER_HOST.set_active(run.thread_id, False)
    if outcome.status in ("completed", "failed"):
        # From the stored run, so steps before a resume count too.
        stored = store.load(trigger.trigger_id)
        done = stored.find_run(run.run_id) if stored is not None else None
        steps = done.steps if done is not None else []
        outputs = [s.get("output") for s in steps if s.get("status") == "done"]
        store.record_evidence(trigger.trigger_id, run.run_id, sites=sites_in(outputs))
        if outcome.status == "completed":
            since = int(datetime.fromisoformat(run.started_at).timestamp())
            files = await asyncio.to_thread(output_files, outputs, ctx.workspace_root, since)
            store.record_evidence(trigger.trigger_id, run.run_id, files=files)
            return "completed", None
    if outcome.status == "waiting":
        return "needs_approval", None
    titles = {placed.step.id: placed.step.title for placed in walk(workflow.steps)}
    title = titles.get(outcome.step_id or "", "A step")
    return "failed", f"{title}: {outcome.error}"


async def _record_failure_page(
    store: ScheduledTriggerStore, trigger: ScheduledTrigger, run: ScheduledRun, session: Any
) -> None:
    screenshot = await asyncio.to_thread(
        page_screenshot, run.thread_id, Path(session.settings.state_dir), saved_workflow=True
    )
    snapshot = session.workflow_context(trigger.model).tools.get("browser_snapshot")
    page = None
    if snapshot is not None:
        try:
            page = str(await asyncio.to_thread(snapshot))[:_PAGE_CHARS]
        except Exception:  # noqa: BLE001 -- the failure itself is what's recorded
            logger.info("scheduled_tasks: no page text for failed run %s", run.run_id)
    store.record_evidence(trigger.trigger_id, run.run_id, screenshot=screenshot, page=page)


async def _first_request(get_session: Callable[[str], Awaitable[Any]], thread_id: str) -> str:
    """The first thing the user asked in `thread_id`, or "" -- it tells the
    assistant what the task was for, and in which language to answer."""
    try:
        session = await get_session(thread_id)
        state = await session.lg_agent.aget_state(session.config)
    except Exception:  # noqa: BLE001 -- the context is a help, not a need
        logger.info("scheduled_tasks: couldn't read conversation %s", thread_id, exc_info=True)
        return ""
    for message in (state.values or {}).get("messages", []):
        if getattr(message, "type", "") == "human":
            return strip_mode_note(extract_text(message.content))[:500]
    return ""


def build_investigation_prompt(trigger: ScheduledTrigger, run: ScheduledRun, request: str) -> str:
    """What a conversation looking into a failed workflow run starts from."""
    titles: dict[str, str] = {}
    if trigger.workflow is not None:
        titles = {p.step.id: p.step.title for p in walk(parse_workflow(trigger.workflow).steps)}
    failed = next((s for s in reversed(run.steps) if s.get("status") == "failed"), None)
    step_id = str(failed.get("step_id")) if failed else ""
    last_ok = next((r for r in reversed(trigger.runs) if r.status == "completed"), None)
    lines = [
        f'{INVESTIGATION_PREFIX}"{trigger.name}" · task {trigger.trigger_id} · run {run.run_id}]',
        "",
        "This saved fixed workflow's run failed. Find the cause and propose a fix. Don't save "
        "anything or change the task yourself: the user reviews your revision and saves it.",
        "",
        f"Failed step: {titles.get(step_id, step_id or 'unknown')} ({step_id or '-'})",
        f"Error: {run.error or '(none recorded)'}",
        f"Inputs: {json.dumps(run.inputs or {}, ensure_ascii=False)}",
        f"Run started: {run.started_at}",
        f"Last successful run: {last_ok.started_at if last_ok else 'none yet'}",
    ]
    if request:
        lines.append(f"What the user asked for when the task was made: {request}")
    if run.page:
        lines += ["", "The page when it failed:", run.page]
    lines += [
        "",
        "1. Reproduce it: test_workflow(task_id, the same inputs).",
        "2. Find the cause from the failed step, its error and the page.",
        "3. Fix it with revise_workflow(task_id, request=the cause and the fix), then test the "
        "revision with test_workflow(draft_id) -- at most 3 rounds.",
        "4. Tell the user in a few sentences what went wrong and what the revision changes, in "
        "the language of their request above.",
        "If the reproduction passes and the failure looks temporary (the site was down, a login "
        "had expired), say so instead of changing the workflow.",
    ]
    return "\n".join(lines)


async def start_investigation(
    state_dir: str | Path,
    trigger_id: str,
    run_id: str,
    thread_id: str,
    model: str | None,
    get_session: Callable[[str], Awaitable[Any]],
    *,
    attended: bool,
) -> None:
    """Open conversation `thread_id` on a failed workflow run and let the
    assistant work on it. `attended`: someone is watching, so an approval
    waits for them; otherwise it's parked for later, as in a run."""
    store = ScheduledTriggerStore(state_dir)
    trigger = store.load(trigger_id)
    run = trigger.find_run(run_id) if trigger is not None else None
    if trigger is None or run is None:
        raise KeyError(f"No run {run_id!r} of scheduled task {trigger_id!r}")
    request = (
        await _first_request(get_session, trigger.source_thread) if trigger.source_thread else ""
    )
    prompt = build_investigation_prompt(trigger, run, request)
    store.record_evidence(trigger_id, run_id, investigation=thread_id)
    session = await get_session(thread_id)
    socket = _RelaySocket(session)
    socket.can_resolve_approvals = attended
    if model and model != getattr(session, "_model_string", None):
        await session.switch_model(model, socket)
    session._title_path().parent.mkdir(parents=True, exist_ok=True)
    session._title_path().write_text(f"Look into: {trigger.name}", encoding="utf-8")
    await socket.send_json({"type": "scheduled_run_started", "text": prompt})
    # Turning on auto-investigation is consent to replay the task's own
    # steps; anything else the assistant tries still waits for the user.
    session.preapproved_tools = frozenset() if attended else frozenset({"test_workflow"})
    try:
        await session.handle_user_message(prompt, socket)
    finally:
        session.preapproved_tools = frozenset()


async def continue_workflow_run(
    state_dir: str | Path,
    trigger_id: str,
    run_id: str,
    get_session: Callable[[str], Awaitable[Any]],
    action: WorkflowAction,
) -> ScheduledRun | None:
    """Take a stopped workflow run further -- an approval answered, a step
    retried -- once the caller has reopened it (ScheduledTriggerStore.
    reopen_run), so a request for a run that's still going is refused
    before anything starts."""
    store = ScheduledTriggerStore(state_dir)
    trigger = store.load(trigger_id)
    run = trigger.find_run(run_id) if trigger is not None else None
    if trigger is None or run is None:
        return None
    try:
        with _running(run_id):
            session = await get_session(run.thread_id)
            status, error = await _run_workflow(store, trigger, run, session, action)
    except asyncio.CancelledError:
        store.finish_run(trigger_id, run_id, "stopped")
        raise
    except Exception as exc:  # noqa: BLE001 -- recorded on the run, not swallowed
        logger.exception("scheduled_tasks: continuing workflow run %s failed", run_id)
        status, error = "failed", str(exc)
    updated = store.finish_run(trigger_id, run_id, status, error)
    return updated.find_run(run_id) if updated is not None else None


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
        with _running(run_id):
            session = await get_session(run.thread_id)
            if trigger.workflow is not None:
                inputs = run.inputs or {}
                status, error = await _run_workflow(
                    store, trigger, run, session, lambda wf: wf.start(inputs)
                )
            else:
                status, error = await _run_in_session(
                    trigger, run, store.read_notes(trigger_id), session
                )
    except asyncio.CancelledError:
        store.finish_run(trigger_id, run_id, "stopped")
        raise
    except Exception as exc:  # noqa: BLE001 -- recorded on the run, not swallowed
        logger.exception(
            "scheduled_tasks: run %s of trigger %s (%r) failed", run_id, trigger_id, trigger.name
        )
        status, error = "failed", str(exc)
    updated = store.finish_run(trigger_id, run_id, status, error)
    if status == "needs_approval" and trigger.workflow is None:
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
    # A run stopped by the user ends in CancelledError; the others go on.
    await asyncio.gather(*executions, return_exceptions=True)
    return [store.load(t.trigger_id) or t for t in fired]


async def fire_trigger_now(
    state_dir: str | Path,
    trigger_id: str,
    get_session: Callable[[str], Awaitable[Any]],
    inputs: dict[str, Any] | None = None,
) -> ScheduledRun | None:
    """Start a manual run and wait for it -- for callers with nothing else
    to do meanwhile (the CLI, tests). web/app.py's Run now endpoint
    instead records the run and executes it in the background, so the
    browser can open the run's conversation and watch it live. Never
    touches the trigger's regular schedule: running a daily 9am task by
    hand at 2pm must not make it skip tomorrow's real 9am fire, and a
    paused task stays paused."""
    store = ScheduledTriggerStore(state_dir)
    _, run, _ = store.start_run(trigger_id, "manual", inputs)
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
