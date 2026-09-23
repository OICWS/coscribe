"""Tests for cli.py -- the CLI on runtime_lg, reusing ChatSessionLG
(web/session.py) via _CliSocket instead of its own bespoke
implementation. Written while cli.py was still named cli_lg.py, proving
it a genuine superset of the original hand-rolled-runtime CLI before that
one was deleted and this file was promoted in its place (see
runtime_lg/README.md's "coscribe-lg reaches real feature parity"
section) -- kept as tests/test_cli.py's real content going forward, not a
second, separate test file.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from typer.testing import CliRunner

from coscribe.cli import app
from coscribe.tools.scheduled_tasks import ScheduledTrigger, ScheduledTriggerStore, ScheduleRule
from coscribe.tools.selfwake import WakeRequest, WakeStore


class FakeToolCallingChatModel(BaseChatModel):
    """Same shape as test_web_lg.py's identical class -- cycles through
    `responses` in order, streaming each as a single AIMessageChunk (the
    only thing session.py's _stream_turn forwards as agent_delta)."""

    responses: list[AIMessage]
    i: int = 0

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = self.responses[self.i]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        message = self.responses[self.i]
        self.i += 1
        chunk = AIMessageChunk(
            content=message.content or "", tool_calls=message.tool_calls, id=message.id
        )
        yield ChatGenerationChunk(message=chunk)

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling-chat-model"


class _FakeContextWindowClient:
    """Stand-in for LLMClient -- ChatSessionLG.send_state only ever calls
    get_context_window() on it; the real LLMClient would try to resolve
    "fake:model" as a live provider and fail."""

    def get_context_window(self, model: str) -> int:
        return 128_000


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id}


def _env(tmp_path: Path, **overrides: str | None) -> dict[str, str | None]:
    values: dict[str, str | None] = {
        "COSCRIBE_DEFAULT_MODEL": "fake:model",
        "COSCRIBE_WORKSPACE_ROOT": str(tmp_path / "workspace"),
        "COSCRIBE_STATE_DIR": str(tmp_path / "state"),
        "COSCRIBE_SKILLS_DIR": str(tmp_path / "skills"),
        "COSCRIBE_MCP_CONFIG_PATH": None,
        "COSCRIBE_HOOKS_CONFIG_PATH": None,
    }
    values.update(overrides)
    return values


def _patch_model(monkeypatch: pytest.MonkeyPatch, fake_model: FakeToolCallingChatModel) -> None:
    monkeypatch.setattr(
        "coscribe.web.session.resolve_chat_model",
        lambda model, custom_providers=None: fake_model,
    )
    monkeypatch.setattr(
        "coscribe.cli.LLMClient", lambda custom_providers=None: _FakeContextWindowClient()
    )


def test_simple_conversation_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    _patch_model(monkeypatch, fake_model)
    runner = CliRunner()

    result = runner.invoke(app, ["--thread", "t1"], input="hello\nexit\n", env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    assert "hi there!" in result.output


def test_plan_toggle_blocks_a_gated_tool_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="noted as a task instead"),
        ]
    )
    _patch_model(monkeypatch, fake_model)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["--thread", "t2"],
        input="/plan\nwrite hi to note.txt\nexit\n",
        env=_env(tmp_path),
    )

    assert result.exit_code == 0, result.output
    assert "you [plan]" in result.output
    assert not (tmp_path / "workspace" / "note.txt").exists()


def test_write_file_requires_approval_and_executes_when_approved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    _patch_model(monkeypatch, fake_model)
    runner = CliRunner()

    result = runner.invoke(
        app, ["--thread", "t3"], input="write hi to note.txt\ny\nexit\n", env=_env(tmp_path)
    )

    assert result.exit_code == 0, result.output
    assert "[approval required]" in result.output
    assert (tmp_path / "workspace" / "note.txt").read_text() == "hi"


