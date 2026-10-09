# ruff: noqa: E402
"""Web tests: the chat turn: approvals, questions, stop, modes, hooks, history, compaction."""

import asyncio
import contextlib
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGenerationChunk

from coscribe.conversation.thread_meta import ThreadMetaStore
from coscribe.runtime_lg.audit import AuditLog
from coscribe.runtime_lg.messages import is_steer

from .helpers import (
    FakeToolCallingChatModel,
    GrpcMetadataOverflowThenSuccessModel,
    HangingChatModel,
    _client_lg,
    _create_workflow_task,
    _receive_until,
    _start_mode,
    _structured,
    _stub_script_env,
    _tool_call,
    _verdict,
    _wait_for_run_status,
    _write_call,
)


def test_chat_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t1") as ws:
            state = ws.receive_json()
            ws.receive_json()  # history
            assert state["type"] == "state"
            assert state["plan_mode"] is False
            assert state["accept_edits"] is False
            assert state["model"] == "fake:model"
            assert isinstance(state["context_window"], int)

            ws.send_json({"type": "user_message", "text": "hello"})
            messages = _receive_until(ws, "tasks_changed")

    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "hi there!"
    assert messages[-1]["type"] == "tasks_changed"


def test_usage_event_sent_when_the_model_reports_usage_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported gap: the WS "usage" event
    (drives the frontend's context-usage bar) was deliberately never sent,
    because an earlier check against streaming Gemini found
    AIMessageChunk.usage_metadata always came back all zeros. Re-checked
    live against the currently pinned langchain-google-genai and found that
    finding stale -- an upstream fix means real per-chunk usage now flows
    through this exact astream(stream_mode=["messages"]) path. See
    runtime_lg/README.md's "usage bar" section for the live verification
    (including a real tool call and a second turn's usage correctly
    growing) this unit test can't reach on its own (FakeToolCallingChatModel
    has no live API to hit)."""
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="hi there!",
                usage_metadata=UsageMetadata(input_tokens=10, output_tokens=5, total_tokens=15),
            )
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_usage") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            messages = _receive_until(ws, "tasks_changed")

    usage_message = next(m for m in messages if m["type"] == "usage")
    assert usage_message == {"type": "usage", "total_tokens": 15}


def test_usage_events_are_live_per_response_not_summed_across_a_tool_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool-calling turn involves two separate model responses (propose
    the call, then respond to its result) -- each one's own usage_metadata
    already reflects the *cumulative* context size at that point (every
    provider reports "tokens in the whole prompt this call sent" as
    input_tokens, not a delta since the last call), so summing both
    responses together would double-count: the sequence below must never
    contain 145 (60 + 85).

    Also the live-per-segment regression test for RunStatus's running
    turn timer: _stream_turn sends "usage" itself at each ToolMessage
    boundary now, not only once the whole turn is over, so the first
    response's total (60) must reach the client *before* that call's own
    "tool_result" event -- proof it's live, not batched to the end. The
    final 85 shows up twice (once from _stream_turn's own end-of-call
    send, once more from this handler's existing post-turn send) --
    harmless, same value both times, not worth suppressing the second
    one for."""
    call = _tool_call("call_1", "list_files", {})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[call],
                usage_metadata=UsageMetadata(input_tokens=50, output_tokens=10, total_tokens=60),
            ),
            AIMessage(
                content="done",
                usage_metadata=UsageMetadata(input_tokens=80, output_tokens=5, total_tokens=85),
            ),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_usage_tool") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "list files"})
            messages = _receive_until(ws, "tasks_changed")

    usage_totals = [m["total_tokens"] for m in messages if m["type"] == "usage"]
    assert usage_totals == [60, 85, 85]

    usage_60 = {"type": "usage", "total_tokens": 60}
    usage_60_index = next(i for i, m in enumerate(messages) if m == usage_60)
    tool_result_index = next(i for i, m in enumerate(messages) if m["type"] == "tool_result")
    assert usage_60_index < tool_result_index


def test_no_usage_event_when_the_model_never_reports_usage_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_usage_none") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            messages = _receive_until(ws, "tasks_changed")

    assert not any(m["type"] == "usage" for m in messages)


def test_agent_delta_events_stream_before_the_final_agent_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t10") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "agent_delta" in types
    assert types.index("agent_delta") < types.index("agent_message")
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "hi there!"


def test_write_file_requires_approval_and_executes_when_approved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="done"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})

            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            assert approval["tool_name"] == "write_file"
            assert approval["arguments"] == {"path": "note.txt", "content": "hi"}

            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "tool_result" in types
    assert types.index("tool_result") < types.index("agent_message")
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "done"
    assert (tmp_path / "workspace" / "note.txt").read_text() == "hi"

    entries = AuditLog(tmp_path / "state").read_all()
    assert len(entries) == 1
    assert entries[0].thread_id == "t2"
    assert entries[0].tool_name == "write_file"
    assert entries[0].decision == "approve"
    assert entries[0].reason == "human"


def test_a_note_sent_mid_turn_reaches_the_model_at_its_next_step_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.runtime_lg.messages import STEER_NOTE

    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_steer1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            approval = ws.receive_json()
            assert approval["type"] == "approval_required"

            ws.send_json({"type": "steer", "id": "s1", "text": "and make it a list"})
            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            messages = _receive_until(ws, "tasks_changed")

    assert {"type": "steers_delivered", "ids": ["s1"]} in messages
    assert not any(m["type"] == "steers_requeued" for m in messages)
    last = fake_model.received[-1][-1]
    assert isinstance(last, HumanMessage)
    assert last.content == STEER_NOTE + "and make it a list"
    assert next(m for m in messages if m["type"] == "agent_message")["text"] == "done"


def test_a_note_no_turn_was_left_to_read_is_sent_as_the_next_message_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="noted")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_steer2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "steer", "id": "s1", "text": "one more thing"})
            requeued = ws.receive_json()
            messages = _receive_until(ws, "tasks_changed")

    assert requeued == {"type": "steers_requeued", "ids": ["s1"], "text": "one more thing"}
    sent = fake_model.received[-1][-1]
    assert isinstance(sent, HumanMessage) and "one more thing" in str(sent.content)
    assert not is_steer(sent)
    assert next(m for m in messages if m["type"] == "agent_message")["text"] == "noted"


