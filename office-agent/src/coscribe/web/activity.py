"""What a conversation has done so far, for the side panel: the files it
produced (Outputs) and the files, tools, skills and connectors it drew on
(Context). Derived from the checkpointed messages rather than recorded as
calls happen, so it covers the whole thread -- including runs nobody
watched -- and can't drift from what actually ran."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from ..runtime.types import ToolMetadata
from ..tools._workspace import WorkspaceScope

_OUTPUT_RISKS = frozenset({"WRITE_LOCAL", "EXTERNAL"})
# Arguments naming a file the call reads, whichever tool takes them.
_INPUT_PATH_ARGS = ("source_path", "template_path", "image_path", "audio_path")
_SCRIPT_TOOLS = frozenset({"run_python_script", "run_node_script"})
_SKILL_TOOLS = frozenset({"load_skill", "read_skill_file"})
# Plumbing the model uses on nearly every turn -- listing them as "tools
# used" would bury the ones that say something about the work.
_UNLISTED_TOOLS = frozenset({"task_create", "task_update", "task_list", "ask_user_question"})

# Opening a file hands it to whatever the OS associates with it, so only
# document/media types are offered -- a script or executable a run wrote
# must never be one click from running.
OPENABLE_EXTENSIONS = frozenset(
    {
        ".doc", ".docx", ".xls", ".xlsx", ".csv", ".ppt", ".pptx", ".pdf",
        ".txt", ".md", ".json", ".png", ".jpg", ".jpeg", ".gif", ".webp",
        ".svg", ".mp3", ".wav", ".mp4",
    }
)  # fmt: skip


def _succeeded_calls(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """Every tool call that ran without error, in order, each with its
    result text -- a rejected or failed call changed nothing."""
    results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    calls = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            result = results.get(call.get("id") or "")
            if result is None or result.status == "error":
                continue
            calls.append({"name": call["name"], "args": call["args"], "result": result.content})
    return calls


def _lines_removed(result: Any) -> int:
    try:
        parsed = json.loads(result) if isinstance(result, str) else result
    except ValueError:
        return 0
    removed = parsed.get("lines_removed") if isinstance(parsed, dict) else None
    return removed if isinstance(removed, int) else 0


def _script_files(result: Any) -> list[str]:
    try:
        parsed = json.loads(result) if isinstance(result, str) else None
    except ValueError:
        return []
    written = parsed.get("files_written") if isinstance(parsed, dict) else None
    return [p for p in written if isinstance(p, str)] if isinstance(written, list) else []


# Folders a working process leaves its checks in: not something the person
# asked for, so not listed among what the conversation produced.
_SCRATCH_FOLDERS = frozenset({"qa", "tmp", "temp", "scratch", "__pycache__", "node_modules"})


def _is_scratch(relative: str) -> bool:
    parts = Path(relative.replace("\\", "/")).parts[:-1]
    return any(part.lower() in _SCRATCH_FOLDERS or part.startswith(".") for part in parts)


def _file_entry(scope: WorkspaceScope, path: str) -> dict[str, Any] | None:
    try:
        resolved = scope.resolve(path)
    except (PermissionError, OSError, ValueError):
        return None
    entry: dict[str, Any] = {
        "path": scope.relative(resolved),
        "name": resolved.name,
        "exists": resolved.is_file(),
        "openable": resolved.suffix.lower() in OPENABLE_EXTENSIONS,
        "modified_at": None,
    }
    if entry["exists"]:
        entry["modified_at"] = datetime.fromtimestamp(resolved.stat().st_mtime).isoformat()
    elif resolved.exists():
        return None  # a directory (list_files(".") and the like)
    return entry


def summarize_activity(
    messages: list[BaseMessage], catalog: dict[str, ToolMetadata], scope: WorkspaceScope
) -> dict[str, Any]:
    # (path, action) in call order; action is "write" (produces a whole
    # file), "edit" (changes one in place) or "read".
    touches: list[tuple[str, str]] = []
    tool_counts: dict[str, int] = {}
    connectors: dict[str, dict[str, int]] = {}
    skills: list[str] = []

    for call in _succeeded_calls(messages):
        name, args = call["name"], call["args"]
        metadata = catalog.get(name)
        category = metadata.category if metadata is not None else None
        if name in _SKILL_TOOLS:
            skill = args.get("name")
            if isinstance(skill, str) and skill not in skills:
                skills.append(skill)
            continue
        if category is not None and category.startswith("mcp:"):
            server_tools = connectors.setdefault(category.removeprefix("mcp:"), {})
            server_tools[name] = server_tools.get(name, 0) + 1
            continue
        if name not in _UNLISTED_TOOLS:
            tool_counts[name] = tool_counts.get(name, 0) + 1
        if name in _SCRIPT_TOOLS:
            touches.extend((p, "write") for p in _script_files(call["result"]))
        path = args.get("path")
        if isinstance(path, str) and path:
            if metadata is None or metadata.risk_category not in _OUTPUT_RISKS:
                touches.append((path, "read"))
            elif name.startswith("write_") and _lines_removed(call["result"]) == 0:
                touches.append((path, "write"))
            else:
                touches.append((path, "edit"))
        touches.extend(
            (a, "read") for k in _INPUT_PATH_ARGS if isinstance(a := args.get(k), str) and a
        )

    # A file the conversation brought into being is "created" however often
    # it was changed after; one it changed that already existed (touched
    # first by a read or an in-place edit) is "edited"; the rest were read.
    actions: dict[str, str] = {}
    entries: dict[str, dict[str, Any]] = {}
    last_touched: list[str] = []
    for path, touch in touches:
        entry = _file_entry(scope, path)
        if entry is None:
            continue
        key = entry["path"]
        entries[key] = entry
        last_touched.append(key)
        previous = actions.get(key)
        if touch == "read":
            actions.setdefault(key, "read")
        elif previous is None:
            actions[key] = "created" if touch == "write" else "edited"
        elif previous == "read":
            actions[key] = "edited"
    outputs: list[dict[str, Any]] = []
    for key in dict.fromkeys(reversed(last_touched)):  # most recently touched first
        if actions[key] != "read" and not _is_scratch(key):
            outputs.append({**entries[key], "action": actions[key]})
    references = [
        {**entries[key], "action": "read"}
        for key in dict.fromkeys(last_touched)  # in the order first read
        if actions[key] == "read" and entries[key]["exists"]
    ]

    return {
        "outputs": outputs,
        "references": references,
        "tools": [{"name": n, "count": c} for n, c in tool_counts.items()],
        "connectors": [
            {"server": server, "tools": [{"name": n, "count": c} for n, c in used.items()]}
            for server, used in connectors.items()
        ],
        "skills": skills,
    }


def open_in_os(path: Path, *, reveal: bool) -> None:
    """Open with the default app, or show it in the file manager. Raises
    OSError (FileNotFoundError when there's no opener, e.g. a headless
    Linux box)."""
    if sys.platform == "win32":
        if reveal:
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606 -- Windows-only
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if reveal else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if reveal else path)])


def _all_steps(steps: list[Any]) -> list[dict[str, Any]]:
    """Every step dict of a stored workflow, branches' and loops' included."""
    found: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        found.append(step)
        for arm in ("then", "otherwise", "steps"):
            children = step.get(arm)
            if isinstance(children, list):
                found.extend(_all_steps(children))
    return found


def summarize_workflow_run(
    workflow: dict[str, Any],
    run: Any,
    catalog: dict[str, ToolMetadata],
    scope: WorkspaceScope,
) -> dict[str, Any]:
    """The same shape as summarize_activity, for a workflow run -- whose
    thread holds no conversation to read, but whose step records say what
    each step did."""
    # One record per step per loop pass; each pass that ran counts.
    steps = {step.get("id"): step for step in _all_steps(workflow.get("steps", []))}
    output_paths: list[str] = []
    tool_counts: dict[str, int] = {}
    connectors: dict[str, dict[str, int]] = {}
    for record in run.steps:
        step = steps.get(record.get("step_id"))
        if step is None or record.get("status") != "done":
            continue
        if step.get("kind") == "script":
            tool_counts["run_python_script"] = tool_counts.get("run_python_script", 0) + 1
            continue
        if step.get("kind") != "tool":
            continue
        name = step.get("tool", "")
        metadata = catalog.get(name)
        category = metadata.category if metadata is not None else None
        if category is not None and category.startswith("mcp:"):
            server_tools = connectors.setdefault(category.removeprefix("mcp:"), {})
            server_tools[name] = server_tools.get(name, 0) + 1
            continue
        tool_counts[name] = tool_counts.get(name, 0) + 1
        output = record.get("output")
        if (
            metadata is not None
            and metadata.risk_category in _OUTPUT_RISKS
            and isinstance(output, dict)
            and isinstance(output.get("path"), str)
            and output["path"] not in output_paths
        ):
            output_paths.append(output["path"])

    outputs = [e for p in reversed(output_paths) if (e := _file_entry(scope, p)) is not None]
    given = run.inputs or {}
    references = []
    for spec in workflow.get("inputs", []):
        value = given.get(spec.get("name"), spec.get("default"))
        if spec.get("type") == "file" and isinstance(value, str) and value:
            entry = _file_entry(scope, value)
            if entry is not None and entry["exists"]:
                references.append(entry)
    return {
        "outputs": outputs,
        "references": references,
        "tools": [{"name": n, "count": c} for n, c in tool_counts.items()],
        "connectors": [
            {"server": server, "tools": [{"name": n, "count": c} for n, c in used.items()]}
            for server, used in connectors.items()
        ],
        "skills": [],
    }
