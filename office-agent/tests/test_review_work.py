"""Tests for runtime_lg/subagents.py's build_review_work_tool -- the
reviewer's real read-only tools and its multimodal (text + rendered-
preview-image) content construction, added to give review_work real
teeth (previously: empty tool list, text-only, see subagents.py's own
docstring for the full "why" and the live-Gemini verification this
session that found and fixed the pre-existing checkpointer/thread_id bug
review_work had never actually been exercised against before).

Needs the langgraph_spike extra installed -- skips cleanly via
importorskip rather than failing collection when it isn't present.
"""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from coscribe.runtime_lg.subagents import build_review_work_tool


class _FakeModel(BaseChatModel):
    """Minimal scripted chat model -- review_work always goes through a
    single sync `.invoke()`, so only `_generate` is needed. Records every
    call's messages so a test can inspect exactly what the reviewer saw."""

    responses: list[AIMessage]
    i: int = 0
    calls: list[list[BaseMessage]] = []

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(messages)
        message = self.responses[self.i]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        return "fake-review-work-model"


def _last_human_message(messages: list[BaseMessage]) -> HumanMessage:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message
    raise AssertionError("no HumanMessage found")


def test_review_work_uses_the_reviewer_tools_it_was_built_with(tmp_path: Path) -> None:
    calls: list[str] = []

    def read_thing(path: str) -> str:
        """A fake read-only tool."""
        calls.append(path)
        return "file contents"

    model = _FakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "read_thing", "args": {"path": "a.docx"}, "id": "c1"}],
            ),
            AIMessage(content="Looks fine."),
        ]
    )
    review_work = build_review_work_tool(model, [read_thing], tmp_path)

    result = review_work(
        original_request="write a report", summary_of_work="wrote report.docx", file_path="a.docx"
    )

    assert calls == ["a.docx"]
    assert result == "Looks fine."


def test_review_work_plain_text_content_without_preview_name(tmp_path: Path) -> None:
    model = _FakeModel(responses=[AIMessage(content="ok")])
    review_work = build_review_work_tool(model, [], tmp_path)

    review_work(original_request="req", summary_of_work="summary")

    sent = _last_human_message(model.calls[0]).content
    assert isinstance(sent, str)
    assert "req" in sent
    assert "summary" in sent


def test_review_work_builds_multimodal_content_when_preview_exists(tmp_path: Path) -> None:
    previews_dir = tmp_path / "previews"
    previews_dir.mkdir()
    image_bytes = b"\x89PNG\r\n\x1a\nfake png bytes for this test"
    (previews_dir / "shot.png").write_bytes(image_bytes)

    model = _FakeModel(responses=[AIMessage(content="ok")])
    review_work = build_review_work_tool(model, [], tmp_path)

    review_work(original_request="req", summary_of_work="summary", preview_name="shot.png")

    sent = _last_human_message(model.calls[0]).content
    assert isinstance(sent, list)
    assert sent[0]["type"] == "text"
    assert "req" in sent[0]["text"]
    assert sent[1]["type"] == "image_url"
    expected_data_url = f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}"
    assert sent[1]["image_url"]["url"] == expected_data_url


def test_review_work_accepts_comma_separated_preview_names_for_a_multi_slide_deck(
    tmp_path: Path,
) -> None:
    previews_dir = tmp_path / "previews"
    previews_dir.mkdir()
    slide1 = b"fake png bytes slide 1"
    slide2 = b"fake png bytes slide 2"
    (previews_dir / "slide1.png").write_bytes(slide1)
    (previews_dir / "slide2.png").write_bytes(slide2)

    model = _FakeModel(responses=[AIMessage(content="ok")])
    review_work = build_review_work_tool(model, [], tmp_path)

    review_work(
        original_request="req",
        summary_of_work="summary",
        preview_name="slide1.png, slide2.png",
    )

    sent = _last_human_message(model.calls[0]).content
    assert isinstance(sent, list)
    assert sent[0]["type"] == "text"
    image_blocks = [block for block in sent[1:] if block["type"] == "image_url"]
    assert len(image_blocks) == 2
    assert image_blocks[0]["image_url"]["url"] == (
        f"data:image/png;base64,{base64.b64encode(slide1).decode('ascii')}"
    )
    assert image_blocks[1]["image_url"]["url"] == (
        f"data:image/png;base64,{base64.b64encode(slide2).decode('ascii')}"
    )