def test_stop_drops_the_notes_the_model_has_not_read_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="", tool_calls=[call])])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_steer3") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            assert ws.receive_json()["type"] == "approval_required"
            ws.send_json({"type": "steer", "id": "s1", "text": "never mind the list"})
            ws.send_json({"type": "stop"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "steers_requeued" not in types and "steers_delivered" not in types
    assert len(fake_model.received) == 1


def test_the_sidebar_status_follows_a_conversation_from_waiting_to_ready_to_seen_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/side1") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            waiting = client.get("/api/threads/status").json()["statuses"]["side1"]
            listed = {t["thread_id"]: t for t in client.get("/api/threads").json()}

            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            _receive_until(ws, "tasks_changed")
            for _ in range(40):  # the turn lock is released just after the event
                watched = client.get("/api/threads/status").json()["statuses"]["side1"]
                if watched != "working":
                    break
                time.sleep(0.05)

        # The tab closed; a turn that ends now has nobody looking.
        unseen = ThreadMetaStore(tmp_path / "state")
        unseen.mark_finished("side1", watched=False)
        ready = client.get("/api/threads/status").json()["statuses"]["side1"]
        with client.websocket_connect("/ws/side1") as ws:
            ws.receive_json()
            seen = client.get("/api/threads/status").json()["statuses"]["side1"]

    assert waiting == "needs_input"
    assert listed["side1"]["status"] == "needs_input"
    assert watched == "idle"
    assert ready == "ready"
    assert seen == "idle"


def test_narration_before_a_gated_tool_call_does_not_get_glued_onto_the_final_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real bug, found live against DeepSeek/GLM (both narrate before a
    gated tool call, unlike Gemini/Anthropic in this app's usual testing):
    the pre-tool narration text streamed live as its own "agent_delta" run,
    then got concatenated with no separator onto the *front* of the final
    "agent_message" -- "I'll write it now.Done, I wrote the file." glued
    together, because session.py's turn handler used to join every model
    response of a turn into one string. Fixed by having _stream_turn/
    _resolve_pending_approvals each return only their own *last* response's
    text -- see their docstrings."""
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="I'll write it now.", tool_calls=[call]),
            AIMessage(content="Done, I wrote the file."),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t2b") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})

            delta = ws.receive_json()
            assert delta == {"type": "agent_delta", "text": "I'll write it now."}
            approval = ws.receive_json()
            assert approval["type"] == "approval_required"

            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            messages = _receive_until(ws, "tasks_changed")

    deltas = [m["text"] for m in messages if m["type"] == "agent_delta"]
    assert deltas == ["Done, I wrote the file."]
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    # The bug's exact shape: "I'll write it now.Done, I wrote the file."
    assert agent_message["text"] == "Done, I wrote the file."


def test_narration_before_an_ungated_tool_call_does_not_get_glued_onto_the_final_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same underlying bug as
    test_narration_before_a_gated_tool_call_does_not_get_glued_onto_the_
    final_reply, but for a tool that never asks for approval -- both
    model responses stream within a *single* _stream_turn call here (no
    approval_required event splits them), so this exercises that
    method's own text_parts.clear() reset at the ToolMessage boundary,
    not _resolve_pending_approvals's fix."""
    call = _tool_call("call_1", "read_file", {"path": "note.txt"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="Let me check that file.", tool_calls=[call]),
            AIMessage(content="It says hello."),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_ungated_narration") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
            (tmp_path / "workspace" / "note.txt").write_text("hello")
            ws.send_json({"type": "user_message", "text": "read note.txt"})
            messages = _receive_until(ws, "tasks_changed")

    deltas = [m["text"] for m in messages if m["type"] == "agent_delta"]
    assert deltas == ["Let me check that file.", "It says hello."]
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    # The bug's exact shape: "Let me check that file.It says hello."
    assert agent_message["text"] == "It says hello."


def test_edit_file_batch_tool_works_end_to_end_through_the_agent_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real, live verification that edit_file_batch's `edits` string
    argument round-trips correctly through the actual LangGraph/
    LangChain tool-schema machinery (build_langgraph_agent -> a real
    StructuredTool, not just this function called directly in Python
    the way test_files_tool.py's unit tests do)."""
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace" / "a.txt").write_text("hello world\nsecond line\n", encoding="utf-8")
    call = _tool_call(
        "call_1",
        "edit_file_batch",
        {
            "path": "a.txt",
            "edits": "world\n---\nthere\n---\nsecond line\n---\nSECOND LINE",
        },
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_batch_edit") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "make both edits"})

            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            assert approval["tool_name"] == "edit_file_batch"
            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            messages = _receive_until(ws, "tasks_changed")

    tool_result = next(m for m in messages if m["type"] == "tool_result")
    assert "2" in json.dumps(tool_result)  # edits_applied
    assert (tmp_path / "workspace" / "a.txt").read_text() == "hello there\nSECOND LINE\n"


def test_ask_user_question_sends_question_required_and_feeds_answer_back_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call(
        "call_1",
        "ask_user_question",
        {
            "questions": [
                {
                    "question": "What kind of plan?",
                    "header": "Plan type",
                    "options": [
                        {"label": "Travel", "description": "Flights, hotels, a route"},
                        {"label": "Study"},
                    ],
                },
                {
                    "question": "Which days?",
                    "options": [{"label": "Weekdays"}, {"label": "Weekends"}],
                    "multi_select": True,
                },
                {"question": "Budget?", "options": [{"label": "Low"}, {"label": "High"}]},
            ]
        },
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_q1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "help me plan something"})

            question = ws.receive_json()
            assert question["type"] == "question_required"
            assert [q["question"] for q in question["questions"]] == [
                "What kind of plan?",
                "Which days?",
                "Budget?",
            ]
            assert question["questions"][0]["header"] == "Plan type"
            assert question["questions"][0]["options"] == [
                {"label": "Travel", "description": "Flights, hotels, a route"},
                {"label": "Study", "description": ""},
            ]
            assert [q["multi_select"] for q in question["questions"]] == [False, True, False]

            ws.send_json(
                {
                    "type": "question_response",
                    "id": question["id"],
                    "answers": ["Travel", "Weekdays, Weekends", None],
                }
            )
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    # No "tool_result" WS event either: the question_required/answered card
    # already fully represents this call, so session.py deliberately skips
    # sending the normal tool_result event for QUESTION_TOOL_NAMES (it would
    # otherwise show up as a redundant second "Asked: ..." row in the log --
    # see _stream_turn's ToolMessage branch). Confirm the answer really did
    # feed back into the model instead by inspecting the follow-up call.
    assert "tool_result" not in types
    tool_messages = [m for m in fake_model.received[-1] if isinstance(m, ToolMessage)]
    assert tool_messages[-1].name == "ask_user_question"
    assert tool_messages[-1].content == (
        "1. What kind of plan? -> Travel\n"
        "2. Which days? -> Weekdays, Weekends\n"
        "3. Budget? -> (skipped)"
    )
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "done"


def test_closing_the_questions_tells_the_model_to_go_on_without_asking_again_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call(
        "call_1",
        "ask_user_question",
        {"questions": [{"question": "Which one?", "options": [{"label": "A"}, {"label": "B"}]}]},
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_q1b") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "ask me something"})
            question = ws.receive_json()
            ws.send_json({"type": "question_response", "id": question["id"], "dismissed": True})
            _receive_until(ws, "tasks_changed")

    tool_messages = [m for m in fake_model.received[-1] if isinstance(m, ToolMessage)]
    assert "closed the questions without answering" in str(tool_messages[-1].content)


def test_ask_user_question_free_text_answer_works_too_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user can always type a custom answer instead of clicking a
    listed option -- there's nothing server-side that validates the
    answer against `options` at all, matching AskUserQuestion's own
    "Other" escape hatch."""
    call = _tool_call(
        "call_1",
        "ask_user_question",
        {"question": "What kind of plan?", "options": "Travel\nStudy"},
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_q2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "help me plan something"})

            question = ws.receive_json()
            ws.send_json(
                {
                    "type": "question_response",
                    "id": question["id"],
                    "answer": "Actually, a birthday party",
                }
            )
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "tool_result" not in types
    tool_messages = [m for m in fake_model.received[-1] if isinstance(m, ToolMessage)]
    assert "birthday party" in json.dumps(tool_messages[-1].content)


def test_ask_user_question_is_not_blocked_by_plan_mode_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike a WRITE_LOCAL/EXEC/EXTERNAL tool, asking a question isn't a
    risky action plan mode's read-only guarantee needs to block -- it's
    pure communication, no side effect."""
    call = _tool_call("call_1", "ask_user_question", {"question": "Which one?", "options": "A\nB"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_q3") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/plan"})
            ws.receive_json()  # state (plan_mode: True)

            ws.send_json({"type": "user_message", "text": "ask me something"})
            question = ws.receive_json()
            assert question["type"] == "question_required"
            ws.send_json({"type": "question_response", "id": question["id"], "answer": "A"})
            _receive_until(ws, "tasks_changed")


def test_ask_user_question_hook_veto_becomes_a_respond_decision_not_a_reject_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PreToolUse hook can still veto ask_user_question, but the
    HumanInTheLoopMiddleware interrupt for this tool only allows a
    "respond" decision -- a bare {"type": "reject", ...} would raise
    inside the middleware, so the veto reason has to be delivered as the
    tool's own "answer" instead."""
    hook_script = tmp_path / "veto_hook.py"
    hook_script.write_text("import sys; sys.stderr.write('no questions allowed'); sys.exit(1)\n")
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps({"PreToolUse": [f"{sys.executable} {hook_script}"]}), encoding="utf-8"
    )
    call = _tool_call("call_1", "ask_user_question", {"question": "Which one?", "options": "A\nB"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="blocked")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, hooks_config_path=hooks_path) as client:
        with client.websocket_connect("/ws/t_q4") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "ask me something"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "question_required" not in types
    assert "tool_result" not in types
    tool_messages = [m for m in fake_model.received[-1] if isinstance(m, ToolMessage)]
    assert "no questions allowed" in json.dumps(tool_messages[-1].content)
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "blocked"


def test_stop_resolves_a_pending_question_with_a_placeholder_answer_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves request_stop() resolves a pending question rather than
    leaving the turn hanging forever -- the same "[stopped]" fallback text
    test_stop_denies_pending_approval_and_marks_reply_stopped's approval
    case gets (see _stream_turn's reply_text fallback), since the resumed
    astream's own cooperative stop check (checked after every chunk,
    including the ToolNode's ToolMessage carrying the placeholder answer)
    breaks the loop before any further model narration exists to report.
    The placeholder's actual content ("(Stopped by user before
    answering.)") isn't independently observable over the wire once
    resolved this way -- ask_user_question's tool_result WS event is
    deliberately suppressed (see _stream_turn's ToolMessage branch), same
    as the non-stopped answer path above -- so this only checks the
    contract a client actually sees: the turn ends promptly, not that it
    hangs waiting on a future nobody will ever resolve."""
    call = _tool_call("call_1", "ask_user_question", {"question": "Which one?", "options": "A\nB"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="stopped")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_q5") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "ask me something"})
            question = ws.receive_json()
            assert question["type"] == "question_required"

            ws.send_json({"type": "stop"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "tool_result" not in types
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "[stopped]"


def test_ask_user_question_still_reads_a_single_question_with_line_options_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call(
        "call_1",
        "ask_user_question",
        {
            "question": "Which ones?",
            "options": "A\n\nB\nC\n",
            "multi_select": True,
        },
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_q6") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "ask me something"})
            question = ws.receive_json()
            # A call saved in a conversation from before questions came in
            # lists still shows: one question, its options one per line,
            # blank lines dropped.
            (only,) = question["questions"]
            assert only["multi_select"] is True
            assert [option["label"] for option in only["options"]] == ["A", "B", "C"]

            ws.send_json({"type": "question_response", "id": question["id"], "answer": "A, C"})
            _receive_until(ws, "tasks_changed")


def test_pending_approval_is_redelivered_on_reconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The checkpointer-backed regression test for cli_lg.py's real
    # process-restart test (see runtime_lg/README.md) -- here, within one
    # process, connect once, trigger an approval, disconnect *without*
    # responding, then reconnect a fresh WebSocket to the same thread_id and
    # confirm the still-pending approval is redelivered rather than lost.
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="done"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t2b") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            first_approval = ws.receive_json()
            assert first_approval["type"] == "approval_required"
        # disconnected without ever sending approval_response

        assert not (tmp_path / "workspace" / "note.txt").exists()

        with client.websocket_connect("/ws/t2b") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            redelivered = ws.receive_json()
            assert redelivered["type"] == "approval_required"
            assert redelivered["tool_name"] == "write_file"

            ws.send_json({"type": "approval_response", "id": redelivered["id"], "approved": True})
            messages = _receive_until(ws, "tasks_changed")

    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "done"
    assert (tmp_path / "workspace" / "note.txt").read_text() == "hi"


def test_a_pending_question_is_redelivered_every_time_the_conversation_is_reopened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Leaving a conversation and coming back again and again, never answering:
    # a redelivered question that outlives its connection must not keep the
    # turn lock, or the next reconnect finds nothing to answer.
    call = _tool_call(
        "call_1",
        "ask_user_question",
        {"questions": [{"question": "Budget?", "options": [{"label": "Low"}, {"label": "High"}]}]},
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_q_again") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "plan"})
            assert ws.receive_json()["type"] == "question_required"

        for _ in range(3):
            with client.websocket_connect("/ws/t_q_again") as ws:
                ws.receive_json()  # state
                ws.receive_json()  # history
                assert ws.receive_json()["type"] == "question_required"

        with client.websocket_connect("/ws/t_q_again") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            question = ws.receive_json()
            ws.send_json({"type": "question_response", "id": question["id"], "answers": ["Low"]})
            messages = _receive_until(ws, "tasks_changed")

    assert next(m for m in messages if m["type"] == "agent_message")["text"] == "done"


def test_write_file_denied_is_not_executed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="ok, cancelled"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t3") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})

            approval = ws.receive_json()
            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": False})
            messages = _receive_until(ws, "tasks_changed")

    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "ok, cancelled"
    assert not (tmp_path / "workspace" / "note.txt").exists()

    entries = AuditLog(tmp_path / "state").read_all()
    assert len(entries) == 1
    assert entries[0].decision == "reject"
    assert entries[0].reason == "human"


def test_clear_queues_behind_a_pending_turn_instead_of_racing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression coverage for _turn_lock (see its docstring in __init__):
    a message sent while a turn is still pending on approval must *queue*
    behind it, not run concurrently -- so /clear here can't even reach its
    own "is anything pending?" check until the first turn's approval is
    answered and that turn fully finishes. Before _turn_lock existed, this
    exact scenario used to run /clear concurrently and see the pending
    approval immediately (asserted as an error); now it can't be observed
    at all through this path, since the lock always resolves the earlier
    turn first -- see _handle_clear's docstring for the one narrow window
    where that guard is still reachable."""
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_clear2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            approval = ws.receive_json()
            assert approval["type"] == "approval_required"

            # Queues behind _turn_lock -- nothing happens with this until
            # the pending approval below is answered.
            ws.send_json({"type": "user_message", "text": "/clear"})

            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            first_turn_messages = _receive_until(ws, "tasks_changed")
            cleared = ws.receive_json()

    assert cleared == {"type": "cleared"}
    agent_message = next(m for m in first_turn_messages if m["type"] == "agent_message")
    assert agent_message["text"] == "done"


def test_stop_denies_pending_approval_and_marks_reply_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_stop") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            approval = ws.receive_json()
            assert approval["type"] == "approval_required"

            ws.send_json({"type": "stop"})
            messages = _receive_until(ws, "tasks_changed")

    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert "[stopped]" in agent_message["text"]
    assert not (tmp_path / "workspace" / "note.txt").exists()


def test_stop_hard_cancels_a_turn_stuck_inside_the_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real, user-reported bug: a provider SDK stuck in its own internal
    retry/backoff loop (e.g. langchain_google_genai on a Gemini quota
    error) never yields a chunk for the cooperative _stop_requested check
    to run against, so Stop did nothing for however long that loop ran
    (up to ~60-90s). request_stop() now hard-cancels the turn's own task
    whenever there's nothing pending to resolve instead (see its
    docstring) -- this proves the cancellation actually reaches a call
    genuinely stuck mid-stream, not just one paused between chunks."""
    started = threading.Event()
    fake_model = HangingChatModel(started=started)
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_hard_stop") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})

            # Wait until the turn has genuinely entered the hanging model
            # call (and _current_turn_task is therefore assigned) before
            # sending stop -- otherwise this races request_stop() against
            # handle_user_message's own task assignment.
            assert started.wait(timeout=5), "model call never started"

            ws.send_json({"type": "stop"})
            # HangingChatModel's _astream awaits asyncio.sleep(999) -- if
            # cancellation isn't actually reaching it, this receive blocks
            # for the test's own default timeout and fails loudly rather
            # than hanging for 999s.
            agent_message = ws.receive_json()
            # The task panel refreshes on this after any turn, a stopped one too.
            refresh = ws.receive_json()

    assert agent_message["type"] == "agent_message"
    assert agent_message["text"] == "[stopped]"
    assert refresh["type"] == "tasks_changed"


def test_close_orphaned_tool_calls_commits_a_finished_parallel_calls_uncommitted_result() -> None:
    """Parallel tool calls, cancelled while one is still running: the one
    that finished only has an uncommitted pending write, which aget_state
    shows but a fresh input would drop. Its real result must survive, and
    only the unfinished call gets the placeholder."""
    from langchain.agents import create_agent
    from langchain_core.messages import ToolCall
    from langgraph.checkpoint.memory import InMemorySaver

    from coscribe.conversation.session import _ORPHANED_TOOL_CALL_NOTE, _close_orphaned_tool_calls

    started = threading.Event()
    release = threading.Event()

    def fast(q: str) -> str:
        """Fast lookup."""
        return "fast result"

    def slow(q: str) -> str:
        """Slow lookup."""
        started.set()
        release.wait(timeout=10)
        return "slow result"

    model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    ToolCall(name="fast", args={"q": "a"}, id="fast_call"),
                    ToolCall(name="slow", args={"q": "b"}, id="slow_call"),
                ],
            ),
            AIMessage(content="hi"),
        ]
    )
    agent = create_agent(model, [fast, slow], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t"}}

    async def scenario() -> None:
        turn = asyncio.create_task(
            agent.ainvoke({"messages": [HumanMessage(content="go")]}, config)
        )
        await asyncio.to_thread(started.wait, 5)
        await asyncio.sleep(0.2)
        turn.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await turn
        release.set()
        assert await _close_orphaned_tool_calls(agent, config) is True
        await agent.ainvoke({"messages": [HumanMessage(content="say hi")]}, config)

    try:
        asyncio.run(scenario())
    finally:
        release.set()

    history = model.received[-1]
    tool_messages = {m.tool_call_id: m.content for m in history if isinstance(m, ToolMessage)}
    assert tool_messages == {"fast_call": "fast result", "slow_call": _ORPHANED_TOOL_CALL_NOTE}
    assert isinstance(history[-1], HumanMessage)


def test_close_orphaned_tool_calls_commits_a_finished_tools_uncommitted_write() -> None:
    """Every tool call finished, but the superstep never committed: the
    ToolMessage exists only as a pending write, visible through aget_state
    yet dropped by a fresh input -- the exact state a live DeepSeek thread
    was found in. Nothing needs a placeholder; the write must still be
    committed or the model sees the orphan anyway."""
    from langchain.agents import create_agent
    from langchain_core.messages import ToolCall
    from langgraph.checkpoint.memory import InMemorySaver

    from coscribe.conversation.session import _close_orphaned_tool_calls

    def lookup(q: str) -> str:
        """Look something up."""
        return "unused"

    checkpointer = InMemorySaver()
    model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    agent = create_agent(model, [lookup], checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "t"}}
    call = ToolCall(name="lookup", args={"q": "a"}, id="call_1")

    async def scenario() -> None:
        await agent.aupdate_state(
            config,
            {"messages": [HumanMessage(content="go"), AIMessage(content="", tool_calls=[call])]},
            as_node="model",
        )
        state = await agent.aget_state(config)
        await checkpointer.aput_writes(
            state.config,
            [("messages", [ToolMessage(content="real result", tool_call_id="call_1")])],
            state.tasks[0].id,
        )
        assert await _close_orphaned_tool_calls(agent, config) is True
        await agent.ainvoke({"messages": [HumanMessage(content="say hi")]}, config)

    asyncio.run(scenario())

    history = model.received[-1]
    assert [type(m).__name__ for m in history] == [
        "HumanMessage",
        "AIMessage",
        "ToolMessage",
        "HumanMessage",
    ]
    assert history[2].content == "real result"


def test_switch_model_rebuilds_the_graph_and_reports_the_new_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model_a = FakeToolCallingChatModel(responses=[AIMessage(content="hi from A")])
    fake_model_b = FakeToolCallingChatModel(responses=[AIMessage(content="hi from B")])
    models = {"fake:model": fake_model_a, "fake:model-b": fake_model_b}
    with _client_lg(tmp_path, monkeypatch, fake_model_a) as client:
        # Must be set *after* entering _client_lg -- its own body sets a
        # default stub for resolve_chat_model on entry, which would
        # otherwise overwrite this one (same gotcha as
        # connect_one_mcp_server_lg elsewhere in this file).
        monkeypatch.setattr(
            "coscribe.conversation.session.resolve_chat_model",
            lambda model, custom_providers=None: models[model],
        )
        with client.websocket_connect("/ws/t_switch") as ws:
            state = ws.receive_json()
            ws.receive_json()  # history
            assert state["model"] == "fake:model"

            ws.send_json({"type": "switch_model", "model": "fake:model-b"})
            switched_state = ws.receive_json()
            assert switched_state["type"] == "state"
            assert switched_state["model"] == "fake:model-b"

            ws.send_json({"type": "user_message", "text": "hello"})
            messages = _receive_until(ws, "tasks_changed")

    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "hi from B"


def test_plan_mode_toggle_updates_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="unused")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t4") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/plan"})
            state = ws.receive_json()
            assert state == {
                "type": "state",
                "plan_mode": True,
                "accept_edits": False,
                "model": "fake:model",
                "context_window": 128_000,
                "enabled_skills": [
                    "Excel Spreadsheets",
                    "PPTX Slides",
                    "Skill Creator",
                    "Word Documents",
                ],
                "workspace_root": str(tmp_path / "workspace"),
                "workspace_explicit": False,
                "folders": [],
                "connector_tools": [],
                "auto_mode": False,
            }
            ws.send_json({"type": "user_message", "text": "/plan"})
            state = ws.receive_json()
            assert state["plan_mode"] is False


def test_accept_edits_toggle_updates_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="unused")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t4b") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/accept-edits"})
            state = ws.receive_json()
            assert state["accept_edits"] is True


def test_plan_mode_blocks_write_file_without_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="blocked"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t4c") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/plan"})
            ws.receive_json()  # state (plan_mode: True)

            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "blocked"

    entries = AuditLog(tmp_path / "state").read_all()
    assert len(entries) == 1
    assert entries[0].decision == "reject"
    assert entries[0].reason == "plan_mode"
    assert not (tmp_path / "workspace" / "note.txt").exists()


def test_accept_edits_auto_approves_without_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="done"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t4d") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/accept-edits"})
            ws.receive_json()  # state (accept_edits: True)

            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "done"
    assert (tmp_path / "workspace" / "note.txt").read_text() == "hi"

    entries = AuditLog(tmp_path / "state").read_all()
    assert len(entries) == 1
    assert entries[0].decision == "approve"
    assert entries[0].reason == "accept_edits"


def test_compact_refuses_when_nothing_to_compact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t4f") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/compact"})
            error = ws.receive_json()

    assert error["type"] == "error"
    assert "Nothing much to compact" in error["message"]


def test_load_older_messages_across_two_compactions_reveals_each_epoch_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="reply one-a"),
            AIMessage(content="reply one-b"),
            AIMessage(content="first summary"),
            AIMessage(content="reply two-a"),
            AIMessage(content="reply two-b"),
            AIMessage(content="second summary"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_older3") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            # /compact refuses below 4 messages (see _handle_compact) --
            # two turns per epoch here, same as
            # test_compact_collapses_history_and_survives_a_later_turn.
            ws.send_json({"type": "user_message", "text": "turn one-a"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "turn one-b"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "/compact"})
            assert ws.receive_json()["type"] == "compacted"

            ws.send_json({"type": "user_message", "text": "turn two-a"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "turn two-b"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "/compact"})
            assert ws.receive_json()["type"] == "compacted"

            ws.send_json({"type": "load_older_messages"})
            first_older = ws.receive_json()
            ws.send_json({"type": "load_older_messages"})
            second_older = ws.receive_json()
            ws.send_json({"type": "load_older_messages"})
            third_older = ws.receive_json()

    # The first compact's own synthetic note is a real HumanMessage
    # aupdate_state adds -- it becomes message #1 of the *next* epoch
    # going forward (the model's context after a compact genuinely
    # starts from that note), so it's what this epoch's own full
    # accumulated list starts with, same as the live conversation would
    # have shown it during epoch two itself.
    first_texts = [(e["kind"], e.get("text")) for e in first_older["entries"]]
    assert first_texts[0][0] == "user"
    assert "first summary" in first_texts[0][1]
    assert first_texts[1:] == [
        ("user", "turn two-a"),
        ("agent", "reply two-a"),
        ("user", "turn two-b"),
        ("agent", "reply two-b"),
    ]
    assert first_older["has_more"] is True
    assert [(e["kind"], e.get("text")) for e in second_older["entries"]] == [
        ("user", "turn one-a"),
        ("agent", "reply one-a"),
        ("user", "turn one-b"),
        ("agent", "reply one-b"),
    ]
    assert second_older["has_more"] is True
    # Nothing left before the very first turn -- and critically, this does
    # NOT re-return the first epoch's messages a second time (the real bug
    # a naive "always subset-check against the current checkpoint" version
    # of this would have hit -- see load_older_messages' own docstring).
    assert third_older == {"type": "older_messages", "entries": [], "has_more": False}


def test_edit_message_rejects_anything_but_the_latest_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="hi there!"), AIMessage(content="nice to hear")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_edit_old") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "how are you"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "edit_message", "index": 0, "text": "hello, edited"})
            error = ws.receive_json()

        with client.websocket_connect("/ws/t_edit_old") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()

    assert error["type"] == "error"
    assert "most recent message" in error["message"]
    assert [e["text"] for e in history["entries"]] == [
        "hello",
        "hi there!",
        "how are you",
        "nice to hear",
    ]


def test_edit_message_rejects_an_out_of_range_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_edit2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "edit_message", "index": 5, "text": "no such turn"})
            error = ws.receive_json()

    assert error["type"] == "error"
    assert "No such message to edit" in error["message"]


def test_rewind_message_truncates_without_regenerating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: the UI's "Rewind"
    button used to call edit_message with the turn's own unedited text,
    which truncates *and* immediately reruns it -- that's retry, not
    rewind. rewind_message must truncate and stop there, with no new turn
    (and so no new agent_message) following it."""
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="hi there!"), AIMessage(content="nice to hear")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_rewind1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "how are you"})
            _receive_until(ws, "tasks_changed")

            # index 1 -- the second turn -- rewound. Only that turn's own
            # "how are you"/"nice to hear" is discarded; the first turn is
            # untouched, and nothing regenerates in its place.
            ws.send_json({"type": "rewind_message", "index": 1})
            rewound = ws.receive_json()

        with client.websocket_connect("/ws/t_rewind1") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()

    assert rewound == {"type": "rewound", "index": 1}
    assert history["entries"] == [
        {"kind": "user", "text": "hello"},
        {"kind": "agent", "text": "hi there!"},
    ]


def test_rewind_message_rejects_an_out_of_range_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_rewind2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "rewind_message", "index": 5})
            error = ws.receive_json()

    assert error["type"] == "error"
    assert "No such message to rewind" in error["message"]


