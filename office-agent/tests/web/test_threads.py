# ruff: noqa: E402
"""Web tests: listing, grouping, renaming, deleting and naming threads."""

import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult

from coscribe.conversation import ThreadMetaStore

from .helpers import (
    FakeToolCallingChatModel,
    _client_lg,
    _receive_until,
    _tool_call,
)


def test_threads_can_be_grouped_archived_and_their_groups_renamed_and_deleted_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/g1") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

        moved = client.post("/api/threads/g1/meta", json={"group": " Reports "}).json()
        again = client.post("/api/threads/g1/meta", json={"group": "reports"}).json()
        archived = client.post("/api/threads/g1/meta", json={"archived": True}).json()
        with client.websocket_connect("/ws/g1") as ws:
            ws.receive_json()
            ws.receive_json()
            # No more scripted replies: the message only has to reach the turn.
            ws.send_json({"type": "user_message", "text": "again"})
            for _ in range(40):
                if not ThreadMetaStore(tmp_path / "state").get("g1")["archived"]:
                    break
                time.sleep(0.05)
        revived = ThreadMetaStore(tmp_path / "state").get("g1")["archived"]
        client.post("/api/threads/g1/meta", json={"archived": True})
        listed = {t["thread_id"]: t for t in client.get("/api/threads").json()}
        renamed = client.post("/api/thread-groups/Reports/rename", json={"name": "Monthly"}).json()
        after_rename = client.get("/api/threads").json()[0]
        client.post("/api/threads/g1/meta", json={"group": "Other"})
        clash = client.post("/api/thread-groups/Other/rename", json={"name": "monthly"})
        blank = client.post("/api/threads/g1/meta", json={"group": "   "})
        client.post("/api/threads/g1/meta", json={"group": "Monthly"})
        deleted = client.delete("/api/thread-groups/Monthly").json()
        after_delete = client.get("/api/threads").json()[0]
        missing = client.delete("/api/thread-groups/Nope")
        client.delete("/api/threads/g1")
        leftover = (tmp_path / "state" / "g1.meta.json").exists()

    assert moved["group"] == "Reports" and moved["groups"] == ["Reports"]
    assert again["group"] == "Reports" and again["groups"] == ["Reports"]
    assert archived["archived"] is True
    assert revived is False
    assert listed["g1"]["group"] == "Reports" and listed["g1"]["archived"] is True
    assert renamed["groups"] == ["Monthly"] and after_rename["group"] == "Monthly"
    assert clash.status_code == 400
    assert blank.status_code == 200 and blank.json()["group"] is None
    assert deleted["groups"] == ["Other"] and after_delete["group"] is None
    assert missing.status_code == 404
    assert not leftover


