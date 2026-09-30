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
