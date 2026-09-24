"""Scheduled Tasks: a real, independently-manageable product feature for
"do this later, once or on a repeating schedule" -- distinct from
tools/selfwake.py's sleep_for/wake_on/wake_on_event, which are agent-
initiated, one-off, thread-scoped pauses only callable from inside an
existing conversation. A ScheduledTrigger is the opposite shape: global,
named, persisted, creatable either mid-conversation (create_scheduled_task)
or from the Settings > Scheduled Tasks panel with no conversation needed
at all -- global, named, and persisted, so it's its own concept rather
than a new WakeRequest kind. See ARCHITECTURE.md's 范围边界 section
for the fuller reasoning.

Split the same way tools/selfwake.py splits its own data model/storage/
tools from the runtime-specific resume logic: this
module holds ScheduleRule/ScheduledTrigger/ScheduledTriggerStore and the
model-callable tools (no live LLM client or checkpointer needed for any of
that); actually firing a due trigger lives in runtime_lg/scheduled_tasks.py
instead, since that needs a live ChatSessionLG.

Schedule times (`ScheduleRule.at`, "HH:MM" for recurring rules) are
interpreted in the server's own local wall-clock time, not UTC -- coscribe
is single-user, local-first software with no per-user timezone concept
(see ROADMAP.md's "explicitly not adopting: any hosted component"),
so "the machine coscribe runs on" and "the user" are assumed to share a
timezone, the same assumption any local scheduled-task tool (cron, Windows
Task Scheduler) already makes. This is a real limitation if coscribe is
ever remotely hosted -- documented, not silently assumed away.
"""

from __future__ import annotations

import calendar
import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from ..runtime.types import tool_metadata
from ..workflows.spec import parse_workflow, workflow_error

VALID_KINDS = ("manual", "once", "hourly", "daily", "weekdays", "weekly", "monthly")
VALID_APPROVAL_MODES = ("manual", "auto", "skip")
RUN_STATUSES = ("running", "completed", "failed", "needs_approval", "stopped")

# Every run's conversation is keyed off this prefix (see run_thread_id) --
# shared with web/app.py's list_threads, which filters by it so run
# conversations only ever show up under Scheduled, never in the ordinary
# chat sidebar.
SCHEDULED_THREAD_PREFIX = "scheduled-"

# Respond-only interrupt tools (like ask_user_question): the model's call
# becomes a draft the user reviews and edits in the UI, and whatever they
# decide is substituted as the tool's result -- the Python body below only
# ever runs outside such a graph (e.g. a direct call in tests).
TASK_DRAFT_TOOL_NAMES: frozenset[str] = frozenset({"create_scheduled_task"})

# Oldest run records beyond this are dropped from the trigger (their
# conversations are deleted along with them, see web/app.py).
MAX_RUNS_KEPT = 50
# The notes a run carries forward are part of every future run's prompt,
# so they're kept deliberately small.
MAX_NOTES_CHARS = 4000


def run_thread_id(trigger_id: str, run_id: str) -> str:
    return f"{SCHEDULED_THREAD_PREFIX}{trigger_id}-{run_id}"


def parse_run_thread_id(thread_id: str) -> tuple[str, str] | None:
    """(trigger_id, run_id) for a run's own thread, None for anything else
    -- including a pre-per-run trigger's single shared thread
    ("scheduled-<trigger_id>", no run part)."""
    if not thread_id.startswith(SCHEDULED_THREAD_PREFIX):
        return None
    # rpartition: a run_id never contains "-", a trigger_id might.
    trigger_id, sep, run_id = thread_id[len(SCHEDULED_THREAD_PREFIX) :].rpartition("-")
    if not sep or not trigger_id or not run_id:
        return None
    return trigger_id, run_id


def _now_iso() -> str:
    return datetime.now().isoformat()


def _to_local_naive(value: datetime) -> datetime:
    """Normalizes any datetime (naive or tz-aware) to a naive local
    wall-clock datetime, so every comparison in this module is apples to
    apples regardless of what a caller passed in."""
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def _parse_local(value: str) -> datetime:
    return _to_local_naive(datetime.fromisoformat(value))


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour_str, minute_str = value.split(":")
        hour, minute = int(hour_str), int(minute_str)
    except ValueError as exc:
        raise ValueError(f'at must be "HH:MM" for a recurring schedule, got {value!r}') from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f'at must be "HH:MM" (00:00-23:59), got {value!r}')
    return hour, minute


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