def test_task_list_endpoint_reflects_task_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "task_create", {"content": "write the report"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="tracked it"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t5") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "please plan this out"})
            _receive_until(ws, "tasks_changed")

        response = client.get("/api/threads/t5/tasks")

    assert response.status_code == 200
    tasks = response.json()
    assert len(tasks) == 1
    assert tasks[0]["content"] == "write the report"
    assert tasks[0]["status"] == "pending"


def test_get_tools_lists_builtin_tools_with_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/tools")

    assert response.status_code == 200
    tools = {tool["name"]: tool for tool in response.json()["tools"]}
    assert tools["read_docx"]["category"] == "documents"
    assert tools["write_xlsx_cells"]["requires_approval"] is True
    assert tools["task_create"]["category"] == "tasks"
    assert tools["web_search"]["category"] == "web"
    assert tools["web_search"]["requires_approval"] is False
    assert tools["modify_xlsx"]["category"] == "documents"
    assert tools["modify_xlsx"]["requires_approval"] is True
    assert tools["recalc_xlsx"]["category"] == "documents"
    assert tools["recalc_xlsx"]["requires_approval"] is True
    assert tools["add_pptx_chart"]["category"] == "documents"
    assert tools["add_pptx_chart"]["requires_approval"] is True
    assert tools["add_pptx_image"]["category"] == "documents"
    assert tools["add_pptx_image"]["requires_approval"] is True
    assert tools["set_pptx_notes"]["category"] == "documents"
    assert tools["set_pptx_notes"]["requires_approval"] is True
    assert tools["set_pptx_transition"]["category"] == "documents"
    assert tools["set_pptx_transition"]["requires_approval"] is True
    assert tools["add_pptx_animation"]["category"] == "documents"
    assert tools["add_pptx_animation"]["requires_approval"] is True
    assert tools["add_pptx_hyperlink"]["category"] == "documents"
    assert tools["add_pptx_hyperlink"]["requires_approval"] is True
    assert tools["edit_pptx_theme_colors"]["category"] == "documents"
    assert tools["edit_pptx_theme_colors"]["requires_approval"] is True
    assert tools["run_python_script"]["category"] == "scripts"
    assert tools["run_python_script"]["risk_category"] == "EXEC"
    assert tools["run_python_script"]["requires_approval"] is True
    assert tools["run_node_script"]["category"] == "scripts"
    assert tools["run_node_script"]["risk_category"] == "EXEC"
    assert tools["run_node_script"]["requires_approval"] is True
    assert tools["set_pptx_background_image"]["category"] == "documents"
    assert tools["set_pptx_background_image"]["requires_approval"] is True
    assert tools["edit_pptx_text"]["category"] == "documents"
    assert tools["edit_pptx_text"]["requires_approval"] is True
    assert tools["delete_pptx_slide"]["category"] == "documents"
    assert tools["delete_pptx_slide"]["requires_approval"] is True
    assert tools["duplicate_pptx_slide"]["category"] == "documents"
    assert tools["duplicate_pptx_slide"]["requires_approval"] is True
    assert tools["reorder_pptx_slide"]["category"] == "documents"
    assert tools["reorder_pptx_slide"]["requires_approval"] is True
    assert tools["edit_file"]["category"] == "filesystem"
    assert tools["edit_file"]["requires_approval"] is True
    assert tools["edit_file_batch"]["category"] == "filesystem"
    assert tools["edit_file_batch"]["requires_approval"] is True
    assert tools["get_file_info"]["category"] == "filesystem"
    assert tools["get_file_info"]["requires_approval"] is False
    assert tools["delete_file"]["category"] == "filesystem"
    assert tools["delete_file"]["requires_approval"] is True
    assert tools["move_file"]["category"] == "filesystem"
    assert tools["move_file"]["requires_approval"] is True
    assert tools["copy_file"]["category"] == "filesystem"
    assert tools["copy_file"]["requires_approval"] is True
    assert tools["list_pptx_shapes"]["category"] == "documents"
    assert tools["list_pptx_shapes"]["requires_approval"] is False
    assert tools["edit_pptx_shape"]["category"] == "documents"
    assert tools["edit_pptx_shape"]["requires_approval"] is True
    assert tools["delete_pptx_shape"]["category"] == "documents"
    assert tools["delete_pptx_shape"]["requires_approval"] is True
    assert tools["replace_pptx_image"]["category"] == "documents"
    assert tools["replace_pptx_image"]["requires_approval"] is True
    assert tools["list_pptx_icons"]["category"] == "documents"
    assert tools["list_pptx_icons"]["requires_approval"] is False
    assert tools["add_pptx_icon"]["category"] == "documents"
    assert tools["add_pptx_icon"]["requires_approval"] is True
    assert tools["recolor_pptx_icon"]["category"] == "documents"
    assert tools["recolor_pptx_icon"]["requires_approval"] is True
    assert tools["edit_pptx_table_cell"]["category"] == "documents"
    assert tools["edit_pptx_table_cell"]["requires_approval"] is True
    assert tools["merge_pptx_table_cells"]["category"] == "documents"
    assert tools["merge_pptx_table_cells"]["requires_approval"] is True
    assert tools["search_images"]["category"] == "web"
    assert tools["search_images"]["requires_approval"] is False
    assert tools["download_image"]["category"] == "web"
    assert tools["download_image"]["requires_approval"] is True
    assert tools["add_pptx_scrim"]["category"] == "documents"
    assert tools["add_pptx_scrim"]["requires_approval"] is True
    assert tools["sleep_until"]["category"] == "selfwake"
    assert tools["sleep_until"]["requires_approval"] is True
    assert tools["list_wakes"]["category"] == "selfwake"
    assert tools["list_wakes"]["requires_approval"] is False


def test_get_commands_includes_plan_accept_edits_and_compact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/commands")

    assert response.status_code == 200
    names = {command["name"] for command in response.json()}
    assert {"init", "plan", "accept-edits", "compact"} <= names


def test_unknown_slash_command_is_still_rejected_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_slash_unknown") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/nope do something"})
            error = ws.receive_json()

    assert error["type"] == "error"
    assert "Unknown command: /nope" in error["message"]
    assert fake_model.i == 0


