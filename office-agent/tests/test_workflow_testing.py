from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from coscribe.workflow_spec import parse_workflow
from coscribe.workflows.engine import StepContext, StepRecord
from coscribe.workflows.testing import run_test, uses_browser


def _ctx(tmp_path: Path, tools: dict[str, Any]) -> StepContext:
    return StepContext(
        tools=tools,
        workspace_root=tmp_path,
        state_dir=tmp_path / "state",
        make_model=lambda name: None,
    )


def _read(step_id: str, path: str) -> dict[str, Any]:
    return {
        "id": step_id,
        "kind": "tool",
        "title": f"Read {path}",
        "tool": "read_file",
        "args": {"path": path},
        "save_as": step_id,
    }


def _read_file(path: str) -> str:
    if path == "missing.txt":
        raise FileNotFoundError(f"No such file: {path}")
    return f"text of {path}" * 100


async def test_a_failed_test_names_the_step_and_lists_the_unreached(tmp_path: Path) -> None:
    workflow = parse_workflow(
        {"steps": [_read("a", "a.txt"), _read("b", "missing.txt"), _read("c", "c.txt")]}
    )
    seen: list[tuple[str, str]] = []

    async def on_step(record: StepRecord) -> None:
        seen.append((record.step_id, record.status))

    result = await run_test(workflow, _ctx(tmp_path, {"read_file": _read_file}), None, on_step)

    assert result["status"] == "failed"
    assert result["failed_step"] == "b" and "missing.txt" in result["error"]
    assert [s["status"] for s in result["steps"]] == ["done", "failed", "not reached"]
    assert result["steps"][0]["output"].endswith("…") and len(result["steps"][0]["output"]) == 401
    assert seen == [("a", "running"), ("a", "done"), ("b", "running"), ("b", "failed")]


async def test_a_test_stops_before_an_approval(tmp_path: Path) -> None:
    approve = {"id": "ok", "kind": "approval", "title": "OK?", "message": "Send it?"}
    workflow = parse_workflow({"steps": [_read("a", "a.txt"), approve, _read("c", "c.txt")]})

    result = await run_test(workflow, _ctx(tmp_path, {"read_file": _read_file}))

    assert result["status"] == "stopped_at_approval"
    assert result["failed_step"] is None and result["error"] is None
    assert [s["status"] for s in result["steps"]] == ["done", "waiting", "not reached"]


def _send(step_id: str) -> dict[str, Any]:
    return {"id": step_id, "kind": "tool", "title": "Submit the claim", "tool": "send",
            "args": {"to": "{{row}}"}, "irreversible": True}  # fmt: skip


async def test_a_test_stops_before_a_step_that_cant_be_taken_back(tmp_path: Path) -> None:
    sent: list[str] = []
    loop = {"id": "each", "kind": "loop", "title": "Each row", "over": "rows", "item": "row",
            "steps": [_send("submit")]}  # fmt: skip
    rows = {"id": "rows", "kind": "tool", "title": "Read the rows", "tool": "rows",
            "save_as": "rows"}  # fmt: skip
    workflow = parse_workflow({"steps": [rows, loop]})
    tools = {"rows": lambda: ["a", "b"], "send": lambda to: sent.append(to)}

    result = await run_test(workflow, _ctx(tmp_path, tools))

    assert result["status"] == "stopped_before_irreversible"
    assert result["stopped_before"] == {"id": "submit", "title": "Submit the claim"}
    assert [s["id"] for s in result["steps"]] == ["rows", "each", "submit"]
    assert result["steps"][2]["status"] == "not reached"
    assert sent == []

    for_real = await run_test(workflow, _ctx(tmp_path, tools), send_for_real=True)

    assert for_real["status"] == "passed"
    assert sent == ["a", "b"]


async def test_a_workflow_that_cant_run_fails_the_test(tmp_path: Path) -> None:
    workflow = parse_workflow({"steps": [_read("a", "a.txt")]})

    result = await run_test(workflow, _ctx(tmp_path, {}))

    assert result["status"] == "failed" and "read_file" in result["error"]


async def test_a_test_lists_the_files_it_wrote_with_their_shape(tmp_path: Path) -> None:
    (tmp_path / "old.csv").write_text("a,b\n", encoding="utf-8")
    os.utime(tmp_path / "old.csv", (1, 1))

    def write_file(path: str, content: str) -> dict[str, Any]:
        (tmp_path / path).write_bytes(content.encode("utf-8"))
        return {"path": path, "note": {"files": ["old.csv", "missing.csv"]}}

    step = {
        "id": "w",
        "kind": "tool",
        "title": "Write",
        "tool": "write_file",
        "args": {"path": "out.csv", "content": "name,amount\nA,1\nB,2\n"},
    }
    result = await run_test(
        parse_workflow({"steps": [step]}), _ctx(tmp_path, {"write_file": write_file})
    )

    assert result["status"] == "passed"
    assert result["files"] == [{"path": "out.csv", "size": 20, "rows": 3, "columns": 2}]


def test_uses_browser_looks_inside_nested_steps() -> None:
    click = {
        "id": "click",
        "kind": "tool",
        "title": "Click",
        "tool": "browser_click",
        "args": {"ref": 'button "Go"'},
    }
    loop = {
        "id": "each",
        "kind": "loop",
        "title": "Each",
        "over": "a",
        "item": "x",
        "steps": [click],
    }

    assert uses_browser(parse_workflow({"steps": [_read("a", "a.txt"), loop]}))
    assert not uses_browser(parse_workflow({"steps": [_read("a", "a.txt")]}))


def test_browser_runs_lock_per_thread() -> None:
    from coscribe.tools import browser_run

    assert browser_run("a") is browser_run("a")
    assert browser_run("a") is not browser_run("b")