def _clamp_day(year: int, month: int, day: int) -> int:
    return min(day, calendar.monthrange(year, month)[1])


@dataclass
class ScheduleRule:
    kind: str  # "manual" | "once" | "hourly" | "daily" | "weekdays" |
    # "weekly" | "monthly" -- plain str rather than Literal: aisuite's
    # schema inference has broken on exotic annotations before. "once" predates the six-value
    # Manual/Hourly/Daily/Weekdays/Weekly/Monthly frequency picker the
    # frontend now offers for new tasks -- kept valid here so an
    # already-created "once" trigger keeps working, not exposed as a
    # creatable choice going forward.
    at: str  # "once": a full ISO-8601 timestamp. "manual": unused, may be
    # "". "hourly": "HH:MM" but only the minute is used (fires every hour
    # at that minute; the hour is ignored). Otherwise: "HH:MM".
    weekday: int | None = None  # 0=Monday..6=Sunday, required for "weekly"
    day_of_month: int | None = None  # 1-31, required for "monthly" --
    # clamped to a given month's real last day if it doesn't have that many.
    start_date: str | None = None  # "YYYY-MM-DD" -- the schedule produces
    # no occurrence before this date; None means "starting now." Unused
    # (and meaningless) for "manual"/"once".

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "at": self.at,
            "weekday": self.weekday,
            "day_of_month": self.day_of_month,
            "start_date": self.start_date,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduleRule:
        return cls(
            kind=data["kind"],
            at=data["at"],
            weekday=data.get("weekday"),
            day_of_month=data.get("day_of_month"),
            start_date=data.get("start_date"),
        )


