from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from coscribe.config import Settings
from coscribe.coordinator import build_coordinator_agent
from coscribe.runtime.types import get_tool_metadata
from coscribe.tools.browser import BrowserHost, BrowserUnavailableError, build_browser_tools


class _FakeHost(BrowserHost):
    def __init__(self, replies: dict[str, dict[str, Any]]) -> None:
        super().__init__()
        self.replies = replies
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    def call(
        self, action: str, args: dict[str, Any], thread_id: str, timeout: float = 30
    ) -> dict[str, Any]:
        self.calls.append((action, args, thread_id))
        return self.replies.get(action, {})


def _tools(host: BrowserHost) -> dict[str, Any]:
    return {tool.__name__: tool for tool in build_browser_tools("t1", host)}


class _ServerLoop:
    """The server's event loop, running in its own thread the way uvicorn's
    does while tool calls arrive from LangGraph's worker threads."""

    def __enter__(self) -> asyncio.AbstractEventLoop:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever)
        self.thread.start()
        return self.loop

    def __exit__(self, *exc: object) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


def _connect_desktop_app(
    host: BrowserHost, loop: asyncio.AbstractEventLoop, reply: dict[str, Any]
) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def desktop_app() -> None:
        outbox = host.attach()
        command = await outbox.get()
        seen.append(command)
        host.resolve(command["id"], reply)

    asyncio.run_coroutine_threadsafe(desktop_app(), loop)
    for _ in range(100):
        if host.connected:
            break
        threading.Event().wait(0.01)
    return seen


def test_a_call_waits_for_the_desktop_apps_reply() -> None:
    host = BrowserHost()
    with _ServerLoop() as loop:
        seen = _connect_desktop_app(host, loop, {"ok": True, "result": {"tab": {"id": 1}}})
        result = host.call("navigate", {"url": "u"}, "t1", timeout=5)

    assert result == {"tab": {"id": 1}}
    assert seen[0]["action"] == "navigate"
    assert seen[0]["args"] == {"url": "u"}
    assert seen[0]["thread_id"] == "t1"


def test_an_error_reply_reaches_the_model_as_the_tools_error() -> None:
    host = BrowserHost()
    with _ServerLoop() as loop:
        _connect_desktop_app(host, loop, {"ok": False, "error": "No element with ref e9."})
        with pytest.raises(ValueError, match="No element with ref e9"):
            host.call("click", {"ref": "e9"}, "t1", timeout=5)


def test_without_the_desktop_app_the_tools_say_so() -> None:
    with pytest.raises(BrowserUnavailableError, match="desktop app"):
        _tools(BrowserHost())["browser_snapshot"]()


def test_disconnecting_fails_whatever_was_waiting() -> None:
    host = BrowserHost()

    async def scenario() -> dict[str, Any]:
        outbox = host.attach()
        pending = asyncio.create_task(host.request("snapshot", {}, "t1", timeout=5))
        await outbox.get()
        host.detach(outbox)
        try:
            return await pending
        except ValueError as exc:
            return {"error": str(exc)}

    result = asyncio.run(scenario())
    assert "desktop app" in result["error"]
    assert not host.connected


def test_snapshot_shows_the_tab_and_where_a_long_page_continues() -> None:
    host = _FakeHost(
        {
            "snapshot": {
                "text": '- link "News" [ref=e1]',
                "total_chars": 30_000,
                "tab": {"id": 2, "title": "Example", "url": "https://example.com/"},
            }
        }
    )
    output = _tools(host)["browser_snapshot"](start=100)

    assert output.startswith("Tab 2: Example -- https://example.com/")
    assert '- link "News" [ref=e1]' in output
    assert "browser_snapshot(start=122)" in output
    assert host.calls[0] == ("snapshot", {"start": 100, "max_chars": 12_000}, "t1")


def test_click_names_the_element_it_clicked() -> None:
    host = _FakeHost({"click": {"element": 'button "Search"', "tab": {"id": 1, "url": "u"}}})
    assert _tools(host)["browser_click"]("e4").startswith('Clicked button "Search".')


def test_tabs_marks_the_current_one() -> None:
    host = _FakeHost(
        {
            "tabs": {
                "tabs": [
                    {"id": 1, "title": "A", "url": "https://a/", "active": False},
                    {"id": 2, "title": "B", "url": "https://b/", "active": True},
                ]
            }
        }
    )
    output = _tools(host)["browser_tabs"]()
    assert output.splitlines() == ["  Tab 1: A -- https://a/", "* Tab 2: B -- https://b/"]


