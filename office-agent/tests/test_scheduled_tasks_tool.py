from datetime import datetime, timedelta
from pathlib import Path

import pytest

from coscribe.runtime.types import get_tool_metadata
from coscribe.tools.scheduled_tasks import (
    ScheduledTriggerStore,
    ScheduleRule,
    build_scheduled_task_tools,
    compute_next_run_at,
    create_trigger,
)
from coscribe.tools.workflows import Workflow, WorkflowStore


def _tools_by_name(state_dir: Path) -> dict[str, object]:
    return {tool.__name__: tool for tool in build_scheduled_task_tools(state_dir)}  # type: ignore[attr-defined]


# -- compute_next_run_at --


def test_compute_next_run_at_once_future() -> None:
    now = datetime(2026, 1, 1, 10, 0, 0)
    rule = ScheduleRule(kind="once", at="2026-01-02T09:00:00")
    assert compute_next_run_at(rule, now) == "2026-01-02T09:00:00"


def test_compute_next_run_at_once_past_returns_none() -> None:
    now = datetime(2026, 1, 2, 10, 0, 0)
    rule = ScheduleRule(kind="once", at="2026-01-01T09:00:00")
    assert compute_next_run_at(rule, now) is None


def test_compute_next_run_at_daily_rolls_to_tomorrow_if_time_passed() -> None:
    now = datetime(2026, 1, 1, 10, 0, 0)
    rule = ScheduleRule(kind="daily", at="09:00")
    assert compute_next_run_at(rule, now) == "2026-01-02T09:00:00"


def test_compute_next_run_at_daily_stays_today_if_time_not_passed() -> None:
    now = datetime(2026, 1, 1, 10, 0, 0)
    rule = ScheduleRule(kind="daily", at="11:00")
    assert compute_next_run_at(rule, now) == "2026-01-01T11:00:00"


def test_compute_next_run_at_weekly_upcoming_this_week() -> None:
    now = datetime(2026, 1, 28, 10, 0, 0)  # Wednesday
    assert now.weekday() == 2
    rule = ScheduleRule(kind="weekly", at="09:00", weekday=5)  # Saturday
    assert compute_next_run_at(rule, now) == "2026-01-31T09:00:00"


def test_compute_next_run_at_weekly_rolls_to_next_week_if_passed() -> None:
    now = datetime(2026, 1, 31, 10, 0, 0)  # Saturday, target time already passed
    assert now.weekday() == 5
    rule = ScheduleRule(kind="weekly", at="09:00", weekday=5)
    assert compute_next_run_at(rule, now) == "2026-02-07T09:00:00"


def test_compute_next_run_at_weekly_requires_weekday() -> None:
    with pytest.raises(ValueError, match="weekday"):
        compute_next_run_at(ScheduleRule(kind="weekly", at="09:00"), datetime.now())


def test_compute_next_run_at_monthly_later_this_month() -> None:
    now = datetime(2026, 1, 28, 10, 0, 0)
    rule = ScheduleRule(kind="monthly", at="09:00", day_of_month=30)
    assert compute_next_run_at(rule, now) == "2026-01-30T09:00:00"


def test_compute_next_run_at_monthly_clamps_to_month_end() -> None:
    # Jan 31st already passed -> Feb has no 31st, clamp to 28 (2026 not leap)
    now = datetime(2026, 1, 31, 10, 0, 0)
    rule = ScheduleRule(kind="monthly", at="09:00", day_of_month=31)
    assert compute_next_run_at(rule, now) == "2026-02-28T09:00:00"


def test_compute_next_run_at_monthly_requires_day_of_month() -> None:
    with pytest.raises(ValueError, match="day_of_month"):
        compute_next_run_at(ScheduleRule(kind="monthly", at="09:00"), datetime.now())


def test_compute_next_run_at_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="Unknown schedule kind"):
        compute_next_run_at(ScheduleRule(kind="yearly", at="09:00"), datetime.now())


# -- create_trigger / create_scheduled_task validation --


def test_create_trigger_requires_exactly_one_of_prompt_or_workflow_name(tmp_path: Path) -> None:
    store = ScheduledTriggerStore(tmp_path)
    workflow_store = WorkflowStore(tmp_path)
    with pytest.raises(ValueError, match="exactly one"):
        create_trigger(store, workflow_store, name="x", kind="daily", at="09:00")
    with pytest.raises(ValueError, match="exactly one"):
        create_trigger(
            store,
            workflow_store,
            name="x",
            kind="daily",
            at="09:00",
            prompt="p",
            workflow_name="wf",
        )


