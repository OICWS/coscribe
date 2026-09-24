"""Draft a workflow from a conversation that already did the task once.

The conversation is the evidence: every tool call that worked, with its
real arguments and what came back. A curator model turns that into a
workflow spec, which is checked here the same way a saved one is -- plus
against the tools that actually exist -- and sent back with the problems
when it doesn't hold up. The person reviews the draft in the step editor
before anything is saved, so this only has to be a good first pass."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from ..runtime_lg.messages import serialize_history_for_ws_lg
from .catalog import describe_params
from .engine import UNAVAILABLE_TOOLS
from .spec import ToolStep, Workflow, parse_workflow, workflow_error

SCRIPT_TOOL = "run_python_script"
MAX_ATTEMPTS = 3
_ARGS_CHARS = 4000
_RESULT_CHARS = 600
_TEXT_CHARS = 1500
_TRANSCRIPT_CHARS = 60_000

CURATOR_INSTRUCTIONS = """\
You turn a conversation in which an assistant did a task with tools into a \
workflow: a fixed list of steps that repeats the same task without an \
assistant deciding anything along the way. Reply with one JSON object and \
nothing else:

{"name": "<a few plain words, e.g. Monthly PDF error audit>", \
"workflow": {"version": 1, "inputs": [...], "steps": [...]}, \
"notes": ["<one line per thing the reviewer should check>"]}

If the conversation holds no repeatable task, reply {"error": "<why>"} instead.

Inputs -- values that change from run to run (usually file paths, dates, \
names): {"name": "pdf_path", "label": "PDF to audit", "type": "text" | \
"file" | "number", "default": <the value used in the conversation>}.

Steps -- every step has "id" (lowercase identifier, unique) and "title" \
(short, plain language), plus one "kind":
- "tool": {"tool": "<tool name>", "args": {...}, "save_as": "<name>"} -- \
calls one tool. Copy the arguments of the call that worked exactly, \
replacing only the values that became inputs or come from earlier steps.
- "script": {"code": "<python>", "inputs": {"pdf": "{{pdf_path}}"}, \
"save_as": "<name>"} -- for a run_python_script call. The code reads \
values from a ready-made `inputs` dict (never paste them into the code) \
and must print its result as JSON on its last line, e.g. \
print(json.dumps({"total": total})).
- "llm": {"prompt": "<text with {{references}}>", "fields": [{"name": \
"...", "type": "text" | "number" | "boolean" | "list", "description": \
"..."}], "save_as": "<name>"} -- one model call with no tools, for the \
places where the assistant judged, summarized or classified something. It \
must return exactly those fields.
- "check": {"conditions": [{"left": <operand>, "op": "eq" | "ne" | "gt" | \
"ge" | "lt" | "le" | "contains" | "not_empty", "right": <operand>}]} -- \
stops the run unless every condition holds. An operand is exactly one of \
{"ref": "name.field"}, {"count": "name"} (length of a list or text) or \
{"value": <literal>}; "not_empty" has no "right".
- "approval": {"message": "<text with {{references}}>", "when": \
<condition, optional>} -- pauses for a person. Use one before a step that \
overwrites or sends something, only if the conversation asked for a \
confirmation there.

References: "{{name}}" or "{{name.field}}" reads an input or an earlier \
step's save_as. A whole-string reference keeps the value's type; inside \
longer text it becomes text. A step can only read what comes before it. \
There are no expressions: no counts, filters or formatting inside {{ }}. \
When a prompt needs a number worked out, compute it in a script step first.

Rules:
- Use only the tools listed below, with only the parameters they take.
- Keep the calls that did the work. Leave out exploration (listing, \
peeking, reading the same thing again), failed attempts and anything \
undone later.
- Add a check wherever the conversation verified something (counts that \
must match, a value that must not be empty), grounded in what it found.
- Keep it as short as the task allows; the reviewer can add steps later.
- "notes" lists what you weren't sure about: guesses, values you turned \
into inputs, steps you dropped on purpose.
"""


class DraftFailed(Exception):
    """The conversation couldn't be turned into a workflow."""


@dataclass
class WorkflowDraft:
    name: str
    workflow: dict[str, Any]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "workflow": self.workflow, "notes": self.notes}


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}… [{len(text) - limit} more characters]"