def test_tabs_select_needs_a_tab_id() -> None:
    with pytest.raises(ValueError, match="tab_id"):
        _tools(_FakeHost({}))["browser_tabs"]("select")


def test_acting_on_a_page_needs_approval_reading_it_does_not() -> None:
    tools = _tools(_FakeHost({}))
    risks = {name: get_tool_metadata(tool).risk_category for name, tool in tools.items()}
    assert risks["browser_click"] == "EXTERNAL"
    assert risks["browser_type"] == "EXTERNAL"
    assert risks["browser_snapshot"] == "READ"
    assert risks["browser_navigate"] == "READ"


def _coordinator_tools(tmp_path: Path, **overrides: object) -> tuple[set[str], str]:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        default_model="fake:model",
        workspace_root=tmp_path / "ws",
        state_dir=tmp_path / "state",
        skills_dir=tmp_path / "skills",
        memory_path=tmp_path / "MEMORY.md",
        **overrides,  # type: ignore[arg-type]
    )
    agent = build_coordinator_agent(settings, thread_id="t1")
    return {getattr(t, "__name__", "") for t in agent.tools}, agent.instructions


def test_browser_tools_are_only_offered_inside_the_desktop_app(tmp_path: Path) -> None:
    names, instructions = _coordinator_tools(tmp_path)
    assert "browser_click" not in names
    assert "browser_* tools" not in instructions

    names, instructions = _coordinator_tools(tmp_path, browser_host_token="secret")
    assert {"browser_navigate", "browser_snapshot", "browser_click"} <= names
    assert "browser_* tools" in instructions


def test_a_step_that_opens_a_dialog_says_so() -> None:
    host = _FakeHost(
        {
            "click": {
                "element": 'button "Delete"',
                "dialog": 'A confirm dialog is open on the page: "Sure?". Answer it with '
                "browser_handle_dialog before anything else.",
            }
        }
    )
    out = _tools(host)["browser_click"]("e3")
    assert out.splitlines()[0] == 'Clicked button "Delete".'
    assert "browser_handle_dialog" in out


def test_handle_dialog_passes_the_answer() -> None:
    host = _FakeHost(
        {"handle_dialog": {"dialog_type": "confirm", "message": "Delete?", "accepted": False}}
    )
    out = _tools(host)["browser_handle_dialog"](accept=False)
    assert host.calls[0][:2] == ("handle_dialog", {"accept": False})
    assert out == 'Dismissed the confirm: "Delete?".'


def test_a_click_that_opens_a_file_chooser_points_to_upload() -> None:
    out = _tools(_FakeHost({"click": {"file_chooser": True}}))["browser_click"]("e5")
    assert "browser_file_upload" in out


def test_upload_sends_resolved_files_inside_the_workspace(tmp_path: Path) -> None:
    from coscribe.tools._workspace import WorkspaceScope

    (tmp_path / "report.pdf").write_bytes(b"%PDF")
    host = _FakeHost({"upload": {"element": 'button "Attach"'}})
    tools = {t.__name__: t for t in build_browser_tools("t1", host, scope=WorkspaceScope(tmp_path))}
    out = tools["browser_file_upload"](["report.pdf"], ref="e9")
    action, args, _ = host.calls[0]
    assert action == "upload"
    assert args == {"ref": "e9", "paths": [str((tmp_path / "report.pdf").resolve())]}
    assert out.startswith('Uploaded report.pdf to button "Attach".')

    with pytest.raises(ValueError, match="No such file"):
        tools["browser_file_upload"](["missing.pdf"])
    outside = tmp_path.parent / "secret.txt"
    with pytest.raises(PermissionError):
        tools["browser_file_upload"]([str(outside)])
    assert len(host.calls) == 1


def test_upload_without_a_workspace_is_refused() -> None:
    with pytest.raises(ValueError, match="isn't available"):
        _tools(_FakeHost({}))["browser_file_upload"](["a.txt"])


def test_evaluate_returns_the_value_clipped() -> None:
    host = _FakeHost({"evaluate": {"value": "x" * 6000}})
    out = _tools(host)["browser_evaluate"]("'x'.repeat(6000)")
    assert out.startswith("x" * 5000 + "\n[... 1000 more characters")


def test_evaluate_runs_only_with_approval() -> None:
    tools = _tools(_FakeHost({}))
    assert get_tool_metadata(tools["browser_evaluate"]).risk_category == "EXEC"
    assert get_tool_metadata(tools["browser_file_upload"]).risk_category == "EXTERNAL"
    assert get_tool_metadata(tools["browser_handle_dialog"]).risk_category == "EXTERNAL"
