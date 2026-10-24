"""The AppContainer a script runs in (`tools/exec/isolation.py`). The registry tests run
everywhere with `icacls` faked; the `windows` ones start real containers."""

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from coscribe.runtime import Environments
from coscribe.tools.exec import (
    Capabilities,
    CapabilityOff,
    FolderGrants,
    Isolation,
    IsolationRefused,
    RunningCommand,
    SelfTest,
    isolation,
    isolation_status,
    self_test,
)
from coscribe.tools.exec._appcontainer import NETWORK_CAPABILITIES, ContainerUnavailable
from tests.environment_helpers import in_environment

windows = pytest.mark.skipif(sys.platform != "win32", reason="AppContainer is Windows only")


@pytest.fixture
def fake_icacls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def run(*args: str) -> bool:
        calls.append(args)
        return True

    monkeypatch.setattr(isolation, "_icacls", run)
    monkeypatch.setattr(isolation._appcontainer, "capability_sid", lambda name: f"S-{name}")
    return calls


def test_a_folder_is_granted_once(tmp_path: Path, fake_icacls: list[tuple[str, ...]]) -> None:
    folder = tmp_path / "work"
    folder.mkdir()
    grants = FolderGrants(tmp_path / "state")
    name, ok = grants.ensure(folder, "write")
    assert ok and name.endswith(".write")
    assert FolderGrants(tmp_path / "state").ensure(folder, "write") == (name, True)
    assert len(fake_icacls) == 1
    assert fake_icacls[0][1:] == ("/grant", f"*S-{name}:(OI)(CI)(RX,W)")


def test_read_and_write_are_separate_capabilities(
    tmp_path: Path, fake_icacls: list[tuple[str, ...]]
) -> None:
    grants = FolderGrants(tmp_path / "state")
    read, _ = grants.ensure(tmp_path, "read")
    write, _ = grants.ensure(tmp_path, "write")
    assert read != write
    assert fake_icacls[0][2].endswith("(RX)")


def test_a_folder_made_again_is_granted_again(
    tmp_path: Path, fake_icacls: list[tuple[str, ...]]
) -> None:
    folder = tmp_path / "work"
    folder.mkdir()
    grants = FolderGrants(tmp_path / "state")
    grants.ensure(folder, "write")
    # What a folder deleted and made again looks like: another creation time.
    entry = next(iter(grants._grants.values()))
    entry.folder_id = -1.0
    grants.ensure(folder, "write")
    assert len(fake_icacls) == 2


def test_a_failed_grant_is_not_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(isolation, "_icacls", lambda *args: False)
    monkeypatch.setattr(isolation._appcontainer, "capability_sid", lambda name: "S-x")
    grants = FolderGrants(tmp_path / "state")
    assert grants.ensure(tmp_path, "read")[1] is False
    assert FolderGrants(tmp_path / "state").entries() == []


def test_a_crash_during_the_grant_leaves_an_entry_to_revoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(isolation._appcontainer, "capability_sid", lambda name: "S-x")

    def crash(*args: str) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(isolation, "_icacls", crash)
    with pytest.raises(KeyboardInterrupt):
        FolderGrants(tmp_path / "state").ensure(tmp_path, "write")
    removed: list[tuple[str, ...]] = []
    monkeypatch.setattr(isolation, "_icacls", lambda *args: removed.append(args) or True)
    after_restart = FolderGrants(tmp_path / "state")
    assert after_restart.entries() == [(str(tmp_path.resolve()), "write")]
    for path, _access in after_restart.entries():
        after_restart.revoke(Path(path))
    assert removed == [(str(tmp_path.resolve()), "/remove:g", "*S-x")]
    assert FolderGrants(tmp_path / "state").entries() == []


def test_revoke_takes_both_accesses_off_one_folder_only(
    tmp_path: Path, fake_icacls: list[tuple[str, ...]]
) -> None:
    kept, dropped = tmp_path / "kept", tmp_path / "dropped"
    kept.mkdir()
    dropped.mkdir()
    grants = FolderGrants(tmp_path / "state")
    grants.ensure(kept, "write")
    grants.ensure(dropped, "read")
    grants.ensure(dropped, "write")
    grants.revoke(dropped)
    assert FolderGrants(tmp_path / "state").entries() == [(str(kept.resolve()), "write")]
    assert [c[1] for c in fake_icacls[3:]] == ["/remove:g", "/remove:g"]