def test_write_file_denied_is_not_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="ok, skipped")]
    )
    _patch_model(monkeypatch, fake_model)
    runner = CliRunner()

    result = runner.invoke(
        app, ["--thread", "t3b"], input="write hi to note.txt\nn\nexit\n", env=_env(tmp_path)
    )

    assert result.exit_code == 0, result.output
    assert not (tmp_path / "workspace" / "note.txt").exists()


def test_compact_command_summarizes_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # _handle_compact requires at least 4 checkpointed messages -- two full
    # human/AI turns' worth -- before it'll actually summarize anything.
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="hi there"),
            AIMessage(content="how can I help"),
            AIMessage(content="a short summary"),
        ]
    )
    _patch_model(monkeypatch, fake_model)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["--thread", "t4"],
        input="hello\nanything else\n/compact\nexit\n",
        env=_env(tmp_path),
    )

    assert result.exit_code == 0, result.output
    assert "Compacted" in result.output


def test_clear_command_wipes_history_and_starts_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there")])
    _patch_model(monkeypatch, fake_model)
    runner = CliRunner()

    result = runner.invoke(
        app, ["--thread", "t5"], input="hello\n/clear\nexit\n", env=_env(tmp_path)
    )

    assert result.exit_code == 0, result.output
    assert "Cleared this thread's conversation history." in result.output


def test_message_flag_sends_one_message_and_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="one-shot reply")])
    _patch_model(monkeypatch, fake_model)
    runner = CliRunner()

    result = runner.invoke(
        app, ["--thread", "t6", "--message", "hello"], input="", env=_env(tmp_path)
    )

    assert result.exit_code == 0, result.output
    assert "one-shot reply" in result.output


def test_message_flag_exits_nonzero_on_llm_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No responses scripted -- FakeToolCallingChatModel._generate raises
    # IndexError on the first call, which _handle_user_message_locked's own
    # except Exception catches and reports as an "error" WS message.
    fake_model = FakeToolCallingChatModel(responses=[])
    _patch_model(monkeypatch, fake_model)
    runner = CliRunner()

    result = runner.invoke(
        app, ["--thread", "t7", "--message", "hello"], input="", env=_env(tmp_path)
    )

    assert result.exit_code == 1


def test_reconnecting_with_the_same_thread_replays_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the same bug fixed in app.py this session:
    a resumed --thread should show what was already said, not start from
    a blank slate, even though the underlying checkpointed state was
    always there."""
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    _patch_model(monkeypatch, fake_model)
    runner = CliRunner()

    first = runner.invoke(
        app, ["--thread", "t11"], input="hello\nexit\n", env=_env(tmp_path)
    )
    assert first.exit_code == 0, first.output

    second = runner.invoke(app, ["--thread", "t11"], input="exit\n", env=_env(tmp_path))

    assert second.exit_code == 0, second.output
    assert "you: hello" in second.output
    assert "agent: hi there!" in second.output


def test_mcp_tools_are_wired_into_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Doesn't exercise a real subprocess (that's scripts/verify_mcp_
    persistent_session.py's job, live-verified manually against a real
    stateful MCP server -- see runtime_lg/README.md) -- this just proves
    connect_mcp_tools_lg's returned tools actually reach the session (the
    part that was structurally impossible before this rewrite, since the
    old sync loop couldn't await connect_mcp_tools_lg's real, working
    async connection at all in a place that mattered)."""
    from langchain_core.tools import tool

    @tool
    def fake_mcp_tool() -> str:
        """A fake MCP-sourced tool."""
        return "mcp tool result"

    async def _fake_connect(path: Any) -> tuple[list[Any], dict[str, Any]]:
        return [fake_mcp_tool], {}

    monkeypatch.setattr("coscribe.runtime_lg.mcp.connect_mcp_tools_lg", _fake_connect)
    call = _tool_call("call_1", "fake_mcp_tool", {})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="got it")]
    )
    _patch_model(monkeypatch, fake_model)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["--thread", "t12", "--accept-edits"],
        input="use the fake mcp tool\nexit\n",
        env=_env(tmp_path, COSCRIBE_MCP_CONFIG_PATH=str(tmp_path / "mcp.json")),
    )

    assert result.exit_code == 0, result.output
    assert "Loaded 1 MCP tool(s)." in result.output
    assert "got it" in result.output


