"""Tests for web/context_usage.py -- the context-window breakdown panel's
backend (see that module's own docstring for the measurement approach).
"""

import pytest
from langchain_core.tools import tool

from coscribe.web import context_usage
from coscribe.web.context_usage import (
    build_context_breakdown,
    count_text_tokens,
    count_tool_schema_tokens,
)


def test_count_text_tokens_counts_real_tokens_not_characters() -> None:
    empty = count_text_tokens("")
    short = count_text_tokens("hello world")
    long = count_text_tokens("hello world " * 50)
    assert empty == 0
    assert 0 < short < long


def test_count_text_tokens_falls_back_to_a_heuristic_when_tiktoken_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Real, live-reported bug: tiktoken.get_encoding() downloads its BPE
    # vocab file from a remote CDN on first use -- an offline/locked-down
    # deployment (or a proxy that doesn't cover that one host) makes every
    # call fail, not just the first. This used to return 0, which silently
    # zeroed out four of build_context_breakdown's seven categories at
    # once (system prompt/skills/system tools/MCP tools all route through
    # this function) with no error shown to the user.
    def _broken_encoder() -> None:
        raise RuntimeError("simulated: no network to fetch tiktoken encoding")

    monkeypatch.setattr(context_usage, "_encoder", _broken_encoder)  # type: ignore[attr-defined]
    assert count_text_tokens("") == 0
    short = count_text_tokens("hello world")
    long = count_text_tokens("hello world " * 50)
    assert short > 0
    assert long > short


@tool
def _sample_tool(text: str) -> str:
    """A sample tool with a real docstring for schema conversion."""
    return text


def test_count_tool_schema_tokens_is_positive_for_a_real_tool() -> None:
    tokens = count_tool_schema_tokens([_sample_tool])
    assert tokens > 0


def test_count_tool_schema_tokens_skips_an_unconvertible_entry() -> None:
    # A plain object with no callable/BaseTool shape -- convert_to_openai_tool
    # should raise on it; the whole count must not blow up over one bad entry.
    tokens = count_tool_schema_tokens([_sample_tool, object()])
    assert tokens == count_tool_schema_tokens([_sample_tool])


def test_breakdown_categories_sum_to_the_real_reported_total() -> None:
    result = build_context_breakdown(
        instructions="You are a helpful assistant. Some skill listing text here.",
        skills_listing="Some skill listing text here.",
        base_tools=[_sample_tool],
        mcp_tools=[],
        context_window=1_000_000,
        auto_compact_threshold=0.8,
        last_total_tokens=50_000,
    )
    assert result["total_tokens"] == 50_000
    assert result["total_is_estimated"] is False
    by_key = {c["key"]: c["tokens"] for c in result["categories"]}
    # messages + system_tools + mcp_tools + system_prompt + skills must
    # reconcile exactly against the real reported total (messages is the
    # residual, by construction -- see build_context_breakdown's own
    # docstring for why).
    assert (
        by_key["messages"]
        + by_key["system_tools"]
        + by_key["mcp_tools"]
        + by_key["system_prompt"]
        + by_key["skills"]
        == 50_000
    )
    # skills text was subtracted out of "system prompt" (instructions
    # contains it verbatim), so it must not be double-counted: the
    # skills-only text has fewer tokens than the full instructions string.
    assert by_key["skills"] > 0
    assert by_key["system_prompt"] < count_text_tokens(
        "You are a helpful assistant. Some skill listing text here."
    )


def test_autocompact_buffer_and_free_space_use_the_real_threshold() -> None:
    result = build_context_breakdown(
        instructions="short",
        skills_listing="",
        base_tools=[],
        mcp_tools=[],
        context_window=1_000_000,
        auto_compact_threshold=0.8,
        last_total_tokens=100_000,
    )
    by_key = {c["key"]: c["tokens"] for c in result["categories"]}
    # 1M * (1 - 0.8) = 200k reserved as buffer, regardless of usage.
    assert by_key["autocompact_buffer"] == 200_000
    # 1M * 0.8 - 100k used = 700k of genuinely free space before compaction.
    assert by_key["free_space"] == 700_000


def test_no_real_usage_yet_falls_back_to_the_static_estimate() -> None:
    result = build_context_breakdown(
        instructions="You are a helpful assistant.",
        skills_listing="",
        base_tools=[_sample_tool],
        mcp_tools=[],
        context_window=1_000_000,
        auto_compact_threshold=0.8,
        last_total_tokens=None,
    )
    assert result["total_is_estimated"] is True
    by_key = {c["key"]: c["tokens"] for c in result["categories"]}
    assert by_key["messages"] == 0
    assert result["total_tokens"] == by_key["system_tools"] + by_key["system_prompt"]


def test_breakdown_does_not_zero_static_categories_when_tiktoken_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reproduces the exact user-reported symptom: with tiktoken unable to
    # load its encoding data, system_tools/mcp_tools/system_prompt/skills
    # all used to read literally 0 while "messages" silently absorbed the
    # entire real total -- looking exactly like "tools and the system
    # prompt cost nothing," the opposite of the truth.
    def _broken_encoder() -> None:
        raise RuntimeError("simulated: no network to fetch tiktoken encoding")

    monkeypatch.setattr(context_usage, "_encoder", _broken_encoder)  # type: ignore[attr-defined]
    result = build_context_breakdown(
        instructions="You are a helpful assistant. Some skill listing text here.",
        skills_listing="Some skill listing text here.",
        base_tools=[_sample_tool],
        mcp_tools=[],
        context_window=128_000,
        auto_compact_threshold=0.8,
        last_total_tokens=78_900,
    )
    by_key = {c["key"]: c["tokens"] for c in result["categories"]}
    assert by_key["system_tools"] > 0
    assert by_key["system_prompt"] > 0
    assert by_key["skills"] > 0


def test_real_total_smaller_than_static_estimate_does_not_go_negative() -> None:
    # A real provider's own tokenizer can legitimately count fewer tokens
    # for the same text than tiktoken's cl100k_base proxy does -- must not
    # surface a negative "messages" count when that happens.
    result = build_context_breakdown(
        instructions="You are a helpful assistant with a fairly long system prompt.",
        skills_listing="",
        base_tools=[_sample_tool],
        mcp_tools=[],
        context_window=1_000_000,
        auto_compact_threshold=0.8,
        last_total_tokens=1,  # implausibly small on purpose
    )
    assert result["total_is_estimated"] is True
    by_key = {c["key"]: c["tokens"] for c in result["categories"]}
    assert by_key["messages"] == 0
    assert all(c["tokens"] >= 0 for c in result["categories"])
