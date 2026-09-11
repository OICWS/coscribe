from pathlib import Path
from typing import Any

import pytest
from aisuite import Tools

from coscribe.tools.workflows import (
    Workflow,
    WorkflowRun,
    WorkflowRunStore,
    WorkflowStep,
    WorkflowStore,
    build_workflow_tools,
    reconcile_interrupted_runs,
)


def _crud_tools(state_dir: Path) -> dict[str, Any]:
    return {tool.__name__: tool for tool in build_workflow_tools(state_dir)}


# -- WorkflowStore -------------------------------------------------------------


def test_workflow_store_round_trip(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path)
    workflow = Workflow(
        name="demo",
        mode="chain",
        summary="does a demo thing",
        steps=[WorkflowStep(tool_name="write_file", arguments={"path": "x.txt"})],
        source_thread_id="t1",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    store.save(workflow)
    loaded = store.load("demo")

    assert loaded == workflow
    assert [w.name for w in store.list_all()] == ["demo"]

    assert store.delete("demo") is True
    assert store.load("demo") is None
    assert store.delete("demo") is False


def test_workflow_store_list_all_empty_when_no_workflows_dir(tmp_path: Path) -> None:
    assert WorkflowStore(tmp_path).list_all() == []


# -- list/get/delete ---------------------------------------------------------------


def test_list_get_delete_workflow(tmp_path: Path) -> None:
    tools = _crud_tools(tmp_path)
    WorkflowStore(tmp_path).save(Workflow(name="demo", mode="agent", summary="a thing"))

    assert [w["name"] for w in tools["list_workflows"]()] == ["demo"]
    assert tools["get_workflow"](name="demo")["summary"] == "a thing"
    assert tools["delete_workflow"](name="demo") == {"deleted": "demo"}
    assert tools["list_workflows"]() == []


def test_get_workflow_unknown_raises(tmp_path: Path) -> None:
    tools = _crud_tools(tmp_path)

    with pytest.raises(KeyError):
        tools["get_workflow"](name="nope")


def test_delete_workflow_unknown_raises(tmp_path: Path) -> None:
    tools = _crud_tools(tmp_path)

    with pytest.raises(KeyError):
        tools["delete_workflow"](name="nope")


# -- schema validity ---------------------------------------------------------------


def test_workflow_tool_schemas_are_valid_openai_tool_spec(tmp_path: Path) -> None:
    """Regression test mirroring test_spawn_agent_schema_is_valid_openai_tool_spec
    -- confirms every model-callable workflow tool (list_workflows,
    get_workflow, delete_workflow) produces a valid schema."""
    crud_tools = list(build_workflow_tools(tmp_path))

    schemas = Tools(crud_tools).tools()

    valid_types = {"string", "integer", "number", "boolean", "array", "object"}
    for schema in schemas:
        properties = schema["function"]["parameters"]["properties"]
        for param_name, prop in properties.items():
            assert prop["type"] in valid_types, (
                f"{schema['function']['name']}.{param_name} has invalid schema "
                f"type {prop['type']!r}"
            )


# -- WorkflowRunStore -----------------------------------------------------------


def test_workflow_run_store_round_trip(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    run = WorkflowRun(
        run_id="run-1",
        workflow_name="demo",
        mode="chain",
        status="running",
        started_at="2026-01-01T00:00:00+00:00",
    )

    store.save(run)
    loaded = store.load("run-1")

    assert loaded == run
    assert store.load("nope") is None


def test_workflow_run_store_list_recent_sorted_newest_first(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    store.save(
        WorkflowRun(
            run_id="older",
            workflow_name="demo",
            mode="chain",
            status="completed",
            started_at="2026-01-01T00:00:00+00:00",
        )
    )
    store.save(
        WorkflowRun(
            run_id="newer",
            workflow_name="demo",
            mode="chain",
            status="completed",
            started_at="2026-01-02T00:00:00+00:00",
        )
    )

    runs = store.list_recent()

    assert [r.run_id for r in runs] == ["newer", "older"]


def test_workflow_run_store_list_recent_respects_limit(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    for i in range(5):
        store.save(
            WorkflowRun(
                run_id=f"run-{i}",
                workflow_name="demo",
                mode="chain",
                status="completed",
                started_at=f"2026-01-0{i + 1}T00:00:00+00:00",
            )
        )

    assert len(store.list_recent(limit=2)) == 2


def test_workflow_run_store_list_recent_empty_when_no_runs_dir(tmp_path: Path) -> None:
    assert WorkflowRunStore(tmp_path).list_recent() == []


def test_workflow_run_store_delete_round_trip(tmp_path: Path) -> None:
    store = WorkflowRunStore(tmp_path)
    store.save(
        WorkflowRun(
            run_id="run-1",
            workflow_name="demo",
            mode="chain",
            status="completed",
            started_at="2026-01-01T00:00:00+00:00",
        )
    )

    assert store.delete("run-1") is True
    assert store.load("run-1") is None
    assert store.delete("run-1") is False


def test_reconcile_interrupted_runs_marks_stuck_running_records_as_failed(
    tmp_path: Path,
) -> None:
    # Direct regression test for the live-reported bug: a run left at
    # status="running" (the previous process died mid-run, before
    # run_workflow's own finalization code ever ran) stays stuck forever
    # with no way to ever revisit it -- reconcile_interrupted_runs, called
    # once at process startup, is what actually clears it.
    store = WorkflowRunStore(tmp_path)
    store.save(
        WorkflowRun(
            run_id="stuck-run",
            workflow_name="demo",
            mode="agent",
            status="running",
            started_at="2026-01-01T00:00:00+00:00",
        )
    )
    store.save(
        WorkflowRun(
            run_id="finished-run",
            workflow_name="demo",
            mode="chain",
            status="completed",
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:01:00+00:00",
        )
    )

    fixed = reconcile_interrupted_runs(tmp_path)

    assert fixed == 1
    stuck = store.load("stuck-run")
    assert stuck is not None
    assert stuck.status == "failed"
    assert stuck.error is not None and "Interrupted" in stuck.error
    assert stuck.finished_at is not None
    # Untouched -- already had a terminal status.
    finished = store.load("finished-run")
    assert finished is not None
    assert finished.status == "completed"
    assert finished.finished_at == "2026-01-01T00:01:00+00:00"


def test_reconcile_interrupted_runs_no_runs_dir_returns_zero(tmp_path: Path) -> None:
    assert reconcile_interrupted_runs(tmp_path) == 0