def test_create_trigger_rejects_unknown_workflow_name(tmp_path: Path) -> None:
    store = ScheduledTriggerStore(tmp_path)
    workflow_store = WorkflowStore(tmp_path)
    with pytest.raises(ValueError, match="No workflow named"):
        create_trigger(
            store, workflow_store, name="x", kind="daily", at="09:00", workflow_name="missing"
        )


def test_create_trigger_accepts_a_real_workflow_name(tmp_path: Path) -> None:
    store = ScheduledTriggerStore(tmp_path)
    workflow_store = WorkflowStore(tmp_path)
    workflow_store.save(
        Workflow(name="nightly-report", mode="agent", summary="Generate the nightly report")
    )

    trigger = create_trigger(
        store, workflow_store, name="x", kind="daily", at="09:00", workflow_name="nightly-report"
    )

    assert trigger.workflow_name == "nightly-report"
    assert trigger.prompt is None


def test_create_trigger_rejects_unknown_kind(tmp_path: Path) -> None:
    store = ScheduledTriggerStore(tmp_path)
    workflow_store = WorkflowStore(tmp_path)
    with pytest.raises(ValueError, match="kind must be one of"):
        create_trigger(store, workflow_store, name="x", kind="yearly", at="09:00", prompt="p")


def test_create_trigger_mints_its_own_dedicated_thread_id(tmp_path: Path) -> None:
    store = ScheduledTriggerStore(tmp_path)
    workflow_store = WorkflowStore(tmp_path)
    trigger = create_trigger(store, workflow_store, name="x", kind="daily", at="09:00", prompt="p")
    assert trigger.thread_id == f"scheduled-{trigger.trigger_id}"


# -- model-callable tools --


def test_create_scheduled_task_tool_persists_and_lists(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    created = tools["create_scheduled_task"](
        name="Daily standup notes", kind="daily", at="09:00", prompt="Summarize yesterday"
    )
    assert created["name"] == "Daily standup notes"
    assert created["enabled"] is True

    listed = tools["list_scheduled_tasks"]()
    assert [t["trigger_id"] for t in listed] == [created["trigger_id"]]


def test_pause_then_resume_recomputes_next_run_at(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    created = tools["create_scheduled_task"](name="x", kind="daily", at="09:00", prompt="p")

    paused = tools["pause_scheduled_task"](trigger_id=created["trigger_id"])
    assert paused["enabled"] is False

    # Directly mutate the stored next_run_at to a stale past value (as if
    # this task had been paused for a very long time), then resume --
    # resuming must recompute forward from *now*, not just flip enabled
    # back and leave the stale value in place (which would make it fire
    # immediately, potentially many times, on the very next poll).
    stale_value = (datetime.now() - timedelta(days=100)).isoformat()
    store = ScheduledTriggerStore(tmp_path)
    stale = store.load(created["trigger_id"])
    assert stale is not None
    stale.next_run_at = stale_value
    store.save(stale)

    resumed = tools["resume_scheduled_task"](trigger_id=created["trigger_id"])
    assert resumed["enabled"] is True
    assert resumed["next_run_at"] > datetime.now().isoformat()
    assert resumed["next_run_at"] != stale_value


def test_pause_unknown_trigger_raises(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    with pytest.raises(KeyError):
        tools["pause_scheduled_task"](trigger_id="does-not-exist")


def test_delete_scheduled_task(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    created = tools["create_scheduled_task"](name="x", kind="daily", at="09:00", prompt="p")

    tools["delete_scheduled_task"](trigger_id=created["trigger_id"])

    assert tools["list_scheduled_tasks"]() == []
    with pytest.raises(KeyError):
        tools["delete_scheduled_task"](trigger_id=created["trigger_id"])


def test_listing_is_global_not_scoped_to_a_single_thread(tmp_path: Path) -> None:
    # build_scheduled_task_tools takes no thread_id at all -- two separate
    # toolkits built against the same state_dir must see each other's tasks.
    tools_a = _tools_by_name(tmp_path)
    tools_b = _tools_by_name(tmp_path)
    tools_a["create_scheduled_task"](name="from a", kind="daily", at="09:00", prompt="p")

    listed_from_b = tools_b["list_scheduled_tasks"]()

    assert [t["name"] for t in listed_from_b] == ["from a"]


# -- risk classification --


def test_mutating_tools_are_write_local_and_gated(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    for name in (
        "create_scheduled_task",
        "pause_scheduled_task",
        "resume_scheduled_task",
        "delete_scheduled_task",
    ):
        metadata = get_tool_metadata(tools[name])
        assert metadata.risk_category == "WRITE_LOCAL"
        assert metadata.requires_approval is True


def test_list_scheduled_tasks_is_read_and_ungated(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    metadata = get_tool_metadata(tools["list_scheduled_tasks"])
    assert metadata.risk_category == "READ"
    assert metadata.requires_approval is False
