"""Small LangGraph-message-shape helpers shared between web/session.py
(a live turn's own streaming) and runtime_lg/workflows.py (workflow save/
replay, which needs the identical logic for its curator/assertion prompts
and recorded-step extraction). Split out here rather than duplicated in
both, or left only in session.py where workflows.py couldn't reach them
without a circular import (session.py already imports from runtime_lg).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

# The exact mode_note prefixes session.py's _handle_user_message_locked
# prepends to every turn's user text before it becomes a checkpointed
# HumanMessage (for the model's benefit, so it always knows the active
# mode) -- defined here, not duplicated as inline literals in session.py,
# so serialize_history_for_ws_lg below can reliably strip them back off:
# the *live* user bubble (appendBubble("user", text) in app.js, sent the
# moment the user hits Send) never includes this prefix, since it's added
# server-side afterward -- a history replay showing it verbatim would be a
# visible inconsistency between a message rendered live vs. rendered from
# history for the exact same turn.
PLAN_MODE_NOTE = (
    "[plan mode is ON: only read-only/task-tracking tools will run, "
    "everything else is blocked] "
)
ACCEPT_EDITS_MODE_NOTE = "[accept-edits mode is ON: tool calls run without asking] "
NORMAL_MODE_NOTE = "[normal mode: risky tool calls ask for approval as usual] "
_MODE_NOTES = (PLAN_MODE_NOTE, ACCEPT_EDITS_MODE_NOTE, NORMAL_MODE_NOTE)

_DATE_NOTE_RE = re.compile(r"^Today's real date is \d{4}-\d{2}-\d{2}\.\s+")


def current_date_note() -> str:
    """Real-world grounding the model has no other way to get: its own
    sense of "now" is anchored to its training cutoff, which is
    frequently well in the past by the time it's actually running here.
    Live-reported symptom this fixes: asked to web_search for current
    news, the model assumed a stale date, got real (correctly dated)
    results back, and -- with no authoritative "today is actually X" to
    reconcile against -- concluded its *own* tool must be broken rather
    than that its internal date guess was wrong.

    Prepended per-turn by session.py's _handle_user_message_locked,
    alongside mode_note -- NOT baked into coordinator.py's INSTRUCTIONS
    (where an earlier version of this lived, computed once at
    build_coordinator_agent time and glued onto the very front of the
    system prompt). That placement put the one genuinely volatile string
    in this whole prompt ahead of the large, otherwise-frozen INSTRUCTIONS
    block -- the worst possible spot for it: it poisoned the entire
    prefix for any future Anthropic cache_control breakpoint, and, since
    this app is multi-provider, just as surely defeated Gemini's and
    OpenAI-compatible providers' *automatic* prefix-based caching too, no
    cache_control needed on their end for the damage to apply. Moving it
    into per-turn message content instead of the frozen system prompt
    fixes this for every provider at once -- there's nothing
    provider-specific to configure, since the underlying cache mechanism
    everywhere is the same byte-prefix match. Bonus fix, not just a
    caching one: the old once-per-thread computation also went stale for
    any conversation that ran past midnight; recomputing every turn keeps
    it accurate throughout.

    strip_mode_note below must stay in sync with this -- a checkpointed
    HumanMessage carries this prefix too, and history replay needs to
    strip it back off the same way it does mode_note's."""
    return f"Today's real date is {datetime.now().strftime('%Y-%m-%d')}. "


def strip_mode_note(text: str) -> str:
    text = _DATE_NOTE_RE.sub("", text, count=1)
    for note in _MODE_NOTES:
        if text.startswith(note):
            return text[len(note) :]
    return text