def test_grpc_metadata_overflow_recovers_by_rebuilding_the_agent_and_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: a long-lived Gemini
    session's gRPC channel accumulates something into its own metadata
    until a call fails with "429 ... Stream removed (received metadata
    size exceeds soft limit ...)" -- session.py's own
    _GRPC_METADATA_OVERFLOW_SIGNATURE comment has the full explanation.
    The turn must recover by rebuilding self.model/self.lg_agent and
    retrying once, not surface that error to the user on the first hit."""
    fake_model = GrpcMetadataOverflowThenSuccessModel(responses=[AIMessage(content="recovered")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_grpc_overflow") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            messages = _receive_until(ws, "tasks_changed")

    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "recovered"
    assert not any(m["type"] == "error" for m in messages)
    assert fake_model.calls == 2


def test_pretool_use_hook_denial_blocks_a_call_even_in_accept_edits_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hook_script = tmp_path / "deny_hook.py"
    hook_script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        'sys.exit(1 if payload.get("tool_name") == "write_file" else 0)\n',
        encoding="utf-8",
    )
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps({"PreToolUse": [f"{sys.executable} {hook_script}"]}), encoding="utf-8"
    )
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="denied by hook"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, hooks_config_path=hooks_path) as client:
        with client.websocket_connect("/ws/t4g") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/accept-edits"})
            ws.receive_json()  # state

            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "denied by hook"

    entries = AuditLog(tmp_path / "state").read_all()
    assert len(entries) == 1
    assert entries[0].decision == "reject"
    assert entries[0].reason == "hook_veto"
    assert entries[0].detail is not None
    assert not (tmp_path / "workspace" / "note.txt").exists()


def test_pretool_use_hook_gates_a_low_risk_tool_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When PreToolUse hooks are configured, even a tool that never
    required approval (task_create, risk_category="READ") is routed through the
    same interrupt point so the hook can veto it -- see agent.py's
    extra_interrupt_tool_names. It's still auto-approved (no
    approval_required sent) since the hook allows it and it was never
    risky enough to ask the user either way."""
    hook_script = tmp_path / "log_hook.py"
    log_path = tmp_path / "pretool.log"
    hook_script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        f'open({str(log_path)!r}, "a").write(payload["tool_name"] + chr(10))\n'
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps({"PreToolUse": [f"{sys.executable} {hook_script}"]}), encoding="utf-8"
    )
    call = _tool_call("call_1", "task_create", {"content": "write the report"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="tracked it"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, hooks_config_path=hooks_path) as client:
        with client.websocket_connect("/ws/t4h") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "please plan this out"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "tracked it"
    assert log_path.read_text(encoding="utf-8").strip() == "task_create"


def test_post_tool_use_hook_receives_the_tool_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hook_script = tmp_path / "posttool_hook.py"
    log_path = tmp_path / "posttool.log"
    hook_script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        f'open({str(log_path)!r}, "a").write(json.dumps(payload) + chr(10))\n'
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps({"PostToolUse": [f"{sys.executable} {hook_script}"]}), encoding="utf-8"
    )
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="done"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, hooks_config_path=hooks_path) as client:
        with client.websocket_connect("/ws/t4i") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            approval = ws.receive_json()
            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            _receive_until(ws, "tasks_changed")

    logged = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert logged["tool_name"] == "write_file"
    assert logged["arguments"] == {"path": "note.txt", "content": "hi"}
    assert logged["result"]["bytes_written"] == 2