def test_review_work_falls_back_to_text_only_when_preview_file_missing(tmp_path: Path) -> None:
    model = _FakeModel(responses=[AIMessage(content="ok")])
    review_work = build_review_work_tool(model, [], tmp_path)

    # No file actually written at tmp_path/previews/missing.png.
    review_work(original_request="req", summary_of_work="summary", preview_name="missing.png")

    sent = _last_human_message(model.calls[0]).content
    assert isinstance(sent, str)


def test_review_work_downscales_a_preview_past_the_max_dimension(tmp_path: Path) -> None:
    """Real, live-reported cost problem: LibreOffice's own PNG export has
    no explicit width/height cap, so a real slide preview commonly lands
    well past what a reviewer actually needs to judge layout/legibility
    -- and vision APIs charge roughly per pixel (see subagents.py's own
    _downscale_preview_for_review docstring), so sending it at full
    resolution is real, avoidable token cost. This is the regression
    test: a preview past _REVIEW_PREVIEW_MAX_DIMENSION on either side
    comes out smaller, not byte-identical to the original."""
    from PIL import Image

    from coscribe.runtime_lg.subagents import _REVIEW_PREVIEW_MAX_DIMENSION

    previews_dir = tmp_path / "previews"
    previews_dir.mkdir()
    oversized = Image.new("RGB", (3200, 1800), color=(200, 50, 50))
    buffer = BytesIO()
    oversized.save(buffer, format="PNG")
    original_bytes = buffer.getvalue()
    (previews_dir / "big.png").write_bytes(original_bytes)

    model = _FakeModel(responses=[AIMessage(content="ok")])
    review_work = build_review_work_tool(model, [], tmp_path)

    review_work(original_request="req", summary_of_work="summary", preview_name="big.png")

    sent = _last_human_message(model.calls[0]).content
    assert isinstance(sent, list)
    data_url = sent[1]["image_url"]["url"]
    assert data_url != f"data:image/png;base64,{base64.b64encode(original_bytes).decode('ascii')}"
    sent_bytes = base64.b64decode(data_url.removeprefix("data:image/png;base64,"))
    with Image.open(BytesIO(sent_bytes)) as resized:
        assert resized.width <= _REVIEW_PREVIEW_MAX_DIMENSION
        assert resized.height <= _REVIEW_PREVIEW_MAX_DIMENSION
        # Aspect ratio preserved (3200:1800 = 16:9).
        assert abs(resized.width / resized.height - 3200 / 1800) < 0.01


def test_review_work_leaves_a_small_preview_untouched(tmp_path: Path) -> None:
    from PIL import Image

    previews_dir = tmp_path / "previews"
    previews_dir.mkdir()
    small = Image.new("RGB", (400, 225), color=(50, 100, 200))
    buffer = BytesIO()
    small.save(buffer, format="PNG")
    original_bytes = buffer.getvalue()
    (previews_dir / "small.png").write_bytes(original_bytes)

    model = _FakeModel(responses=[AIMessage(content="ok")])
    review_work = build_review_work_tool(model, [], tmp_path)

    review_work(original_request="req", summary_of_work="summary", preview_name="small.png")

    sent = _last_human_message(model.calls[0]).content
    expected_data_url = f"data:image/png;base64,{base64.b64encode(original_bytes).decode('ascii')}"
    assert sent[1]["image_url"]["url"] == expected_data_url


