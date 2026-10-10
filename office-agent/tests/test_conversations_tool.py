from pathlib import Path

import keyring.errors
import pytest

from coscribe.runtime import secrets
from coscribe.runtime.secret_store import SecretStore
from coscribe.tools.conversations import (
    MAX_MESSAGE_CHARS,
    MessagingError,
    MessagingStore,
    build_conversation_tools,
    format_incoming,
)


def _tools(state: Path, thread_id: str) -> dict[str, object]:
    return {tool.__name__: tool for tool in build_conversation_tools(thread_id, state)}  # type: ignore[attr-defined]


def test_a_conversation_allows_nobody_until_the_user_says_so(tmp_path: Path) -> None:
    store = MessagingStore(tmp_path)

    assert store.get_policy("b") == {"mode": "off", "senders": []}
    assert not store.allows("b", "a")
    assert _tools(tmp_path, "a")["list_conversations"]() == []  # type: ignore[operator]


def test_any_lets_every_other_conversation_in_but_not_itself(tmp_path: Path) -> None:
    store = MessagingStore(tmp_path)
    store.set_policy("b", "any", [])

    assert store.allows("b", "a")
    assert not store.allows("b", "b")


def test_selected_lets_only_the_named_conversations_in(tmp_path: Path) -> None:
    store = MessagingStore(tmp_path)
    store.set_policy("b", "selected", ["a"])

    assert store.allows("b", "a")
    assert not store.allows("b", "c")


def test_list_conversations_shows_only_those_that_allowed_the_sender(tmp_path: Path) -> None:
    store = MessagingStore(tmp_path)
    store.set_policy("b", "any", [])
    store.set_policy("c", "selected", ["x"])
    (tmp_path / "b.title").write_text("Quarterly deck\n", encoding="utf-8")

    listed = _tools(tmp_path, "a")["list_conversations"]()  # type: ignore[operator]

    assert listed == [{"id": "b", "title": "Quarterly deck"}]


def test_sending_puts_the_text_in_the_receivers_inbox(tmp_path: Path) -> None:
    store = MessagingStore(tmp_path)
    store.set_policy("b", "any", [])
    (tmp_path / "a.title").write_text("Excel analysis", encoding="utf-8")

    result = _tools(tmp_path, "a")["send_to_conversation"]("b", "The totals are in col F.")  # type: ignore[operator]
    messages = store.take_all("b")

    assert result["sent"] is True
    assert [(m["from_id"], m["from_title"], m["text"]) for m in messages] == [
        ("a", "Excel analysis", "The totals are in col F.")
    ]
    assert store.take_all("b") == []


def test_sending_to_a_conversation_that_did_not_allow_it_is_refused(tmp_path: Path) -> None:
    store = MessagingStore(tmp_path)
    store.set_policy("b", "selected", ["other"])
    send = _tools(tmp_path, "a")["send_to_conversation"]

    with pytest.raises(MessagingError):
        send("b", "hi")  # type: ignore[operator]
    with pytest.raises(MessagingError):
        send("never-opened", "hi")  # type: ignore[operator]
    assert store.pending("b") == 0


def test_a_conversation_cannot_message_itself_or_use_a_path_as_an_id(tmp_path: Path) -> None:
    store = MessagingStore(tmp_path)
    store.set_policy("a", "any", [])
    send = _tools(tmp_path, "a")["send_to_conversation"]

    for target in ("a", "../x", ""):
        with pytest.raises(MessagingError):
            send(target, "hi")  # type: ignore[operator]


def test_empty_and_overlong_messages_are_refused(tmp_path: Path) -> None:
    MessagingStore(tmp_path).set_policy("b", "any", [])
    send = _tools(tmp_path, "a")["send_to_conversation"]

    with pytest.raises(MessagingError):
        send("b", "   ")  # type: ignore[operator]
    with pytest.raises(MessagingError):
        send("b", "x" * (MAX_MESSAGE_CHARS + 1))  # type: ignore[operator]


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


def test_a_stored_secret_value_is_redacted_before_it_leaves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(secrets, "keyring", _FakeKeyring())
    monkeypatch.setattr(secrets, "keychain_backend_usable", lambda: True)
    SecretStore(tmp_path).save("API_KEY", "sk-live-0123456789abcdef", ["example.com"])
    MessagingStore(tmp_path).set_policy("b", "any", [])

    _tools(tmp_path, "a")["send_to_conversation"]("b", "use sk-live-0123456789abcdef please")  # type: ignore[operator]
    text = MessagingStore(tmp_path).take_all("b")[0]["text"]

    assert "sk-live-0123456789abcdef" not in text


def test_an_inbox_that_is_never_read_stops_growing(tmp_path: Path) -> None:
    store = MessagingStore(tmp_path)
    store.set_policy("b", "any", [])
    send = _tools(tmp_path, "a")["send_to_conversation"]

    with pytest.raises(MessagingError):
        for _ in range(100):
            send("b", "again")  # type: ignore[operator]


def test_the_receiver_is_told_who_wrote_and_that_it_is_not_the_user() -> None:
    text = format_incoming(
        [{"from_id": "a", "from_title": "Excel analysis", "text": "Totals are in col F."}]
    )

    assert 'from "Excel analysis"' in text
    assert "Totals are in col F." in text
    assert "not from the user" in text


def test_deleting_a_conversation_removes_its_policy_and_inbox(tmp_path: Path) -> None:
    store = MessagingStore(tmp_path)
    store.set_policy("b", "any", [])
    _tools(tmp_path, "a")["send_to_conversation"]("b", "hi")  # type: ignore[operator]

    store.delete("b")

    assert store.get_policy("b")["mode"] == "off" and store.pending("b") == 0


def test_a_bad_policy_is_refused(tmp_path: Path) -> None:
    store = MessagingStore(tmp_path)

    for mode, senders in (("everyone", []), ("any", "a"), ("selected", ["../x"])):
        with pytest.raises(MessagingError):
            store.set_policy("b", mode, senders)
