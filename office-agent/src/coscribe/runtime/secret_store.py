"""Global secrets the user adds in Settings > Secrets, and what each session
is allowed to use.

A secret is a name, a value and the hosts it may be sent to. The value goes
to the OS keychain and nowhere else; no API returns it, and a machine with no
keychain refuses to save one rather than fall back to a plaintext file (as
the provider and connector keys do), since these are meant to stay out of
reach. What the model gets to see of a secret is its name and hosts; the
value is put in place by coscribe at the moment a request is made.

The guarantee is that the model cannot SEE a value, not that it is defended
against code that goes digging: with no sandbox, a script runs as the same
user and could read the keychain itself.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import secrets as _secrets

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_HOST_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"(\*\.)?{_HOST_LABEL}(\.{_HOST_LABEL})+")
MAX_HOSTS = 20
MIN_VALUE = 8
MAX_VALUE = 16384
MAX_VARIABLES = 100
MAX_VARIABLE_VALUE = 4096

_LOCK = threading.Lock()


class SecretError(ValueError):
    """A request the store refuses, worded for the person who made it."""


class KeychainUnavailable(SecretError):
    pass


def keychain_available() -> bool:
    return _secrets.keychain_backend_usable()


def _ref(name: str) -> str:
    return f"secret:{name}"


def clean_hosts(hosts: Any) -> list[str]:
    if not isinstance(hosts, list) or not all(isinstance(h, str) for h in hosts):
        raise SecretError("hosts must be a list of host names.")
    cleaned: list[str] = []
    for host in hosts:
        host = host.strip().lower()
        if not _HOST.fullmatch(host):
            raise SecretError(
                f"{host!r} isn't a host name: give just the host, like api.example.com "
                "or *.example.com, with no https:// or path."
            )
        if host not in cleaned:
            cleaned.append(host)
    if not cleaned:
        raise SecretError("A secret needs at least one host it may be sent to.")
    if len(cleaned) > MAX_HOSTS:
        raise SecretError(f"At most {MAX_HOSTS} hosts per secret.")
    return cleaned


def host_allowed(pattern: str, host: str) -> bool:
    """Whether `host` is covered by `pattern` ("a.b.com" or "*.b.com", which
    covers subdomains but not b.com itself)."""
    host = host.lower()
    if pattern.startswith("*."):
        return host.endswith(pattern[1:]) and host != pattern[2:]
    return host == pattern


class SecretStore:
    """Names, hosts and keychain references in `secrets.json`; values in the
    keychain."""

    def __init__(self, state_dir: str | Path) -> None:
        self.path = Path(state_dir) / "secrets.json"
        self.state_dir = Path(state_dir)

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        _secrets.harden_file_permissions(self.path)

    def entries(self) -> list[dict[str, Any]]:
        """Names and hosts, never values."""
        data = self._read()
        return [
            {"name": name, "hosts": entry.get("hosts", []), "created_at": entry.get("created_at")}
            for name, entry in sorted(data.items())
        ]

    def names(self) -> set[str]:
        return set(self._read())

    def hosts_of(self, name: str) -> list[str] | None:
        entry = self._read().get(name)
        return None if entry is None else list(entry.get("hosts", []))

    def save(self, name: str, value: str | None, hosts: Any) -> dict[str, Any]:
        """Add a secret, or change an existing one's hosts and, when `value`
        is given, its value."""
        if not NAME.fullmatch(name):
            raise SecretError(
                "A secret's name is letters, digits and underscores, not starting with a digit."
            )
        cleaned = clean_hosts(hosts)
        with _LOCK:
            data = self._read()
            entry = data.get(name)
            if value is None:
                if entry is None:
                    raise SecretError("A new secret needs a value.")
                entry["hosts"] = cleaned
            else:
                if not isinstance(value, str) or not value:
                    raise SecretError("The value can't be empty.")
                if len(value) < MIN_VALUE:
                    raise SecretError(
                        f"A value is at least {MIN_VALUE} characters: a shorter one can't be told "
                        "from ordinary text, so it could not be kept out of what the model reads."
                    )
                if len(value) > MAX_VALUE:
                    raise SecretError(f"A value is at most {MAX_VALUE} characters.")
                if not keychain_available():
                    raise KeychainUnavailable(
                        "This computer has no keychain to keep a secret in, and secrets are "
                        "never written to a plain file. On Windows that is Credential Manager; "
                        "on Linux it needs a Secret Service such as GNOME Keyring."
                    )
                stored = _secrets.store_secret(_ref(name), value)
                if isinstance(stored, str):
                    # The keychain refused it: nothing was kept, and the value
                    # must not end up anywhere else.
                    raise KeychainUnavailable("The keychain refused to store this secret.")
                entry = {
                    "ref": stored,
                    "hosts": cleaned,
                    "created_at": (entry or {}).get("created_at") or datetime.now(UTC).isoformat(),
                }
            data[name] = entry
            self._write(data)
        return {"name": name, "hosts": entry["hosts"], "created_at": entry.get("created_at")}

    def resolve(self, name: str) -> str:
        """The value, for coscribe's own use at request time. Raises
        SecretError, naming the secret, when it can't be read back."""
        entry = self._read().get(name)
        if entry is None:
            raise SecretError(f"There is no secret named {name}.")
        try:
            value = _secrets.resolve_secret(entry.get("ref"))
        except RuntimeError as exc:
            raise SecretError(
                f"The secret {name} can't be read from the keychain ({exc}). "
                "Enter it again in Settings > Secrets."
            ) from exc
        if value is None:
            raise SecretError(f"The secret {name} has no value. Enter it again in Settings.")
        return value

    def delete(self, name: str) -> bool:
        """Remove it, its keychain entry, and its place in every session's
        list."""
        with _LOCK:
            data = self._read()
            entry = data.pop(name, None)
            if entry is None:
                return False
            _secrets.delete_secret(entry.get("ref"))
            self._write(data)
        SessionEnvironments(self.state_dir).detach_everywhere(name)
        return True


