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


def _script_files(result: Any) -> list[str]:
    try:
        parsed = json.loads(result) if isinstance(result, str) else None
    except ValueError:
        return []
    written = parsed.get("files_written") if isinstance(parsed, dict) else None
    return [p for p in written if isinstance(p, str)] if isinstance(written, list) else []


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
    output_paths: list[str] = []
    input_paths: list[str] = []
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
            output_paths.extend(_script_files(call["result"]))
        path = args.get("path")
        if isinstance(path, str) and path:
            is_output = metadata is not None and metadata.risk_category in _OUTPUT_RISKS
            (output_paths if is_output else input_paths).append(path)
        input_paths.extend(a for k in _INPUT_PATH_ARGS if isinstance(a := args.get(k), str) and a)

    outputs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in reversed(output_paths):  # most recently touched first
        entry = _file_entry(scope, path)
        if entry is not None and entry["path"] not in seen:
            seen.add(entry["path"])
            outputs.append(entry)
    references: list[dict[str, Any]] = []
    for path in input_paths:
        entry = _file_entry(scope, path)
        if entry is not None and entry["exists"] and entry["path"] not in seen:
            seen.add(entry["path"])
            references.append(entry)

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


def summarize_workflow_run(
    workflow: dict[str, Any],
    run: Any,
    catalog: dict[str, ToolMetadata],
    scope: WorkspaceScope,
) -> dict[str, Any]:
    """The same shape as summarize_activity, for a workflow run -- whose
    thread holds no conversation to read, but whose step records say what
    each step did."""
    records = {r.get("step_id"): r for r in run.steps}
    output_paths: list[str] = []
    tool_counts: dict[str, int] = {}
    connectors: dict[str, dict[str, int]] = {}
    for step in workflow.get("steps", []):
        record = records.get(step.get("id"))
        if record is None or record.get("status") != "done":
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
