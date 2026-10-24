# ruff: noqa: E402
"""Web tests: messages between conversations."""

from pathlib import Path

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langchain_core.messages import (
    AIMessage,
)

from .helpers import (
    FakeToolCallingChatModel,
    _client_lg,
    _receive_until,
    _send_between,
)


def test_a_message_from_another_conversation_becomes_a_turn_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="Noted the totals.")])
    state_dir = tmp_path / "state"
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/b") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            # A sync tool runs in a worker thread, as it does inside a turn.
            _send_between(client, state_dir, "a", "b", "The totals are in col F.")
            messages = _receive_until(ws, "tasks_changed")

    started = next(m for m in messages if m["type"] == "turn_started")
    assert 'from "a"' in started["text"] or "(conversation a)" in started["text"]
    assert "The totals are in col F." in started["text"]
    reply = next(m for m in messages if m["type"] == "agent_message")
    assert reply["text"] == "Noted the totals."
    seen = fake_model.received[0][-1].content
    assert "The totals are in col F." in str(seen)


def test_a_message_to_a_conversation_nobody_has_opened_waits_until_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.bookkeeping.conversations import MessagingStore

    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="Read it.")])
    state_dir = tmp_path / "state"
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        _send_between(client, state_dir, "a", "b", "Please check sheet 2.")
        assert MessagingStore(state_dir).pending("b") == 1

        with client.websocket_connect("/ws/b") as ws:
            ws.receive_json()
            ws.receive_json()
            messages = _receive_until(ws, "tasks_changed")

    assert any("Please check sheet 2." in m.get("text", "") for m in messages)
    assert MessagingStore(state_dir).pending("b") == 0


def test_messages_stop_after_the_cap_until_the_user_speaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.conversation.session import _MAX_REPORT_TURNS_IN_ROW
    from coscribe.tools.bookkeeping.conversations import MessagingStore

    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content=f"reply {i}") for i in range(_MAX_REPORT_TURNS_IN_ROW + 2)]
    )
    state_dir = tmp_path / "state"
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/b") as ws:
            ws.receive_json()
            ws.receive_json()
            for i in range(_MAX_REPORT_TURNS_IN_ROW):
                _send_between(client, state_dir, "a", "b", f"ping {i}")
                _receive_until(ws, "tasks_changed")

            _send_between(client, state_dir, "a", "b", "one too many")
            ws.send_json({"type": "user_message", "text": "hello"})
            messages = _receive_until(ws, "tasks_changed")
            more = _receive_until(ws, "tasks_changed")

    assert any(m["type"] == "agent_message" for m in messages)
    assert any("one too many" in m.get("text", "") for m in [*messages, *more])
    assert MessagingStore(state_dir).pending("b") == 0
