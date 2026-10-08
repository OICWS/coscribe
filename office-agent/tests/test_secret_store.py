"""Global secrets and per-session environments (runtime/secret_store.py): the
value goes to the keychain only, and nothing hands it back out."""

from __future__ import annotations

import json
from pathlib import Path

import keyring.errors
import pytest

from coscribe.runtime import secrets
from coscribe.runtime.secret_store import (
    KeychainUnavailable,
    SecretError,
    SecretStore,
    SessionEnvironments,
    host_allowed,
)


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


@pytest.fixture
def keychain(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets, "keyring", fake)
    monkeypatch.setattr(secrets, "keychain_backend_usable", lambda: True)
    return fake


def test_a_value_goes_to_the_keychain_and_never_to_a_file(
    tmp_path: Path, keychain: _FakeKeyring
) -> None:
    store = SecretStore(tmp_path)

    saved = store.save("STRIPE_KEY", "sk-live-123", ["api.stripe.com"])

    assert saved["name"] == "STRIPE_KEY" and saved["hosts"] == ["api.stripe.com"]
    assert "value" not in saved
    assert keychain.store == {("coscribe", "secret:STRIPE_KEY"): "sk-live-123"}
    assert "sk-live-123" not in (tmp_path / "secrets.json").read_text()
    assert "sk-live-123" not in json.dumps(store.entries())
    assert store.resolve("STRIPE_KEY") == "sk-live-123"


def test_a_machine_with_no_keychain_refuses_rather_than_write_a_plain_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(secrets, "keychain_backend_usable", lambda: False)

    with pytest.raises(KeychainUnavailable, match="never written to a plain file"):
        SecretStore(tmp_path).save("A", "value-cccc", ["example.com"])

    assert not (tmp_path / "secrets.json").exists()


def test_a_keychain_that_refuses_the_write_leaves_no_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Refusing(_FakeKeyring):
        def set_password(self, service: str, ref: str, value: str) -> None:
            raise keyring.errors.PasswordSetError("locked")

    monkeypatch.setattr(secrets, "keyring", Refusing())
    monkeypatch.setattr(secrets, "keychain_backend_usable", lambda: True)

    with pytest.raises(KeychainUnavailable):
        SecretStore(tmp_path).save("A", "value-cccc", ["example.com"])

    assert not (tmp_path / "secrets.json").exists()


def test_hosts_can_be_changed_without_the_value_and_the_value_replaced(
    tmp_path: Path, keychain: _FakeKeyring
) -> None:
    store = SecretStore(tmp_path)
    store.save("K", "value-one-1", ["a.example.com"])

    store.save("K", None, ["b.example.com", "*.c.example.com"])
    assert store.hosts_of("K") == ["b.example.com", "*.c.example.com"]
    assert store.resolve("K") == "value-one-1"

    store.save("K", "value-two-2", ["b.example.com"])
    assert store.resolve("K") == "value-two-2"


@pytest.mark.parametrize(
    ("name", "value", "hosts", "message"),
    [
        ("1bad", "value-ok-1", ["a.com"], "name"),
        ("has space", "value-ok-1", ["a.com"], "name"),
        ("OK", "", ["a.com"], "empty"),
        ("OK", "value-ok-1", [], "at least one host"),
        ("OK", "value-ok-1", ["https://a.com"], "isn't a host name"),
        ("OK", "value-ok-1", ["a.com/path"], "isn't a host name"),
        ("OK", "value-ok-1", ["a.com:443"], "isn't a host name"),
        ("OK", "value-ok-1", ["localhost"], "isn't a host name"),
        ("OK", "value-ok-1", "a.com", "list of host names"),
    ],
)
def test_what_the_store_refuses(
    name: str, value: str, hosts: object, message: str, tmp_path: Path, keychain: _FakeKeyring
) -> None:
    with pytest.raises(SecretError, match=message):
        SecretStore(tmp_path).save(name, value, hosts)

    assert keychain.store == {}


def test_a_value_too_short_to_tell_from_ordinary_text_is_refused(
    tmp_path: Path, keychain: _FakeKeyring
) -> None:
    with pytest.raises(SecretError, match="at least 8"):
        SecretStore(tmp_path).save("PIN", "1234567", ["a.com"])

    assert keychain.store == {}


def test_a_new_secret_needs_a_value(tmp_path: Path, keychain: _FakeKeyring) -> None:
    with pytest.raises(SecretError, match="needs a value"):
        SecretStore(tmp_path).save("NEW", None, ["a.com"])


@pytest.mark.parametrize(
    ("pattern", "host", "allowed"),
    [
        ("api.example.com", "api.example.com", True),
        ("api.example.com", "API.example.com", True),
        ("api.example.com", "evil.example.com", False),
        ("api.example.com", "api.example.com.evil.net", False),
        ("*.example.com", "api.example.com", True),
        ("*.example.com", "a.b.example.com", True),
        ("*.example.com", "example.com", False),
        ("*.example.com", "badexample.com", False),
    ],
)
def test_host_matching(pattern: str, host: str, allowed: bool) -> None:
    assert host_allowed(pattern, host) is allowed


def test_a_secret_missing_from_the_keychain_is_a_clear_error_naming_it(
    tmp_path: Path, keychain: _FakeKeyring
) -> None:
    store = SecretStore(tmp_path)
    store.save("GONE", "value-gone-1", ["a.com"])
    keychain.store.clear()

    with pytest.raises(SecretError, match="GONE"):
        store.resolve("GONE")
    with pytest.raises(SecretError, match="no secret named NOPE"):
        store.resolve("NOPE")


def test_deleting_a_secret_removes_its_keychain_entry_and_every_sessions_use_of_it(
    tmp_path: Path, keychain: _FakeKeyring
) -> None:
    store = SecretStore(tmp_path)
    store.save("A", "value-aaaa", ["a.com"])
    store.save("B", "value-bbbb", ["b.com"])
    environments = SessionEnvironments(tmp_path)
    environments.set("s1", {}, ["A", "B"], store.names())
    environments.set("s2", {"X": "1"}, ["A"], store.names())

    assert store.delete("A") is True

    assert store.names() == {"B"}
    assert ("coscribe", "secret:A") not in keychain.store
    assert environments.get("s1")["secrets"] == ["B"]
    assert environments.get("s2") == {"variables": {"X": "1"}, "secrets": []}
    assert store.delete("A") is False


def test_a_session_environment_is_checked(tmp_path: Path, keychain: _FakeKeyring) -> None:
    store = SecretStore(tmp_path)
    store.save("A", "value-aaaa", ["a.com"])
    environments = SessionEnvironments(tmp_path)

    saved = environments.set("s", {"MODE": "fast"}, ["A", "A"], store.names())
    assert saved == {"variables": {"MODE": "fast"}, "secrets": ["A"]}
    assert environments.get("s") == saved
    assert environments.get("other") == {"variables": {}, "secrets": []}

    for variables, secrets_, message in [
        ({"bad name": "1"}, [], "variable name"),
        ({"X": 1}, [], "text values"),
        ({"X": "1"}, ["NOPE"], "No such secret"),
        ({"A": "1"}, ["A"], "both a variable and a secret"),
        ({"X": "y" * 5000}, [], "longer than"),
        ({f"V{i}": "1" for i in range(101)}, [], "At most"),
    ]:
        with pytest.raises(SecretError, match=message):
            environments.set("s", variables, secrets_, store.names())
    assert environments.get("s") == saved