def test_get_threads_endpoint_includes_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = tmp_path / "project-e"
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="hi"), AIMessage(content="hi again")]
    )
    chosen.mkdir()
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        env = client.post("/api/environments", json={"name": "E", "folders": [str(chosen)]})
        with client.websocket_connect("/ws/t_ws_list_a") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "set_environment", "environment": env.json()["id"]})
            ws.receive_json()  # state
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

        with client.websocket_connect("/ws/t_ws_list_b") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello again"})
            _receive_until(ws, "tasks_changed")

        threads = {t["thread_id"]: t for t in client.get("/api/threads").json()}

    assert threads["t_ws_list_a"]["workspace_root"] == str(chosen)
    assert threads["t_ws_list_b"]["workspace_root"] == str(tmp_path / "workspace")


def test_get_threads_endpoint_lists_saved_threads_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: list_threads used to
    glob settings.state_dir for *.json files -- the old runtime's storage
    shape, not this one's. runtime_lg persists conversation history in the
    shared AsyncSqliteSaver checkpointer, so that glob always came back
    empty and the session-switcher UI never showed any history, even
    though every thread's real state was sitting right there in the
    checkpoint database."""
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_list1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

        response = client.get("/api/threads")

    assert response.status_code == 200
    threads = {t["thread_id"]: t for t in response.json()}
    assert "t_list1" in threads
    entry = threads["t_list1"]
    assert entry["preview"] == "hello"
    assert entry["message_count"] == 2  # the human "hello" + the AI's "hi" reply
    assert entry["updated_at"]  # a real ISO timestamp, not asserting the exact value


def test_get_threads_endpoint_strips_mode_note_and_sorts_by_recency_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Covers strip_mode_note (a live turn's mode-note prefix, e.g.
    "[normal mode: ...] ", shouldn't leak into the preview text a user
    never actually typed) and the most-recently-updated-first sort."""
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="hi"), AIMessage(content="hi again")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_older") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "first thread"})
            _receive_until(ws, "tasks_changed")

        with client.websocket_connect("/ws/t_newer") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "second thread"})
            _receive_until(ws, "tasks_changed")

        threads = client.get("/api/threads").json()

    assert [t["thread_id"] for t in threads] == ["t_newer", "t_older"]
    newer = threads[0]
    assert not newer["preview"].startswith("[")
    assert "second thread" in newer["preview"]


def test_delete_thread_endpoint_removes_checkpoints_and_tasks_sidecar_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.bookkeeping.tasks import TaskToolkit

    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_del1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

        TaskToolkit("t_del1", tmp_path / "state").create("write the report")
        assert (tmp_path / "state" / "t_del1.tasks.json").is_file()
        thread_ids = {t["thread_id"] for t in client.get("/api/threads").json()}
        assert "t_del1" in thread_ids

        response = client.delete("/api/threads/t_del1")

        assert response.status_code == 200
        assert response.json() == {"deleted": "t_del1"}
        assert not (tmp_path / "state" / "t_del1.tasks.json").exists()
        thread_ids = {t["thread_id"] for t in client.get("/api/threads").json()}
        assert "t_del1" not in thread_ids


