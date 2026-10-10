import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from coscribe.tools import node_env, script_env


def test_two_first_runs_create_and_seed_the_script_env_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "venv" in command:
            python = script_env.venv_python(Path(command[-1]))
            python.parent.mkdir(parents=True)
            python.write_text("")
        time.sleep(0.2)  # the seeding takes a while; the other caller arrives meanwhile
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(script_env.subprocess, "run", fake_run)
    monkeypatch.setattr(script_env, "_venv_create_candidates", lambda state_dir: [sys.executable])
    results: list[Path] = []

    def run() -> None:
        results.append(script_env.ensure_script_env(tmp_path))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2
    assert sum("venv" in call for call in calls) == 1
    assert sum("pip" in call for call in calls) == 1


def test_two_first_runs_install_the_node_env_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installs: list[list[str]] = []

    def fake_run(command: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        installs.append(command)
        time.sleep(0.2)
        (tmp_path / "node-env" / "node_modules" / "pptxgenjs").mkdir(parents=True)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(node_env.subprocess, "run", fake_run)
    monkeypatch.setattr(node_env, "_require_node_and_npm", lambda: None)
    monkeypatch.setattr(node_env, "_npm", lambda: "npm")

    threads = [
        threading.Thread(target=node_env.ensure_node_env, args=(tmp_path,)) for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(installs) == 1