def test_review_work_extracts_plain_text_from_a_thinking_style_reply(tmp_path: Path) -> None:
    """A real Gemini "thinking" reply's .content is a list of blocks
    including a large opaque signature blob, not a plain string -- this
    session's live testing confirmed returning that raw would leak the
    whole blob into the caller. review_work must return clean text."""
    model = _FakeModel(
        responses=[
            AIMessage(
                content=[
                    {"type": "text", "text": "Looks good."},
                    {"type": "thinking", "extras": {"signature": "not-real-but-huge"}},
                ]
            )
        ]
    )
    review_work = build_review_work_tool(model, [], tmp_path)

    result = review_work(original_request="req", summary_of_work="summary")

    assert result == "Looks good."


def test_review_work_reports_no_reply_gracefully(tmp_path: Path) -> None:
    model = _FakeModel(responses=[AIMessage(content="")])
    review_work = build_review_work_tool(model, [], tmp_path)

    result = review_work(original_request="req", summary_of_work="summary")

    assert result == "(reviewer produced no text reply)"


def test_a_reviewer_that_keeps_digging_is_stopped_after_a_few_model_calls(tmp_path: Path) -> None:
    def look(path: str) -> str:
        """A fake read-only tool."""
        return "nothing here"

    digging = [
        AIMessage(
            content="",
            tool_calls=[{"name": "look", "args": {"path": f"{i}.docx"}, "id": f"c{i}"}],
        )
        for i in range(40)
    ]
    model = _FakeModel(responses=digging)
    review_work = build_review_work_tool(model, [look], tmp_path)

    review_work(original_request="write a report", summary_of_work="wrote it")

    assert len(model.calls) <= 8


def test_the_reviewer_sees_what_the_work_actually_observed(tmp_path: Path) -> None:
    """A data-problems sheet listed trailing spaces in a customer file that
    no script had found; reading only the request, the summary and the file,
    the reviewer had nothing to tell an invented finding from a real one."""
    from langchain_core.messages import ToolMessage

    from coscribe.runtime_lg.agent import build_langgraph_agent

    def run_python_script(description: str, script: str) -> str:
        """A fake script runner."""
        return "{'exit_code': 0, 'stdout': 'orders with a padded region: 10'}"

    reviewer_model = _FakeModel(responses=[AIMessage(content="ok")])
    review_work = build_review_work_tool(reviewer_model, [], tmp_path)
    worker = _FakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_python_script",
                        "args": {"description": "find data problems", "script": "..."},
                        "id": "s1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "review_work",
                        "args": {"original_request": "clean it", "summary_of_work": "done"},
                        "id": "r1",
                    }
                ],
            ),
            AIMessage(content="finished"),
        ]
    )
    agent = build_langgraph_agent(worker, [run_python_script, review_work], "work")
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"configurable": {"thread_id": "t"}},
    )

    prompt = _last_human_message(reviewer_model.calls[0]).content
    assert "run_python_script(find data problems)" in prompt
    assert "orders with a padded region: 10" in prompt
    tool_results = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_results[-1].content == "ok"


async def test_the_reviewer_streams_nothing_into_the_parents_chat(tmp_path: Path) -> None:
    """The parent's chat is its "messages" stream: the reviewer's reply
    showed there as the parent's own words, and its token count was added
    to the parent's response, so the context counter jumped mid-review."""
    from coscribe.runtime_lg.agent import build_langgraph_agent

    reviewer_model = _FakeModel(responses=[AIMessage(content="REVIEWER VERDICT")])
    review_work = build_review_work_tool(reviewer_model, [], tmp_path)
    worker = _FakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "review_work",
                        "args": {"original_request": "clean it", "summary_of_work": "done"},
                        "id": "r1",
                    }
                ],
            ),
            AIMessage(content="finished"),
        ]
    )
    agent = build_langgraph_agent(worker, [review_work], "work")

    streamed = [
        message
        async for message, _metadata in agent.astream(
            {"messages": [{"role": "user", "content": "go"}]},
            config={"configurable": {"thread_id": "t"}},
            stream_mode="messages",
        )
        if isinstance(message, AIMessage)
    ]

    assert reviewer_model.i == 1
    assert [m.content for m in streamed if m.content] == ["finished"]