def _wait_for_file(path: Path, timeout: float = 10.0) -> None:
    """Some hook events (Interrupt, SessionEnd) fire from a background
    task/exception handler rather than inline with a message the test can
    wait on with ws.receive_json() -- unlike a normal request/response
    exchange, there's no built-in synchronization point proving the
    server has actually finished running the hook by the time the test's
    own code continues, so this polls briefly instead of asserting
    immediately."""
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)


def _logging_hook_config(tmp_path: Path, event: str, log_path: Path) -> Path:
    """Same pattern as test_post_tool_use_hook_receives_the_tool_result
    above: a hook script that appends its received JSON payload to
    log_path, one line per invocation -- shared by the Phase 7 item 3
    event tests below (SessionEnd/UserPromptSubmit/PreCompact/
    PostCompact/Interrupt) since they all just need to prove the payload
    actually arrived, not exercise anything event-specific about how the
    hook script itself behaves."""
    hook_script = tmp_path / f"{event.lower()}_hook.py"
    hook_script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        f'open({str(log_path)!r}, "a").write(json.dumps(payload) + chr(10))\n'
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps({event: [f"{sys.executable} {hook_script}"]}), encoding="utf-8"
    )
    return hooks_path


def test_user_prompt_submit_hook_receives_the_message_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = tmp_path / "log.jsonl"
    hooks_path = _logging_hook_config(tmp_path, "UserPromptSubmit", log_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model, hooks_config_path=hooks_path) as client:
        with client.websocket_connect("/ws/t_hook_ups") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello there"})
            _receive_until(ws, "tasks_changed")

    logged = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert logged["event"] == "UserPromptSubmit"
    assert logged["text"] == "hello there"


