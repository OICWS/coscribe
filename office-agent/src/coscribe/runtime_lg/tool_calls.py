"""Every tool call in a history needs a result after it, or the provider
rejects the whole request with a 400."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

ORPHANED_TOOL_CALL_NOTE = "Stopped before this tool finished -- it produced no result."


def answer_every_tool_call(messages: list[Any]) -> tuple[list[Any], bool]:
    """`messages` with a placeholder result for each tool call that never got
    one, and whether any was added. The placeholder sits directly after its
    AIMessage -- after the results that did arrive (a partly finished
    parallel batch), before whatever follows -- since a history already
    corrupted has a newer message after the orphan."""
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    rebuilt: list[Any] = []
    patched = False
    index = 0
    while index < len(messages):
        message = messages[index]
        rebuilt.append(message)
        index += 1
        if not isinstance(message, AIMessage) or not message.tool_calls:
            continue
        while index < len(messages) and isinstance(messages[index], ToolMessage):
            rebuilt.append(messages[index])
            index += 1
        for call in message.tool_calls:
            if call["id"] not in answered:
                patched = True
                rebuilt.append(
                    ToolMessage(
                        content=ORPHANED_TOOL_CALL_NOTE,
                        tool_call_id=call["id"],
                        name=call["name"],
                    )
                )
    return rebuilt, patched
