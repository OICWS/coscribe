"""Scheduled Tasks: a real, independently-manageable product feature for
"do this later, once or on a repeating schedule" -- distinct from
tools/selfwake.py's sleep_for/wake_on/wake_on_event, which are agent-
initiated, one-off, thread-scoped pauses only callable from inside an
existing conversation. A ScheduledTrigger is the opposite shape: global,
named, persisted, creatable either mid-conversation (create_scheduled_task)
or from the Settings > Scheduled Tasks panel with no conversation needed
at all -- much closer in spirit to tools/workflows.py's Workflow (global,
named, persisted) than to selfwake's WakeRequest, so it's its own concept
rather than a new WakeRequest kind. See ARCHITECTURE.md's 范围边界 section
for the fuller reasoning.

Split the same way tools/workflows.py and tools/selfwake.py split their
own data model/storage/tools from the runtime-specific resume logic: this
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
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from ..runtime.types import tool_metadata
from .workflows import WorkflowStore

VALID_KINDS = ("manual", "once", "hourly", "daily", "weekdays", "weekly", "monthly")
VALID_APPROVAL_MODES = ("manual", "auto", "skip")

# A trigger's own dedicated conversation is keyed off this prefix (see
# create_trigger below) -- shared with web/app.py's list_threads, which
# filters threads by it so a fired trigger's conversation doesn't also
# show up in the ordinary chat sidebar (the frontend's own Scheduled
# section is the one place to find it, per an explicit request).
SCHEDULED_THREAD_PREFIX = "scheduled-"


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
    # "weekly" | "monthly" -- plain str, same Literal-avoidance reasoning as
    # tools/workflows.py's Workflow.mode. "once" predates the six-value
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
class ScheduledTrigger:
    trigger_id: str
    name: str
    thread_id: str  # this trigger's own dedicated, persistent thread --
    # never the thread it was created from, so a recurring fire never
    # injects messages into whatever conversation the user happened to be
    # in when they asked for it.
    schedule: ScheduleRule
    enabled: bool
    created_at: str
    next_run_at: str | None  # None only once a "once" trigger has fired,
    # or always for a "manual" schedule (see ScheduleRule.kind)
    workflow_name: str | None = None  # exactly one of workflow_name/prompt
    prompt: str | None = None
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "name": self.name,
            "thread_id": self.thread_id,
            "schedule": self.schedule.to_dict(),
            "enabled": self.enabled,
            "created_at": self.created_at,
            "next_run_at": self.next_run_at,
            "workflow_name": self.workflow_name,
            "prompt": self.prompt,
            "last_run_at": self.last_run_at,
            "last_run_status": self.last_run_status,
            "model": self.model,
            "approval_mode": self.approval_mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduledTrigger:
        return cls(
            trigger_id=data["trigger_id"],
            name=data["name"],
            thread_id=data["thread_id"],
            schedule=ScheduleRule.from_dict(data["schedule"]),
            enabled=data["enabled"],
            created_at=data["created_at"],
            next_run_at=data.get("next_run_at"),
            workflow_name=data.get("workflow_name"),
            prompt=data.get("prompt"),
            last_run_at=data.get("last_run_at"),
            last_run_status=data.get("last_run_status"),
            model=data.get("model"),
            approval_mode=data.get("approval_mode", "manual"),
        )


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
        return existed

    def _path_for(self, trigger_id: str) -> Path:
        return self.root / f"{trigger_id}.json"


def _validate_and_build_schedule(
    workflow_store: WorkflowStore,
    *,
    kind: str,
    at: str,
    prompt: str | None,
    workflow_name: str | None,
    weekday: int | None,
    day_of_month: int | None,
    start_date: str | None,
    approval_mode: str,
) -> tuple[ScheduleRule, str | None]:
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
    if bool(prompt) == bool(workflow_name):
        raise ValueError("exactly one of prompt or workflow_name must be given")
    if workflow_name is not None and workflow_store.load(workflow_name) is None:
        raise ValueError(f"No workflow named {workflow_name!r}")

    rule = ScheduleRule(
        kind=kind, at=at, weekday=weekday, day_of_month=day_of_month, start_date=start_date
    )
    next_run_at = compute_next_run_at(rule, datetime.now())
    # "manual" never has a next_run_at by design (see ScheduleRule.kind) --
    # only every other kind treats a None result as "at was in the past."
    if next_run_at is None and kind != "manual":
        raise ValueError(f"at must be in the future, got {at!r}")
    return rule, next_run_at


def create_trigger(
    store: ScheduledTriggerStore,
    workflow_store: WorkflowStore,
    *,
    name: str,
    kind: str,
    at: str,
    prompt: str | None = None,
    workflow_name: str | None = None,
    weekday: int | None = None,
    day_of_month: int | None = None,
    start_date: str | None = None,
    model: str | None = None,
    approval_mode: str = "manual",
) -> ScheduledTrigger:
    """Validate and persist a new ScheduledTrigger -- shared by
    build_scheduled_task_tools' model-callable create_scheduled_task and
    web/app.py's POST /api/scheduled-tasks endpoint (the UI's create form,
    which needs the exact same validation without going through a live
    conversation), so the two creation paths can never silently drift out
    of sync with each other."""
    rule, next_run_at = _validate_and_build_schedule(
        workflow_store,
        kind=kind,
        at=at,
        prompt=prompt,
        workflow_name=workflow_name,
        weekday=weekday,
        day_of_month=day_of_month,
        start_date=start_date,
        approval_mode=approval_mode,
    )

    trigger_id = uuid.uuid4().hex[:12]
    trigger = ScheduledTrigger(
        trigger_id=trigger_id,
        name=name,
        thread_id=f"{SCHEDULED_THREAD_PREFIX}{trigger_id}",
        schedule=rule,
        enabled=True,
        created_at=_now_iso(),
        next_run_at=next_run_at,
        workflow_name=workflow_name,
        prompt=prompt,
        model=model,
        approval_mode=approval_mode,
    )
    store.save(trigger)
    return trigger


def update_trigger(
    store: ScheduledTriggerStore,
    workflow_store: WorkflowStore,
    trigger_id: str,
    *,
    name: str,
    kind: str,
    at: str,
    prompt: str | None = None,
    workflow_name: str | None = None,
    weekday: int | None = None,
    day_of_month: int | None = None,
    start_date: str | None = None,
    model: str | None = None,
    approval_mode: str = "manual",
) -> ScheduledTrigger:
    """Edit an existing trigger in place -- the Edit modal's Save action.
    Same validation as create_trigger (via _validate_and_build_schedule),
    so an edited task can never end up in a state a *new* task couldn't
    also be created in. Preserves trigger_id/thread_id/created_at/enabled/
    last_run_at/last_run_status; recomputes next_run_at from now against
    the (possibly changed) schedule, same as resume_scheduled_task already
    does when re-enabling a paused trigger -- editing a currently-paused
    task's schedule doesn't un-pause it, but next_run_at still reflects
    what it *would* run next if resumed, not a stale value from before
    the edit."""
    trigger = store.load(trigger_id)
    if trigger is None:
        raise KeyError(f"No scheduled task with id {trigger_id!r}")

    rule, next_run_at = _validate_and_build_schedule(
        workflow_store,
        kind=kind,
        at=at,
        prompt=prompt,
        workflow_name=workflow_name,
        weekday=weekday,
        day_of_month=day_of_month,
        start_date=start_date,
        approval_mode=approval_mode,
    )

    trigger.name = name
    trigger.schedule = rule
    trigger.next_run_at = next_run_at
    trigger.workflow_name = workflow_name
    trigger.prompt = prompt
    trigger.model = model
    trigger.approval_mode = approval_mode
    store.save(trigger)
    return trigger


def build_scheduled_task_tools(state_dir: str | Path) -> list[Callable[..., Any]]:
    """Return the Scheduled Tasks tool callables. Every tool here operates
    globally, not scoped to the calling thread (unlike tools/selfwake.py's
    list_wakes/cancel_wake) -- same global-by-design scope as
    list_workflows/delete_workflow, matching ScheduledTrigger's own
    "independent entity, not tied to the conversation that created it"
    nature (each trigger mints its own new, independent thread id, never
    reusing whatever thread called create_scheduled_task).
    """
    store = ScheduledTriggerStore(state_dir)
    workflow_store = WorkflowStore(state_dir)

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
        prompt: Optional[str] = None,  # noqa: UP045
        workflow_name: Optional[str] = None,  # noqa: UP045
        weekday: Optional[int] = None,  # noqa: UP045
        day_of_month: Optional[int] = None,  # noqa: UP045
        start_date: Optional[str] = None,  # noqa: UP045
        model: Optional[str] = None,  # noqa: UP045
        approval_mode: str = "manual",
    ) -> dict[str, Any]:
        """Create a scheduled task -- runs once at a specific time, or
        repeatedly on an hourly/daily/weekdays/weekly/monthly schedule
        ("manual" never fires on its own, only via a future explicit
        run), independent of whether any conversation or browser tab is
        open. Runs in its own dedicated, persistent conversation (not
        this one), so it never interrupts whatever you're doing when it
        fires.

        Args:
            name: short, human-readable name, e.g. "Weekly sales report".
            kind: "manual", "hourly", "daily", "weekdays", "weekly", or
                "monthly".
            at: unused for kind="manual". For kind="hourly", "HH:MM" but
                only the minute is used (fires every hour at that
                minute). Otherwise a 24-hour "HH:MM" time of day, e.g.
                "09:00" -- interpreted in this machine's own local
                timezone.
            prompt: what to do when it fires, in plain language. Exactly
                one of prompt/workflow_name must be given.
            workflow_name: instead of a freeform prompt, run this
                already-saved workflow (see list_workflows) each time.
                Exactly one of prompt/workflow_name must be given.
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
                approval -- the default, and the only option that
                behaves safely if nobody is watching when it fires),
                "auto" (auto-approves like accept-edits mode, still
                pausing if something looks genuinely unsafe), or "skip"
                (never pauses, even for unsafe actions -- use with real
                caution for an unattended task).
        """
        trigger = create_trigger(
            store,
            workflow_store,
            name=name,
            kind=kind,
            at=at,
            prompt=prompt,
            workflow_name=workflow_name,
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
    return [
        tool_metadata(create_scheduled_task, risk_category="WRITE_LOCAL", category=category),
        tool_metadata(list_scheduled_tasks, risk_category="READ", category=category),
        tool_metadata(pause_scheduled_task, risk_category="WRITE_LOCAL", category=category),
        tool_metadata(resume_scheduled_task, risk_category="WRITE_LOCAL", category=category),
        tool_metadata(delete_scheduled_task, risk_category="WRITE_LOCAL", category=category),
    ]