def test_pre_and_post_compact_hooks_fire_around_manual_compact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pre_log = tmp_path / "pre.jsonl"
    post_log = tmp_path / "post.jsonl"
    pre_script = tmp_path / "pre_hook.py"
    pre_script.write_text(
        "import json, sys\n"
        "json.load(sys.stdin)\n"
        f'open({str(pre_log)!r}, "a").write("fired\\n")\n'
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    post_script = tmp_path / "post_hook.py"
    post_script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        f'open({str(post_log)!r}, "a").write(json.dumps(payload) + chr(10))\n'
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "PreCompact": [f"{sys.executable} {pre_script}"],
                "PostCompact": [f"{sys.executable} {post_script}"],
            }
        ),
        encoding="utf-8",
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content=f"reply {i}") for i in range(6)]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, hooks_config_path=hooks_path) as client:
        with client.websocket_connect("/ws/t_hook_compact") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            for i in range(3):
                ws.send_json({"type": "user_message", "text": f"message {i}"})
                _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "/compact"})
            compacted = ws.receive_json()
            assert compacted["type"] == "compacted"

    assert pre_log.read_text(encoding="utf-8").strip() == "fired"
    logged = json.loads(post_log.read_text(encoding="utf-8").strip())
    assert logged["event"] == "PostCompact"
    assert logged["before"] == compacted["before"]
    assert logged["after"] == compacted["after"]