def test_the_sweep_keeps_the_folders_in_use_and_what_interpreters_read(
    tmp_path: Path, fake_icacls: list[tuple[str, ...]]
) -> None:
    state = tmp_path / "state"
    used, removed, venv = tmp_path / "used", tmp_path / "removed", state / "script-env"
    for folder in (used, removed, venv):
        folder.mkdir(parents=True)
    grants = FolderGrants(state)
    for folder, access in ((used, "write"), (removed, "write"), (venv, "read")):
        grants.ensure(folder, access)
    assert isolation.sweep_folder_grants(state, [used]) == [str(removed.resolve())]
    assert sorted(p for p, _ in FolderGrants(state).entries()) == sorted(
        [str(used.resolve()), str(venv.resolve())]
    )
    assert isolation.sweep_folder_grants(state, [used]) == []
    # At uninstall every grant goes.
    assert len(isolation.sweep_folder_grants(state, None)) == 2
    assert FolderGrants(state).entries() == []


def test_a_folder_that_is_gone_leaves_the_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "gone"
    folder.mkdir()
    monkeypatch.setattr(isolation._appcontainer, "capability_sid", lambda name: "S-x")
    monkeypatch.setattr(isolation, "_icacls", lambda *args: True)
    grants = FolderGrants(tmp_path / "state")
    grants.ensure(folder, "write")
    folder.rmdir()
    # icacls fails on a missing path; the entry must still go.
    monkeypatch.setattr(isolation, "_icacls", lambda *args: False)
    grants.revoke(folder)
    assert grants.entries() == []


def test_a_run_holds_its_folders_and_the_network_only_when_on(
    tmp_path: Path, fake_icacls: list[tuple[str, ...]]
) -> None:
    work, ref, venv = tmp_path / "work", tmp_path / "ref", tmp_path / "venv"
    for folder in (work, ref, venv):
        folder.mkdir()
    spec = Isolation(
        tmp_path / "state",
        writable=(work,),
        readable=(ref, tmp_path / "missing"),
        runtime=(venv,),
        network=False,
    )
    names, failed = isolation.capabilities_for(spec)
    assert names == [
        isolation.capability_name(work, "write"),
        isolation.capability_name(ref, "read"),
        isolation.capability_name(venv, "read"),
    ]
    assert failed == []
    names, _ = isolation.capabilities_for(
        Isolation(tmp_path / "state", writable=(work,), network=True)
    )
    assert names[1:] == list(NETWORK_CAPABILITIES)


@pytest.fixture
def failed_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A Windows machine whose self-test failed, as seen by `for_scripts`."""
    state = tmp_path / "state"
    python = isolation.venv_python(state / "script-env")
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    monkeypatch.setattr(isolation, "available", lambda: True)
    monkeypatch.setattr(
        isolation, "self_test", lambda *a, **k: SelfTest(False, "read outside", "read it")
    )
    return state


def test_a_failed_self_test_refuses_scripts_unless_the_conversation_allows_it(
    failed_machine: Path, tmp_path: Path
) -> None:
    def run(thread_id: str | None) -> Isolation | None:
        return isolation.for_scripts(failed_machine, writable=[tmp_path], thread_id=thread_id)

    with pytest.raises(IsolationRefused, match="this conversation's environment"):
        run("t1")
    with pytest.raises(IsolationRefused):
        run(None)
    env_id = in_environment(failed_machine, "t1", unisolated=True)
    assert run("t1") is None
    with pytest.raises(IsolationRefused):
        run("t2")
    Environments(failed_machine).update(env_id, {"unisolated": False}, set())
    with pytest.raises(IsolationRefused):
        run("t1")


def test_the_status_runs_the_check_only_when_asked(failed_machine: Path) -> None:
    assert isolation_status(failed_machine)["state"] == "untested"
    assert isolation_status(failed_machine, run=True) == {
        "state": "failed",
        "failed": "read outside",
        "detail": "read it",
    }


@pytest.mark.skipif(sys.platform == "win32", reason="the fallback off Windows")
def test_off_windows_isolation_is_unavailable(tmp_path: Path) -> None:
    assert not isolation.available()
    assert self_test(tmp_path, Path(sys.executable), ()).failed == "not Windows"
    assert isolation_status(tmp_path)["state"] == "unsupported"
    with pytest.raises(ContainerUnavailable):
        RunningCommand([sys.executable, "-c", "pass"], str(tmp_path), {}, Isolation(tmp_path))


# ---- Real containers ---------------------------------------------------------------------


_PROBE = r"""
import json, os, socket, subprocess, sys
work, ref, outside = sys.argv[1:4]
def check(name, fn):
    try:
        fn()
        ok = True
    except Exception:
        ok = False
    print(json.dumps({name: ok}), flush=True)
def write(path):
    with open(path, "w") as fh:
        fh.write("x")