def compute_next_run_at(rule: ScheduleRule, after: datetime) -> str | None:
    """The next real occurrence strictly after `after`, as a naive local
    ISO string. None for a "manual" rule (never fires on its own -- only
    via an explicit run-now), and for a "once" rule whose `at` is already
    in the past -- create_trigger rejects the latter case up front (same
    "reject a past timestamp" posture sleep_until already has); pause/
    resume_scheduled_task never re-run this against a "once" rule that
    already fired (it disables itself instead, see poll_due_scheduled_tasks).

    `rule.start_date`, if set, floors the search: no occurrence before
    that date is ever returned, by raising the effective `after` to just
    before start_date's own start-of-day when that's later than the real
    `after` -- a no-op once the schedule's actual occurrences have caught
    up to or passed start_date."""
    after = _to_local_naive(after)
    if rule.kind == "manual":
        return None
    if rule.start_date is not None:
        floor = datetime.fromisoformat(rule.start_date) - timedelta(microseconds=1)
        if floor > after:
            after = floor
    if rule.kind == "once":
        candidate = _parse_local(rule.at)
        return candidate.isoformat() if candidate > after else None

    hour, minute = _parse_hhmm(rule.at)

    if rule.kind == "hourly":
        # Only the minute matters -- fires every hour at that minute,
        # regardless of what hour `at` itself names.
        candidate = after.replace(minute=minute, second=0, microsecond=0)
        if candidate <= after:
            candidate += timedelta(hours=1)
        return candidate.isoformat()

    if rule.kind == "daily":
        candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= after:
            candidate += timedelta(days=1)
        return candidate.isoformat()

    if rule.kind == "weekdays":
        candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= after:
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:  # 5=Saturday, 6=Sunday
            candidate += timedelta(days=1)
        return candidate.isoformat()

    if rule.kind == "weekly":
        if rule.weekday is None or not (0 <= rule.weekday <= 6):
            raise ValueError('weekday (0=Monday..6=Sunday) is required for a "weekly" schedule')
        candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        days_ahead = (rule.weekday - candidate.weekday()) % 7
        candidate += timedelta(days=days_ahead)
        if candidate <= after:
            candidate += timedelta(days=7)
        return candidate.isoformat()

    if rule.kind == "monthly":
        if rule.day_of_month is None or not (1 <= rule.day_of_month <= 31):
            raise ValueError('day_of_month (1-31) is required for a "monthly" schedule')
        year, month = after.year, after.month
        day = _clamp_day(year, month, rule.day_of_month)
        candidate = after.replace(
            year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate <= after:
            year, month = _add_months(year, month, 1)
            day = _clamp_day(year, month, rule.day_of_month)
            candidate = candidate.replace(year=year, month=month, day=day)
        return candidate.isoformat()

    raise ValueError(f"Unknown schedule kind {rule.kind!r}, expected one of {VALID_KINDS}")


@dataclass
class ScheduledRun:
    """One execution of a trigger, in its own fresh conversation -- never
    the thread the task was created from, and never a previous run's, so
    history doesn't pile up run after run (continuity comes from the
    task's notes instead)."""

    run_id: str
    thread_id: str
    started_at: str
    source: str  # "manual" (Run now) | "scheduled"
    status: str = "running"  # one of RUN_STATUSES
    finished_at: str | None = None
    error: str | None = None
    # A workflow run's per-step records (workflows/engine.py's StepRecord),
    # in the order the steps first ran, and the inputs it was given.
    steps: list[dict[str, Any]] = field(default_factory=list)
    inputs: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "started_at": self.started_at,
            "source": self.source,
            "status": self.status,
            "finished_at": self.finished_at,
            "error": self.error,
            "steps": self.steps,
            "inputs": self.inputs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduledRun:
        return cls(
            run_id=data["run_id"],
            thread_id=data["thread_id"],
            started_at=data["started_at"],
            source=data.get("source", "scheduled"),
            status=data.get("status", "completed"),
            finished_at=data.get("finished_at"),
            error=data.get("error"),
            steps=data.get("steps") or [],
            inputs=data.get("inputs"),
        )


@dataclass
class ScheduledTrigger:
    trigger_id: str
    name: str
    schedule: ScheduleRule
    enabled: bool
    created_at: str
    next_run_at: str | None  # None only once a "once" trigger has fired,
    # or always for a "manual" schedule (see ScheduleRule.kind)
    prompt: str = ""
    last_run_at: str | None = None
    last_run_status: str | None = None  # "completed" | "failed" | "stopped"
    model: str | None = None  # "provider:model", e.g. "anthropic:claude-
    # opus-5" -- None means the app's own configured default model, same
    # meaning None already has wherever a per-thread model override is
    # optional elsewhere in this codebase.
    approval_mode: str = "manual"  # one of VALID_APPROVAL_MODES. Default
    # "manual" matches this field's real pre-existing behavior for every
    # trigger created before this field existed (ChatSessionLG.
    # accept_edits itself defaults to False) -- loading an old on-disk
    # trigger with no "approval_mode" key via from_dict must not silently
    # change its real behavior.
    notes_enabled: bool = True
    runs: list[ScheduledRun] = field(default_factory=list)  # oldest first
    # A workflows/spec.py Workflow as JSON: when set, a run executes these
    # steps instead of handing `prompt` to the model.
    workflow: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "name": self.name,
            "schedule": self.schedule.to_dict(),
            "enabled": self.enabled,
            "created_at": self.created_at,
            "next_run_at": self.next_run_at,
            "prompt": self.prompt,
            "last_run_at": self.last_run_at,
            "last_run_status": self.last_run_status,
            "model": self.model,
            "approval_mode": self.approval_mode,
            "notes_enabled": self.notes_enabled,
            "runs": [run.to_dict() for run in self.runs],
            "workflow": self.workflow,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduledTrigger:
        runs = [ScheduledRun.from_dict(r) for r in data.get("runs", [])]
        if "runs" not in data and data.get("last_run_at") and data.get("thread_id"):
            # A trigger from before per-run conversations ran every time
            # in one shared thread -- surfaced as a single run so its
            # history stays reachable.
            runs = [
                ScheduledRun(
                    run_id="earlier",
                    thread_id=data["thread_id"],
                    started_at=data["last_run_at"],
                    source="scheduled",
                    status=data.get("last_run_status") or "completed",
                    finished_at=data["last_run_at"],
                )
            ]
        return cls(
            trigger_id=data["trigger_id"],
            name=data["name"],
            schedule=ScheduleRule.from_dict(data["schedule"]),
            enabled=data["enabled"],
            created_at=data["created_at"],
            next_run_at=data.get("next_run_at"),
            prompt=data.get("prompt") or "",
            last_run_at=data.get("last_run_at"),
            last_run_status=data.get("last_run_status"),
            model=data.get("model"),
            approval_mode=data.get("approval_mode", "manual"),
            notes_enabled=data.get("notes_enabled", True),
            runs=runs,
            workflow=data.get("workflow"),
        )

    def find_run(self, run_id: str) -> ScheduledRun | None:
        return next((run for run in self.runs if run.run_id == run_id), None)


class ScheduledTriggerStore:
    """One JSON file per trigger id, global by id (not scoped to any one
    thread) -- same atomic tmp+os.replace() write every other *Store in
    this codebase uses."""

    def __init__(self, state_dir: str | Path) -> None:
        self.root = Path(state_dir) / "scheduled_tasks"

    def save(self, trigger: ScheduledTrigger) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path_for(trigger.trigger_id)
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(trigger.to_dict(), handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

    def load(self, trigger_id: str) -> ScheduledTrigger | None:
        path = self._path_for(trigger_id)
        if not path.exists():
            return None
        return ScheduledTrigger.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_all(self) -> list[ScheduledTrigger]:
        if not self.root.is_dir():
            return []
        return [
            ScheduledTrigger.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(self.root.glob("*.json"))
        ]

    def list_due(self, now: datetime) -> list[ScheduledTrigger]:
        now_iso = _to_local_naive(now).isoformat()
        return [
            t
            for t in self.list_all()
            if t.enabled and t.next_run_at is not None and t.next_run_at <= now_iso
        ]

    def delete(self, trigger_id: str) -> bool:
        path = self._path_for(trigger_id)
        existed = path.exists()
        path.unlink(missing_ok=True)
        self._notes_path(trigger_id).unlink(missing_ok=True)
        return existed

    def start_run(
        self, trigger_id: str, source: str, inputs: dict[str, Any] | None = None
    ) -> tuple[ScheduledTrigger, ScheduledRun, list[ScheduledRun]]:
        """Record a new run as "running". Returns the freshly loaded
        trigger, the new run, and any runs dropped for exceeding
        MAX_RUNS_KEPT (oldest first) so the caller can delete their
        conversations too."""
        trigger = self.load(trigger_id)
        if trigger is None:
            raise KeyError(f"No scheduled task with id {trigger_id!r}")
        run_id = uuid.uuid4().hex[:8]
        run = ScheduledRun(
            run_id=run_id,
            thread_id=run_thread_id(trigger_id, run_id),
            started_at=_now_iso(),
            source=source,
            inputs=inputs,
        )
        trigger.runs.append(run)
        pruned = trigger.runs[:-MAX_RUNS_KEPT]
        trigger.runs = trigger.runs[-MAX_RUNS_KEPT:]
        trigger.last_run_at = run.started_at
        trigger.last_run_status = run.status
        self.save(trigger)
        return trigger, run, pruned

    def finish_run(
        self, trigger_id: str, run_id: str, status: str, error: str | None = None
    ) -> ScheduledTrigger | None:
        """Re-reads the trigger before writing: a run can take minutes, and
        the user may have edited the task meanwhile -- saving the copy
        loaded at start would silently undo that edit."""
        trigger = self.load(trigger_id)
        if trigger is None:
            return None
        run = trigger.find_run(run_id)
        if run is None:
            return trigger
        run.status = status
        run.error = error
        run.finished_at = _now_iso()
        if trigger.runs and trigger.runs[-1] is run:
            trigger.last_run_status = status
        self.save(trigger)
        return trigger

    def record_step(self, trigger_id: str, run_id: str, record: dict[str, Any]) -> None:
        """Replace this step's record on the run -- this pass of it, inside
        a loop -- or append it."""
        trigger = self.load(trigger_id)
        run = trigger.find_run(run_id) if trigger is not None else None
        if trigger is None or run is None:
            return
        for index, existing in enumerate(run.steps):
            if existing.get("step_id") == record.get("step_id") and (
                existing.get("iteration") or []
            ) == (record.get("iteration") or []):
                run.steps[index] = record
                break
        else:
            run.steps.append(record)
        self.save(trigger)

    def reopen_run(self, trigger_id: str, run_id: str) -> ScheduledRun:
        """Mark a finished run as running again -- it's being resumed, a
        step retried, or an approval answered."""
        trigger = self.load(trigger_id)
        run = trigger.find_run(run_id) if trigger is not None else None
        if trigger is None or run is None:
            raise KeyError(f"No run {run_id!r} of scheduled task {trigger_id!r}")
        if run.status == "running":
            raise ValueError("This run is still going")
        run.status = "running"
        run.error = None
        run.finished_at = None
        if trigger.runs[-1] is run:
            trigger.last_run_status = "running"
        self.save(trigger)
        return run

    def read_notes(self, trigger_id: str) -> str:
        path = self._notes_path(trigger_id)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def write_notes(self, trigger_id: str, notes: str) -> None:
        if len(notes) > MAX_NOTES_CHARS:
            raise ValueError(
                f"Notes are {len(notes)} characters, over the {MAX_NOTES_CHARS} limit -- "
                "condense them to what future runs actually need."
            )
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._notes_path(trigger_id)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(notes, encoding="utf-8")
        os.replace(tmp_path, path)

    def _path_for(self, trigger_id: str) -> Path:
        return self.root / f"{trigger_id}.json"

    def _notes_path(self, trigger_id: str) -> Path:
        return self.root / f"{trigger_id}.notes.md"


def _validate_and_build_schedule(
    *,
    kind: str,
    at: str,
    prompt: str,
    weekday: int | None,
    day_of_month: int | None,
    start_date: str | None,
    approval_mode: str,
    workflow: dict[str, Any] | None = None,
) -> tuple[ScheduleRule, str | None, dict[str, Any] | None]:
    """Shared validation + ScheduleRule construction for create_trigger
    and update_trigger -- kept as one function so the two paths can never
    silently drift out of sync with each other, same reasoning
    create_trigger's own docstring already gives for being shared between
    the model tool and the REST endpoint."""
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}, got {kind!r}")
    if approval_mode not in VALID_APPROVAL_MODES:
        raise ValueError(
            f"approval_mode must be one of {VALID_APPROVAL_MODES}, got {approval_mode!r}"
        )
    normalized_workflow = None
    if workflow is not None:
        try:
            normalized_workflow = parse_workflow(workflow).model_dump(mode="json")
        except ValidationError as exc:
            raise ValueError(f"The workflow isn't valid: {workflow_error(exc, workflow)}") from exc
    elif not prompt.strip():
        raise ValueError("prompt cannot be blank")

    rule = ScheduleRule(
        kind=kind, at=at, weekday=weekday, day_of_month=day_of_month, start_date=start_date
    )
    next_run_at = compute_next_run_at(rule, datetime.now())
    # "manual" never has a next_run_at by design (see ScheduleRule.kind) --
    # only every other kind treats a None result as "at was in the past."
    if next_run_at is None and kind != "manual":
        raise ValueError(f"at must be in the future, got {at!r}")
    return rule, next_run_at, normalized_workflow


def create_trigger(
    store: ScheduledTriggerStore,
    *,
    name: str,
    kind: str,
    at: str,
    prompt: str,
    weekday: int | None = None,
    day_of_month: int | None = None,
    start_date: str | None = None,
    model: str | None = None,
    approval_mode: str = "manual",
    notes_enabled: bool = True,
    workflow: dict[str, Any] | None = None,
) -> ScheduledTrigger:
    """Validate and persist a new ScheduledTrigger -- shared by
    build_scheduled_task_tools' model-callable create_scheduled_task and
    web/app.py's POST /api/scheduled-tasks endpoint (the UI's create form,
    which needs the exact same validation without going through a live
    conversation), so the two creation paths can never silently drift out
    of sync with each other."""
    rule, next_run_at, normalized_workflow = _validate_and_build_schedule(
        kind=kind,
        at=at,
        prompt=prompt,
        weekday=weekday,
        day_of_month=day_of_month,
        start_date=start_date,
        approval_mode=approval_mode,
        workflow=workflow,
    )

    trigger_id = uuid.uuid4().hex[:12]
    trigger = ScheduledTrigger(
        trigger_id=trigger_id,
        name=name,
        schedule=rule,
        enabled=True,
        created_at=_now_iso(),
        next_run_at=next_run_at,
        prompt=prompt,
        model=model,
        approval_mode=approval_mode,
        notes_enabled=notes_enabled,
        workflow=normalized_workflow,
    )
    store.save(trigger)
    return trigger


def update_trigger(
    store: ScheduledTriggerStore,
    trigger_id: str,
    *,
    name: str,
    kind: str,
    at: str,
    prompt: str,
    weekday: int | None = None,
    day_of_month: int | None = None,
    start_date: str | None = None,
    model: str | None = None,
    approval_mode: str = "manual",
    notes_enabled: bool = True,
    workflow: dict[str, Any] | None = None,
) -> ScheduledTrigger:
    """Edit an existing trigger in place -- the Edit modal's Save action.
    Same validation as create_trigger (via _validate_and_build_schedule),
    so an edited task can never end up in a state a *new* task couldn't
    also be created in. Preserves trigger_id/created_at/enabled/runs/
    last_run_at/last_run_status; recomputes next_run_at from now against
    the (possibly changed) schedule, same as resume_scheduled_task already
    does when re-enabling a paused trigger -- editing a currently-paused
    task's schedule doesn't un-pause it, but next_run_at still reflects
    what it *would* run next if resumed, not a stale value from before
    the edit."""
    trigger = store.load(trigger_id)
    if trigger is None:
        raise KeyError(f"No scheduled task with id {trigger_id!r}")

    rule, next_run_at, normalized_workflow = _validate_and_build_schedule(
        kind=kind,
        at=at,
        prompt=prompt,
        weekday=weekday,
        day_of_month=day_of_month,
        start_date=start_date,
        approval_mode=approval_mode,
        workflow=workflow,
    )

    trigger.name = name
    trigger.schedule = rule
    trigger.next_run_at = next_run_at
    trigger.prompt = prompt
    trigger.model = model
    trigger.approval_mode = approval_mode
    trigger.notes_enabled = notes_enabled
    trigger.workflow = normalized_workflow
    store.save(trigger)
    return trigger


def build_scheduled_task_tools(
    state_dir: str | Path, thread_id: str | None = None
) -> list[Callable[..., Any]]:
    """Return the Scheduled Tasks tool callables. They operate globally,
    not scoped to the calling thread -- a task is an independent entity,
    not tied to the conversation that created it. The one exception is
    update_task_notes, only present when `thread_id` is a run's own
    thread (see parse_run_thread_id), and only ever touching that run's
    own task.
    """
    store = ScheduledTriggerStore(state_dir)

    # Optional[X], not X | None: aisuite's Tools.__infer_from_signature
    # only unwraps typing.Optional (checks `get_origin(t) is Union`), and
    # PEP 604 `X | None` has origin `types.UnionType` instead -- it slips
    # through unwrapped and gets serialized as the literal string
    # "X | None" for the "type" field, which Gemini's strict
    # OpenAPI-subset schema rejects (see tools/spreadsheets.py's read_xlsx
    # for the same fix, applied first).
    def create_scheduled_task(
        name: str,
        kind: str,
        at: str,
        prompt: str,
        weekday: Optional[int] = None,  # noqa: UP045
        day_of_month: Optional[int] = None,  # noqa: UP045
        start_date: Optional[str] = None,  # noqa: UP045
        model: Optional[str] = None,  # noqa: UP045
        approval_mode: str = "manual",
    ) -> dict[str, Any]:
        """Draft a scheduled task (a reusable workflow) for the user to
        review -- they can edit any field before saving, or dismiss it;
        the result tells you which. Runs repeatedly on an hourly/daily/
        weekdays/weekly/monthly schedule, or only when the user starts it
        ("manual"), independent of whether any conversation is open. Each
        run starts in a brand-new conversation with none of this one's
        context, so `prompt` must stand on its own.

        Args:
            name: short, human-readable name, e.g. "Weekly sales report".
            kind: "manual", "hourly", "daily", "weekdays", "weekly", or
                "monthly".
            at: unused for kind="manual". For kind="hourly", "HH:MM" but
                only the minute is used (fires every hour at that
                minute). Otherwise a 24-hour "HH:MM" time of day, e.g.
                "09:00" -- interpreted in this machine's own local
                timezone.
            prompt: complete, standalone instructions for a run: the
                goal, the concrete steps and inputs that worked, pitfalls
                to avoid, and the expected output.
            weekday: required for kind="weekly" -- 0=Monday .. 6=Sunday.
            day_of_month: required for kind="monthly" -- 1-31 (clamped to
                the real last day of a shorter month).
            start_date: "YYYY-MM-DD" -- the schedule produces no
                occurrence before this date. Optional; defaults to
                starting immediately.
            model: "provider:model" to run this task with, e.g.
                "anthropic:claude-opus-5". Optional; defaults to the
                app's own configured default model.
            approval_mode: "manual" (pauses for every action needing
                approval -- the default), "auto" (creates and edits
                local files without asking, but pauses before running
                code or using connectors/downloads), or "skip" (never
                pauses, including for running code -- use with real
                caution for an unattended task).
        """
        trigger = create_trigger(
            store,
            name=name,
            kind=kind,
            at=at,
            prompt=prompt,
            weekday=weekday,
            day_of_month=day_of_month,
            start_date=start_date,
            model=model,
            approval_mode=approval_mode,
        )
        return trigger.to_dict()

    def list_scheduled_tasks() -> list[dict[str, Any]]:
        """List every scheduled task, across all conversations."""
        return [t.to_dict() for t in store.list_all()]

    def _load_or_raise(trigger_id: str) -> ScheduledTrigger:
        trigger = store.load(trigger_id)
        if trigger is None:
            raise KeyError(f"No scheduled task with id {trigger_id!r}")
        return trigger

    def pause_scheduled_task(trigger_id: str) -> dict[str, Any]:
        """Pause a scheduled task -- it won't fire again until resumed.

        Args:
            trigger_id: id of the task, as returned by create_scheduled_task
                or list_scheduled_tasks.
        """
        trigger = _load_or_raise(trigger_id)
        trigger.enabled = False
        store.save(trigger)
        return trigger.to_dict()

    def resume_scheduled_task(trigger_id: str) -> dict[str, Any]:
        """Resume a paused scheduled task. Its next run time is recomputed
        from now (a task paused for two weeks won't fire twenty times
        back to back on resume).

        Args:
            trigger_id: id of the task, as returned by create_scheduled_task
                or list_scheduled_tasks.
        """
        trigger = _load_or_raise(trigger_id)
        trigger.enabled = True
        trigger.next_run_at = compute_next_run_at(trigger.schedule, datetime.now())
        store.save(trigger)
        return trigger.to_dict()

    def delete_scheduled_task(trigger_id: str) -> dict[str, Any]:
        """Permanently delete a scheduled task.

        Args:
            trigger_id: id of the task, as returned by create_scheduled_task
                or list_scheduled_tasks.
        """
        if not store.delete(trigger_id):
            raise KeyError(f"No scheduled task with id {trigger_id!r}")
        return {"deleted": trigger_id}

    category = "scheduled_tasks"
    run = parse_run_thread_id(thread_id) if thread_id else None
    extra: list[Callable[..., Any]] = []
    if run is not None:
        own_trigger_id = run[0]

        def update_task_notes(notes: str) -> dict[str, Any]:
            """Replace this scheduled task's notes -- your memory across its
            runs, shown to you at the start of every future run. Write the
            full updated notes (not a diff): progress markers, where you
            left off, what changed, pitfalls worth remembering. Drop
            anything stale. Keep them short.

            Args:
                notes: the complete new notes, plain text or markdown.
            """
            store.write_notes(own_trigger_id, notes.strip())
            return {"saved": True, "characters": len(notes.strip())}

        # READ tier: the notes are the task's own bookkeeping, not user
        # content -- gating them would park every "ask first" run on an
        # approval nobody is there to give.
        extra.append(tool_metadata(update_task_notes, risk_category="READ", category=category))
    return extra + [
        tool_metadata(create_scheduled_task, risk_category="WRITE_LOCAL", category=category),
        tool_metadata(list_scheduled_tasks, risk_category="READ", category=category),
        tool_metadata(pause_scheduled_task, risk_category="WRITE_LOCAL", category=category),
        tool_metadata(resume_scheduled_task, risk_category="WRITE_LOCAL", category=category),
        tool_metadata(delete_scheduled_task, risk_category="WRITE_LOCAL", category=category),
    ]
