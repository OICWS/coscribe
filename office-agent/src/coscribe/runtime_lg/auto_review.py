"""Auto mode's reviewer: a second model call that decides, in place of the
user, whether one risky action may run.

Modeled on Claude Code's auto mode (code.claude.com/docs/en/permission-modes):
the reviewer allows routine work inside the conversation's folders and
blocks the kinds of action that are hard to take back or reach outside the
computer, unless the user asked for that specific action in the
conversation. A blocked action is refused back to the model with the
reason; the session falls back to asking the user after repeated blocks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.constants import TAG_NOSTREAM

from .messages import extract_text

REVIEWER_INSTRUCTIONS = """\
You review one action an assistant is about to take on the user's computer, \
in place of asking the user. Decide whether it may run.

Allow it when it is ordinary work toward what the user asked: creating or \
editing files in the conversation's folders, running a script that reads, \
computes or writes there, opening or reading web pages, searching, and any \
action the user asked for in so many words.

Block it when it is one of these, unless the user asked for this specific \
action (naming what makes it risky -- "you can delete old.xlsx", not just \
"go ahead"):
- downloading code or programs and running them, or installing software \
system-wide
- sending the user's files or information outside this computer (email, \
upload, posting, a form) when the user didn't ask for that send
- deleting or overwriting files that existed before this conversation, or \
changing files outside the conversation's folders
- mass changes: many files, whole folders, bulk operations the task doesn't need
- acting in the user's accounts: sending messages, submitting forms, \
purchases, changing settings or permissions
- changing system settings or anything shared with other people
- anything the user said not to do (a stated boundary stays until they lift it)

When unsure whether something is destructive or leaves the computer, block.

Reply with one JSON object and nothing else: \
{"decision": "allow" | "block", "reason": "<one short sentence>"}"""

_ARGS_CHARS = 3000
_CONTEXT_CHARS = 6000


@dataclass
class Verdict:
    allow: bool
    reason: str


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " [...]"


def recent_user_requests(messages: list[Any], limit: int = 8) -> str:
    """What the user has said lately -- where requests, approvals and
    boundaries come from."""
    said = [
        extract_text(m.content).strip()
        for m in messages
        if getattr(m, "type", None) == "human" and extract_text(m.content).strip()
    ]
    return _clip("\n---\n".join(said[-limit:]), _CONTEXT_CHARS)


def _parse(text: str) -> Verdict | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("decision") not in ("allow", "block"):
        return None
    return Verdict(allow=data["decision"] == "allow", reason=str(data.get("reason") or "").strip())


async def review_action(
    model: Any,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    risk: str,
    folders: list[str],
    user_requests: str,
) -> Verdict | None:
    """The reviewer's verdict, or None when it gave none it could read."""
    if "temperature" in getattr(type(model), "model_fields", {}):
        model = model.model_copy(update={"temperature": 0})
    action = _clip(json.dumps(arguments, ensure_ascii=False, default=str), _ARGS_CHARS)
    request = (
        f"The conversation's folders: {', '.join(folders) or '(none)'}\n\n"
        f"What the user has said, oldest first:\n{user_requests or '(nothing yet)'}\n\n"
        f"The action: {tool_name}({action})\n"
        f"Its kind: {risk} (WRITE_LOCAL changes this computer's files or data, EXEC runs "
        "code, EXTERNAL acts outside this computer)"
    )
    try:
        reply = await model.ainvoke(
            [SystemMessage(REVIEWER_INSTRUCTIONS), HumanMessage(request)],
            config={"tags": [TAG_NOSTREAM]},
        )
    except Exception:  # noqa: BLE001 -- no verdict; the caller asks the user instead
        return None
    return _parse(extract_text(reply.content))
