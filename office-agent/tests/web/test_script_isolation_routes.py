# ruff: noqa: E402
"""Web tests: the machine's isolation check and Settings › Capabilities, and what an environment
may reach: its network, its switches and running scripts without isolation."""

import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from coscribe.runtime_lg import AuditLog
from coscribe.tools.exec import command_sandbox
from coscribe.web.routes.settings import environments as environment_routes

from .helpers import FakeToolCallingChatModel, _client_lg


@pytest.mark.skipif(sys.platform == "win32", reason="the answer off Windows")
def test_the_check_says_unsupported_off_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client_lg(tmp_path, monkeypatch, FakeToolCallingChatModel(responses=[])) as client:
        status = client.get("/api/script-isolation").json()
        retest = client.post("/api/script-isolation/test").json()
    assert status["state"] == retest["state"] == "unsupported"


def test_what_an_environment_may_reach_is_checked_and_each_change_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client_lg(tmp_path, monkeypatch, FakeToolCallingChatModel(responses=[])) as client:
        made = client.post("/api/environments", json={"name": "Web"}).json()
        url = f"/api/environments/{made['id']}"
        limited = client.put(
            url,
            json={
                "network": {
                    "mode": "limited",
                    "domains": ["https://www.Example.com/a", "*.wiki.org", ""],
                }
            },
        ).json()
        bad = client.put(url, json={"network": {"mode": "limited", "domains": ["a b"]}})
        empty = client.put(url, json={"network": {"mode": "limited", "domains": []}})
        unisolated = client.put(url, json={"unisolated": True}).json()
        renamed = client.put(url, json={"name": "Web pages"}).json()
        not_a_switch = client.put(url, json={"commands": "yes"})
    assert made["network"] == {"mode": "limited", "domains": ["*"]}
    assert made["scripts"] is made["commands"] is True and made["unisolated"] is False
    assert limited["network"] == {"mode": "limited", "domains": ["example.com", "wiki.org"]}
    assert bad.status_code == empty.status_code == not_a_switch.status_code == 422
    assert unisolated["unisolated"] is True and renamed["name"] == "Web pages"
    entries = AuditLog(tmp_path / "state").read_all()
    created = ["commands", "environment", "name", "network", "scripts", "unisolated"]
    assert [(e.tool_name, sorted(e.arguments)) for e in entries] == [
        ("set_environment", created),
        ("set_environment", ["environment", "name", "network"]),
        ("set_environment", ["environment", "name", "unisolated"]),
    ]


def test_capabilities_change_only_by_name_and_each_change_is_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client_lg(tmp_path, monkeypatch, FakeToolCallingChatModel(responses=[])) as client:
        before = client.get("/api/capabilities").json()
        on = client.put("/api/capabilities/commands", json={"on": True}).json()
        again = client.put("/api/capabilities/commands", json={"on": True}).json()
        unknown = client.put("/api/capabilities/everything", json={"on": True})
        off = client.put("/api/capabilities/commands", json={"on": False}).json()
    none = {"scripts": True, "commands": False, "commands_without_sandbox": False}
    assert before == off == none
    assert on == again == {**none, "commands": True}
    assert unknown.status_code == 404
    entries = AuditLog(tmp_path / "state").read_all()
    assert [(e.tool_name, e.decision) for e in entries] == [
        ("capability_commands", "approve"),
        ("capability_commands", "reject"),
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="the answer off Windows")
def test_the_command_sandbox_is_unsupported_off_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client_lg(tmp_path, monkeypatch, FakeToolCallingChatModel(responses=[])) as client:
        status = client.get("/api/command-sandbox").json()
        set_up = client.post("/api/command-sandbox").json()
    assert status["state"] == set_up["state"] == "unsupported"
    assert AuditLog(tmp_path / "state").read_all() == []


def _fake_codex(tmp_path: Path, exit_code: int) -> Path:
    """Stands in for Codex: records its arguments and CODEX_HOME, and fails with `exit_code`."""
    codex = tmp_path / f"codex-{exit_code}"
    log = tmp_path / "codex-calls.txt"
    codex.write_text(
        f'#!/bin/sh\necho "$CODEX_HOME $*" >> "{log}"\n'
        f'[ {exit_code} -eq 0 ] || echo "The administrator said no." >&2\nexit {exit_code}\n'
    )
    codex.chmod(0o755)
    return codex


@pytest.mark.skipif(sys.platform == "win32", reason="the fake Codex is a shell script")
@pytest.mark.parametrize("exit_code", [0, 1])
def test_setting_the_sandbox_up_records_which_codex_did_it_and_removing_it_forgets(
    exit_code: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = _fake_codex(tmp_path, exit_code)

    class _Service:
        async def download(self) -> Path:
            return codex

    def service(settings: Any) -> _Service:
        return _Service()

    monkeypatch.setattr(command_sandbox, "supported", lambda: True)
    monkeypatch.setattr(environment_routes, "code_service", service)
    with _client_lg(tmp_path, monkeypatch, FakeToolCallingChatModel(responses=[])) as client:
        before = client.get("/api/command-sandbox").json()
        set_up = client.post("/api/command-sandbox").json()
        removed = client.delete("/api/command-sandbox").json()
    home = tmp_path / "state" / "command-sandbox" / "codex-home"
    calls = (tmp_path / "codex-calls.txt").read_text().splitlines()
    assert before["state"] == "off" and removed["state"] == "off"
    entries = [(e.tool_name, e.decision) for e in AuditLog(tmp_path / "state").read_all()]
    if exit_code == 0:
        assert set_up["state"] == "ready"
        assert calls == [
            f"{home} sandbox setup --elevated --current-user",
            f"{home} sandbox uninstall",
        ]
        assert entries == [
            ("command_sandbox_setup", "approve"),
            ("command_sandbox_uninstall", "approve"),
        ]
    else:
        assert set_up == {"state": "failed", "detail": "The administrator said no."}
        assert calls == [f"{home} sandbox setup --elevated --current-user"]
        assert entries == [
            ("command_sandbox_setup", "reject"),
            ("command_sandbox_uninstall", "approve"),
        ]
