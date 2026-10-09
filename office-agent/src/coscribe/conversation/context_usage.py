"""Context-window breakdown: where a turn's token budget actually goes --
the data behind ContextRing.tsx's expanded panel (click the ring in the
composer). Real, previously-recorded research (runtime_lg/README.md's
"Tool-loading context cost" section) already measured this once with an
ad-hoc throwaway script (90 built-in tools ~27k tokens of JSON schema,
INSTRUCTIONS ~7.4k tokens); this module ships the same kind of
measurement as a real, on-demand feature instead, so the next
architecture discussion about deferred tool loading has a live picture
to point at instead of a stale one-time number.

**Why tiktoken, not each provider's own tokenizer.** cl100k_base is a
proxy -- Claude/Gemini/DeepSeek/Kimi/GLM/Ollama each have their own real
tokenizer, none of them shipped as an installable Python package the
same lightweight way tiktoken is. Same "right order of magnitude, not
exact" trade-off the earlier ad-hoc investigation already accepted. The
one number in this breakdown that actually matters for correctness --
`total_tokens` -- comes from the provider's own last real usage report
(ChatSessionLG._last_usage_metadata) when one exists, not this estimate;
`messages` is the residual between that real total and the
independently-measured static categories, so the reported total never
drifts from what the provider actually charged for, and any tiktoken/
real-tokenizer discrepancy is absorbed by the one category (messages)
that's largest and most variable anyway, rather than distorting every
category's own number with an artificial rescale.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def _encoder() -> Any:
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def count_text_tokens(text: str) -> int:
    """Best-effort token count for a plain string. Falls back to a rough
    chars-per-token estimate (never a crash, never 0 for non-empty text) if
    tiktoken's encoding data can't be loaded -- it downloads
    `cl100k_base.tiktoken` from a remote CDN on first use and caches it
    locally, so a genuinely offline deployment (or a proxy that doesn't
    cover that one host) fails every call, not just the first. A real,
    live-reported bug: this used to return 0 on that failure, which -- since
    every one of build_context_breakdown's static categories (system
    prompt/skills/system tools/MCP tools) routes through this function --
    silently zeroed out four of its seven categories at once with no error
    shown, while `messages` (computed from the provider's own real usage
    report, not this estimate) kept reporting correctly and absorbed the
    entire total. That looked exactly like "tools/prompt cost nothing,"
    the opposite of the real problem, on a screen whose whole purpose is
    showing where the budget actually goes."""
    if not text:
        return 0
    try:
        return len(_encoder().encode(text))
    except Exception:
        return _fallback_token_estimate(text)


def _fallback_token_estimate(text: str) -> int:
    """~4 chars/token is the standard rough English-text heuristic (the same
    order of magnitude OpenAI's own tokenizer docs quote) -- not accurate,
    but "roughly right order of magnitude" is what this module's own
    docstring already promises for tiktoken's cl100k_base proxy itself, so
    this fallback only has to clear that same bar, not be exact."""
    return max(1, len(text) // 4)


def count_tool_schema_tokens(tools: list[Any]) -> int:
    """Sum of each tool's JSON schema, as actually sent to a provider --
    via the same `convert_to_openai_tool` every provider integration in
    this codebase already goes through, so this measures the real
    payload shape, not a guess at it. One bad tool (a schema that fails
    to convert) is skipped, not fatal to the whole count -- a diagnostic
    panel degrading gracefully beats it crashing over one odd tool."""
    from langchain_core.utils.function_calling import convert_to_openai_tool

    total = 0
    for tool in tools:
        try:
            schema = convert_to_openai_tool(tool)
        except Exception:
            continue
        total += count_text_tokens(json.dumps(schema))
    return total


def build_context_breakdown(
    *,
    instructions: str,
    skills_listing: str,
    base_tools: list[Any],
    mcp_tools: list[Any],
    context_window: int,
    auto_compact_threshold: float,
    last_total_tokens: int | None,
) -> dict[str, Any]:
    """Assemble the full breakdown dict the GET /api/threads/{id}/
    context-breakdown endpoint returns. `instructions` is
    ChatSessionLG._instructions (the full, already-assembled system
    prompt string); `skills_listing` is the exact substring
    format_skill_listing(...) produced for the currently-enabled skills
    (recomputed fresh here, not re-derived from the concatenated
    instructions string, since splitting a string back into its own
    parts is fragile where independently re-running the same formatter
    that produced it in the first place is not) -- subtracted out of
    `instructions` before counting so "system prompt" and "skills" don't
    double-count the same text. `base_tools` excludes MCP-sourced tools
    (ChatSessionLG._base_tools); `mcp_tools` is exactly those
    (ChatSessionLG._extra_tools).
    """
    system_prompt_text = (
        instructions.replace(skills_listing, "") if skills_listing else instructions
    )
    system_prompt_tokens = count_text_tokens(system_prompt_text)
    skills_tokens = count_text_tokens(skills_listing)
    system_tools_tokens = count_tool_schema_tokens(base_tools)
    mcp_tools_tokens = count_tool_schema_tokens(mcp_tools)
    static_tokens = system_prompt_tokens + skills_tokens + system_tools_tokens + mcp_tools_tokens

    if last_total_tokens is not None and last_total_tokens > static_tokens:
        total_tokens = last_total_tokens
        messages_tokens = last_total_tokens - static_tokens
        total_is_estimated = False
    else:
        # No real usage yet (a brand-new thread), or the real total came
        # back smaller than this estimate's own static portion (the
        # tiktoken proxy overcounting relative to the real provider
        # tokenizer) -- either way, there's no trustworthy real number
        # to anchor "messages" against, so report the static estimate
        # as the whole total instead of showing a nonsensical negative
        # messages count.
        total_tokens = static_tokens
        messages_tokens = 0
        total_is_estimated = True

    usable_before_compact = context_window * auto_compact_threshold
    autocompact_buffer_tokens = max(0, context_window - int(usable_before_compact))
    free_space_tokens = max(0, int(usable_before_compact) - total_tokens)

    return {
        "context_window": context_window,
        "total_tokens": total_tokens,
        "total_is_estimated": total_is_estimated,
        "categories": [
            {"key": "messages", "label": "Messages", "tokens": messages_tokens},
            {"key": "system_tools", "label": "System tools", "tokens": system_tools_tokens},
            {"key": "mcp_tools", "label": "MCP tools", "tokens": mcp_tools_tokens},
            {"key": "system_prompt", "label": "System prompt", "tokens": system_prompt_tokens},
            {"key": "skills", "label": "Skills", "tokens": skills_tokens},
            {
                "key": "autocompact_buffer",
                "label": "Autocompact buffer",
                "tokens": autocompact_buffer_tokens,
            },
            {"key": "free_space", "label": "Free space", "tokens": free_space_tokens},
        ],
    }