class SessionEnvironments:
    """Per session: plain environment variables (the model can see them) and
    which secrets it may use. `<thread>.env.json` in the state folder."""

    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir)

    def _path(self, thread_id: str) -> Path:
        return self.state_dir / f"{thread_id}.env.json"

    def get(self, thread_id: str) -> dict[str, Any]:
        try:
            raw = json.loads(self._path(thread_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        variables = raw.get("variables")
        secrets = raw.get("secrets")
        return {
            "variables": variables if isinstance(variables, dict) else {},
            "secrets": [s for s in secrets if isinstance(s, str)]
            if isinstance(secrets, list)
            else [],
        }

    def set(self, thread_id: str, variables: Any, secrets: Any, known: set[str]) -> dict[str, Any]:
        if not isinstance(variables, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in variables.items()
        ):
            raise SecretError("variables must be a map of names to text values.")
        if not isinstance(secrets, list) or not all(isinstance(s, str) for s in secrets):
            raise SecretError("secrets must be a list of secret names.")
        if len(variables) > MAX_VARIABLES:
            raise SecretError(f"At most {MAX_VARIABLES} variables per session.")
        for key, value in variables.items():
            if not NAME.fullmatch(key):
                raise SecretError(
                    f"{key!r} isn't a variable name: letters, digits and underscores."
                )
            if len(value) > MAX_VARIABLE_VALUE:
                raise SecretError(
                    f"The value of {key} is longer than {MAX_VARIABLE_VALUE} characters."
                )
        chosen = list(dict.fromkeys(secrets))
        missing = [s for s in chosen if s not in known]
        if missing:
            raise SecretError(f"No such secret: {', '.join(missing)}.")
        clash = sorted(set(variables) & set(chosen))
        if clash:
            raise SecretError(f"{', '.join(clash)} is both a variable and a secret; keep one.")
        data = {"variables": variables, "secrets": chosen}
        with _LOCK:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            path = self._path(thread_id)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        return data

    def delete(self, thread_id: str) -> None:
        self._path(thread_id).unlink(missing_ok=True)

    def detach_everywhere(self, name: str) -> None:
        with _LOCK:
            for path in self.state_dir.glob("*.env.json"):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(raw, dict) and name in (raw.get("secrets") or []):
                    raw["secrets"] = [s for s in raw["secrets"] if s != name]
                    tmp = path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
                    os.replace(tmp, path)


PLACEHOLDER = re.compile(r"\{\{secret:([A-Za-z_][A-Za-z0-9_]{0,63})\}\}")


def placeholders_in(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))


def substitute(text: str, values: dict[str, str]) -> str:
    """`text` with each `{{secret:NAME}}` replaced by `values[NAME]`."""
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], text)


def _forms(value: str) -> set[str]:
    """The value as it might come back in a response: as is, URL-encoded,
    base64 (standard and URL-safe, padded or not), hex, and JSON-escaped."""
    raw = value.encode("utf-8")
    forms = {value, quote(value, safe=""), quote(value), raw.hex(), raw.hex().upper()}
    forms.add(json.dumps(value)[1:-1])
    for encoder in (base64.b64encode, base64.urlsafe_b64encode):
        encoded = encoder(raw).decode("ascii")
        forms.update({encoded, encoded.rstrip("=")})
    return {f for f in forms if len(f) >= MIN_VALUE}


_REDACTION_MARK = "[REDACTED SECRET]"
_FORMS_CACHE: dict[str, tuple[tuple[int, int], list[str]]] = {}


def _redaction_forms(state_dir: Path) -> list[str]:
    """Every stored secret's forms, longest first. Re-read when `secrets.json`
    changes, so a value added a moment ago is already covered."""
    path = state_dir / "secrets.json"
    try:
        stat = path.stat()
    except OSError:
        return []
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _FORMS_CACHE.get(str(path))
    if cached is not None and cached[0] == stamp:
        return cached[1]
    store = SecretStore(state_dir)
    forms: set[str] = set()
    for name in store.names():
        try:
            forms |= _forms(store.resolve(name))
        except SecretError:
            continue  # unreadable now, so it can't be in anything either
    ordered = sorted(forms, key=len, reverse=True)
    _FORMS_CACHE[str(path)] = (stamp, ordered)
    return ordered


def redactor(state_dir: str | Path) -> Callable[[str], str]:
    """A function that blanks every stored secret's value, and its common
    encodings, out of a text. Best effort: it cannot see a value the code
    that produced the text has transformed some other way."""
    directory = Path(state_dir)

    def redact(text: str) -> str:
        if not text:
            return text
        for form in _redaction_forms(directory):
            if form in text:
                text = text.replace(form, _REDACTION_MARK)
        return text

    return redact

