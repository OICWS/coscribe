from datetime import datetime, timedelta
from pathlib import Path

import pytest

from coscribe.runtime.types import get_tool_metadata
from coscribe.tools.scheduled_tasks import (
    ScheduledTrigger,
    ScheduledTriggerStore,
    ScheduleRule,
    build_scheduled_task_tools,
    compute_next_run_at,
    create_trigger,
    update_trigger,
)


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


def test_compute_next_run_at_manual_is_always_none() -> None:
    # "at" is unused/meaningless for "manual" -- confirms it's never even
    # parsed (an invalid "at" would raise inside _parse_hhmm if it were).
    rule = ScheduleRule(kind="manual", at="not a real time")
    assert compute_next_run_at(rule, datetime.now()) is None


def test_compute_next_run_at_hourly_uses_only_the_minute() -> None:
    now = datetime(2026, 1, 1, 10, 20, 0)
    # "at"'s hour (14) is irrelevant -- only :45 matters, and 10:45 is
    # still ahead of 10:20 today.
    rule = ScheduleRule(kind="hourly", at="14:45")
    assert compute_next_run_at(rule, now) == "2026-01-01T10:45:00"


def test_compute_next_run_at_hourly_rolls_to_the_next_hour_if_minute_passed() -> None:
    now = datetime(2026, 1, 1, 10, 50, 0)
    rule = ScheduleRule(kind="hourly", at="00:20")
    assert compute_next_run_at(rule, now) == "2026-01-01T11:20:00"


def test_compute_next_run_at_weekdays_skips_saturday_and_sunday() -> None:
    now = datetime(2026, 1, 30, 10, 0, 0)  # Friday, target time already passed
    assert now.weekday() == 4
    rule = ScheduleRule(kind="weekdays", at="09:00")
    # Saturday 31st and Sunday Feb 1st both skipped -> Monday Feb 2nd.
    assert compute_next_run_at(rule, now) == "2026-02-02T09:00:00"


def test_compute_next_run_at_weekdays_stays_within_the_same_week() -> None:
    now = datetime(2026, 1, 28, 10, 0, 0)  # Wednesday, target time not yet passed
    assert now.weekday() == 2
    rule = ScheduleRule(kind="weekdays", at="11:00")
    assert compute_next_run_at(rule, now) == "2026-01-28T11:00:00"


def test_compute_next_run_at_start_date_floors_the_first_occurrence() -> None:
    now = datetime(2026, 1, 1, 10, 0, 0)
    rule = ScheduleRule(kind="daily", at="09:00", start_date="2026-01-10")
    # Without start_date this would be tomorrow (Jan 2); start_date pushes
    # the very first occurrence out to Jan 10 instead.
    assert compute_next_run_at(rule, now) == "2026-01-10T09:00:00"


def test_compute_next_run_at_start_date_in_the_past_is_a_no_op() -> None:
    now = datetime(2026, 1, 15, 10, 0, 0)
    rule = ScheduleRule(kind="daily", at="09:00", start_date="2026-01-01")
    assert compute_next_run_at(rule, now) == "2026-01-16T09:00:00"


# -- create_trigger / create_scheduled_task validation --


def test_create_trigger_rejects_unknown_kind(tmp_path: Path) -> None:
    store = ScheduledTriggerStore(tmp_path)
    with pytest.raises(ValueError, match="kind must be one of"):
        create_trigger(store, name="x", kind="yearly", at="09:00", prompt="p")


def test_create_trigger_mints_its_own_dedicated_thread_id(tmp_path: Path) -> None:
    store = ScheduledTriggerStore(tmp_path)
    trigger = create_trigger(store, name="x", kind="daily", at="09:00", prompt="p")
    assert trigger.thread_id == f"scheduled-{trigger.trigger_id}"


def test_create_trigger_manual_kind_has_no_next_run_at_and_does_not_raise(tmp_path: Path) -> None:
    store = ScheduledTriggerStore(tmp_path)
    trigger = create_trigger(store, name="x", kind="manual", at="", prompt="p")
    assert trigger.next_run_at is None
    assert trigger.enabled is True  # created enabled, just never auto-fires


