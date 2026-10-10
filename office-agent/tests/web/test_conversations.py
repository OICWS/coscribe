"""A conversation that is open takes a message another one sent it as a turn
of its own."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langchain_core.messages import AIMessage, BaseMessage

from coscribe.tools.conversation_messages import (
    ConversationMessages,
    build_conversation_message_tools,
)

from .helpers import _client_lg, _receive_until, _start_mode, _tool_call, _verdict
from .test_subagents import RoutedChatModel


def test_a_message_sent_from_one_open_conversation_becomes_a_turn_in_the_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ConversationMessages(tmp_path / "state").set_accepts("t_receiver", True)

    def route(messages: list[BaseMessage]) -> AIMessage:
        system = str(messages[0].content) if messages[0].type == "system" else ""
        last = messages[-1]
        if "You review one action" in system:
            return _verdict("allow", "the user asked for this message")
        if last.type == "human" and "[Message from another conversation]" in str(last.content):
            return AIMessage(content="Noted: the totals are ready.")
        if last.type == "tool":
            return AIMessage(content="Sent it.")
        return AIMessage(
            content="",
            tool_calls=[
                _tool_call(
                    "m1",
                    "send_to_conversation",
                    {"thread_id_to": "t_receiver", "text": "The totals are ready."},
                )
            ],
        )

    fake_model = RoutedChatModel(responses=[], route=route)
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_receiver") as receiver:
            _start_mode(receiver, "/auto")
            with client.websocket_connect("/ws/t_sender") as sender:
                _start_mode(sender, "/auto")
                sender.send_json({"type": "user_message", "text": "Tell the other one."})
                _receive_until(sender, "agent_message")
            started = _receive_until(receiver, "turn_started")[-1]
            reply = _receive_until(receiver, "agent_message")[-1]

    assert started["text"].startswith("[Message from another conversation]")
    assert "The totals are ready." in started["text"]
    assert reply["text"] == "Noted: the totals are ready."
    assert ConversationMessages(tmp_path / "state").pending("t_receiver") == 0


def test_a_message_that_arrived_while_the_conversation_was_closed_is_taken_when_it_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages = ConversationMessages(tmp_path / "state")
    messages.set_accepts("t_closed", True)
    _, sending = build_conversation_message_tools(tmp_path / "state", "t_other")
    sending("t_closed", "Please check the totals.")

    def route(messages: list[BaseMessage]) -> AIMessage:
        return AIMessage(content="Will do.")

    with _client_lg(tmp_path, monkeypatch, RoutedChatModel(responses=[], route=route)) as client:
        with client.websocket_connect("/ws/t_closed") as ws:
            started = _receive_until(ws, "turn_started")[-1]
            reply = _receive_until(ws, "agent_message")[-1]

    assert "Please check the totals." in started["text"]
    assert reply["text"] == "Will do."