def _review_call(call_id: str, file_path: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "review_work",
                "args": {"original_request": "r", "summary_of_work": "s", "file_path": file_path},
                "id": call_id,
            }
        ],
    )


def test_a_file_gets_a_review_and_one_recheck_per_turn(tmp_path: Path) -> None:
    """Fixing and re-reviewing had no end but the prompt's say-so; the main
    loop's call cap restarts at every approval, so in Auto mode nothing
    else stopped it."""
    from langchain_core.messages import ToolMessage

    from coscribe.runtime_lg.agent import build_langgraph_agent

    reviewer_model = _FakeModel(responses=[AIMessage(content="fix the legend")] * 3)
    review_work = build_review_work_tool(reviewer_model, [], tmp_path)
    worker = _FakeModel(
        responses=[
            _review_call("r1", "输出/deck.pptx"),
            _review_call("r2", "./输出/deck.pptx"),
            _review_call("r3", "输出\\deck.pptx"),
            AIMessage(content="finished"),
        ]
    )
    agent = build_langgraph_agent(worker, [review_work], "work")

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"configurable": {"thread_id": "t"}},
    )

    replies = [m.content for m in result["messages"] if isinstance(m, ToolMessage)]
    assert reviewer_model.i == 2
    assert replies[:2] == ["fix the legend", "fix the legend"]
    assert replies[2].startswith("Not reviewed: this turn has used its reviews")


def test_reviews_are_capped_per_turn_and_counted_from_the_users_last_message(
    tmp_path: Path,
) -> None:
    from langchain_core.messages import ToolMessage

    from coscribe.runtime_lg.subagents import reviews_this_turn

    reviewer_model = _FakeModel(responses=[AIMessage(content="ok")] * 2)
    review_work = build_review_work_tool(reviewer_model, [], tmp_path)
    earlier = [HumanMessage("first"), _review_call("old", "a.docx")]
    earlier.append(ToolMessage("ok", name="review_work", tool_call_id="old"))
    this_turn: list[BaseMessage] = [HumanMessage("again")]
    for i in range(6):
        this_turn += [
            _review_call(f"c{i}", f"f{i}.docx"),
            ToolMessage("ok", name="review_work", tool_call_id=f"c{i}"),
        ]

    assert reviews_this_turn([*earlier, HumanMessage("again")]) == []
    assert reviews_this_turn(earlier) == ["a.docx"]
    refused = review_work("r", "s", file_path="g.docx", state={"messages": earlier + this_turn})
    assert refused.startswith("Not reviewed: this turn has used its reviews")
    assert reviewer_model.i == 0
    assert review_work("r", "s", file_path="a.docx", state={"messages": earlier}) == "ok"
    assert reviewer_model.i == 1


def test_evidence_log_leaves_out_bookkeeping_and_fits_its_budget() -> None:
    from langchain_core.messages import ToolMessage

    from coscribe.runtime_lg.subagents import _EVIDENCE_CHARS, evidence_log

    calls = [
        {"name": name, "args": {"path": f"f{i}"}, "id": f"c{i}"}
        for i, name in enumerate(["task_create", "search_tools", "read_xlsx", "read_xlsx"])
    ]
    messages = [
        AIMessage(content="", tool_calls=calls),
        *(
            ToolMessage(content="x" * 50_000, name=call["name"], tool_call_id=call["id"])
            for call in calls
        ),
    ]

    log = evidence_log(messages)

    assert "task_create" not in log and "search_tools" not in log
    assert "[1] read_xlsx(f2)" in log and "[2] read_xlsx(f3)" in log
    assert len(log) < _EVIDENCE_CHARS + 200
    assert evidence_log([]) == ""
