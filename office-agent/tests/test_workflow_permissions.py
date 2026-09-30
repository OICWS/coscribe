from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langgraph.checkpoint.memory import InMemorySaver

from coscribe.needs_permission import NeedsPermission
from coscribe.workflows.engine import StepContext, StepRecord, WorkflowRun
from coscribe.workflows.permissions import granted_by, site_of, summarize
from coscribe.workflows.spec import parse_workflow


def _workflow(steps: list[dict[str, Any]], inputs: list[dict[str, Any]] | None = None) -> Any:
    return parse_workflow({"inputs": inputs or [], "steps": steps})


def test_site_of_names_a_site_as_the_desktop_app_does() -> None:
    assert site_of("https://www.Example.com/a?b=1") == "example.com"
    assert site_of("sap.corp.local:8443/ui") == "sap.corp.local"
    assert site_of("") is None


def test_granted_by_lists_the_sites_and_folders_the_steps_name() -> None:
    workflow = _workflow(
        [
            {"id": "a", "title": "Open", "kind": "tool", "tool": "browser_navigate",
             "args": {"url": "https://www.sap.example.com/start"}},
            {"id": "b", "title": "Wait", "kind": "tool", "tool": "browser_wait_for",
             "args": {"download": True, "save_to": "D:\\reports\\monthly"}},
            {"id": "c", "title": "Write", "kind": "tool", "tool": "write_file",
             "args": {"path": "/srv/out/result.txt", "content": "x"}},
            {"id": "d", "title": "Inside", "kind": "tool", "tool": "write_file",
             "args": {"path": "notes.txt", "content": "x"}},
        ]
    )
    named = granted_by(workflow)
    assert named["sites"] == ["sap.example.com"]
    assert named["folders"] == ["D:\\reports\\monthly", "/srv/out"]


def test_a_site_from_an_input_counts_through_its_default_or_is_left_for_run_time() -> None:
    steps = [
        {"id": "a", "title": "Open", "kind": "tool", "tool": "browser_navigate",
         "args": {"url": "{{site}}"}}
    ]
    with_default = _workflow(steps, [{"name": "site", "default": "https://erp.example.com"}])
    assert granted_by(with_default)["sites"] == ["erp.example.com"]
    without = _workflow(steps, [{"name": "site"}])
    assert granted_by(without)["sites"] == []
    assert summarize(without, {})["sites_at_run_time"] == ["Open"]


def test_summarize_lists_scripts_and_consequential_tools_but_not_browser_steps() -> None:
    workflow = _workflow(
        [
            {"id": "a", "title": "Crunch", "kind": "script", "code": "print('{}')"},
            {"id": "b", "title": "Tell the team", "kind": "tool", "tool": "slack_post_message",
             "args": {"text": "hi"}},
            {"id": "c", "title": "Open", "kind": "tool", "tool": "browser_navigate",
             "args": {"url": "example.com"}},
            {"id": "d", "title": "Read", "kind": "tool", "tool": "read_file",
             "args": {"path": "a.txt"}},
        ]
    )
    summary = summarize(
        workflow,
        {"slack_post_message": "EXTERNAL", "browser_navigate": "EXTERNAL", "read_file": "READ"},
        {"folders": ["D:\\extra"], "sites": []},
    )
    assert summary["scripts"] == ["Crunch"]
    assert summary["notable"] == [
        {"tool": "slack_post_message", "title": "Tell the team", "risk": "EXTERNAL"}
    ]
    assert summary["folders"] == ["D:\\extra"]
    assert summary["sites"] == ["example.com"]


class _Harness:
    """A workflow with one tool step that needs a folder until allowed."""

    def __init__(self, tmp_path: Path) -> None:
        self.allowed = False
        self.calls = 0
        self.records: list[StepRecord] = []
        self.workflow = _workflow(
            [
                {"id": "save", "title": "Save the report", "kind": "tool", "tool": "save",
                 "args": {}, "save_as": "saved"},
            ]
        )

        def save() -> str:
            self.calls += 1
            if not self.allowed:
                raise NeedsPermission("folder", "D:\\reports", "D:\\reports isn't allowed.")
            return "saved"

        self.ctx = StepContext(
            tools={"save": save},
            workspace_root=tmp_path,
            state_dir=tmp_path,
            make_model=lambda _model: None,
            ask_permission=True,
        )

    async def on_step(self, record: StepRecord) -> None:
        self.records.append(record)

    def run(self) -> WorkflowRun:
        return WorkflowRun(self.workflow, self.ctx, InMemorySaver(), "t1", self.on_step)