def test_builtin_skills_are_enabled_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live-reproduced defect: web/app.py's new-thread default was changed
    (commit 5c397cf) to offer the 3 built-in skills, but cli.py's own
    ChatSessionLG(...) construction was never updated to match -- it fell
    straight through to ChatSessionLG's own default, an *empty* set, so
    load_skill was never even in the CLI's tool list. Confirmed against a
    real DeepSeek run: it called load_skill("PPTX Slides") and got back
    "Unknown skill" (paraphrased by the model as "the tool is
    unavailable"), then recovered by guessing from list_files instead.

    Asserts directly on the enabled_skill_names ChatSessionLG was actually
    constructed with, rather than routing through FakeToolCallingChatModel
    (which cycles its canned `responses` regardless of what a tool call
    actually returned, so it can't distinguish load_skill succeeding from
    it raising -- a weaker assertion here would pass either way)."""
    import coscribe.cli as cli_module

    real_chat_session_lg = cli_module.ChatSessionLG
    captured: dict[str, Any] = {}

    def _capturing(*args: Any, **kwargs: Any) -> Any:
        captured["enabled_skill_names"] = kwargs.get("enabled_skill_names")
        return real_chat_session_lg(*args, **kwargs)

    monkeypatch.setattr(cli_module, "ChatSessionLG", _capturing)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    _patch_model(monkeypatch, fake_model)
    runner = CliRunner()

    result = runner.invoke(app, ["--thread", "t13"], input="hi\nexit\n", env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    assert captured["enabled_skill_names"] == {
        "PPTX Slides",
        "Excel Spreadsheets",
        "Word Documents",
        "Skill Creator",
    }


def test_check_wakes_resumes_a_due_thread_and_prints_a_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    WakeStore(state_dir).save(
        WakeRequest(
            wake_id="wake-1",
            thread_id="t-check",
            kind="timer",
            reason="say good morning",
            created_at=datetime.now(UTC).isoformat(),
            status="pending",
            wake_at=(datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        )
    )
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="good morning!")])
    _patch_model(monkeypatch, fake_model)
    runner = CliRunner()

    result = runner.invoke(app, ["--check-wakes"], env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    assert "Resumed thread 't-check' (timer): say good morning" in result.output
    resolved = WakeStore(state_dir).load("wake-1")
    assert resolved is not None
    assert resolved.status == "woken"


def test_check_wakes_with_nothing_due_prints_no_due_wakes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    _patch_model(monkeypatch, fake_model)
    runner = CliRunner()

    result = runner.invoke(app, ["--check-wakes"], env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    assert "No due wakes." in result.output


def test_check_wakes_also_fires_a_due_scheduled_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    ScheduledTriggerStore(state_dir).save(
        ScheduledTrigger(
            trigger_id="trig-1",
            name="Daily standup notes",
            thread_id="scheduled-trig-1",
            schedule=ScheduleRule(kind="daily", at="09:00"),
            enabled=True,
            created_at=datetime.now().isoformat(),
            next_run_at=(datetime.now() - timedelta(minutes=5)).isoformat(),
            prompt="Summarize yesterday",
        )
    )
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="here's your summary")])
    _patch_model(monkeypatch, fake_model)
    runner = CliRunner()

    result = runner.invoke(app, ["--check-wakes"], env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    assert "Ran scheduled task 'Daily standup notes' (trig-1)" in result.output
    resolved = ScheduledTriggerStore(state_dir).load("trig-1")
    assert resolved is not None
    assert resolved.last_run_status == "completed"
