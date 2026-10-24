"""Checks that need the real Windows parts, not a fake of them: the Credential Manager, cmd and
PowerShell, taskkill, the Store python stub, resvg's font lookup, Codex's command sandbox.

They run in `.github/workflows/windows-more-checks.yml` only (COSCRIBE_WINDOWS_REAL=1), so a
failure here does not reset the nightly run of green nights in windows.yml (decision 0009)."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from coscribe.code_runtime.install import ensure_codex
from coscribe.code_runtime.thread import reads_only
from coscribe.runtime.hooks import run_hook
from coscribe.runtime.secrets import SERVICE_NAME, resolve_secret, store_secret
from coscribe.tools.bookkeeping.background_tasks import build_background_task_tools
from coscribe.tools.exec import (
    Capabilities,
    record_sandbox_setup,
    sandbox_codex,
    sandbox_home,
    sandbox_setup_argv,
    sandbox_uninstall_argv,
    script_env,
    warm_up_sandbox,
)

real = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("COSCRIBE_WINDOWS_REAL") != "1",
    reason="needs a real Windows machine; set by windows-more-checks.yml",
)

_POWERSHELL = "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"


def _cmdkey_list() -> str:
    return subprocess.run(["cmdkey", "/list"], capture_output=True, text=True, check=False).stdout


@real
def test_a_secret_goes_into_the_credential_manager_and_survives_a_new_process() -> None:
    import keyring

    ref = f"coscribe-check-{uuid.uuid4().hex}"
    try:
        stored = store_secret(ref, "s3cret-value")
        assert stored == {"keyring_ref": ref}, "store_secret fell back to plaintext"
        assert "WinVault" in type(keyring.get_keyring()).__name__
        assert ref in _cmdkey_list(), "the entry is not in Windows Credential Manager"
        assert resolve_secret(stored) == "s3cret-value"
        # The app is restarted: a separate process must read the same value.
        code = (
            "from coscribe.runtime.secrets import resolve_secret;"
            f"print(resolve_secret({{'keyring_ref': '{ref}'}}))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        assert out.stdout.strip() == "s3cret-value"
    finally:
        _forget(ref)


def _forget(ref: str) -> None:
    import keyring

    if keyring.get_password(SERVICE_NAME, ref) is not None:
        keyring.delete_password(SERVICE_NAME, ref)


@real
def test_an_entry_removed_behind_the_apps_back_is_reported_not_a_crash() -> None:
    ref = f"coscribe-check-{uuid.uuid4().hex}"
    stored = store_secret(ref, "gone-soon")
    assert isinstance(stored, dict)
    subprocess.run(["cmdkey", f"/delete:{SERVICE_NAME}:{ref}"], capture_output=True, check=False)
    _forget(ref)

    with pytest.raises(RuntimeError, match="re-enter it via Settings"):
        resolve_secret(stored)


@real
def test_a_hook_runs_under_cmd_and_under_powershell() -> None:
    payload = {"tool": "Bash"}

    assert run_hook("exit 0", payload).allowed
    denied = run_hook("echo blocked by cmd 1>&2 & exit 3", payload)
    assert not denied.allowed and denied.reason == "blocked by cmd"

    # The payload reaches a PowerShell hook on stdin.
    read = (
        "$p = [Console]::In.ReadToEnd(); "
        "if ($p -notmatch 'Bash') { [Console]::Error.Write('no payload'); exit 4 }"
    )
    assert run_hook(f'powershell -NoProfile -Command "{read}"', payload).allowed
    refused = run_hook(
        "powershell -NoProfile -Command \"[Console]::Error.Write('no'); exit 5\"", payload
    )
    assert not refused.allowed and refused.reason == "no"


@real
def test_format_hex_returns_the_bytes_unscrambled_and_counts_as_a_read(tmp_path: Path) -> None:
    (tmp_path / "data.bin").write_bytes(bytes(range(16)))
    # No -Count: Windows PowerShell 5.1, the one every Windows has, lacks it (added in 6.1).
    script = "Format-Hex -LiteralPath data.bin"
    command = (
        f"{_POWERSHELL} -NoProfile -Command "
        f'"[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; {script}"'
    )

    result = subprocess.run(
        command, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", check=True
    )

    assert "00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F" in result.stdout
    assert reads_only(command, str(tmp_path), tmp_path)


def _tools(tmp_path: Path, state: Path | None = None) -> dict[str, Any]:
    """The background tools; with `state`, one where the command sandbox is set up."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = state or tmp_path / "state"
    Capabilities(state).set("commands", True)
    Capabilities(state).set("commands_without_sandbox", True)
    return {t.__name__: t for t in build_background_task_tools("t1", workspace, state)}


