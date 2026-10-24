import asyncio
import json
from pathlib import Path

import keyring.errors
import pytest

from coscribe.runtime import SecretStore, secrets
from coscribe.tools.bookkeeping.conversations import (
    MAX_MESSAGE_CHARS,
    MAX_READ_MESSAGE_CHARS,
    ConversationTranscript,
    MessagingError,
    MessagingStore,
    build_conversation_tools,
    format_incoming,
)


class _Source:
    def __init__(self, transcripts: dict[str, ConversationTranscript]) -> None:
        self.transcripts = transcripts

    async def thread_ids(self) -> list[str]:
        return sorted(self.transcripts)

    async def transcript(self, thread_id: str) -> ConversationTranscript | None:
        return self.transcripts.get(thread_id)


def _talk(*messages: tuple[str, str], at: str = "2026-10-23T10:00:00Z") -> ConversationTranscript:
    return ConversationTranscript(updated_at=at, messages=list(messages))


def _tools(state: Path, thread_id: str, *others: str) -> dict[str, object]:
    source = _Source({other: _talk(("user", "hi")) for other in (thread_id, *others)})
    return _tools_over(state, thread_id, source)


def _tools_over(state: Path, thread_id: str, source: _Source) -> dict[str, object]:
    return {tool.__name__: tool for tool in build_conversation_tools(thread_id, state, source)}  # type: ignore[attr-defined]


def _call(tool: object, *args: object) -> object:
    return asyncio.run(tool(*args))  # type: ignore[operator]


def _archive(state: Path, thread_id: str) -> None:
    (state / f"{thread_id}.meta.json").write_text(json.dumps({"archived": True}), encoding="utf-8")


def test_every_other_conversation_is_listed_newest_first_without_asking_anyone(
    tmp_path: Path,
) -> None:
    (tmp_path / "b.title").write_text("Quarterly deck\n", encoding="utf-8")
    source = _Source(
        {
            "a": _talk(("user", "hi")),
            "b": _talk(("user", "one"), ("assistant", "two"), at="2026-10-23T09:00:00Z"),
            "c": _talk(("user", "x"), at="2026-10-23T11:00:00Z"),
        }
    )

    listed = _call(_tools_over(tmp_path, "a", source)["list_conversations"])

    assert listed == [
        {"id": "c", "title": "c", "updated_at": "2026-10-23T11:00:00Z", "messages": 1},
        {"id": "b", "title": "Quarterly deck", "updated_at": "2026-10-23T09:00:00Z", "messages": 2},
    ]


def test_an_archived_conversation_cannot_be_listed_read_or_messaged(tmp_path: Path) -> None:
    _archive(tmp_path, "b")
    tools = _tools(tmp_path, "a", "b", "c")

    assert [c["id"] for c in _call(tools["list_conversations"])] == ["c"]  # type: ignore[attr-defined]
    with pytest.raises(MessagingError):
        _call(tools["read_conversation"], "b")
    with pytest.raises(MessagingError):
        _call(tools["send_to_conversation"], "b", "hi")
    assert MessagingStore(tmp_path).pending("b") == 0


def test_reading_returns_the_latest_messages_and_how_many_there_are(tmp_path: Path) -> None:
    source = _Source(
        {
            "a": _talk(),
            "b": _talk(*[("user" if i % 2 == 0 else "assistant", f"m{i}") for i in range(30)]),
        }
    )
    read = _tools_over(tmp_path, "a", source)["read_conversation"]

    default = _call(read, "b")
    few = _call(read, "b", 2)

    assert default["total_messages"] == 30  # type: ignore[index]
    assert [m["text"] for m in default["messages"]] == [f"m{i}" for i in range(10, 30)]  # type: ignore[index]
    assert [m["text"] for m in few["messages"]] == ["m28", "m29"]  # type: ignore[index]
    assert few["messages"][0]["role"] == "user"  # type: ignore[index]