async def test_a_step_that_needs_a_folder_waits_then_carries_on_once_allowed(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    run = harness.run()

    outcome = await run.start({})
    assert outcome.status == "waiting"
    assert outcome.step_id == "save"
    assert outcome.request is not None
    assert outcome.request["permission"] == {
        "kind": "folder",
        "target": "D:\\reports",
        "message": "D:\\reports isn't allowed.",
    }
    waiting = [r for r in harness.records if r.status == "waiting"]
    assert waiting and waiting[-1].permission is not None

    harness.allowed = True
    outcome = await run.answer(True)
    assert outcome.status == "completed"
    assert outcome.values["saved"] == "saved"


async def test_declining_a_permission_fails_the_step(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    run = harness.run()
    await run.start({})

    outcome = await run.answer(False)
    assert outcome.status == "failed"
    assert outcome.error is not None and outcome.error.startswith("Not allowed")


async def test_without_permission_to_ask_the_step_just_fails_with_what_to_allow(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    harness.ctx.ask_permission = False

    outcome = await harness.run().start({})

    assert outcome.status == "failed"
    assert outcome.error == "D:\\reports isn't allowed."


async def test_a_script_step_the_guard_stopped_needs_the_folder(tmp_path: Path) -> None:
    workflow = _workflow(
        [{"id": "s", "title": "Write it", "kind": "script", "code": "print('{}')"}]
    )

    def blocked(workspace: Path, state: Path, script: str, timeout: float) -> dict[str, object]:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "blocked_write": "D:\\reports\\a.xlsx",
            "blocked_folder": "D:\\reports",
        }

    ctx = StepContext(
        tools={},
        workspace_root=tmp_path,
        state_dir=tmp_path,
        make_model=lambda _model: None,
        run_script=blocked,
        ask_permission=True,
    )
    outcome = await WorkflowRun(workflow, ctx, InMemorySaver(), "t2", lambda _r: None).start({})

    assert outcome.status == "waiting"
    assert outcome.request is not None
    assert outcome.request["permission"]["target"] == "D:\\reports"


def test_a_path_outside_the_scope_asks_for_its_nearest_folder(tmp_path: Path) -> None:
    from coscribe.tools._workspace import WorkspaceScope

    (tmp_path / "elsewhere").mkdir()
    scope = WorkspaceScope(tmp_path / "workspace")

    with pytest.raises(NeedsPermission) as raised:
        scope.resolve(str(tmp_path / "elsewhere" / "new" / "a.txt"), write=True)

    assert raised.value.kind == "folder"
    assert raised.value.target == str(tmp_path / "elsewhere")
    assert "outside any allowed directory" in str(raised.value)


def test_the_browser_host_turns_the_desktop_apps_site_refusal_into_a_permission_ask() -> None:
    import asyncio

    from coscribe.needs_permission import SITE_MARK
    from coscribe.tools.browser import BrowserHost

    host = BrowserHost()

    async def scenario() -> NeedsPermission:
        outbox = host.attach()

        async def desktop() -> None:
            sent = await outbox.get()
            host.resolve(sent["id"], {"ok": False, "error": f"{SITE_MARK}evil.example.com"})

        asyncio.create_task(desktop())
        try:
            await host.request("navigate", {"url": "evil.example.com"}, "t", 5)
        except NeedsPermission as exc:
            return exc
        raise AssertionError("no refusal")

    asked = asyncio.run(scenario())
    assert (asked.kind, asked.target) == ("site", "evil.example.com")


def test_sites_in_reads_the_hosts_a_runs_outputs_show() -> None:
    from coscribe.workflows.permissions import sites_in

    outputs = [
        "Tab 1: Start -- https://www.sap.example.com/start?x=1",
        {"tab": {"url": "https://erp.example.com/a"}},
        "Tab 1: Start -- https://sap.example.com/again",
    ]
    assert sites_in(outputs) == ["sap.example.com", "erp.example.com"]


def test_a_task_keeps_what_its_runs_were_allowed(tmp_path: Path) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore, create_trigger

    store = ScheduledTriggerStore(tmp_path)
    created = create_trigger(
        store,
        name="Report",
        kind="manual",
        at="",
        prompt="",
        workflow={"steps": [{"id": "a", "title": "Wait", "kind": "check", "conditions": [
            {"left": {"value": 1}, "op": "eq", "right": {"value": 1}}]}]},
    )
    store.grant(created.trigger_id, "folders", "D:\\reports")
    store.grant(created.trigger_id, "folders", "D:\\reports")
    store.grant(created.trigger_id, "sites", "sap.example.com")

    loaded = store.load(created.trigger_id)
    assert loaded is not None
    assert loaded.permissions == {"folders": ["D:\\reports"], "sites": ["sap.example.com"]}