def child_write(path):
    code = f"open({path!r}, 'w').write('x')"
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)
check("write work", lambda: write(os.path.join(work, "new.txt")))
check("overwrite work", lambda: write(os.path.join(work, "existing.txt")))
check("delete in work", lambda: os.remove(os.path.join(work, "existing.txt")))
check("rename in work", lambda: os.rename(os.path.join(work, "new.txt"), work + "/r.txt"))
check("read ref", lambda: open(os.path.join(ref, "input.txt")).read())
check("write ref", lambda: write(os.path.join(ref, "new.txt")))
check("read outside", lambda: open(os.path.join(outside, "secret.txt")).read())
check("write outside", lambda: write(os.path.join(outside, "new.txt")))
check("child writes work", lambda: child_write(os.path.join(work, "child.txt")))
check("child writes outside", lambda: child_write(os.path.join(outside, "child.txt")))
check("network", lambda: socket.create_connection(("1.1.1.1", 443), timeout=3).close())
print("to stderr", file=sys.stderr, flush=True)
"""


@pytest.fixture
def folders(tmp_path: Path) -> Iterator[tuple[Path, Path, Path, Path]]:
    state = tmp_path / "state"
    work, ref, outside = tmp_path / "work", tmp_path / "ref", tmp_path / "outside"
    for folder in (work, ref, outside):
        folder.mkdir()
    (work / "existing.txt").write_text("old", encoding="utf-8")
    (ref / "input.txt").write_text("ref", encoding="utf-8")
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    # Its own folder: a grant on tmp_path would reach `outside` through inheritance.
    (tmp_path / "probe").mkdir()
    (tmp_path / "probe" / "probe.py").write_text(_PROBE, encoding="utf-8")
    yield state, work, ref, outside
    _revoke_all(state)


def _revoke_all(state: Path) -> None:
    # The interpreter's folders stay granted: their ACE belongs to the folder, not to this
    # state, and the script tools in other workers make their venvs from the same Python.
    shared = {os.path.normcase(str(p)) for p in _runtime()}
    grants = FolderGrants(state)
    for path in {path for path, _access in grants.entries()}:
        if os.path.normcase(path) not in shared:
            grants.revoke(Path(path))


def _runtime() -> tuple[Path, ...]:
    return tuple({Path(sys.base_prefix).resolve(), Path(sys.prefix).resolve()})


@windows
def test_a_script_is_held_to_its_folders(folders: tuple[Path, ...], tmp_path: Path) -> None:
    state, work, ref, outside = folders
    probe = tmp_path / "probe"
    spec = Isolation(
        state, writable=(work,), readable=(ref,), runtime=(*_runtime(), probe), network=False
    )
    argv = [sys.executable, str(probe / "probe.py"), str(work), str(ref), str(outside)]
    command = RunningCommand(argv, str(work), dict(os.environ), spec)
    assert command.wait(120) == "exited"
    rows: dict[str, bool] = {}
    for line in command.stdout_text().splitlines():
        rows.update(json.loads(line))
    assert rows == {
        "write work": True,
        "overwrite work": True,
        "delete in work": False,
        "rename in work": False,
        "read ref": True,
        "write ref": False,
        "read outside": False,
        "write outside": False,
        "child writes work": True,
        "child writes outside": False,
        "network": False,
    }, command.stderr_text()
    assert command.stderr_text().strip() == "to stderr"
    assert command.returncode == 0
    assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"]


@windows
def test_a_stop_ends_the_container_and_its_children(folders: tuple[Path, ...]) -> None:
    state, work, _ref, _outside = folders
    code = (
        "import subprocess, sys, time\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
        "print(c.pid, flush=True)\n"
        "time.sleep(300)\n"
    )
    spec = Isolation(state, writable=(work,), runtime=_runtime())
    command = RunningCommand([sys.executable, "-c", code], str(work), dict(os.environ), spec)
    deadline = time.monotonic() + 60
    while not command.stdout_text().strip() and time.monotonic() < deadline:
        time.sleep(0.2)
    child = int(command.stdout_text().split()[0])
    command.kill()
    command.wait_exit(30)
    time.sleep(1)
    listed = subprocess.run(
        ["tasklist", "/FI", f"PID eq {child}", "/NH"], capture_output=True, text=True, check=False
    ).stdout
    assert str(child) not in listed


@windows
def test_the_self_test_passes_and_is_cached(tmp_path: Path) -> None:
    state = tmp_path / "state"
    try:
        result = self_test(state, Path(sys.executable), _runtime())
        assert result.ok, result
        cached = json.loads((state / "isolation-self-test.json").read_text(encoding="utf-8"))
        assert cached["result"]["ok"] is True
        assert self_test(state, Path(sys.executable), _runtime()) == result
        # Only the runtime folders stay granted; the test's own folders are revoked.
        assert {access for _path, access in FolderGrants(state).entries()} == {"read"}
    finally:
        _revoke_all(state)


@windows
def test_revoking_takes_the_ace_off(folders: tuple[Path, ...]) -> None:
    state, work, _ref, _outside = folders
    grants = FolderGrants(state)
    name, ok = grants.ensure(work, "write")
    assert ok
    sid = isolation._appcontainer.capability_sid(name)
    acl = subprocess.run(["icacls", str(work)], capture_output=True, text=True, check=False)
    assert sid in acl.stdout
    grants.revoke(work)
    acl = subprocess.run(["icacls", str(work)], capture_output=True, text=True, check=False)
    assert sid not in acl.stdout


# ---- The tools ---------------------------------------------------------------------------


@windows
async def test_the_script_tools_run_contained(tmp_path: Path) -> None:
    from coscribe.tools.bookkeeping.background_tasks import build_background_task_tools
    from coscribe.tools.exec import build_script_tools

    workspace, reference, outside = tmp_path / "ws", tmp_path / "ref", tmp_path / "outside"
    for folder in (workspace, reference, outside):
        folder.mkdir()
    (workspace / "old.txt").write_text("old", encoding="utf-8")
    (reference / "input.txt").write_text("ref", encoding="utf-8")
    state = tmp_path / "state"
    try:
        (run,) = build_script_tools(workspace, state, extra_readable=[reference])
        script = (
            "import os\n"
            f"print(open({str(reference / 'input.txt')!r}).read())\n"
            "open('new.txt', 'w').write('x')\n"
            f"open({str(outside / 'x.txt')!r}, 'w').write('x')\n"
        )
        result = run(script=script, description="probe")
        assert "ref" in str(result["stdout"])
        assert (workspace / "new.txt").exists() and not (outside / "x.txt").exists()
        assert "ran isolated" in str(result["stderr"]), result

        removed = run(script="import os\nos.remove('old.txt')\n", description="delete")
        assert (workspace / "old.txt").exists()
        assert "delete_file" in str(removed["stderr"]), removed

        # Isolated scripts don't make a command safe: it needs the sandbox or a second yes.
        Capabilities(state).set("commands", True)
        tools = {
            tool.__name__: tool for tool in build_background_task_tools("t1", workspace, state)
        }
        with pytest.raises(CapabilityOff, match="sandbox"):
            await tools["run_background_command"](
                command=f'echo x > "{outside / "shell.txt"}"', description="write outside"
            )
        assert not (outside / "shell.txt").exists()
    finally:
        _revoke_all(state)


def test_a_command_line_is_refused_in_the_container(tmp_path: Path) -> None:
    with pytest.raises(IsolationRefused, match="run_background_script"):
        isolation.start("dir /b", str(tmp_path), {}, Isolation(tmp_path))


def test_node_keeps_the_script_path_it_was_given_in_the_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[list[str]] = []
    monkeypatch.setattr(isolation, "capabilities_for", lambda iso: ([], []))
    monkeypatch.setattr(
        isolation._appcontainer, "start", lambda argv, cwd, env, caps: started.append(list(argv))
    )

    isolation.start([r"C:\node\node.exe", "s.js"], str(tmp_path), {}, Isolation(tmp_path))
    isolation.start(["python.exe", "s.py"], str(tmp_path), {}, Isolation(tmp_path))

    assert started == [
        [r"C:\node\node.exe", "--preserve-symlinks", "--preserve-symlinks-main", "s.js"],
        ["python.exe", "s.py"],
    ]


def test_the_test_makes_the_script_venv_first_and_says_when_it_cannot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_python(state_dir: Path) -> Path:
        raise RuntimeError("Could not create the script environment (tried: py).")

    monkeypatch.setattr(isolation, "available", lambda: True)
    monkeypatch.setattr(isolation, "ensure_script_env", no_python)
    assert isolation_status(tmp_path)["state"] == "untested"
    assert isolation_status(tmp_path, run=True) == {
        "state": "setup_failed",
        "failed": "",
        "detail": "Could not create the script environment (tried: py).",
    }


def test_a_venv_made_from_another_python_is_tested_again(tmp_path: Path) -> None:
    python = isolation.venv_python(tmp_path / "script-env")
    python.parent.mkdir(parents=True)
    config = tmp_path / "script-env" / "pyvenv.cfg"
    config.write_text("home = C:\\Python312\n", encoding="utf-8")
    first = isolation._machine_key(python)
    config.write_text("home = C:\\Users\\a\\miniconda3\n", encoding="utf-8")
    assert isolation._machine_key(python) != first


def test_the_line_python_3_13_prints_in_the_container_is_dropped() -> None:
    stderr = (
        "Failed to find real location of C:\\coscribe\\script-env\\Scripts\\python.exe\r\n"
        "Traceback (most recent call last):\n  ValueError: bad\n"
    )
    traceback = "Traceback (most recent call last):\n  ValueError: bad\n"
    assert isolation.quiet_stderr(stderr) == traceback
