from __future__ import annotations

import asyncio

import pytest

from coscribe.runtime_lg.scheduled_tasks import (
    INVESTIGATION_PREFIX,
    _running,
    build_investigation_prompt,
    stop_run,
)
from coscribe.tools.scheduled_tasks import ScheduledRun, ScheduledTrigger, ScheduleRule
from coscribe.web.background_events import run_event


async def test_stop_cancels_the_task_driving_the_run() -> None:
    started = asyncio.Event()

    async def drive() -> None:
        with _running("r1"):
            started.set()
            await asyncio.sleep(60)

    task = asyncio.create_task(drive())
    await started.wait()

    assert stop_run("r1", "t1") is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stop_run("r1", "t1") is False


def test_the_investigation_names_the_failed_step_and_the_page() -> None:
    workflow = {
        "steps": [
            {"id": "open", "kind": "tool", "title": "Open SAP", "tool": "browser_navigate",
             "args": {"url": "http://sap"}},
            {"id": "tick", "kind": "tool", "title": "Tick All items", "tool": "browser_click",
             "args": {"ref": 'checkbox "All items"'}},
        ]
    }  # fmt: skip
    ok = ScheduledRun(run_id="a", thread_id="x", started_at="2026-09-29T09:00", source="scheduled",
                      status="completed")  # fmt: skip
    failed = ScheduledRun(
        run_id="b",
        thread_id="y",
        started_at="2026-09-30T09:00",
        source="scheduled",
        status="failed",
        error='Tick All items: No checkbox "All items" appeared within 30s.',
        steps=[{"step_id": "open", "status": "done"}, {"step_id": "tick", "status": "failed"}],
        inputs={"from": "2026.09.01"},
        page='- heading "SAP GUI: System Information"',
    )
    trigger = ScheduledTrigger(
        trigger_id="t1",
        name="FBL5N export",
        schedule=ScheduleRule(kind="manual", at=""),
        enabled=True,
        created_at="2026-09-01T00:00",
        next_run_at=None,
        runs=[ok, failed],
        workflow=workflow,
    )

    prompt = build_investigation_prompt(trigger, failed, "每天导出 FBL5N")

    assert prompt.startswith(f'{INVESTIGATION_PREFIX}"FBL5N export" · task t1 · run b]')
    assert "Failed step: Tick All items (tick)" in prompt
    assert '"from": "2026.09.01"' in prompt
    assert "Last successful run: 2026-09-29T09:00" in prompt
    assert "What the user asked for when the task was made: 每天导出 FBL5N" in prompt
    assert "SAP GUI: System Information" in prompt


def _task(runs: list[ScheduledRun]) -> ScheduledTrigger:
    workflow = {
        "steps": [
            {"id": "ok", "kind": "approval", "title": "Send the report?", "message": "Send it?"},
        ]
    }
    return ScheduledTrigger(
        trigger_id="t1",
        name="FBL5N export",
        schedule=ScheduleRule(kind="manual", at=""),
        enabled=True,
        created_at="2026-09-01T00:00",
        next_run_at=None,
        runs=runs,
        workflow=workflow,
    )


def _run(status: str, **fields: object) -> ScheduledRun:
    return ScheduledRun(
        run_id="r", thread_id="run-thread", started_at="2026-09-30T09:00", source="scheduled",
        status=status, **fields,  # type: ignore[arg-type]
    )  # fmt: skip


def test_a_notification_says_what_the_run_produced_or_where_it_stopped() -> None:
    completed = _run("completed", files=[{"path": "exports/FBL5N.XLSX", "size": 1, "rows": 450}])
    failed = _run("failed", error="Open transaction FBL5N: No combobox appeared within 30s.")
    waiting = _run("needs_approval", steps=[{"step_id": "ok", "status": "waiting"}])

    done_event = run_event(_task([completed]), completed)
    failed_event = run_event(_task([failed]), failed, investigation="inv1")
    waiting_event = run_event(_task([waiting]), waiting)

    assert done_event is not None and done_event.title == "FBL5N export"
    assert done_event.body == "Completed · 449 rows · saved to exports/FBL5N.XLSX"
    assert done_event.thread_id == "run-thread"
    assert failed_event is not None and failed_event.thread_id == "inv1"
    assert failed_event.body == (
        "Failed -- Open transaction FBL5N: No combobox appeared within 30s. "
        "coscribe is looking into it."
    )
    assert waiting_event is not None and waiting_event.body == "Waiting for you at Send the report?"
    assert run_event(_task([]), _run("stopped")) is None
