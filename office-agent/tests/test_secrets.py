"""Tests for runtime/secrets.py -- both the keyring-available path (real
keyring mocked out via monkeypatch, so this doesn't depend on a real OS
keychain existing) and the keyring-unavailable/fallback path (forced via a
fake backend that always raises, rather than relying on this sandbox's own
real absence of a backend as the only coverage)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import keyring.errors
import pytest

from coscribe.runtime import secrets


class _FakeKeyring:
    """In-memory stand-in for the `keyring` module -- just the three
    functions runtime/secrets.py actually calls, backed by a dict."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.errors = keyring.errors

    def set_password(self, service: str, ref: str, value: str) -> None:
        self.store[(service, ref)] = value

    def get_password(self, service: str, ref: str) -> str | None:
        return self.store.get((service, ref))

    def delete_password(self, service: str, ref: str) -> None:
        self.store.pop((service, ref), None)


class _AlwaysFailingKeyring:
    """Simulates a keyring install with no real backend (this sandbox's
    own actual state, confirmed live: `keyring.backends.fail.Keyring`) --
    every call raises, exercising the fallback path deterministically."""

    def __init__(self) -> None:
        self.errors = keyring.errors

    def set_password(self, service: str, ref: str, value: str) -> None:
        raise keyring.errors.PasswordSetError("no backend available")

    def get_password(self, service: str, ref: str) -> str | None:
        raise keyring.errors.KeyringError("no backend available")

    def delete_password(self, service: str, ref: str) -> None:
        raise keyring.errors.PasswordDeleteError("no backend available")


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets, "keyring", fake)
    return fake


@pytest.fixture
def failing_keyring(monkeypatch: pytest.MonkeyPatch) -> _AlwaysFailingKeyring:
    fake = _AlwaysFailingKeyring()
    monkeypatch.setattr(secrets, "keyring", fake)
    return fake


# -- store_secret / resolve_secret round trip --


def test_store_then_resolve_round_trips_when_keyring_available(fake_keyring: _FakeKeyring) -> None:
    stored = secrets.store_secret("custom-provider:deepseek", "sk-real-value")

    assert stored == {"keyring_ref": "custom-provider:deepseek"}
    assert secrets.resolve_secret(stored) == "sk-real-value"
    # The real secret never ends up in what gets persisted.
    assert "sk-real-value" not in str(stored)


def test_store_falls_back_to_plaintext_when_keyring_unavailable(
    failing_keyring: _AlwaysFailingKeyring,
) -> None:
    stored = secrets.store_secret("custom-provider:deepseek", "sk-real-value")

    assert stored == "sk-real-value"
    assert secrets.resolve_secret(stored) == "sk-real-value"


def test_resolve_secret_none_safe() -> None:
    assert secrets.resolve_secret(None) is None


def test_resolve_secret_legacy_plaintext_passthrough() -> None:
    # A value already on disk from before this module existed, or written
    # by the keyring-unavailable fallback -- same shape, same handling.
    assert secrets.resolve_secret("sk-already-on-disk") == "sk-already-on-disk"


def test_resolve_secret_raises_clearly_when_ref_cannot_be_resolved(
    failing_keyring: _AlwaysFailingKeyring,
) -> None:
    with pytest.raises(RuntimeError, match="re-enter it via Settings"):
        secrets.resolve_secret({"keyring_ref": "custom-provider:deepseek"})


def test_resolve_secret_raises_when_ref_present_but_backend_has_nothing(
    fake_keyring: _FakeKeyring,
) -> None:
    # Backend works, but this specific ref was never actually stored there
    # (e.g. the keychain was cleared out from under the app).
    with pytest.raises(RuntimeError, match="re-enter it via Settings"):
        secrets.resolve_secret({"keyring_ref": "never-stored"})


# -- delete_secret --


def test_delete_secret_removes_from_keyring(fake_keyring: _FakeKeyring) -> None:
    stored = secrets.store_secret("custom-provider:deepseek", "sk-real-value")

    secrets.delete_secret(stored)

    with pytest.raises(RuntimeError):
        secrets.resolve_secret(stored)


def test_delete_secret_is_a_noop_for_a_plain_string() -> None:
    secrets.delete_secret("sk-plaintext")  # must not raise


def test_delete_secret_is_a_noop_for_none() -> None:
    secrets.delete_secret(None)  # must not raise


def test_delete_secret_swallows_keyring_errors(failing_keyring: _AlwaysFailingKeyring) -> None:
    secrets.delete_secret({"keyring_ref": "whatever"})  # must not raise


# -- .env-specific helpers --


def test_env_value_for_storage_and_display_round_trip(fake_keyring: _FakeKeyring) -> None:
    stored = secrets.env_value_for_storage("builtin-provider:ANTHROPIC_API_KEY", "sk-ant-real")

    assert stored.startswith(secrets.ENV_KEYRING_PREFIX)
    assert "sk-ant-real" not in stored
    assert secrets.env_resolve_secret_for_display(stored) == "sk-ant-real"


def test_env_value_for_storage_is_plaintext_when_keyring_unavailable(
    failing_keyring: _AlwaysFailingKeyring,
) -> None:
    stored = secrets.env_value_for_storage("builtin-provider:ANTHROPIC_API_KEY", "sk-ant-real")

    assert stored == "sk-ant-real"
    assert secrets.env_resolve_secret_for_display(stored) == "sk-ant-real"


def test_env_resolve_secret_for_display_passthrough_for_non_sentinel() -> None:
    assert secrets.env_resolve_secret_for_display("sk-plain") == "sk-plain"
    assert secrets.env_resolve_secret_for_display(None) is None


def test_env_delete_secret_if_ref_cleans_up_keyring(fake_keyring: _FakeKeyring) -> None:
    stored = secrets.env_value_for_storage("builtin-provider:ANTHROPIC_API_KEY", "sk-ant-real")

    secrets.env_delete_secret_if_ref(stored)

    with pytest.raises(RuntimeError):
        secrets.env_resolve_secret_for_display(stored)


def test_env_delete_secret_if_ref_is_a_noop_for_plain_value() -> None:
    secrets.env_delete_secret_if_ref("sk-plain")  # must not raise
    secrets.env_delete_secret_if_ref(None)  # must not raise


def test_resolve_env_keyring_refs_replaces_sentinels_in_os_environ(
    fake_keyring: _FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = secrets.env_value_for_storage("builtin-provider:ANTHROPIC_API_KEY", "sk-ant-real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", stored)
    monkeypatch.setenv("UNRELATED_VAR", "plain-value")

    secrets.resolve_env_keyring_refs()

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-real"
    assert os.environ["UNRELATED_VAR"] == "plain-value"


# -- harden_file_permissions --


def test_harden_file_permissions_sets_owner_only_mode(tmp_path: Path) -> None:
    path = tmp_path / "providers.json"
    path.write_text("{}", encoding="utf-8")
    os.chmod(path, 0o644)

    secrets.harden_file_permissions(path)

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_harden_file_permissions_swallows_errors_for_a_missing_file(tmp_path: Path) -> None:
    secrets.harden_file_permissions(tmp_path / "does-not-exist.json")  # must not raise