def test_a_long_message_is_cut_when_read(tmp_path: Path) -> None:
    source = _Source({"a": _talk(), "b": _talk(("assistant", "x" * 5000))})

    result = _call(_tools_over(tmp_path, "a", source)["read_conversation"], "b")

    assert len(result["messages"][0]["text"]) == MAX_READ_MESSAGE_CHARS  # type: ignore[index]


def test_reading_refuses_itself_a_path_and_an_unknown_conversation(tmp_path: Path) -> None:
    read = _tools(tmp_path, "a", "b")["read_conversation"]

    for target in ("a", "../x", "", "never-opened"):
        with pytest.raises(MessagingError):
            _call(read, target)


def test_without_a_source_no_conversation_is_reachable(tmp_path: Path) -> None:
    tools = {t.__name__: t for t in build_conversation_tools("a", tmp_path)}  # type: ignore[attr-defined]

    assert _call(tools["list_conversations"]) == []
    with pytest.raises(MessagingError):
        _call(tools["send_to_conversation"], "b", "hi")


def test_sending_puts_the_text_in_the_receivers_inbox(tmp_path: Path) -> None:
    (tmp_path / "a.title").write_text("Excel analysis", encoding="utf-8")
    store = MessagingStore(tmp_path)

    result = _call(
        _tools(tmp_path, "a", "b")["send_to_conversation"], "b", "The totals are in col F."
    )
    messages = store.take_all("b")

    assert result["sent"] is True  # type: ignore[index]
    assert [(m["from_id"], m["from_title"], m["text"]) for m in messages] == [
        ("a", "Excel analysis", "The totals are in col F.")
    ]
    assert store.take_all("b") == []


def test_a_conversation_cannot_message_itself_or_use_a_path_as_an_id(tmp_path: Path) -> None:
    send = _tools(tmp_path, "a")["send_to_conversation"]

    for target in ("a", "../x", ""):
        with pytest.raises(MessagingError):
            _call(send, target, "hi")


def test_empty_and_overlong_messages_are_refused(tmp_path: Path) -> None:
    send = _tools(tmp_path, "a", "b")["send_to_conversation"]

    with pytest.raises(MessagingError):
        _call(send, "b", "   ")
    with pytest.raises(MessagingError):
        _call(send, "b", "x" * (MAX_MESSAGE_CHARS + 1))


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
    leak = _talk(("assistant", "the key is sk-live-0123456789abcdef"))
    source = _Source({"a": _talk(), "b": leak})
    tools = _tools_over(tmp_path, "a", source)

    _call(tools["send_to_conversation"], "b", "use sk-live-0123456789abcdef please")
    sent = MessagingStore(tmp_path).take_all("b")[0]["text"]
    read = _call(tools["read_conversation"], "b")["messages"][0]["text"]  # type: ignore[index]

    assert "sk-live-0123456789abcdef" not in sent
    assert "sk-live-0123456789abcdef" not in read


def test_an_inbox_that_is_never_read_stops_growing(tmp_path: Path) -> None:
    send = _tools(tmp_path, "a", "b")["send_to_conversation"]

    with pytest.raises(MessagingError):
        for _ in range(100):
            _call(send, "b", "again")


def test_the_receiver_is_told_who_wrote_and_that_it_is_not_the_user() -> None:
    text = format_incoming(
        [{"from_id": "a", "from_title": "Excel analysis", "text": "Totals are in col F."}]
    )

    assert 'from "Excel analysis"' in text
    assert "Totals are in col F." in text
    assert "not from the user" in text


def test_deleting_a_conversation_removes_its_inbox(tmp_path: Path) -> None:
    store = MessagingStore(tmp_path)
    _call(_tools(tmp_path, "a", "b")["send_to_conversation"], "b", "hi")

    store.delete("b")

    assert store.pending("b") == 0


def test_a_non_numeric_count_falls_back_to_the_default(tmp_path: Path) -> None:
    source = _Source({"a": _talk(), "b": _talk(*[("user", f"m{i}") for i in range(30)])})

    result = _call(_tools_over(tmp_path, "a", source)["read_conversation"], "b", "many")

    assert len(result["messages"]) == 20  # type: ignore[index]
