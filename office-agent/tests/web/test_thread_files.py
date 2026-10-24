# ruff: noqa: E402
"""Web tests: the thread-files service without the app."""

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")


from coscribe.runtime import DEFAULT_ENVIRONMENT, Environments
from coscribe.web.thread_files import ThreadFiles
from tests.environment_helpers import in_environment

from .helpers import (
    _settings,
)


def test_a_conversations_folders_are_its_environments(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    files = ThreadFiles(settings)
    in_environment(settings.state_dir, "t1", folders=[str(tmp_path)])

    assert files.folders_of("t1") == [str(tmp_path)]
    assert files.resolve_folders("t1") == [tmp_path]
    assert files.folders_of("other") == []
    # Only a scheduled run keeps folders of its own.
    files.write_workspace_sidecar("t1", [str(tmp_path / "x")])
    assert not files.workspace_sidecar_path("t1").exists()


def test_a_scheduled_runs_sidecar_round_trips_and_reads_one_from_before_multi_folder(
    tmp_path: Path,
) -> None:
    files = ThreadFiles(_settings(tmp_path))
    files.write_workspace_sidecar("scheduled-x-r1", [str(tmp_path / "a"), str(tmp_path / "b")])
    assert files.folders_of("scheduled-x-r1") == [str(tmp_path / "a"), str(tmp_path / "b")]
    files.write_workspace_sidecar("scheduled-x-r1", [])
    assert not files.workspace_sidecar_path("scheduled-x-r1").exists()

    files.workspace_sidecar_path("t_old").write_text(str(tmp_path / "old"), encoding="utf-8")
    assert files.legacy_folders("t_old") == [str(tmp_path / "old")]


def test_thread_files_open_a_scheduled_run_in_its_tasks_environments_folders(
    tmp_path: Path,
) -> None:
    from coscribe.tools.bookkeeping.scheduled_tasks import (
        ScheduledTriggerStore,
        create_trigger,
        run_thread_id,
    )

    settings = _settings(tmp_path)
    folder = tmp_path / "task-folder"
    folder.mkdir()
    env = Environments(settings.state_dir).create({"name": "T", "folders": [str(folder)]}, set())
    task = create_trigger(
        ScheduledTriggerStore(settings.state_dir),
        name="t",
        kind="manual",
        at="",
        prompt="p",
        environment=env["id"],
    )
    files = ThreadFiles(settings)
    thread_id = run_thread_id(task.trigger_id, "r1")

    assert files.resolve_folders(thread_id) == [folder]
    assert files.folders_of(thread_id) == [str(folder)]


def test_thread_files_delete_every_file_of_a_thread_and_no_other(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    files = ThreadFiles(settings)
    env_id = in_environment(settings.state_dir, "t1")
    Environments(settings.state_dir).assign("t2", env_id)
    files.title_sidecar_path("t1").write_text("Renamed", encoding="utf-8")
    (settings.state_dir / "t1.tasks.json").write_text("[]", encoding="utf-8")

    files.delete("t1")

    assert Environments(settings.state_dir).environment_of("t1") == "default"
    assert not files.title_sidecar_path("t1").exists()
    assert not (settings.state_dir / "t1.tasks.json").exists()
    assert Environments(settings.state_dir).environment_of("t2") == env_id


def test_a_task_saved_with_a_folder_moves_into_an_environment_with_it(tmp_path: Path) -> None:
    from coscribe.tools.bookkeeping.scheduled_tasks import ScheduledTriggerStore, create_trigger
    from coscribe.web.environment_migration import _migrate_scheduled_tasks

    settings = _settings(tmp_path)
    folder = tmp_path / "reports"
    folder.mkdir()
    store = ScheduledTriggerStore(settings.state_dir)
    tasks = [create_trigger(store, name=n, kind="manual", at="", prompt="p") for n in "AB"]
    for task in tasks:
        raw = {**task.to_dict(), "workspace": str(folder)}
        raw.pop("environment")
        (store.root / f"{task.trigger_id}.json").write_text(json.dumps(raw), encoding="utf-8")

    _migrate_scheduled_tasks(settings)

    first, second = (store.load(t.trigger_id) for t in tasks)
    assert first is not None and second is not None
    assert first.environment == second.environment != DEFAULT_ENVIRONMENT
    assert first.legacy_workspace is None and "workspace" not in first.to_dict()
    environment = Environments(settings.state_dir).get(first.environment)
    assert environment is not None and environment["folders"] == [str(folder)]