def test_create_trigger_rejects_unknown_approval_mode(tmp_path: Path) -> None:
    store = ScheduledTriggerStore(tmp_path)
    with pytest.raises(ValueError, match="approval_mode must be one of"):
        create_trigger(
            store,
            name="x",
            kind="daily",
            at="09:00",
            prompt="p",
            approval_mode="yolo",
        )


def test_create_trigger_persists_model_and_approval_mode(tmp_path: Path) -> None:
    store = ScheduledTriggerStore(tmp_path)
    trigger = create_trigger(
        store,
        name="x",
        kind="daily",
        at="09:00",
        prompt="p",
        model="anthropic:claude-opus-5",
        approval_mode="skip",
    )
    assert trigger.model == "anthropic:claude-opus-5"
    assert trigger.approval_mode == "skip"

    reloaded = store.load(trigger.trigger_id)
    assert reloaded is not None
    assert reloaded.model == "anthropic:claude-opus-5"
    assert reloaded.approval_mode == "skip"


def test_scheduled_trigger_from_dict_defaults_approval_mode_for_old_records(tmp_path: Path) -> None:
    """A trigger persisted before approval_mode existed has no such key on
    disk -- from_dict must default it to "manual", the real pre-existing
    behavior (ChatSessionLG.accept_edits itself defaults to False), not
    silently change what an old trigger does."""
    trigger = ScheduledTrigger.from_dict(
        {
            "trigger_id": "old-1",
            "name": "Pre-existing task",
            "thread_id": "scheduled-old-1",
            "schedule": {"kind": "daily", "at": "09:00", "weekday": None, "day_of_month": None},
            "enabled": True,
            "created_at": datetime.now().isoformat(),
            "next_run_at": None,
            "prompt": "p",
        }
    )
    assert trigger.approval_mode == "manual"
    assert trigger.model is None
    assert trigger.schedule.start_date is None


# -- update_trigger --


def test_update_trigger_changes_name_schedule_and_permission_fields(tmp_path: Path) -> None:
    store = ScheduledTriggerStore(tmp_path)
    created = create_trigger(store, name="Old name", kind="daily", at="09:00", prompt="old prompt")

    updated = update_trigger(
        store,
        created.trigger_id,
        name="New name",
        kind="weekly",
        at="10:00",
        weekday=2,
        prompt="new prompt",
        model="anthropic:claude-opus-5",
        approval_mode="auto",
    )

    assert updated.trigger_id == created.trigger_id  # identity preserved
    assert updated.thread_id == created.thread_id
    assert updated.created_at == created.created_at
    assert updated.name == "New name"
    assert updated.schedule.kind == "weekly"
    assert updated.schedule.weekday == 2
    assert updated.prompt == "new prompt"
    assert updated.model == "anthropic:claude-opus-5"
    assert updated.approval_mode == "auto"

    reloaded = store.load(created.trigger_id)
    assert reloaded is not None
    assert reloaded.name == "New name"


def test_update_trigger_preserves_enabled_state(tmp_path: Path) -> None:
    store = ScheduledTriggerStore(tmp_path)
    created = create_trigger(store, name="x", kind="daily", at="09:00", prompt="p")
    created.enabled = False
    store.save(created)

    updated = update_trigger(
        store, created.trigger_id, name="x", kind="daily", at="10:00", prompt="p"
    )

    assert updated.enabled is False


def test_update_trigger_unknown_id_raises_keyerror(tmp_path: Path) -> None:
    store = ScheduledTriggerStore(tmp_path)
    with pytest.raises(KeyError):
        update_trigger(store, "does-not-exist", name="x", kind="daily", at="09:00", prompt="p")


def test_update_trigger_same_validation_as_create(tmp_path: Path) -> None:
    store = ScheduledTriggerStore(tmp_path)
    created = create_trigger(store, name="x", kind="daily", at="09:00", prompt="p")
    with pytest.raises(ValueError, match="prompt cannot be blank"):
        update_trigger(store, created.trigger_id, name="x", kind="daily", at="09:00", prompt="  ")


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