def extract_text(content: Any) -> str:
    """A message's .content is a plain string for most providers, but a
    list of content blocks for Gemini's thinking models -- pull the text
    out either way."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def tool_result_value(content: Any) -> Any:
    """ToolMessage.content is usually a JSON-serialized string of the
    tool's real return value -- decode it back so callers see the raw
    object, not a JSON string. Falls back to the raw string if it isn't
    JSON."""
    if isinstance(content, str):
        try:
            return json.loads(content)
        except ValueError:
            return content
    return content


def serialize_history_for_ws_lg(messages: list[Any]) -> list[dict[str, Any]]:
    """Structured, chronological replay of a thread's checkpointed message
    history for the WS "history" event app.py sends once right after
    connect -- lets the frontend rehydrate the chat log when a browser tab
    (re)connects to an *existing* thread.

    Real, live-reported bug this closes: the visible chat log was only
    ever built up from live events during the *current* WS connection's
    lifetime (agent_delta/agent_message/tool_result), and nothing persists
    it client-side either (no localStorage) -- so switching sessions always
    showed a blank pane, even though the model itself still remembered
    everything via the checkpointer. Confirmed the same gap exists
    verbatim in the old runtime's web/app.py/session.py (ChatSession.
    send_state has no history field either, and app.js has no client-side
    cache) -- not a runtime_lg regression, just never noticed there before
    this session's thread-switcher work made it easy to reach.

    Skips a tool call with no matching ToolMessage yet -- same reasoning as
    workflows.py's recorded_tool_call_steps_lg: an in-flight/still-pending
    call (most commonly a not-yet-answered approval from before a
    disconnect) hasn't produced anything to show yet, and
    resume_after_reconnect already redelivers its own live
    "approval_required" event for that separately -- showing it here too
    would just be a duplicate.

    Real, live-reported bug this also closes: a "tool" entry's own result
    used to be silently dropped -- `results_by_id` was already computed
    just to decide whether to include the entry at all (the skip check
    above), but the looked-up value itself never made it into the emitted
    dict. A reloaded/reconnected thread's own past tool calls (and
    ask_user_question's, whose "result" is the user's chosen answer) were
    each still individually click-to-expand, but expanding one showed
    nothing -- ToolCallRow's own `item.result !== undefined` guard always
    failed on a replayed item. Same tool_result_value(...) call the live
    "tool_result" WS event already uses, so a replayed entry's result has
    the exact same decoded shape a live one does.

    A "user" entry has its mode_note prefix (see strip_mode_note above)
    stripped back off -- the live bubble the user actually saw when they
    hit Send never had it, so a history replay shouldn't show it either.

    Known, accepted gap for v1: a /compact'd thread's history includes the
    synthetic HumanMessage _handle_compact replaces the tail with (the
    rendered pre-compaction transcript, folded into one message) -- it
    replays as one large "user" entry rather than being specially
    unpacked. Same trade-off render_transcript_lg's own callers (the
    workflow curator prompt) already accept for that message; not solved
    here either."""
    results_by_id: dict[str, Any] = {}
    for message in messages:
        if message.type == "tool" and getattr(message, "tool_call_id", None):
            results_by_id[message.tool_call_id] = tool_result_value(message.content)
    entries: list[dict[str, Any]] = []
    for message in messages:
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            text = extract_text(message.content)
            if text:
                entries.append({"kind": "agent", "text": text})
            for call in tool_calls:
                if call["id"] not in results_by_id:
                    continue
                entries.append(
                    {
                        "kind": "tool",
                        "tool_name": call["name"],
                        "arguments": dict(call["args"]),
                        "result": results_by_id[call["id"]],
                    }
                )
            continue
        if message.type == "tool":
            continue  # already folded into its call's own entry above
        text = extract_text(message.content)
        if not text:
            continue
        if message.type == "human":
            entries.append({"kind": "user", "text": strip_mode_note(text)})
        else:
            entries.append({"kind": "agent", "text": text})
    return entries


def render_transcript_lg(messages: list[Any]) -> str:
    """LangGraph message-object equivalent of runtime/compaction.py's
    render_transcript (which works on dict-shaped OpenAI messages instead).
    Doesn't special-case/drop a system message the way that function does
    -- verified empirically that create_agent's checkpointed state never
    contains one in the first place (system_prompt is injected at call time
    only, not stored in the thread's message history)."""
    lines = []
    for message in messages:
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            names = ", ".join(tc["name"] for tc in tool_calls)
            lines.append(f"{message.type}: (called {names})")
            continue
        text = extract_text(message.content)
        if text:
            lines.append(f"{message.type}: {text}")
    return "\n".join(lines)