@pytest.fixture(scope="module")
def sandboxed_state(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A state dir whose command sandbox is set up as Settings › Capabilities does it; the
    runner is an administrator, so Windows asks nobody."""
    state = tmp_path_factory.mktemp("sandbox-state")
    codex = ensure_codex(state)
    home = sandbox_home(state)
    home.mkdir(parents=True)
    done = subprocess.run(
        sandbox_setup_argv(codex),
        env={**os.environ, "CODEX_HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    record_sandbox_setup(state, codex)
    warm_up_sandbox(state)
    return state


async def _finished(tools: dict[str, Any], task_id: str) -> dict[str, Any]:
    for _ in range(600):
        result: dict[str, Any] = tools["check_background_task"](task_id)
        if result["status"] != "running":
            return result
        await asyncio.sleep(0.1)
    raise AssertionError("the command did not finish")


def _alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
    ).stdout
    return str(pid) in out


@real
async def test_the_first_sandboxed_command_after_setup_runs(
    tmp_path: Path, sandboxed_state: Path
) -> None:
    tools = _tools(tmp_path, sandboxed_state)
    started = await tools["run_background_command"](command="whoami", description="first")
    result = await _finished(tools, started["task_id"])
    assert "codexsandbox" in result.get("output", "").lower(), result.get("output")


@real
@pytest.mark.parametrize("sandboxed", [False, True])
async def test_stopping_a_background_command_ends_its_whole_process_tree(
    sandboxed: bool, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    state = request.getfixturevalue("sandboxed_state") if sandboxed else None
    tools = _tools(tmp_path, state)
    # In the workspace, the one folder a sandboxed command may write.
    pid_file = tmp_path / "workspace" / "child.pid"
    child = "import time; time.sleep(120)"
    parent = (
        "import subprocess, sys, time; "
        f"p = subprocess.Popen([sys.executable, '-c', '{child}']); "
        f"open(r'{pid_file}', 'w').write(str(p.pid)); time.sleep(120)"
    )
    command = f'whoami & "{sys.executable}" -c "{parent}"'

    started = await tools["run_background_command"](command=command, description="tree")
    # A sandboxed command's first run in a folder waits for Codex to grant it to the sandbox user.
    for _ in range(600):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        await asyncio.sleep(0.1)
    task = tools["check_background_task"](started["task_id"])
    assert pid_file.exists(), task.get("output")
    grandchild = int(pid_file.read_text())
    assert _alive(grandchild)
    assert tools["check_background_task"](started["task_id"])["status"] == "running"

    await tools["kill_background_task"](started["task_id"])
    for _ in range(100):
        if tools["check_background_task"](started["task_id"])["status"] != "running":
            break
        await asyncio.sleep(0.1)
    for _ in range(100):
        if not _alive(grandchild):
            break
        await asyncio.sleep(0.1)

    assert tools["check_background_task"](started["task_id"])["status"] == "killed"
    assert not _alive(grandchild), "taskkill /T left the child running"


@real
def test_a_python_that_is_only_the_store_stub_is_replaced_by_a_working_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Store alias exits at once with 9009 and a message; a .cmd in front of the real
    # Python on PATH behaves the same for shutil.which and for running it.
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    (stub_dir / "python.cmd").write_text("@echo Python was not found 1>&2\n@exit /b 9009\n")
    monkeypatch.setenv("PATH", f"{stub_dir}{os.pathsep}{os.environ['PATH']}")

    chosen = script_env.resolve_python_command("python", None)

    assert chosen != "python"
    assert script_env.working_interpreters([chosen]) == [chosen]


@real
def test_chinese_text_is_drawn_with_the_installed_yahei(tmp_path: Path) -> None:
    import resvg_py

    assert Path("C:/Windows/Fonts/msyh.ttc").is_file(), "Microsoft YaHei is not installed"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80">'
        '<text x="10" y="55" font-size="48" font-family="Microsoft YaHei">\u4e2d\u6587</text></svg>'
    )
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg, width=200, background="#ffffff"))

    import io

    from PIL import Image

    image = Image.open(io.BytesIO(png)).convert("L")
    ink = sum(image.histogram()[:128])
    assert ink > 400, f"the text drew {ink} dark pixels: no font was found"


def test_a_script_without_node_installed_says_what_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.exec.node_env import ensure_node_env

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    with pytest.raises(RuntimeError, match=r"node.*not found on PATH.*install Node\.js"):
        ensure_node_env(tmp_path / "state")


@real
async def test_a_sandboxed_command_writes_only_its_workspace_and_cannot_read_the_profile(
    tmp_path: Path, sandboxed_state: Path
) -> None:
    # A workspace in Documents, as a person's would be, with a file beside it and one at the
    # profile's top. Codex's setup lets its users read Documents, so the one beside it is
    # readable (decision 0022 says so); the top of the profile is not.
    base = Path(os.environ["USERPROFILE"]) / "Documents" / f"coscribe-{uuid.uuid4().hex}"
    base.mkdir(parents=True)
    tools = _tools(base, sandboxed_state)
    outside = base / "outside"
    outside.mkdir()
    beside = outside / "secret.txt"
    beside.write_text("BESIDE-SECRET")
    top = Path(os.environ["USERPROFILE"]) / f"coscribe-secret-{uuid.uuid4().hex}.txt"
    top.write_text("TOP-SECRET")
    made = outside / "made.txt"
    connect = (
        "print('PY-RAN'); import socket; "
        "socket.create_connection(('1.1.1.1', 443), 5); print('NET-OPEN')"
    )
    command = (
        f'whoami & echo inside> made.txt & type "{beside}" & type "{top}" & '
        f'echo outside> "{made}" & "{sys.executable}" -c "{connect}"'
    )

    try:
        started = await tools["run_background_command"](command=command, description="probe")
        result = await _finished(tools, started["task_id"])
        output = result.get("output", "")
        acls = "\n".join(
            subprocess.run(["icacls", str(p)], capture_output=True, text=True, check=False).stdout
            for p in (*reversed(beside.parents[:4]), beside, tmp_path.parents[2], tmp_path)
        )
        assert (base / "workspace" / "made.txt").read_text().strip() == "inside"
        assert not made.exists(), output
        assert "TOP-SECRET" not in output, output
        assert "BESIDE-SECRET" in output, output + acls
        assert "codexsandbox" in output.lower(), output
        # Default's network is not Full, so the command is offline.
        assert "PY-RAN" in output and "NET-OPEN" not in output, output
    finally:
        top.unlink()
        shutil.rmtree(base, ignore_errors=True)


def _icacls(*paths: Path) -> str:
    return "\n".join(
        subprocess.run(["icacls", str(p)], capture_output=True, text=True, check=False).stdout
        for p in paths
    )


@real
async def test_a_deny_read_entry_hides_a_folder_from_the_sandbox_but_not_its_workspace(
    tmp_path: Path, sandboxed_state: Path
) -> None:
    base = Path(os.environ["USERPROFILE"]) / "Documents" / f"coscribe-{uuid.uuid4().hex}"
    base.mkdir(parents=True)
    tools = _tools(base, sandboxed_state)
    private = base / "private"
    private.mkdir()
    secret = private / "secret.txt"
    secret.write_text("PRIVATE-SECRET")
    command = f'whoami & echo inside> made.txt & type made.txt & type "{secret}"'

    async def run() -> str:
        started = await tools["run_background_command"](command=command, description="probe")
        return str((await _finished(tools, started["task_id"])).get("output", ""))

    try:
        denied = subprocess.run(
            ["icacls", str(base), "/deny", "CodexSandboxUsers:(OI)(CI)(RD)"],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        assert denied.returncode == 0, denied.stdout + denied.stderr
        hidden = await run()
        acls = _icacls(base, base / "workspace", base / "workspace" / "made.txt", secret)
        print(hidden, acls)
        assert "PRIVATE-SECRET" not in hidden, hidden + acls
        assert (base / "workspace" / "made.txt").read_text().strip() == "inside", hidden + acls
        assert "inside" in hidden, hidden + acls

        subprocess.run(["icacls", str(base), "/remove:d", "CodexSandboxUsers"], check=True)
        shown = await run()
        assert "PRIVATE-SECRET" in shown, shown + _icacls(base, secret)
    finally:
        shutil.rmtree(base, ignore_errors=True)


@real
async def test_probe_codex_deny_entries(
    tmp_path: Path, sandboxed_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.exec import command_sandbox, sandboxed_command

    profile = Path(os.environ["USERPROFILE"])
    tag = uuid.uuid4().hex[:8]
    state = Path(os.environ["LOCALAPPDATA"]) / f"coscribe-probe-{tag}"
    state.mkdir()
    record_sandbox_setup(state, sandbox_codex(sandboxed_state))
    places = {
        "documents": profile / "Documents",
        "desktop": profile / "Desktop",
        "downloads": profile / "Downloads",
        "roaming": Path(os.environ["APPDATA"]),
        "localappdata": Path(os.environ["LOCALAPPDATA"]),
        "temp": Path(os.environ["TEMP"]),
    }
    secrets = {}
    for key, folder in places.items():
        f = folder / f"coscribe-probe-{tag}-{key}.txt"
        f.write_text(f"SECRET-{key.upper()}")
        secrets[key] = f
    base = profile / "Documents" / f"coscribe-{tag}"
    workspace = base / "workspace"
    workspace.mkdir(parents=True)
    ref = base / "ref"
    ref.mkdir()
    (ref / "r.txt").write_text("REF-READ")
    denied = [places[k] for k in ("documents", "desktop", "downloads", "roaming", "localappdata")]
    original = command_sandbox._section

    def with_denies(name: str, prof: dict) -> str:
        text = original(name, prof)
        lines = "".join(f'{command_sandbox._toml_key(str(d))} = "deny"\n' for d in denied)
        return text.replace('":root" = "read"\n', '":root" = "read"\n' + lines, 1)

    survey = " & ".join(f'type "{f}"' for f in secrets.values())
    report = []

    def run(command: str, deny: bool) -> str:
        monkeypatch.setattr(command_sandbox, "_section", with_denies if deny else original)
        # Rebuild the profile's text but keep Codex's own records in the home.
        for name in ("profiles.json", "config.toml"):
            (sandbox_home(state) / name).unlink(missing_ok=True)
        sandboxed = sandboxed_command(state, command, workspace, [workspace], [ref], False)
        assert sandboxed is not None
        argv, env = sandboxed
        done = subprocess.run(argv, env={**os.environ, **env}, capture_output=True,
                              text=True, timeout=300, check=False)  # fmt: skip
        return f"rc={done.returncode}\n{done.stdout}\n{done.stderr}"

    grants = [ref, state / "command-sandbox" / "commands"]
    try:
        run("rem", deny=False)
        report.append("NO DENY:\n" + run(survey, deny=False))
        for g in grants:
            g.mkdir(parents=True, exist_ok=True)
            report.append(subprocess.run(
                ["icacls", str(g), "/grant", "CodexSandboxUsers:(OI)(CI)(RX)"],
                capture_output=True, text=True, check=False).stdout)  # fmt: skip
        report.append("CONFIG:\n" + (sandbox_home(state) / "config.toml").read_text()[:600])
        report.append("WITH DENY (warm):\n" + run("rem", deny=True))
        report.append("WITH DENY:\n" + run(
            f'echo inside> made.txt & type made.txt & type "{ref / "r.txt"}" & '
            f'"{sys.executable}" -c "print(42)" & set TEMP & set TMP & ' + survey, deny=True))
        report.append("HOME:\n" + "\n".join(str(p) for p in sandbox_home(state).rglob("*") if "deny" in p.name.lower() or p.suffix == ".json"))
        report.append("ACL WITH DENY:\n" + _icacls(*places.values(), base, workspace, ref, state))
        report.append("DENY OFF AGAIN:\n" + run(survey, deny=False))
        report.append("DENY OFF, SECOND RUN:\n" + run(survey, deny=False))
        report.append("ACL AFTER OFF:\n" + _icacls(*places.values()))
    finally:
        for g in grants:
            subprocess.run(["icacls", str(g), "/remove:g", "CodexSandboxUsers"], check=False)
        for d in denied:
            subprocess.run(["icacls", str(d), "/remove:d", "CodexSandboxUsers"], check=False)
        for f in secrets.values():
            f.unlink(missing_ok=True)
        shutil.rmtree(base, ignore_errors=True)
        shutil.rmtree(state, ignore_errors=True)
    raise AssertionError("PROBE REPORT\n" + "\n----\n".join(report))


# Last in the file: it removes the sandbox the tests above share.
@real
async def test_a_command_fails_closed_when_the_sandbox_was_removed_behind_the_apps_back(
    tmp_path: Path, sandboxed_state: Path
) -> None:
    tools = _tools(tmp_path, sandboxed_state)
    codex = sandbox_codex(sandboxed_state)
    assert codex is not None
    home = {**os.environ, "CODEX_HOME": str(sandbox_home(sandboxed_state))}
    removed = subprocess.run(
        sandbox_uninstall_argv(codex), env=home, capture_output=True, text=True, check=False
    )
    assert removed.returncode == 0, removed.stdout + removed.stderr

    started = await tools["run_background_command"](
        command="whoami & echo inside> made.txt", description="after removal"
    )
    result = await _finished(tools, started["task_id"])

    # Codex either refuses or sets the sandbox up again (the runner is an administrator); never
    # does the command run as the person.
    output = result.get("output", "")
    ran_as = output.splitlines()[0].lower() if output.strip() else ""
    assert os.environ["USERNAME"].lower() not in ran_as, output
    if result["status"] == "succeeded":
        assert "codexsandbox" in ran_as, output
    else:
        assert not (tmp_path / "workspace" / "made.txt").exists(), output