def test_deleting_the_open_conversation_stops_its_turn_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A turn still waiting on an approval would write its checkpoint after the delete and bring
    the conversation back."""
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="stopped")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_del_open") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            approval = ws.receive_json()
            assert approval["type"] == "approval_required"

            response = client.delete("/api/threads/t_del_open")
            # Too late: the delete already ended the turn.
            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            _receive_until(ws, "tasks_changed")

            assert response.status_code == 200
        thread_ids = {t["thread_id"] for t in client.get("/api/threads").json()}
        assert "t_del_open" not in thread_ids
    assert not (tmp_path / "workspace" / "note.txt").exists()


def test_delete_thread_endpoint_unknown_thread_404s_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.delete("/api/threads/nope")

    assert response.status_code == 404


def test_rename_thread_endpoint_overrides_preview_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_rename1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "original first message"})
            _receive_until(ws, "tasks_changed")

        before = next(t for t in client.get("/api/threads").json() if t["thread_id"] == "t_rename1")
        assert "original first message" in before["preview"]

        response = client.post(
            "/api/threads/t_rename1/rename", json={"title": "Quarterly report draft"}
        )

        assert response.status_code == 200
        assert response.json() == {"thread_id": "t_rename1", "title": "Quarterly report draft"}
        after = next(t for t in client.get("/api/threads").json() if t["thread_id"] == "t_rename1")
        assert after["preview"] == "Quarterly report draft"


def test_rename_thread_endpoint_unknown_thread_404s_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post("/api/threads/nope/rename", json={"title": "New title"})

    assert response.status_code == 404


def test_rename_thread_endpoint_rejects_blank_title_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_rename2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

        response = client.post("/api/threads/t_rename2/rename", json={"title": "   "})

    assert response.status_code == 400


def test_delete_thread_endpoint_removes_title_sidecar_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_del_title") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

        client.post("/api/threads/t_del_title/rename", json={"title": "renamed"})
        assert (tmp_path / "state" / "t_del_title.title").is_file()

        response = client.delete("/api/threads/t_del_title")

        assert response.status_code == 200
        assert not (tmp_path / "state" / "t_del_title.title").exists()


def test_browse_dirs_lists_subdirectories_only_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: app.py never
    defined /api/browse-dirs at all (unlike web/app.py), even though the
    shared app.js frontend's Settings-panel folder picker calls it
    unconditionally -- clicking "select folder" 404'd and the picker
    modal opened with nothing in it, with no visible error."""
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Documents").mkdir()
    (tmp_path / "not_a_dir.txt").write_text("x", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/browse-dirs", params={"path": str(tmp_path)})

    assert response.status_code == 200
    body = response.json()
    resolved = tmp_path.resolve()
    assert body["path"] == str(resolved)
    assert body["directories"] == [
        {"name": "Documents", "path": str(resolved / "Documents")},
        {"name": "Downloads", "path": str(resolved / "Downloads")},
        # create_app_lg() auto-creates settings.skills_dir at startup --
        # the skills_by_name dict built for GET /api/skills/select_skills
        # validation calls load_skills(settings.skills_dir), which
        # creates it if missing.
        {"name": "skills", "path": str(resolved / "skills")},
        # The lifespan also opens the AsyncSqliteSaver checkpointer under
        # settings.state_dir (tmp_path/state), creating that directory too
        # -- app.py's equivalent test has no state_dir-creating lifespan
        # step, so this entry is specific to runtime_lg.
        {"name": "state", "path": str(resolved / "state")},
    ]
    assert body["parent"] == str(resolved.parent)


def test_browse_dirs_defaults_to_home_when_no_path_given_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/browse-dirs")

    assert response.status_code == 200
    assert response.json()["path"] == str(Path.home().resolve())


def test_browse_dirs_nonexistent_path_returns_error_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/browse-dirs", params={"path": str(tmp_path / "does-not-exist")})

    assert response.status_code == 200
    assert "error" in response.json()


def test_browse_dirs_file_path_returns_error_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file_path = tmp_path / "a.txt"
    file_path.write_text("x", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/browse-dirs", params={"path": str(file_path)})

    assert response.status_code == 200
    assert "error" in response.json()


class _TitlingModel(FakeToolCallingChatModel):
    """Answers the naming request on its own, so that it neither takes a
    scripted reply nor depends on whether it comes before or after the
    turn's own call."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if "Name this conversation" in str(messages[0].content):
            self.received.append(list(messages))
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content='"Q3 销售汇总"'))]
            )
        return super()._generate(messages, stop, run_manager, **kwargs)


def test_a_conversation_is_named_from_its_first_message_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = _TitlingModel(responses=[AIMessage(content="Here's the summary.")])
    with _client_lg(tmp_path, monkeypatch, fake_model, auto_title_threads=True) as client:
        with client.websocket_connect("/ws/t_named") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "帮我汇总一下第三季度的销售数据"})
            titled = _receive_until(ws, "thread_titled")[-1]
            _receive_until(ws, "tasks_changed")
        threads = client.get("/api/threads").json()

    assert titled == {"type": "thread_titled", "title": "Q3 销售汇总"}
    [thread] = [t for t in threads if t["thread_id"] == "t_named"]
    assert thread["preview"] == "Q3 销售汇总"
    [title_request] = [
        str(m[-1].content) for m in fake_model.received if "Name this" in str(m[0].content)
    ]
    # Asked for before there is a reply, so a long first turn is not unnamed.
    assert title_request == "User: 帮我汇总一下第三季度的销售数据"


def test_deleting_a_conversation_lets_go_of_its_environment_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        env = client.post("/api/environments", json={"name": "Reports"}).json()
        with client.websocket_connect("/ws/envthread") as ws:
            _receive_until(ws, "history")
            ws.send_json({"type": "set_environment", "environment": env["id"]})
            assert _receive_until(ws, "state")[-1]["environment"] == env["id"]
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "agent_message")
        used = client.get("/api/environments").json()["environments"]
        client.delete("/api/threads/envthread")
        after = client.get("/api/environments").json()["environments"]

    assert [e["used_by"] for e in used if e["id"] == env["id"]] == [1]
    assert [e["used_by"] for e in after if e["id"] == env["id"]] == [0]


def test_deleting_a_conversation_forgets_its_waiting_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/b") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")
        inbox = tmp_path / "state" / "b.inbox.json"
        inbox.write_text("[]", encoding="utf-8")

        client.delete("/api/threads/b")

        assert not inbox.exists()


class _SuggestingModel(FakeToolCallingChatModel):
    """Answers the prediction request on its own, like _TitlingModel does for naming."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if "predict what the user will type next" in str(messages[0].content):
            self.received.append(list(messages))
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="“把第二张也补回来”"))]
            )
        return super()._generate(messages, stop, run_manager, **kwargs)


def test_a_finished_turn_offers_the_next_message_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = _SuggestingModel(responses=[AIMessage(content="第一张已经补回来了。")])
    with _client_lg(tmp_path, monkeypatch, fake_model, prompt_suggestions=True) as client:
        with client.websocket_connect("/ws/t_suggest") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "把第一张补回来"})
            suggestion = _receive_until(ws, "prompt_suggestion")[-1]

    assert suggestion == {"type": "prompt_suggestion", "text": "把第二张也补回来"}
    [request] = [m for m in fake_model.received if "predict what" in str(m[0].content)]
    assert "把第一张补回来" in str(request[-1].content)
    assert "第一张已经补回来了" in str(request[-1].content)


def test_no_suggestion_is_made_when_the_setting_is_off_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = _SuggestingModel(responses=[AIMessage(content="Done.")])
    with _client_lg(tmp_path, monkeypatch, fake_model, prompt_suggestions=False) as client:
        with client.websocket_connect("/ws/t_nosuggest") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "user_message", "text": "hi"})
            _receive_until(ws, "tasks_changed")

    assert not [m for m in fake_model.received if "predict what" in str(m[0].content)]
