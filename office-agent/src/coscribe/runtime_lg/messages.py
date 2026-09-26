"""Small LangGraph-message-shape helpers shared between web/session.py
(a live turn's own streaming) and the curator prompts in runtime_lg/
(e.g. skill_authoring.py), kept here so runtime_lg modules can use them
without importing session.py (which already imports from runtime_lg).
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
    "[plan mode is ON: research with read-only tools and write a plan; nothing "
    "else will run. When the plan is ready, call exit_plan_mode(plan) -- the "
    "user approves it before any change is made] "
)
ACCEPT_EDITS_MODE_NOTE = (
    "[accept-edits mode is ON: file edits in your folders run without asking; "
    "running code and acting outside this computer still ask] "
)
AUTO_MODE_NOTE = (
    "[auto mode is ON: a reviewer model checks each risky action instead of the "
    "user; one it blocks comes back refused with the reason -- find another way, "
    "or ask the user to allow that specific action] "
)
NORMAL_MODE_NOTE = "[normal mode: risky tool calls ask for approval as usual] "
# Earlier wordings, still at the front of checkpointed messages.
_OLD_MODE_NOTES = (
    "[plan mode is ON: only read-only/task-tracking tools will run, everything else is blocked] ",
    "[accept-edits mode is ON: tool calls run without asking] ",
)
_MODE_NOTES = (
    PLAN_MODE_NOTE,
    ACCEPT_EDITS_MODE_NOTE,
    AUTO_MODE_NOTE,
    NORMAL_MODE_NOTE,
    *_OLD_MODE_NOTES,
)

# Also matches the older date-only note, which checkpointed history still
# carries.
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_DATE_NOTE_RE = re.compile(
    r"^Today's real date is \d{4}-\d{2}-\d{2}"
    r"(?: \([A-Za-z]+\); local time \d{2}:\d{2} \(UTC[+-]\d{2}:\d{2}\))?\.\s+"
)


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
    strip it back off the same way it does mode_note's.

    Also carries the weekday, local time and UTC offset: the model has no
    other clock. The weekday is spelled out here, not with %A, which follows
    the OS locale and would stop _DATE_NOTE_RE from matching."""
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    offset = f"{offset[:3]}:{offset[3:]}" if offset else "+00:00"
    return (
        f"Today's real date is {now.strftime('%Y-%m-%d')} ({_WEEKDAYS[now.weekday()]}); "
        f"local time {now.strftime('%H:%M')} (UTC{offset}). "
    )


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


def extract_images(content: Any) -> list[str]:
    """A HumanMessage's own image_url content blocks (see web/session.py's
    _handle_user_message_locked, which appends `{"type": "image_url",
    "image_url": {"url": ...}}` per attached image) -- the data URLs
    themselves, in the order they were attached. Mirrors extract_text's
    same list-of-content-blocks walk, just pulling a different block
    type; a plain-string .content (every non-multimodal message) has no
    images by construction."""
    if not isinstance(content, list):
        return []
    urls = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "image_url":
            url = block.get("image_url", {}).get("url")
            if url:
                urls.append(url)
    return urls


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

    Skips a tool call with no matching ToolMessage yet: an in-flight/
    still-pending call (most commonly a not-yet-answered approval from before a
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

    Each entry also carries `is_error`, straight off the checkpointed
    ToolMessage's own `.status` field ("success"/"error", set by
    agent.py's _CatchToolErrorsMiddleware whenever the tool's Python body
    raised) -- the one place a genuinely generic, works-for-every-tool
    failure signal already exists server-side. Same field the live
    "tool_result" WS event now sends (see session.py's _stream_turn), so a
    replayed entry's failure indicator matches a live one's exactly.

    A "user" entry has its mode_note prefix (see strip_mode_note above)
    stripped back off -- the live bubble the user actually saw when they
    hit Send never had it, so a history replay shouldn't show it either.

    Real, user-reported bug this also closes: a "user" entry's own
    attached images used to be silently dropped on replay -- the images
    were already faithfully checkpointed (baked into the HumanMessage's
    own image_url content blocks, see web/session.py's
    _handle_user_message_locked), just never read back out here, so they
    rendered fine for the rest of the *live* WS connection that sent them
    (the frontend's own optimistic local echo still had the data URLs in
    memory) but vanished the moment that connection's history got
    replayed -- a reload, a reconnect, or switching threads and back.
    Fixed via extract_images (this module) alongside extract_text; an
    image-only send (no caption) is also no longer dropped entirely by
    the empty-text skip below, which used to fire before any image was
    ever looked at.

    Known, accepted gap for v1: a /compact'd thread's history includes the
    synthetic HumanMessage _handle_compact replaces the tail with (the
    rendered pre-compaction transcript, folded into one message) -- it
    replays as one large "user" entry rather than being specially
    unpacked. Same trade-off render_transcript_lg's own callers (the
    /saveskill curator prompt) already accept for that message."""
    results_by_id: dict[str, tuple[Any, bool]] = {}
    for message in messages:
        if message.type == "tool" and getattr(message, "tool_call_id", None):
            is_error = getattr(message, "status", "success") == "error"
            results_by_id[message.tool_call_id] = (tool_result_value(message.content), is_error)
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
                result, is_error = results_by_id[call["id"]]
                entries.append(
                    {
                        "kind": "tool",
                        "tool_name": call["name"],
                        "arguments": dict(call["args"]),
                        "result": result,
                        "is_error": is_error,
                    }
                )
            continue
        if message.type == "tool":
            continue  # already folded into its call's own entry above
        text = extract_text(message.content)
        if message.type == "human":
            # Checked before the `if not text` skip below -- an image-only
            # send (no caption text) has an empty extract_text() result,
            # but real content (the image itself) that a bare text check
            # would otherwise drop the whole entry over.
            images = extract_images(message.content)
            if not text and not images:
                continue
            entry: dict[str, Any] = {"kind": "user", "text": strip_mode_note(text)}
            if images:
                entry["images"] = images
            entries.append(entry)
            continue
        if not text:
            continue
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