def test_interrupt_hook_fires_on_a_stop_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = tmp_path / "log.jsonl"
    hooks_path = _logging_hook_config(tmp_path, "Interrupt", log_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model, hooks_config_path=hooks_path) as client:
        with client.websocket_connect("/ws/t_hook_interrupt") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "stop"})
            _wait_for_file(log_path)

    logged = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert logged["event"] == "Interrupt"
    assert logged["thread_id"] == "t_hook_interrupt"


def test_session_end_hook_fires_on_disconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = tmp_path / "log.jsonl"
    hooks_path = _logging_hook_config(tmp_path, "SessionEnd", log_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model, hooks_config_path=hooks_path) as client:
        with client.websocket_connect("/ws/t_hook_end") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            # Close explicitly and wait *inside* this `with` block, same
            # pattern as test_interrupt_hook_fires_on_a_stop_message right
            # above -- not incidental. Found live, genuinely flaky (not a
            # CI-only artifact -- reproduced locally too): starlette's own
            # WebSocketTestSession.__exit__ (relied on if this block is
            # left to close the socket implicitly on exit) sends the
            # disconnect message and then *immediately* hard-cancels the
            # whole in-flight ASGI app task via its cancel scope, racing
            # ahead of app.py's own `except WebSocketDisconnect:` handler
            # actually finishing `await session.run_session_end_hooks()`
            # (which shells out to a real subprocess) -- confirmed by
            # reading starlette/testclient.py's own __exit__/_run methods,
            # not guessed. Closing here instead means the wait below has
            # real, uncancelled wall-clock time for the hook to actually
            # run before this `with` block's own __exit__ can race it.
            ws.close()
            _wait_for_file(log_path)

    logged = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert logged["event"] == "SessionEnd"
    assert logged["thread_id"] == "t_hook_end"


def test_tool_raising_a_plain_exception_is_reported_to_the_model_not_a_crashed_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: create_agent's
    ToolNode only converts a tool's raised exception into an error
    ToolMessage for its own ToolInvocationError -- a plain ValueError
    (exactly what every built-in coscribe tool raises for an ordinary
    "bad input" case, e.g. read_file's "File does not exist: ...") used to
    propagate all the way out of astream()/ainvoke(), hit
    _handle_user_message_locked's except Exception, and end the turn dead
    with a bare {"type": "error"} message -- no agent_message, no
    tasks_changed, indistinguishable from the turn hanging forever. Fixed
    by _CatchToolErrorsMiddleware (runtime_lg/agent.py) -- this proves the
    turn now completes normally instead, with the model seeing the error
    and getting to respond."""
    call = _tool_call("call_1", "read_file", {"path": "sap_login.md"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="sorry, that file doesn't exist"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_tool_error") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "read sap_login.md"})
            messages = _receive_until(ws, "tasks_changed")

    assert not any(m["type"] == "error" for m in messages)
    tool_result = next(m for m in messages if m["type"] == "tool_result")
    assert tool_result["tool_name"] == "read_file"
    assert "File does not exist" in str(tool_result["result"])
    # This same ToolMessage.status the middleware sets to "error" (see this
    # test's own docstring) is also what the transcript's failure styling
    # keys off -- see wire.ts's ToolResultEvent.is_error.
    assert tool_result["is_error"] is True
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "sorry, that file doesn't exist"


def test_date_note_is_per_turn_message_content_not_baked_into_the_system_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caching regression test: the date note used to be glued onto the
    very front of the system prompt (recomputed only when the agent got
    rebuilt), which poisoned the entire prefix for any provider's
    prefix-based caching -- Anthropic cache_control, and just as much
    Gemini/OpenAI-compatible providers' automatic caching, no
    cache_control needed on their end for the damage to apply. Fixed by
    moving it into per-turn message content instead (see
    current_date_note's docstring in runtime_lg/messages.py) -- this
    tests both halves: the system prompt sent to the model stays frozen
    (no date in it), and the actual HumanMessage the model sees each turn
    still carries the date, so real-world grounding isn't lost."""
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_date_note") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

    assert len(fake_model.received) == 1
    sent_messages = fake_model.received[0]
    system_messages = [m for m in sent_messages if isinstance(m, SystemMessage)]
    assert system_messages, "expected a system message in the request"
    assert "Today's real date" not in system_messages[0].content

    human_messages = [m for m in sent_messages if isinstance(m, HumanMessage)]
    assert human_messages, "expected a human message in the request"
    today = datetime.now().strftime("%Y-%m-%d")
    assert human_messages[-1].content.startswith(f"Today's real date is {today} (")


def test_a_chinese_message_tells_the_model_to_narrate_in_chinese_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asked in Chinese, deepseek-flash wrote every between-step note of a
    fresh conversation in English; the note is what the model sees, never
    what the history or the sidebar show."""
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="好的")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_zh") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "帮我做 Q3 经营回顾"})
            _receive_until(ws, "tasks_changed")
        with client.websocket_connect("/ws/t_zh") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()
        threads = client.get("/api/threads").json()

    human = [m for m in fake_model.received[0] if isinstance(m, HumanMessage)][-1]
    assert "[The user writes in Chinese:" in human.content
    assert human.content.endswith("帮我做 Q3 经营回顾")
    user_entries = [e for e in history["entries"] if e["kind"] == "user"]
    assert [e["text"] for e in user_entries] == ["帮我做 Q3 经营回顾"]
    assert threads[0]["preview"].startswith("帮我做")


def test_exec_policy_allow_rule_skips_approval_and_runs_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_script_env(monkeypatch)
    policy_path = tmp_path / "exec_policy.json"
    policy_path.write_text(
        json.dumps({"rules": [{"pattern": r"print\(", "decision": "allow"}]}), encoding="utf-8"
    )
    call = _tool_call(
        "call_1", "run_python_script", {"script": "print('ok')", "description": "print ok"}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, exec_policy_path=policy_path) as client:
        with client.websocket_connect("/ws/t_exec1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "run it"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    assert "tool_result" in types

    entries = AuditLog(tmp_path / "state").read_all()
    assert len(entries) == 1
    assert entries[0].tool_name == "run_python_script"
    assert entries[0].decision == "approve"
    assert entries[0].reason == "exec_policy"


def test_exec_policy_forbidden_rule_rejects_without_approval_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_script_env(monkeypatch)
    policy_path = tmp_path / "exec_policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "pattern": r"shutil\.rmtree",
                        "decision": "forbidden",
                        "justification": "no bulk deletes",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    call = _tool_call(
        "call_1",
        "run_python_script",
        {"script": "import shutil; shutil.rmtree('/tmp/x')", "description": "delete a dir"},
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, exec_policy_path=policy_path) as client:
        with client.websocket_connect("/ws/t_exec2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "run it"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    tool_result = next(m for m in messages if m["type"] == "tool_result")
    assert "no bulk deletes" in json.dumps(tool_result)

    entries = AuditLog(tmp_path / "state").read_all()
    assert len(entries) == 1
    assert entries[0].tool_name == "run_python_script"
    assert entries[0].decision == "reject"
    assert entries[0].reason == "exec_policy"
    assert entries[0].detail == "no bulk deletes"


def test_exec_policy_no_match_falls_through_to_normal_approval_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_script_env(monkeypatch)
    policy_path = tmp_path / "exec_policy.json"
    policy_path.write_text(
        json.dumps({"rules": [{"pattern": r"this-does-not-appear", "decision": "allow"}]}),
        encoding="utf-8",
    )
    call = _tool_call(
        "call_1", "run_python_script", {"script": "print('ok')", "description": "print ok"}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, exec_policy_path=policy_path) as client:
        with client.websocket_connect("/ws/t_exec3") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "run it"})
            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            _receive_until(ws, "tasks_changed")

    entries = AuditLog(tmp_path / "state").read_all()
    assert entries[0].reason == "human"


def test_exec_policy_allow_does_not_override_plan_mode_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_script_env(monkeypatch)
    policy_path = tmp_path / "exec_policy.json"
    policy_path.write_text(
        json.dumps({"rules": [{"pattern": r"print\(", "decision": "allow"}]}), encoding="utf-8"
    )
    call = _tool_call(
        "call_1", "run_python_script", {"script": "print('ok')", "description": "print ok"}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, exec_policy_path=policy_path) as client:
        with client.websocket_connect("/ws/t_exec4") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/plan"})
            ws.receive_json()  # state (plan_mode: True)

            ws.send_json({"type": "user_message", "text": "run it"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    entries = AuditLog(tmp_path / "state").read_all()
    assert entries[0].decision == "reject"
    assert entries[0].reason == "plan_mode"


def test_reconnect_replays_a_sent_images_data_urls_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real, user-reported bug: an attached image rendered fine for the
    rest of the live WS connection that sent it (the browser's own
    optimistic local echo, still holding the data URL in memory), but
    vanished on reload/reconnect/thread-switch-and-back -- the image was
    already faithfully checkpointed (baked into the HumanMessage's own
    image_url content blocks), just never read back out by
    serialize_history_for_ws_lg. See messages.py's extract_images and
    this same function's docstring."""
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="nice photo")])
    data_url = "data:image/png;base64,aGVsbG8="
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_hist_images") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history -- empty, brand new thread
            ws.send_json({"type": "user_message", "text": "what's in this?", "images": [data_url]})
            _receive_until(ws, "tasks_changed")

        with client.websocket_connect("/ws/t_hist_images") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()

    assert history == {
        "type": "history",
        "entries": [
            {"kind": "user", "text": "what's in this?", "images": [data_url]},
            {"kind": "agent", "text": "nice photo"},
        ],
        "has_older": False,
    }