def _as_json(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def render_conversation(messages: list[Any]) -> tuple[str, list[str]]:
    """The conversation as the curator reads it, and the tools it used
    successfully, in first-use order."""
    lines: list[str] = []
    used: list[str] = []
    call_number = 0
    for entry in serialize_history_for_ws_lg(messages):
        if entry["kind"] == "user":
            lines.append(f"[user] {_clip(entry['text'], _TEXT_CHARS)}")
        elif entry["kind"] == "agent":
            lines.append(f"[assistant] {_clip(entry['text'], _TEXT_CHARS)}")
        else:
            call_number += 1
            args = _clip(_as_json(entry["arguments"]), _ARGS_CHARS)
            result = _clip(_as_json(entry["result"]), _RESULT_CHARS)
            if entry["is_error"]:
                lines.append(f"[tool call #{call_number}, FAILED] {entry['tool_name']}({args})")
                lines.append(f"  error: {result}")
            else:
                lines.append(f"[tool call #{call_number}] {entry['tool_name']}({args})")
                lines.append(f"  result: {result}")
                if entry["tool_name"] not in used:
                    used.append(entry["tool_name"])
    transcript = "\n".join(lines)
    if len(transcript) > _TRANSCRIPT_CHARS:
        # The opening request says what the task is; the end is where it
        # got done. The middle is the likeliest to be exploration.
        half = _TRANSCRIPT_CHARS // 2
        transcript = (
            f"{transcript[:half]}\n[… middle of the conversation left out …]\n{transcript[-half:]}"
        )
    return transcript, used


def describe_tools(names: list[str], tools: dict[str, Callable[..., Any]]) -> str:
    lines = []
    for name in names:
        if name in UNAVAILABLE_TOOLS or name not in tools:
            continue
        if name == SCRIPT_TOOL:
            lines.append(f'- {SCRIPT_TOOL}: becomes a "script" step, not a tool step')
            continue
        doc = (inspect.getdoc(tools[name]) or "").split("\n\n", 1)[0].replace("\n", " ")
        params = ", ".join(
            f"{p['name']}{'' if p['required'] else '?'}: {p['type']}"
            + (f" -- {p['description']}" if p["description"] else "")
            for p in describe_params(tools[name])
        )
        lines.append(f"- {name}({params})\n  {_clip(doc, 300)}")
    return "\n".join(lines) or "(none)"


def check_draft(workflow: Workflow, tools: dict[str, Callable[..., Any]]) -> list[str]:
    """What stops this workflow from running here, beyond what the spec
    itself enforces."""
    if not workflow.steps:
        return ["the workflow has no steps"]
    problems = []
    for index, step in enumerate(workflow.steps, start=1):
        if not isinstance(step, ToolStep):
            continue
        if step.tool == SCRIPT_TOOL:
            problems.append(f"step {index}: use a script step instead of calling {SCRIPT_TOOL}")
            continue
        if step.tool in UNAVAILABLE_TOOLS or step.tool not in tools:
            problems.append(f"step {index}: there's no tool called {step.tool!r} to use")
            continue
        params = {p["name"]: p for p in describe_params(tools[step.tool])}
        unknown = sorted(set(step.args) - set(params))
        if unknown:
            problems.append(f"step {index}: {step.tool} takes no {', '.join(unknown)}")
        missing = [n for n, p in params.items() if p["required"] and n not in step.args]
        if missing:
            problems.append(f"step {index}: {step.tool} needs {', '.join(missing)}")
    return problems


def _reply_object(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("the reply has no JSON object")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("the reply isn't a JSON object")
    return data


def _reply_text(message: Any) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "") if isinstance(block, dict) else str(block) for block in content
    )


def _parse_reply(text: str, tools: dict[str, Callable[..., Any]], name_hint: str) -> WorkflowDraft:
    """The draft, or ValueError with every problem the curator should fix."""
    data = _reply_object(text)
    if "error" in data and "workflow" not in data:
        raise DraftFailed(str(data["error"]))
    try:
        workflow = parse_workflow(data.get("workflow"))
    except ValidationError as exc:
        raise ValueError(workflow_error(exc)) from exc
    problems = check_draft(workflow, tools)
    if problems:
        raise ValueError("; ".join(problems))
    notes = data.get("notes") or []
    if isinstance(notes, str):
        notes = [notes]
    name = name_hint or str(data.get("name") or "").strip() or "Untitled workflow"
    return WorkflowDraft(
        name=name[:120],
        workflow=workflow.model_dump(mode="json"),
        notes=[str(n) for n in notes if str(n).strip()],
    )


async def draft_workflow(
    model: Any,
    messages: list[Any],
    tools: dict[str, Callable[..., Any]],
    name_hint: str = "",
) -> WorkflowDraft:
    transcript, used = render_conversation(messages)
    if not used:
        raise DraftFailed(
            "This conversation hasn't used any tools yet, so there's nothing to repeat. "
            "Do the task once here first."
        )
    if "temperature" in getattr(type(model), "model_fields", {}):
        model = model.model_copy(update={"temperature": 0})
    name_line = f'Name the workflow "{name_hint}".\n\n' if name_hint else ""
    conversation: list[Any] = [
        SystemMessage(CURATOR_INSTRUCTIONS),
        HumanMessage(
            f"{name_line}Tools you can use:\n{describe_tools(used, tools)}\n\n"
            f"The conversation:\n\n{transcript}"
        ),
    ]
    problem = ""
    for _attempt in range(MAX_ATTEMPTS):
        reply = await model.ainvoke(conversation)
        text = _reply_text(reply)
        try:
            return _parse_reply(text, tools, name_hint)
        except (ValueError, TypeError) as exc:
            problem = str(exc)
        conversation += [
            AIMessage(text),
            HumanMessage(f"That draft doesn't work: {problem}. Reply with the corrected JSON."),
        ]
    raise DraftFailed(
        f"Couldn't draft a valid workflow ({problem}). Try again, or build it by hand."
    )
