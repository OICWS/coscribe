"""Secrets hardening (ROADMAP.md Phase 4): two independent, additive
layers applied to every secret coscribe itself persists (built-in provider
API keys in `.env`, custom providers' `api_key` in `providers.json`, MCP
server `env`/`headers` values in `mcp.json`, including the GitHub
device-flow OAuth bearer token):

1. File permission hardening (harden_file_permissions) -- unconditional,
   no new dependency, no failure mode: chmod 0o600 on a secret-bearing
   file after every write.
2. OS-keychain-backed storage (store_secret/resolve_secret/delete_secret)
   -- optional, via the `keyring` package. When a real backend is
   available and working, the actual secret *value* lives in the OS
   keychain and the on-disk file holds only a small {"keyring_ref": ...}
   marker. When keyring is unavailable or a call fails for any reason (no
   backend -- true of this project's own headless-Linux sandbox, no
   Secret Service running -- or the file was copied to a different
   machine), storage falls back to today's plaintext value, still gets
   layer 1's permission hardening, and the write still succeeds. Same
   "optional, gracefully degrading system dependency" shape as the
   LibreOffice/poppler-utils checks in tools/presentations.py --
   best-effort, never the reason a save fails.

A plain string stored where store_secret's result is expected covers two
cases identically, by design: legacy plaintext already on disk from
before this module existed, and today's keyring-unavailable fallback.
resolve_secret treats both the same way -- return it unchanged -- so no
forced migration step is needed for an existing install.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import keyring
    import keyring.errors
except ImportError:  # pragma: no cover -- keyring is a normal dependency,
    # but this keeps the module importable (falling back to plaintext
    # everywhere) if it's ever missing from an environment for some reason.
    keyring = None  # type: ignore[assignment]

SERVICE_NAME = "coscribe"

# Sentinel prefix for a keyring-backed value stored in `.env`, which (unlike
# providers.json/mcp.json) is flat KEY=value text and can't hold a
# {"keyring_ref": ...} dict directly.
ENV_KEYRING_PREFIX = "__coscribe_keyring__:"


def _keyring_available() -> bool:
    return keyring is not None


def store_secret(ref: str, value: str) -> str | dict[str, str]:
    """Try to store `value` in the OS keychain under `ref`. Returns what
    the caller should persist in its place: {"keyring_ref": ref} on
    success, or `value` itself unchanged if keyring is unavailable or the
    call fails for any reason (the caller then writes it as plaintext and
    should call harden_file_permissions on the file)."""
    if not _keyring_available():
        return value
    try:
        keyring.set_password(SERVICE_NAME, ref, value)
    except keyring.errors.KeyringError:
        return value
    return {"keyring_ref": ref}


def resolve_secret(stored: str | dict[str, Any] | None) -> str | None:
    """Resolve a value previously returned by store_secret (or a legacy
    plain string already on disk) back to the real secret. None-safe."""
    if stored is None:
        return None
    if isinstance(stored, str):
        return stored
    ref = stored.get("keyring_ref")
    if ref is None:
        return None
    if not _keyring_available():
        raise RuntimeError(
            f"Secret {ref!r} was stored in the OS keychain, but keyring is not "
            "available in this environment -- re-enter it via Settings."
        )
    try:
        value = keyring.get_password(SERVICE_NAME, ref)
    except keyring.errors.KeyringError as exc:
        raise RuntimeError(
            f"Secret {ref!r} could not be read back from the OS keychain "
            f"({exc}) -- re-enter it via Settings."
        ) from exc
    if value is None:
        raise RuntimeError(
            f"Secret {ref!r} was expected in the OS keychain but is missing -- "
            "re-enter it via Settings."
        )
    return value


def delete_secret(stored: str | dict[str, Any] | None) -> None:
    """Best-effort keyring cleanup when a provider/server/token is removed
    -- a no-op for a plain string (nothing was ever stored in keyring for
    it) or if keyring itself is unavailable. Never raises: a failed
    cleanup shouldn't block the caller's own removal from succeeding."""
    if not isinstance(stored, dict):
        return
    ref = stored.get("keyring_ref")
    if ref is None or not _keyring_available():
        return
    try:
        keyring.delete_password(SERVICE_NAME, ref)
    except keyring.errors.KeyringError:
        pass


def harden_file_permissions(path: Path) -> None:
    """chmod 0o600 (owner read/write only) -- best-effort defense in
    depth, called after every write to a secret-bearing file regardless of
    whether keyring is in play (a keyring-unavailable fallback still needs
    this, and a keyring-available file may still hold non-secret
    structural data worth restricting anyway)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def env_value_for_storage(ref: str, value: str) -> str:
    """`.env`-specific wrapper around store_secret: serializes its
    dict-or-string result into the single sentinel-prefixed string `.env`
    can actually hold. Pair with resolve_env_keyring_refs to reverse this
    at startup."""
    stored = store_secret(ref, value)
    if isinstance(stored, str):
        return stored
    return ENV_KEYRING_PREFIX + stored["keyring_ref"]


def env_delete_secret_if_ref(value: str | None) -> None:
    """`.env`-specific counterpart to delete_secret: `value` is whatever
    was previously read back from `.env` for this key (a real value, a
    sentinel string, or None if it was never set) -- cleans up the
    keyring entry only if it actually is one."""
    if value is not None and value.startswith(ENV_KEYRING_PREFIX):
        delete_secret({"keyring_ref": value[len(ENV_KEYRING_PREFIX) :]})


def env_resolve_secret_for_display(value: str | None) -> str | None:
    """`.env`-specific counterpart to resolve_secret: unlike
    providers.json/mcp.json (where a keyring ref is a {"keyring_ref": ...}
    dict, distinguishable from a plain string on sight), a value read back
    from `.env` is always a string -- resolve_secret alone can't tell a
    real plaintext value apart from a `.env` sentinel string, since both
    are plain strs. This unwraps the sentinel first; a non-sentinel value
    (real plaintext, or None) passes through unchanged."""
    if value is None or not value.startswith(ENV_KEYRING_PREFIX):
        return value
    return resolve_secret({"keyring_ref": value[len(ENV_KEYRING_PREFIX) :]})


def resolve_env_keyring_refs() -> None:
    """Startup pass: for every os.environ value that's a `.env`-sentinel
    (see env_value_for_storage), replace it in os.environ with the real
    resolved secret -- so every downstream `os.getenv("ANTHROPIC_API_KEY")`
    call (inside the anthropic/openai/google-genai SDKs themselves,
    unmodified) sees the real value, never the sentinel. Call once, right
    after load_dotenv(...), in cli.py's _load_settings() and web/app.py's
    create_app_lg startup -- the same point .env already gets loaded into
    the real process environment today."""
    for key, value in list(os.environ.items()):
        if not value.startswith(ENV_KEYRING_PREFIX):
            continue
        os.environ[key] = env_resolve_secret_for_display(value) or ""