class _SlowSecondReplyModel(FakeToolCallingChatModel):
    """The second reply takes a while, so a test can open and close a tab
    while that turn is still going."""

    slow_started: list[bool] = []

    def _stream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Any:
        if self.i == 1:
            self.slow_started.append(True)
            time.sleep(1.0)
        yield from super()._stream(messages, stop, run_manager, **kwargs)


def test_closing_a_tab_on_a_run_being_looked_into_leaves_it_going_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.txt").write_text("one two", encoding="utf-8")
    fake_model = _SlowSecondReplyModel(
        responses=[_structured({"words": 0}), AIMessage(content="The file was empty.")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        task = _create_workflow_task(client)
        run = client.post(f"/api/scheduled-tasks/{task['trigger_id']}/run", json={}).json()["run"]
        _wait_for_run_status(client, task["trigger_id"], run["run_id"])
        base = f"/api/scheduled-tasks/{task['trigger_id']}/runs/{run['run_id']}"
        thread_id = client.post(f"{base}/investigate", json={}).json()["thread_id"]
        deadline = time.time() + 5
        while not fake_model.slow_started and time.time() < deadline:
            time.sleep(0.02)
        # A tab opens the conversation mid-turn and is closed again.
        with client.websocket_connect(f"/ws/{thread_id}") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
        time.sleep(1.5)
        with client.websocket_connect(f"/ws/{thread_id}") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()["entries"]

    assert any(e.get("text") == "The file was empty." for e in history), history


def test_a_tool_call_is_announced_before_it_runs_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "list_files", {"path": "."})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_tool_started") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "list files"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    started = messages[types.index("tool_started")]
    assert started["tool_name"] == "list_files"
    assert started["arguments"] == {"path": "."}
    assert types.index("tool_started") < types.index("tool_result")


def test_a_call_waiting_for_approval_is_shown_only_as_its_approval_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_tool_started_gated") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write it"})
            before = _receive_until(ws, "approval_required")
            ws.send_json({"type": "approval_response", "id": before[-1]["id"], "approved": True})
            after = _receive_until(ws, "tasks_changed")

    assert not any(m["type"] == "tool_started" for m in before + after)


def test_an_auto_approved_call_is_announced_before_it_runs_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_tool_started_auto") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/accept-edits"})
            ws.receive_json()  # state
            ws.send_json({"type": "user_message", "text": "write it"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    assert types.index("tool_started") < types.index("tool_result")
    assert messages[types.index("tool_started")]["tool_name"] == "write_file"


def test_the_chat_model_tests_its_draft_and_fixes_what_failed_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    ids = iter(["d1", "d2"])
    monkeypatch.setattr("coscribe.conversation.session._new_draft_id", lambda: next(ids))
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "input.txt").write_text("data", encoding="utf-8")

    def reading(path: str) -> dict[str, Any]:
        return {"steps": [{"id": "read", "kind": "tool", "title": "Read it", "tool": "read_file",
                           "args": {"path": path}, "save_as": "text"}]}  # fmt: skip

    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="", tool_calls=[_tool_call("c1", "read_file", {"path": "input.txt"})]
            ),
            AIMessage(content="", tool_calls=[_tool_call("c2", "draft_workflow", {"name": ""})]),
            AIMessage(content=json.dumps({"name": "Read", "workflow": reading("inptu.txt")})),
            AIMessage(
                content="", tool_calls=[_tool_call("c3", "test_workflow", {"draft_id": "d1"})]
            ),
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "c4",
                        "revise_workflow",
                        {"draft_id": "d1", "request": "step read failed: the file is input.txt"},
                    )
                ],
            ),
            AIMessage(
                content=json.dumps(
                    {"workflow": reading("input.txt"), "changes": ["Reads input.txt"], "notes": []}
                )
            ),
            AIMessage(
                content="", tool_calls=[_tool_call("c5", "test_workflow", {"draft_id": "d2"})]
            ),
            AIMessage(content="The test passed; review it on the card."),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_test_draft") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/accept-edits"})
            _receive_until(ws, "state")
            ws.send_json({"type": "user_message", "text": "read input.txt, then make it fixed"})
            messages: list[dict[str, Any]] = []
            while not messages or messages[-1]["type"] != "tasks_changed":
                messages.append(ws.receive_json())
                if messages[-1]["type"] == "approval_required":
                    assert messages[-1]["tool_name"] == "test_workflow"
                    ws.send_json(
                        {"type": "approval_response", "id": messages[-1]["id"], "approved": True}
                    )

    first, second = [
        m["result"]
        for m in messages
        if m["type"] == "tool_result" and m["tool_name"] == "test_workflow"
    ]
    assert first["draft_id"] == "d1" and first["status"] == "failed", first
    assert first["failed_step"] == "read" and "inptu.txt" in first["error"]
    assert "revise_workflow" in first["next"]
    [revised] = [
        m["result"]
        for m in messages
        if m["type"] == "tool_result" and m["tool_name"] == "revise_workflow"
    ]
    assert revised["draft_id"] == "d2" and "trigger_id" not in revised
    assert revised["changes"] == ["Reads input.txt"]
    assert "step read failed" in str(fake_model.received[5][-1].content)
    assert second["status"] == "passed", second
    assert second["steps"][0]["status"] == "done" and second["steps"][0]["output"] == "data"
    progress = [m for m in messages if m["type"] == "workflow_test_progress"]
    assert {m["tool_call_id"] for m in progress} == {"c3", "c5"}
    assert progress[-1]["done"] == progress[-1]["total"] == 1
    assert ScheduledTriggerStore(tmp_path / "state").list_all() == []


def test_testing_an_unknown_draft_says_so_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="", tool_calls=[_tool_call("c1", "test_workflow", {"draft_id": "nope"})]
            ),
            AIMessage(content="There's no such draft."),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_test_unknown") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "test it"})
            approval = _receive_until(ws, "approval_required")[-1]
            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            messages = _receive_until(ws, "tasks_changed")

    [result] = [m["result"] for m in messages if m["type"] == "tool_result"]
    assert result == {"status": "failed", "error": "There's no draft 'nope' in this conversation."}


