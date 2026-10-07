"""The code module's Codex adapter, against tests/fake_codex_app_server.py."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest

from coscribe.code_runtime import client as client_module
from coscribe.code_runtime import launch as launch_module
from coscribe.code_runtime.client import AppServerError
from coscribe.code_runtime.install import CodexUnavailable, codex_path_dir
from coscribe.code_runtime.launch import (
    DISABLED_FEATURES,
    CodexHost,
    CodexModel,
    LaunchSpec,
    codex_model,
    config_toml,
    launch_spec,
)
from coscribe.code_runtime.thread import (
    ApprovalRequest,
    CodexEvent,
    CodexThread,
    changed_files,
    reads_only,
)

FAKE = Path(__file__).with_name("fake_codex_app_server.py")
MODEL = CodexModel("coscribe_deepseek", "deepseek-flash")


def _spec(tmp_path: Path, config: str = "") -> LaunchSpec:
    env = {**os.environ, "FAKE_CODEX_LOG": str(tmp_path / "requests.jsonl")}
    return LaunchSpec(
        argv=(sys.executable, str(FAKE)),
        env=env,
        config=config,
        home=tmp_path / "home",
        log_path=tmp_path / "app-server.log",
    )


def _requests(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "requests.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _thread(host: CodexHost, workdir: Path, **options: Any) -> CodexThread:
    # Short enough that a turn which never ends fails the test instead of
    # hanging it.
    options.setdefault("stall_seconds", 10.0)
    return CodexThread(host, MODEL, workdir, **options)


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture
async def host(tmp_path: Path) -> Any:
    codex = CodexHost(lambda: _spec(tmp_path))
    yield codex
    await codex.shutdown(force=True)


class _Approvals:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.seen: list[ApprovalRequest] = []

    async def __call__(self, request: ApprovalRequest) -> bool:
        self.seen.append(request)
        return self.answer


async def test_a_turn_streams_text_and_reports_usage(host: CodexHost, workdir: Path) -> None:
    thread = _thread(host, workdir)
    events: list[CodexEvent] = []

    async def on_event(event: CodexEvent) -> None:
        events.append(event)

    result = await thread.run_turn("basic", _Approvals(True), on_event)

    assert result.status == "completed"
    assert result.text == "Hello from Codex"
    assert "".join(e.data["text"] for e in events if e.kind == "text") == "Hello from Codex"
    assert (result.usage.input_tokens, result.usage.cached_input_tokens) == (1000, 800)
    assert result.usage.output_tokens == 200
    assert result.files_changed == []
    assert thread.thread_id is not None


async def test_events_reach_a_slow_watcher_in_order_and_before_the_turn_ends(
    host: CodexHost, workdir: Path
) -> None:
    events: list[CodexEvent] = []

    async def slow_on_first(event: CodexEvent) -> None:
        if not events:
            await asyncio.sleep(0.2)
        events.append(event)

    await _thread(host, workdir).run_turn("basic", _Approvals(True), slow_on_first)

    assert [e.kind for e in events] == ["text", "text", "message", "usage"]
    assert "".join(e.data["text"] for e in events if e.kind == "text") == "Hello from Codex"


async def test_a_codex_that_never_starts_is_an_error_not_a_hang(
    tmp_path: Path, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client_module, "_START_SECONDS", 0.5)

    def spec() -> LaunchSpec:
        base = _spec(tmp_path)
        return LaunchSpec(
            base.argv, {**base.env, "FAKE_CODEX_NO_INIT": "1"}, "", base.home, base.log_path
        )

    codex = CodexHost(spec)
    try:
        with pytest.raises(AppServerError, match="didn't start"):
            await asyncio.wait_for(_thread(codex, workdir).run_turn("basic", _Approvals(True)), 20)
    finally:
        await codex.shutdown(force=True)


async def test_the_thread_runs_unsandboxed_with_every_action_coming_back(
    host: CodexHost, workdir: Path, tmp_path: Path
) -> None:
    thread = _thread(
        host, workdir, developer_instructions="Reply in Chinese.", context_window=1048576
    )
    await thread.run_turn("basic", _Approvals(True))

    sent = {r["method"]: r["params"] for r in _requests(tmp_path) if "method" in r}
    assert sent["initialize"]["capabilities"] == {"experimentalApi": True}
    started = sent["thread/start"]
    assert started["approvalPolicy"] == "untrusted"
    assert started["sandbox"] == "danger-full-access"
    assert (started["model"], started["modelProvider"]) == ("deepseek-flash", "coscribe_deepseek")
    assert started["cwd"] == str(workdir)
    assert started["developerInstructions"] == "Reply in Chinese."
    assert started["config"] == {"model_context_window": 1048576}


async def test_an_approved_command_runs_and_its_file_is_reported(
    host: CodexHost, workdir: Path
) -> None:
    approvals = _Approvals(True)
    result = await _thread(host, workdir).run_turn("approve", approvals)

    [request] = approvals.seen
    assert request.kind == "command"
    assert request.command == "/bin/bash -lc 'echo made > made.txt'"
    assert request.cwd == str(workdir)
    assert request.reason == "writes made.txt"
    assert result.text == "ran it"
    assert result.files_changed == ["made.txt"]
    assert result.commands == 1


async def test_a_declined_command_does_not_run(host: CodexHost, workdir: Path) -> None:
    result = await _thread(host, workdir).run_turn("approve", _Approvals(False))

    assert result.text == "was declined"
    assert not (workdir / "made.txt").exists()
    assert result.files_changed == []


async def test_a_read_inside_the_folder_runs_without_asking(host: CodexHost, workdir: Path) -> None:
    approvals = _Approvals(False)
    events: list[CodexEvent] = []

    async def on_event(event: CodexEvent) -> None:
        events.append(event)

    result = await _thread(host, workdir).run_turn("read", approvals, on_event)

    assert approvals.seen == []
    assert result.text == "ran it"
    assert [e.data["command"] for e in events if e.kind == "auto_approved"] == [
        "/bin/bash -lc 'ls -la'"
    ]


async def test_a_read_outside_the_folder_still_asks(host: CodexHost, workdir: Path) -> None:
    approvals = _Approvals(False)

    result = await _thread(host, workdir).run_turn("readout", approvals)

    assert [r.command for r in approvals.seen] == ["/bin/bash -lc 'cat ../x.txt'"]
    assert result.text == "was declined"


@pytest.mark.parametrize(
    "script",
    [
        "ls -la",
        "cat summary.csv",
        "head -n 3 sub/a.csv",
        "grep -rn 华北 .",
        "rg 华北",
        "find . -name x.csv",
        "wc -l summary.csv",
        "cat 'my file.csv'",
        "du -sh sub",
    ],
)
def test_plain_reads_of_the_folder_pass(script: str, tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()

    assert reads_only(f"/bin/bash -lc {shlex.quote(script)}", str(tmp_path), tmp_path)


@pytest.mark.parametrize(
    "script",
    [
        # Taken for reads by Codex's own commandActions, live.
        "cat summary.csv | tee copy.csv",
        "find . -name '*.csv' -delete",
        "find . -exec rm {} +",
        "cat summary.csv > copy.csv",
        "head -n 1 summary.csv; rm -f summary.csv",
        "sed -i s/a/b/ summary.csv",
        "python -c 'print(1)'",
        "cat ../../etc/hostname",
        "ls ..",
        "cat /etc/passwd",
        "cat ~/.ssh/id_rsa",
        "cat $HOME/.netrc",
        "cat .*/secret",
        "rg --pre=sh x .",
        "grep -f ../patterns x .",
        "./cat x",
        "file -C -m magic",
        "cat x && rm x",
        "cat `whoami`",
    ],
)
def test_anything_else_is_asked_about(script: str, tmp_path: Path) -> None:
    assert not reads_only(f"/bin/bash -lc {shlex.quote(script)}", str(tmp_path), tmp_path)


def test_codex_cd_prefix_into_the_folder_still_counts_as_a_read(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    folder = str(tmp_path)

    def wrapped(script: str) -> str:
        return f"/bin/bash -lc {shlex.quote(script)}"

    assert reads_only(wrapped(f'cd "{tmp_path}" && ls -la'), folder, tmp_path)
    assert reads_only(wrapped(f"cd '{tmp_path}' && cat summary.csv"), folder, tmp_path)
    assert reads_only(wrapped("cd sub && head -n 3 a.csv"), folder, tmp_path)
    for script in [
        "cd .. && ls",
        "cd / && cat etc/passwd",
        "cd ~ && ls",
        "cd $HOME && ls",
        'cd "$HOME" && ls',
        "cd sub && cd .. && cd .. && ls",
        f'cd "{tmp_path}" && rm -f summary.csv',
        f'cd "{tmp_path}" && cat a > b',
        f'cd "{tmp_path}" && python x.py',
        "cd sub* && ls",
        "cd sub && ls ../..",
    ]:
        assert not reads_only(wrapped(script), folder, tmp_path), script


def test_a_read_outside_the_folder_or_from_outside_it_is_asked_about(tmp_path: Path) -> None:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "link").symlink_to(tmp_path)

    assert not reads_only("/bin/bash -lc 'ls'", str(tmp_path), folder)
    assert not reads_only("/bin/bash -lc 'cat link/secret'", str(folder), folder)
    assert reads_only("powershell.exe -Command Get-ChildItem", str(folder), folder)
    assert not reads_only("powershell.exe -Command Get-ChildItem", str(tmp_path), folder)
    assert not reads_only("powershell.exe -Command Get-Content link/secret", str(folder), folder)


async def test_a_file_change_is_asked_about_too(host: CodexHost, workdir: Path) -> None:
    approvals = _Approvals(True)
    result = await _thread(host, workdir).run_turn("file", approvals)

    assert [r.kind for r in approvals.seen] == ["file_change"]
    assert result.files_changed == ["notes.md"]


async def test_a_request_nobody_handles_gets_an_error_not_silence(
    host: CodexHost, workdir: Path
) -> None:
    result = await _thread(host, workdir).run_turn("tool", _Approvals(True))

    assert result.status == "completed"
    assert result.text == "-32603"


async def test_events_sent_before_turn_start_answers_are_kept(
    host: CodexHost, workdir: Path
) -> None:
    result = await _thread(host, workdir).run_turn("early", _Approvals(True))

    assert result.status == "completed"
    assert result.text == "early bird"
    assert result.usage.input_tokens == 1000


async def test_a_failed_turn_reports_the_final_error_not_a_retried_one(
    host: CodexHost, workdir: Path
) -> None:
    result = await _thread(host, workdir).run_turn("fail", _Approvals(True))

    assert result.status == "failed"
    assert result.error == "model unavailable"


async def test_codex_dying_mid_turn_fails_the_turn_and_the_next_one_restarts_it(
    host: CodexHost, workdir: Path
) -> None:
    thread = _thread(host, workdir)
    crashed = await thread.run_turn("crash", _Approvals(True))

    assert crashed.status == "failed"
    assert "exit code 3" in (crashed.error or "")

    again = await thread.run_turn("basic", _Approvals(True))
    assert again.status == "completed"


async def test_cancelling_a_turn_interrupts_it_in_codex(
    host: CodexHost, workdir: Path, tmp_path: Path
) -> None:
    run = asyncio.create_task(_thread(host, workdir).run_turn("hang", _Approvals(True)))
    await asyncio.sleep(0.5)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    methods = [r.get("method") for r in _requests(tmp_path)]
    assert "turn/interrupt" in methods


async def test_a_turn_that_goes_silent_is_stopped(
    host: CodexHost, workdir: Path, tmp_path: Path
) -> None:
    thread = CodexThread(host, MODEL, workdir, stall_seconds=0.5)

    result = await asyncio.wait_for(thread.run_turn("hang", _Approvals(True)), 20)

    assert result.status in ("failed", "interrupted")
    assert "sent nothing" in (result.error or "")
    assert "turn/interrupt" in [r.get("method") for r in _requests(tmp_path)]


async def test_a_long_quiet_command_is_not_taken_for_a_stall(
    host: CodexHost, workdir: Path
) -> None:
    thread = CodexThread(host, MODEL, workdir, stall_seconds=0.5)

    result = await asyncio.wait_for(thread.run_turn("slowcmd", _Approvals(True)), 20)

    assert result.status == "completed"
    assert result.text == "slept"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed child of an exited parent may linger as a zombie.
    status = Path(f"/proc/{pid}/status")
    return not (status.exists() and "State:\tZ" in status.read_text())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
async def test_shutting_down_ends_what_codex_started(host: CodexHost, workdir: Path) -> None:
    run = asyncio.create_task(_thread(host, workdir).run_turn("child", _Approvals(True)))
    pid_file = workdir / "child.pid"
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text():
            break
        await asyncio.sleep(0.05)
    child = int(pid_file.read_text())
    assert _alive(child)

    await host.shutdown(force=True)

    result = await run
    assert result.status == "failed"
    for _ in range(40):
        if not _alive(child):
            break
        await asyncio.sleep(0.05)
    assert not _alive(child)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
async def test_a_cancelled_turn_ends_the_commands_it_started(
    host: CodexHost, workdir: Path
) -> None:
    run = asyncio.create_task(_thread(host, workdir).run_turn("child", _Approvals(True)))
    pid_file = workdir / "child.pid"
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text():
            break
        await asyncio.sleep(0.05)
    child = int(pid_file.read_text())

    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    assert not _alive(child)
    assert host.server is not None and host.server.running


async def test_an_idle_codex_stops_on_its_own(tmp_path: Path, workdir: Path) -> None:
    codex = CodexHost(lambda: _spec(tmp_path), idle_seconds=0.2)
    try:
        await _thread(codex, workdir).run_turn("basic", _Approvals(True))
        assert codex.server is not None
        await asyncio.sleep(0.6)
        assert codex.server is None
    finally:
        await codex.shutdown(force=True)


async def test_a_changed_setup_restarts_codex_once_it_is_free(
    tmp_path: Path, workdir: Path
) -> None:
    config = {"text": "a"}
    codex = CodexHost(lambda: _spec(tmp_path, config["text"]))
    try:
        await _thread(codex, workdir).run_turn("basic", _Approvals(True))
        first = codex.server
        config["text"] = "b"
        await _thread(codex, workdir).run_turn("basic", _Approvals(True))
        assert codex.server is not first
        assert (tmp_path / "home" / "config.toml").read_text() == "b"
    finally:
        await codex.shutdown(force=True)


def test_only_responses_api_providers_map_to_codex() -> None:
    providers = {"deepseek": {"base_url": "https://api.deepseek.com/v1", "api_key": "k"}}

    assert codex_model("deepseek:deepseek-flash", providers) == CodexModel(
        "coscribe_deepseek", "deepseek-flash"
    )
    assert codex_model("openai:gpt-5", providers).provider_id == "coscribe_openai"
    with pytest.raises(CodexUnavailable, match="Responses API"):
        codex_model("anthropic:claude", providers)
    with pytest.raises(CodexUnavailable):
        codex_model("deepseek-flash", providers)


def test_config_turns_off_overlapping_features_and_uses_responses() -> None:
    text = config_toml(
        {"deepseek": {"base_url": "https://api.deepseek.com/v1/", "api_key": "sk-secret"}},
        openai_key=False,
    )

    assert text.startswith('web_search = "disabled"\n')
    assert "[analytics]\nenabled = false" in text
    assert '[shell_environment_policy]\nexclude = ["COSCRIBE_CODEX_KEY_*"]' in text
    for feature in DISABLED_FEATURES:
        assert f"\n{feature} = false" in text
    assert "[model_providers.coscribe_deepseek]" in text
    assert 'base_url = "https://api.deepseek.com/v1"' in text
    assert 'env_key = "COSCRIBE_CODEX_KEY_DEEPSEEK"' in text
    assert 'wire_api = "responses"' in text
    assert "coscribe_openai" not in text
    assert 'env_key = "OPENAI_API_KEY"' in config_toml({}, openai_key=True)
    assert "sk-secret" not in text


def test_the_launch_puts_the_script_env_and_rg_first_and_keys_in_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script_bin = tmp_path / "script-env" / "bin"
    script_bin.mkdir(parents=True)
    codex_path_dir(tmp_path).mkdir(parents=True)
    monkeypatch.setattr(launch_module, "configured_proxy", lambda: "http://proxy:8080")
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)

    spec = launch_spec(
        tmp_path / "codex",
        tmp_path,
        {"deepseek": {"base_url": "https://api.deepseek.com/v1", "api_key": "sk-1"}},
        script_bin,
    )

    path = spec.env["PATH"].split(os.pathsep)
    assert path[:2] == [str(script_bin), str(codex_path_dir(tmp_path))]
    assert spec.env["CODEX_HOME"] == str(tmp_path / "codex" / "home")
    assert spec.env["COSCRIBE_CODEX_KEY_DEEPSEEK"] == "sk-1"
    assert spec.env["HTTPS_PROXY"] == "http://proxy:8080"
    assert spec.argv == (str(tmp_path / "codex"), "app-server")
    assert "sk-1" not in spec.config


def test_changed_files_lists_new_and_modified_only() -> None:
    before = {"a.txt": (1, 10), "b.txt": (2, 20)}
    after = {"a.txt": (1, 10), "b.txt": (3, 30), "c.txt": (1, 40)}

    assert changed_files(before, after) == ["b.txt", "c.txt"]


def _powershell(script: str, flags: str = "-NoProfile -Command") -> str:
    return f"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe {flags} {script!r}"


@pytest.mark.parametrize(
    "script",
    [
        "Get-ChildItem",
        "Get-ChildItem -Recurse -Filter *.csv",
        "gci sub",
        "dir",
        "Get-Content summary.csv",
        "Get-Content -Path summary.csv -TotalCount 5",
        "Get-Content -Path:summary.csv",
        "gc 'my file.csv'",
        "type sub\\a.csv",
        "Select-String -Path summary.csv -Pattern North",
        "sls North summary.csv",
        "Get-Item summary.csv",
        "Get-Location",
        "Test-Path summary.csv",
    ],
)
def test_powershell_reads_of_the_folder_pass(script: str, tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()

    assert reads_only(_powershell(script), str(tmp_path), tmp_path)


@pytest.mark.parametrize(
    "script",
    [
        # A read that goes on to do something else.
        "Get-Content summary.csv | Set-Content copy.csv",
        "Get-ChildItem; Remove-Item summary.csv",
        "Get-ChildItem ; Remove-Item summary.csv",
        "Get-Content summary.csv > copy.csv",
        "Get-ChildItem -Path (Get-Location)",
        "Get-Content $env:USERPROFILE\\.ssh\\id_rsa",
        "Get-ChildItem `Remove-Item",
        "Get-ChildItem && Remove-Item x",
        "Get-Content a.csv, b.csv",
        # Outside the folder, or not a read at all.
        "Get-Content ..\\..\\secret.txt",
        "Get-ChildItem ..",
        "Get-Content /etc/passwd",
        "Get-Content ~\\.netrc",
        "Get-Content \\\\server\\share\\x",
        "Remove-Item summary.csv",
        "Set-Content summary.csv x",
        "Invoke-WebRequest http://example.com",
        "Get-Process",
        ".\\Get-Content x",
        "Get-Content 'unclosed",
    ],
)
def test_other_powershell_is_asked_about(script: str, tmp_path: Path) -> None:
    assert not reads_only(_powershell(script), str(tmp_path), tmp_path)


def test_a_powershell_wrapper_is_only_read_when_it_runs_a_command(tmp_path: Path) -> None:
    folder = str(tmp_path)

    for flags in ("-NoProfile -NonInteractive -Command", "-ExecutionPolicy Bypass -Command"):
        assert reads_only(_powershell("Get-ChildItem", flags), folder, tmp_path)
    assert reads_only("powershell -Command Get-Content summary.csv", folder, tmp_path)
    assert reads_only('pwsh -c "Get-Content summary.csv"', folder, tmp_path)
    # A script file, an encoded command, or a flag coscribe doesn't know.
    assert not reads_only("powershell -File read.ps1", folder, tmp_path)
    assert not reads_only("powershell -EncodedCommand R2V0LUNoaWxkSXRlbQ==", folder, tmp_path)
    assert not reads_only("powershell -WindowStyle Hidden -Command Get-ChildItem", folder, tmp_path)
    assert not reads_only("powershell", folder, tmp_path)
