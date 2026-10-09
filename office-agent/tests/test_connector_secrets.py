"""Connectors that refer to a secret from Settings > Secrets instead of holding
a key: `{{secret:NAME}}` in a header or an env value is filled in when the
connector connects, and only for a host the secret is allowed for."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import keyring.errors
import pytest

from coscribe.runtime import secrets
from coscribe.runtime.secret_store import SecretError, SecretStore
from coscribe.tools.mcp import (
    load_mcp_server_configs,
    prepare_for_connect,
    validate_mcp_config,
    with_secrets,
)

from .web.helpers import FakeToolCallingChatModel, _client_lg

KEY = "sk-connector-abcdef123456"


class _FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.errors = keyring.errors

    def set_password(self, service: str, ref: str, value: str) -> None:
        self.store[(service, ref)] = value

    def get_password(self, service: str, ref: str) -> str | None:
        return self.store.get((service, ref))

    def delete_password(self, service: str, ref: str) -> None:
        self.store.pop((service, ref), None)


@pytest.fixture(autouse=True)
def keychain(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets, "keyring", fake)
    monkeypatch.setattr(secrets, "keychain_backend_usable", lambda: True)
    return fake


def _remote(headers: dict[str, str], url: str = "https://mcp.example.com/mcp") -> Any:
    return validate_mcp_config(
        {"type": "mcp", "name": "svc", "server_url": url, "headers": headers}
    )


def test_a_header_gets_the_secret_for_the_connectors_own_host(tmp_path: Path) -> None:
    SecretStore(tmp_path).save("SVC_KEY", KEY, ["mcp.example.com"])

    config = with_secrets(_remote({"Authorization": "Bearer {{secret:SVC_KEY}}"}), tmp_path)

    assert config["headers"] == {"Authorization": f"Bearer {KEY}"}


@pytest.mark.parametrize(
    ("hosts", "url"),
    [
        (["other.example.com"], "https://mcp.example.com/mcp"),
        (["example.com"], "https://mcp.example.com/mcp"),
        (["mcp.example.com"], "https://mcp.example.com.evil.net/mcp"),
    ],
)
def test_a_header_is_refused_a_secret_that_is_not_for_that_host(
    hosts: list[str], url: str, tmp_path: Path
) -> None:
    SecretStore(tmp_path).save("SVC_KEY", KEY, hosts)

    with pytest.raises(SecretError, match="SVC_KEY") as caught:
        with_secrets(_remote({"Authorization": "{{secret:SVC_KEY}}"}, url), tmp_path)

    assert KEY not in str(caught.value) and "svc" in str(caught.value)


def test_an_env_value_for_a_local_command_gets_the_secret_without_a_host_check(
    tmp_path: Path,
) -> None:
    SecretStore(tmp_path).save("SVC_KEY", KEY, ["api.example.com"])
    config = validate_mcp_config(
        {"type": "mcp", "name": "local", "command": "npx", "env": {"TOKEN": "{{secret:SVC_KEY}}"}}
    )

    assert with_secrets(config, tmp_path)["env"] == {"TOKEN": KEY}


@pytest.mark.parametrize("state", ["none", "unknown", "missing"])
def test_a_secret_that_cannot_be_found_is_an_error_naming_it(
    state: str, tmp_path: Path, keychain: _FakeKeyring
) -> None:
    if state != "unknown":
        SecretStore(tmp_path).save("SVC_KEY", KEY, ["mcp.example.com"])
    if state == "missing":
        keychain.store.clear()
    config = _remote({"A": "{{secret:SVC_KEY}}"})

    with pytest.raises(SecretError, match="SVC_KEY|secret"):
        with_secrets(config, None if state == "none" else tmp_path)


def test_one_connectors_missing_secret_leaves_the_others_loaded(tmp_path: Path) -> None:
    SecretStore(tmp_path).save("SVC_KEY", KEY, ["mcp.example.com"])
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "good": {
                        "server_url": "https://mcp.example.com/mcp",
                        "headers": {"A": "{{secret:SVC_KEY}}"},
                    },
                    "broken": {
                        "server_url": "https://mcp.example.com/mcp",
                        "headers": {"A": "{{secret:NOPE}}"},
                    },
                    "plain": {"command": "npx", "env": {"B": "literal"}},
                }
            }
        )
    )

    loaded = load_mcp_server_configs(path, tmp_path)
    shown = load_mcp_server_configs(path, tmp_path, fill_secrets=False)

    assert set(loaded) == {"good", "plain"}
    assert loaded["good"]["headers"] == {"A": KEY}
    assert shown["broken"]["headers"] == {"A": "{{secret:NOPE}}"}


def test_a_stored_keychain_reference_and_a_placeholder_are_both_read_back(
    tmp_path: Path,
) -> None:
    SecretStore(tmp_path).save("SVC_KEY", KEY, ["mcp.example.com"])
    stored = secrets.store_secret("mcp:svc:headers:A", "Bearer {{secret:SVC_KEY}}")
    config = _remote({})
    config["headers"] = {"A": stored, "B": "plain"}  # type: ignore[dict-item]

    prepared = prepare_for_connect(config, tmp_path)

    assert prepared["headers"] == {"A": f"Bearer {KEY}", "B": "plain"}


def test_through_the_api_the_value_is_never_stored_shown_or_deleted_while_in_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    seen: list[Any] = []

    async def recording_connect(name: str, config: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append(config)
        return [], None, None

    with _client_lg(tmp_path, monkeypatch, FakeToolCallingChatModel(responses=[])) as client:
        monkeypatch.setattr("coscribe.runtime_lg.mcp.connect_one_mcp_server_lg", recording_connect)
        client.put("/api/secrets/SVC_KEY", json={"value": KEY, "hosts": ["mcp.example.com"]})
        wrong_host = client.post(
            "/api/mcp/servers",
            json={
                "name": "bad",
                "server_url": "https://elsewhere.example.net/mcp",
                "headers": {"Authorization": "Bearer {{secret:SVC_KEY}}"},
            },
        )
        added = client.post(
            "/api/mcp/servers",
            json={
                "name": "svc",
                "server_url": "https://mcp.example.com/mcp",
                "headers": {"Authorization": "Bearer {{secret:SVC_KEY}}"},
            },
        )
        listed = client.get("/api/mcp/servers")
        refused = client.delete("/api/secrets/SVC_KEY")
        removed = client.delete("/api/mcp/servers/svc")
        deleted = client.delete("/api/secrets/SVC_KEY")

    assert "SVC_KEY" in wrong_host.json()["rejected"]["bad"]
    assert added.json()["rejected"] == {}
    assert [c["headers"] for c in seen] == [{"Authorization": f"Bearer {KEY}"}]
    saved = (tmp_path / "mcp.json").read_text()
    assert KEY not in saved

    shown = listed.json()["svc"]["masked_headers"]["Authorization"]
    assert "{{secret:SVC_KEY}}" in shown and KEY not in shown and KEY[-4:] not in shown
    assert refused.status_code == 409 and refused.json()["connectors"] == ["svc"]
    assert removed.status_code == 200
    assert deleted.status_code == 200


def test_a_secrets_value_that_looks_like_a_placeholder_is_not_expanded_again(
    tmp_path: Path,
) -> None:
    store = SecretStore(tmp_path)
    store.save("INNER_KEY", "inner-secret-value", ["mcp.example.com"])
    store.save("OUTER_KEY", "x{{secret:INNER_KEY}}y-padding", ["mcp.example.com"])

    config = with_secrets(_remote({"A": "{{secret:OUTER_KEY}}"}), tmp_path)

    assert config["headers"] == {"A": "x{{secret:INNER_KEY}}y-padding"}


def test_the_connector_list_says_why_a_connector_has_no_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    (tmp_path / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "svc": {
                        "server_url": "https://mcp.example.com/mcp",
                        "headers": {"A": "{{secret:NOPE}}"},
                    },
                    "ok": {"server_url": "https://mcp.example.com/mcp", "headers": {"A": "x"}},
                }
            }
        )
    )
    with _client_lg(
        tmp_path,
        monkeypatch,
        FakeToolCallingChatModel(responses=[]),
        mcp_config_path=tmp_path / "mcp.json",
    ) as client:
        listed = client.get("/api/mcp/servers").json()

    assert "NOPE" in listed["svc"]["secret_error"]
    assert listed["ok"]["secret_error"] is None


def test_an_unreadable_connector_setting_blocks_deleting_a_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keychain: _FakeKeyring
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    stored = secrets.store_secret("mcp:svc:headers:A", "Bearer {{secret:SVC_KEY}}")
    (tmp_path / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "svc": {
                        "server_url": "https://mcp.example.com/mcp",
                        "headers": {"A": stored},
                    }
                }
            }
        )
    )
    with _client_lg(
        tmp_path,
        monkeypatch,
        FakeToolCallingChatModel(responses=[]),
        mcp_config_path=tmp_path / "mcp.json",
    ) as client:
        client.put("/api/secrets/SVC_KEY", json={"value": KEY, "hosts": ["mcp.example.com"]})
        for key in [k for k in keychain.store if k[1].startswith("mcp:")]:
            del keychain.store[key]
        blocked = client.delete("/api/secrets/SVC_KEY")

    assert blocked.status_code == 503
    assert SecretStore(tmp_path / "state").names() == {"SVC_KEY"}