def test_editing_a_saved_task_asks_the_user_with_the_whole_task_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore, create_trigger

    store = ScheduledTriggerStore(tmp_path / "state")
    task = create_trigger(
        store, name="Morning digest", kind="daily", at="09:00", prompt="Summarize the news."
    )
    edit = _tool_call("c1", "edit_scheduled_task", {"trigger_id": task.trigger_id, "at": "08:00"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[edit]), AIMessage(content="Left it as is.")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_edit_task") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "run it at 8 instead"})
            draft_event = _receive_until(ws, "task_draft_required")[-1]
            ws.send_json(
                {
                    "type": "question_response",
                    "id": draft_event["id"],
                    "answer": "The user dismissed the draft without saving it.",
                }
            )
            _receive_until(ws, "tasks_changed")

    draft = draft_event["draft"]
    assert draft["trigger_id"] == task.trigger_id
    assert draft["changed"] == ["at"]
    assert (draft["name"], draft["kind"], draft["at"]) == ("Morning digest", "daily", "08:00")
    assert draft["prompt"] == "Summarize the news."
    unchanged = store.load(task.trigger_id)
    assert unchanged is not None and unchanged.schedule.at == "09:00"


def test_accept_edits_still_asks_before_running_code_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _tool_call("c1", "run_python_script", {"script": "print(1)", "description": "print"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[run]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_accept_exec") as ws:
            _start_mode(ws, "/accept-edits")
            ws.send_json({"type": "user_message", "text": "run it"})
            approval = _receive_until(ws, "approval_required")[-1]
            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": False})
            _receive_until(ws, "tasks_changed")

    assert approval["tool_name"] == "run_python_script"


def test_auto_mode_runs_what_the_reviewer_allows_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[_write_call("c1", "note.txt")]),
            _verdict("allow", "writing the note the user asked for"),
            AIMessage(content="done"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_auto_allow") as ws:
            state = _start_mode(ws, "/auto")
            ws.send_json({"type": "user_message", "text": "write the note"})
            messages = _receive_until(ws, "tasks_changed")

    assert state["auto_mode"] is True
    assert not any(m["type"] == "approval_required" for m in messages)
    assert (tmp_path / "workspace" / "note.txt").read_text() == "hi"
    review_request = str(fake_model.received[1][-1].content)
    assert "write_file(" in review_request
    assert "write the note" in review_request


def test_auto_mode_refuses_what_the_reviewer_blocks_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[_write_call("c1", "old.txt")]),
            _verdict("block", "old.txt existed before this conversation."),
            AIMessage(content="I'll ask first."),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_auto_block") as ws:
            _start_mode(ws, "/auto")
            ws.send_json({"type": "user_message", "text": "overwrite old.txt"})
            messages = _receive_until(ws, "tasks_changed")

    assert not any(m["type"] == "approval_required" for m in messages)
    assert not (tmp_path / "workspace" / "old.txt").exists()
    refusal = str(fake_model.received[2][-1].content)
    assert "Auto mode blocked this: old.txt existed before this conversation." in refusal


def test_auto_mode_asks_after_three_blocks_in_a_row_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[_write_call("c1", "a.txt")]),
            _verdict("block", "no"),
            AIMessage(content="", tool_calls=[_write_call("c2", "b.txt")]),
            _verdict("block", "no"),
            AIMessage(content="", tool_calls=[_write_call("c3", "c.txt")]),
            _verdict("block", "still no"),
            AIMessage(content="done"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_auto_fallback") as ws:
            _start_mode(ws, "/auto")
            ws.send_json({"type": "user_message", "text": "write the files"})
            approval = _receive_until(ws, "approval_required")[-1]
            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            _receive_until(ws, "tasks_changed")

    assert approval["arguments"]["path"] == "c.txt"
    assert "still no" in approval["reviewer_note"]
    assert (tmp_path / "workspace" / "c.txt").exists()
    assert not (tmp_path / "workspace" / "a.txt").exists()


def test_approving_a_plan_leaves_plan_mode_for_auto_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exit_plan = _tool_call("c1", "exit_plan_mode", {"plan": "1. Write note.txt"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[exit_plan]), AIMessage(content="On it.")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_plan_exit") as ws:
            _start_mode(ws, "/plan")
            ws.send_json({"type": "user_message", "text": "plan the note"})
            plan = _receive_until(ws, "plan_ready")[-1]
            ws.send_json({"type": "question_response", "id": plan["id"], "answer": "auto"})
            messages = _receive_until(ws, "tasks_changed")

    assert plan["plan"] == "1. Write note.txt"
    state = next(m for m in messages if m["type"] == "state")
    assert (state["plan_mode"], state["auto_mode"]) == (False, True)
    result = str(fake_model.received[1][-1].content)
    assert "approved the plan and switched to auto mode" in result


class _IndexedToolCallModel(FakeToolCallingChatModel):
    """Streams tool calls the way real providers do: numbered from index 0
    in every response, the arguments split across chunks where only the
    first carries the call's id and name."""

    def _stream(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        self.received.append(list(messages))
        message = self.responses[self.i]
        self.i += 1
        yield ChatGenerationChunk(message=AIMessageChunk(content=message.content or ""))
        for index, call in enumerate(message.tool_calls):
            text = json.dumps(call["args"])
            half = len(text) // 2
            for piece, first in ((text[:half], True), (text[half:], False)):
                chunk = {
                    "name": call["name"] if first else None,
                    "args": piece,
                    "id": call["id"] if first else None,
                    "index": index,
                }
                yield ChatGenerationChunk(
                    message=AIMessageChunk(content="", tool_call_chunks=[chunk])  # type: ignore[list-item]
                )


def test_each_tool_result_carries_its_own_arguments_across_responses_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace" / "a.txt").write_text("A", encoding="utf-8")
    (tmp_path / "workspace" / "b.txt").write_text("B", encoding="utf-8")
    fake_model = _IndexedToolCallModel(
        responses=[
            AIMessage(content="", tool_calls=[_tool_call("c1", "read_file", {"path": "a.txt"})]),
            AIMessage(content="", tool_calls=[_tool_call("c2", "read_file", {"path": "b.txt"})]),
            AIMessage(content="done"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_two_reads") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "user_message", "text": "read a then b"})
            messages = _receive_until(ws, "tasks_changed")

    results = [m["arguments"] for m in messages if m["type"] == "tool_result"]
    assert results == [{"path": "a.txt"}, {"path": "b.txt"}]


def test_a_new_conversation_starts_in_the_default_mode_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model, default_permission_mode="auto") as client:
        with client.websocket_connect("/ws/t_default_auto") as ws:
            chat = ws.receive_json()
        with client.websocket_connect("/ws/scheduled-trig_x-run1") as ws:
            run = ws.receive_json()

    assert (chat["plan_mode"], chat["accept_edits"], chat["auto_mode"]) == (False, False, True)
    assert (run["plan_mode"], run["accept_edits"], run["auto_mode"]) == (False, False, False)


def test_the_default_mode_is_set_live_and_checked_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        bad = client.post(
            "/api/config", json={"updates": {"COSCRIBE_DEFAULT_PERMISSION_MODE": "yolo"}}
        )
        good = client.post(
            "/api/config", json={"updates": {"COSCRIBE_DEFAULT_PERMISSION_MODE": "plan"}}
        )
        with client.websocket_connect("/ws/t_default_plan") as ws:
            state = ws.receive_json()

    assert "COSCRIBE_DEFAULT_PERMISSION_MODE" in bad.json()["rejected"]
    assert good.json()["restart_required"] is False
    assert state["plan_mode"] is True


def test_modes_are_one_at_a_time_lg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_modes") as ws:
            plan = _start_mode(ws, "/plan")
            ws.send_json({"type": "user_message", "text": "/auto"})
            auto = ws.receive_json()
            ws.send_json({"type": "user_message", "text": "/auto"})
            manual = ws.receive_json()

    assert (plan["plan_mode"], plan["auto_mode"]) == (True, False)
    assert (auto["plan_mode"], auto["accept_edits"], auto["auto_mode"]) == (False, False, True)
    assert (manual["plan_mode"], manual["accept_edits"], manual["auto_mode"]) == (
        False,
        False,
        False,
    )
