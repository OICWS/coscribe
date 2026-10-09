"""What a conversation sends the model, checked for what providers cache:
they cache by prefix (system prompt, then the tool list, then the
messages), so anything that differs between conversations or turns has to
come after everything that doesn't."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from coscribe.config import Settings
from coscribe.conversation.session import ChatSessionLG
from coscribe.runtime import empty_hooks_config
from coscribe.runtime_lg.messages import (
    context_note,
    last_conversation_context,
    strip_mode_note,
)


class _RecordingModel(BaseChatModel):
    responses: list[AIMessage]
    i: int = 0
    received: list[list[BaseMessage]] = []

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        return self

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.received.append(list(messages))
        message = self.responses[self.i]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Any:
        self.received.append(list(messages))
        message = self.responses[self.i]
        self.i += 1
        yield ChatGenerationChunk(message=AIMessageChunk(content=message.content or ""))

    @property
    def _llm_type(self) -> str:
        return "recording"


class _Socket:
    async def send_json(self, data: dict[str, Any]) -> None:
        pass


class _ContextWindow:
    def get_context_window(self, model: str) -> int:
        return 1_000_000


def _session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: _RecordingModel,
    thread: str,
    folder: Path,
) -> ChatSessionLG:
    monkeypatch.setattr(
        "coscribe.conversation.session.resolve_chat_model",
        lambda name, custom_providers=None: model,
    )
    (tmp_path / "MEMORY.md").write_text("- The user prefers metric units.\n", encoding="utf-8")
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        default_model="fake:model",
        workspace_root=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        skills_dir=tmp_path / "skills",
        memory_path=tmp_path / "MEMORY.md",
        auto_title_threads=False,
    )
    folder.mkdir(parents=True, exist_ok=True)
    return ChatSessionLG(
        thread_id=thread,
        settings=settings,
        context_window_client=_ContextWindow(),
        custom_providers={},
        extra_tools=[],
        checkpointer=InMemorySaver(),
        hooks_config=empty_hooks_config(),
        enabled_skill_names=set(),
        workspace_root=folder,
    )


def _last_user_text(model: _RecordingModel) -> str:
    human = [m for m in model.received[-1] if isinstance(m, HumanMessage)][-1]
    return str(human.content)


async def test_the_system_prompt_is_identical_across_conversations_with_other_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DeepSeek caches system prompt, then tools, then messages: a folder
    path in the system prompt made a conversation in another folder re-read
    the rest of it and ~35k tokens of tool schemas uncached."""
    model = _RecordingModel(responses=[AIMessage(content="a"), AIMessage(content="b")])
    one = _session(tmp_path, monkeypatch, model, "t1", tmp_path / "reports")
    two = _session(tmp_path, monkeypatch, model, "t2", tmp_path / "invoices")

    await one.handle_user_message("hello", _Socket())  # type: ignore[arg-type]
    await two.handle_user_message("hello", _Socket())  # type: ignore[arg-type]

    first_system, second_system = (calls[0].content for calls in model.received)
    assert first_system == second_system
    assert str(tmp_path / "reports") not in str(first_system)
    assert "metric units" not in str(first_system)


async def test_the_context_is_sent_once_and_again_when_it_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = _RecordingModel(responses=[AIMessage(content=str(i)) for i in range(3)])
    session = _session(tmp_path, monkeypatch, model, "t1", tmp_path / "reports")

    await session.handle_user_message("first", _Socket())  # type: ignore[arg-type]
    first = _last_user_text(model)
    await session.handle_user_message("second", _Socket())  # type: ignore[arg-type]
    second = _last_user_text(model)
    (tmp_path / "invoices").mkdir()
    await session.set_folders([str(tmp_path / "invoices")], _Socket())  # type: ignore[arg-type]
    await session.handle_user_message("third", _Socket())  # type: ignore[arg-type]
    third = _last_user_text(model)

    assert "[Conversation context]" in first and str(tmp_path / "reports") in first
    assert "metric units" in first
    assert "[Conversation context]" not in second
    assert str(tmp_path / "invoices") in third
    assert [strip_mode_note(t) for t in (first, second, third)] == ["first", "second", "third"]


def test_last_conversation_context_reads_the_most_recent_note() -> None:
    old = HumanMessage(context_note("folders: /a") + "hi")
    plain = HumanMessage("no note here")
    new = HumanMessage(
        "Today's real date is 2026-10-05 (Monday); local time 14:05 (UTC+08:00). "
        + context_note("folders: /b")
        + "next"
    )

    assert last_conversation_context([old, plain, new]) == "folders: /b"
    assert last_conversation_context([old, plain]) == "folders: /a"
    assert last_conversation_context([plain]) is None


def _deepseek() -> Any:
    from coscribe.runtime_lg.providers import resolve_chat_model

    return resolve_chat_model(
        "deepseek:deepseek-flash",
        {"deepseek": {"base_url": "https://api.deepseek.com/v1", "api_key": "k"}},
    )


def test_deepseek_reasoning_is_kept_and_sent_back() -> None:
    """DeepSeek wants reasoning_content back on every request with tools;
    without it each new turn re-read the conversation uncached (10,478 of
    11,246 prompt tokens missed, 191 with it)."""
    from langchain_core.messages import AIMessageChunk, ToolMessage

    model = _deepseek()
    streamed = model._convert_chunk_to_generation_chunk(
        {"choices": [{"delta": {"role": "assistant", "reasoning_content": "check the sheet"}}]},
        AIMessageChunk,
        {},
    )
    full = model._create_chat_result(
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ok", "reasoning_content": "done"},
                    "finish_reason": "stop",
                }
            ]
        }
    )
    history = [
        HumanMessage("hi"),
        AIMessage(
            "",
            tool_calls=[{"name": "read", "args": {}, "id": "c1"}],
            additional_kwargs={"reasoning_content": "check the sheet"},
        ),
        ToolMessage("rows", tool_call_id="c1"),
        AIMessage("no reasoning on this one"),
    ]
    sent = model._get_request_payload(history)["messages"]

    assert streamed.message.additional_kwargs["reasoning_content"] == "check the sheet"
    assert full.generations[0].message.additional_kwargs["reasoning_content"] == "done"
    assert sent[1]["reasoning_content"] == "check the sheet"
    assert "reasoning_content" not in sent[3]


def test_other_openai_compatible_providers_send_no_reasoning_field() -> None:
    from coscribe.runtime_lg.providers import resolve_chat_model

    model = resolve_chat_model(
        "kimi:k2", {"kimi": {"base_url": "https://api.moonshot.cn/v1", "api_key": "k"}}
    )
    sent = model._get_request_payload(
        [AIMessage("x", additional_kwargs={"reasoning_content": "kept locally"})]
    )["messages"]

    assert "reasoning_content" not in sent[0]
