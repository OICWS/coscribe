"""Conversations messaging each other: who can be written to, what crosses,
and that the text is blanked of secret values."""

from __future__ import annotations

from pathlib import Path

import keyring.errors
import pytest

from coscribe.runtime import secrets
from coscribe.runtime.secret_store import SecretStore
from coscribe.tools.conversation_messages import (
    MAX_INBOX,
    MAX_MESSAGE_CHARS,
    ConversationMessages,
    build_conversation_message_tools,
    message_turn_text,
)


class _FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.errors = keyring.errors

    def set_password(self, service: str, ref: str, value: str) -> None:
        self.store[(service, ref)] = value

    def get_password(self, service: str, ref: str) -> str | None:
        return self.store.get((service, ref))

    def delete_password(self, service: str, ref: str) -> None:
        self.store.pop((service, ref), None)


@pytest.fixture(autouse=True)
def keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets, "keyring", _FakeKeyring())
    monkeypatch.setattr(secrets, "keychain_backend_usable", lambda: True)


def _tools(
    state: Path, thread_id: str, delivered: list[str] | None = None
) -> tuple[object, object]:
    deliver = delivered.append if delivered is not None else None
    listing, sending = build_conversation_message_tools(state, thread_id, deliver)
    return listing, sending


def test_no_conversation_accepts_messages_until_the_user_allows_it(tmp_path: Path) -> None:
    messages = ConversationMessages(tmp_path)
    listing, sending = _tools(tmp_path, "a")

    assert listing() == []  # type: ignore[operator]
    assert sending("b", "hello").startswith("Not sent")  # type: ignore[operator]
    assert messages.pending("b") == 0


def test_the_list_holds_only_other_conversations_that_accept(tmp_path: Path) -> None:
    messages = ConversationMessages(tmp_path)
    messages.set_accepts("a", True)
    messages.set_accepts("b", True)
    messages.set_accepts("c", False)
    (tmp_path / "b.title").write_text("Sales deck", encoding="utf-8")
    listing, _ = _tools(tmp_path, "a")

    assert listing() == [{"thread_id": "b", "title": "Sales deck"}]  # type: ignore[operator]


def test_a_message_waits_in_the_inbox_and_is_taken_once(tmp_path: Path) -> None:
    messages = ConversationMessages(tmp_path)
    messages.set_accepts("b", True)
    (tmp_path / "a.title").write_text("Excel analysis", encoding="utf-8")
    delivered: list[str] = []
    _, sending = _tools(tmp_path, "a", delivered)

    result = sending("b", "  the totals are in sheet 2  ")  # type: ignore[operator]

    assert result.startswith("Sent")
    assert delivered == ["b"]
    assert messages.pending("b") == 1
    taken = messages.take("b")
    assert [m["text"] for m in taken] == ["the totals are in sheet 2"]
    assert taken[0]["from_thread"] == "a"
    assert taken[0]["from_title"] == "Excel analysis"
    assert messages.take("b") == []


def test_what_cannot_be_sent_says_why_and_delivers_nothing(tmp_path: Path) -> None:
    messages = ConversationMessages(tmp_path)
    messages.set_accepts("a", True)
    messages.set_accepts("b", True)
    delivered: list[str] = []
    _, sending = _tools(tmp_path, "a", delivered)

    refused = [
        sending("a", "to myself"),  # type: ignore[operator]
        sending("../b", "path"),  # type: ignore[operator]
        sending("b", "   "),  # type: ignore[operator]
        sending("b", "x" * (MAX_MESSAGE_CHARS + 1)),  # type: ignore[operator]
    ]

    assert all(r.startswith("Not sent") for r in refused)
    assert delivered == []
    assert messages.pending("b") == 0


def test_a_full_inbox_refuses_more(tmp_path: Path) -> None:
    messages = ConversationMessages(tmp_path)
    messages.set_accepts("b", True)
    _, sending = _tools(tmp_path, "a")

    for _ in range(MAX_INBOX):
        assert sending("b", "hi").startswith("Sent")  # type: ignore[operator]

    assert sending("b", "one more").startswith("Not sent")  # type: ignore[operator]
    assert messages.pending("b") == MAX_INBOX


def test_a_secret_value_in_the_text_is_blanked_before_it_is_kept(tmp_path: Path) -> None:
    value = "sk-conv-abcdef123456"
    SecretStore(tmp_path).save("SVC_KEY", value, ["api.example.com"])
    messages = ConversationMessages(tmp_path)
    messages.set_accepts("b", True)
    _, sending = _tools(tmp_path, "a")

    sending("b", f"use the key {value} for the export")  # type: ignore[operator]

    kept = messages.take("b")[0]["text"]
    assert value not in kept
    assert "use the key" in kept


def test_the_turn_text_says_it_is_not_from_the_user(tmp_path: Path) -> None:
    text = message_turn_text(
        [{"from_thread": "a", "from_title": "Excel analysis", "text": "totals ready"}]
    )

    assert text.startswith('[Message from another conversation] "Excel analysis"')
    assert "totals ready" in text
    assert "not from the user" in text
