"""Named environments (#588): where a conversation works and what it may reach. Each one holds
folders, network access, the secrets it may use, plain variables, switches that narrow scripts and
commands below Settings › Capabilities, and the audited choice to run scripts without isolation.
A conversation uses exactly one; one with none chosen uses Default, which can't be deleted.

`environments.json` in the state folder holds every environment and which conversation uses
which. Read at each call, so an edit applies to the next step of a running turn.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from .network_access import MODES, clean_network
from .secret_store import MAX_VARIABLE_VALUE, MAX_VARIABLES, NAME

DEFAULT_ID = "default"
MAX_NAME = 60
_FILE = "environments.json"
_LOCK = threading.Lock()


class EnvironmentRefused(ValueError):
    """A change the store refuses, worded for the person who made it."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def blank(name: str) -> dict[str, Any]:
    """What a new environment holds: the defaults every conversation had before #588."""
    return {
        "name": name,
        "folders": [],
        "network": {"mode": "limited", "domains": ["*"]},
        "secrets": [],
        "variables": {},
        "scripts": True,
        "commands": True,
        "unisolated": False,
    }


def _clean_variables(variables: Any) -> dict[str, str]:
    if not isinstance(variables, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in variables.items()
    ):
        raise EnvironmentRefused("variables must be a map of names to text values.")
    if len(variables) > MAX_VARIABLES:
        raise EnvironmentRefused(f"At most {MAX_VARIABLES} variables.")
    for key, value in variables.items():
        if not NAME.fullmatch(key):
            raise EnvironmentRefused(
                f"{key!r} isn't a variable name: letters, digits and underscores."
            )
        if len(value) > MAX_VARIABLE_VALUE:
            raise EnvironmentRefused(
                f"The value of {key} is longer than {MAX_VARIABLE_VALUE} characters."
            )
    return dict(variables)


def _clean_folders(folders: Any) -> list[str]:
    if not isinstance(folders, list) or not all(isinstance(f, str) for f in folders):
        raise EnvironmentRefused("folders must be a list of folder paths.")
    kept = list(dict.fromkeys(f for f in folders if f.strip()))
    for folder in kept:
        if not Path(folder).is_absolute() or not Path(folder).is_dir():
            raise EnvironmentRefused(f"{folder!r} isn't an existing folder.")
    return kept


def _clean(fields: dict[str, Any], current: dict[str, Any], known: set[str]) -> dict[str, Any]:
    """`current` with `fields` applied, each one checked; the fields not given stay."""
    env = {**current}
    if "name" in fields:
        name = fields["name"].strip() if isinstance(fields["name"], str) else ""
        if not name or len(name) > MAX_NAME:
            raise EnvironmentRefused(f"Give the environment a name of up to {MAX_NAME} characters.")
        env["name"] = name
    if "folders" in fields:
        env["folders"] = _clean_folders(fields["folders"])
    if "network" in fields:
        network = fields["network"] if isinstance(fields["network"], dict) else {}
        try:
            env["network"] = clean_network(network.get("mode"), network.get("domains", []))
        except ValueError as exc:
            raise EnvironmentRefused(str(exc)) from exc
    if "variables" in fields:
        env["variables"] = _clean_variables(fields["variables"])
    if "secrets" in fields:
        chosen = fields["secrets"]
        if not isinstance(chosen, list) or not all(isinstance(s, str) for s in chosen):
            raise EnvironmentRefused("secrets must be a list of secret names.")
        missing = [s for s in chosen if s not in known]
        if missing:
            raise EnvironmentRefused(f"No such secret: {', '.join(missing)}.")
        env["secrets"] = list(dict.fromkeys(chosen))
    for switch in ("scripts", "commands", "unisolated"):
        if switch in fields:
            if not isinstance(fields[switch], bool):
                raise EnvironmentRefused(f"{switch} must be true or false.")
            env[switch] = fields[switch]
    clash = sorted(set(env["variables"]) & set(env["secrets"]))
    if clash:
        raise EnvironmentRefused(f"{', '.join(clash)} is both a variable and a secret; keep one.")
    return env


def _readable(raw: Any) -> dict[str, Any]:
    """One stored environment as the rest of coscribe reads it: a field missing or of the wrong
    shape falls back to its default rather than failing every conversation that uses it."""
    if not isinstance(raw, dict):
        return blank("")
    env = blank(raw["name"] if isinstance(raw.get("name"), str) else "")
    if isinstance(raw.get("folders"), list):
        env["folders"] = [f for f in raw["folders"] if isinstance(f, str)]
    network = raw.get("network")
    if isinstance(network, dict) and network.get("mode") in MODES:
        domains = network.get("domains")
        env["network"] = {
            "mode": network["mode"],
            "domains": [d for d in domains if isinstance(d, str)]
            if isinstance(domains, list)
            else [],
        }
    if isinstance(raw.get("secrets"), list):
        env["secrets"] = [s for s in raw["secrets"] if isinstance(s, str)]
    if isinstance(raw.get("variables"), dict):
        env["variables"] = {
            k: v for k, v in raw["variables"].items() if isinstance(k, str) and isinstance(v, str)
        }
    for switch in ("scripts", "commands", "unisolated"):
        if isinstance(raw.get(switch), bool):
            env[switch] = raw[switch]
    return env


def _settings_key(env: dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in env.items() if k != "name"}, sort_keys=True)


class Environments:
    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / _FILE

    def exists(self) -> bool:
        return self.path.is_file()

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        stored = raw.get("environments")
        envs = {
            str(k): _readable(v) for k, v in (stored.items() if isinstance(stored, dict) else [])
        }
        envs.setdefault(DEFAULT_ID, blank("Default"))
        envs[DEFAULT_ID]["name"] = envs[DEFAULT_ID]["name"] or "Default"
        threads = raw.get("threads")
        uses = {
            str(t): str(e)
            for t, e in (threads.items() if isinstance(threads, dict) else [])
            if str(e) in envs and str(e) != DEFAULT_ID
        }
        return {"environments": envs, "threads": uses}

    def _write(self, data: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def all(self) -> list[dict[str, Any]]:
        """Every environment, Default first, each with its id and how many conversations use it."""
        data = self._read()
        counts: dict[str, int] = {}
        for env_id in data["threads"].values():
            counts[env_id] = counts.get(env_id, 0) + 1
        ordered = sorted(
            data["environments"].items(),
            key=lambda item: (item[0] != DEFAULT_ID, item[1]["name"].lower()),
        )
        return [{"id": env_id, **env, "used_by": counts.get(env_id, 0)} for env_id, env in ordered]

    def get(self, env_id: str) -> dict[str, Any] | None:
        env = self._read()["environments"].get(env_id)
        return {"id": env_id, **env} if env is not None else None

    def environment_of(self, thread_id: str | None) -> str:
        if thread_id is None:
            return DEFAULT_ID
        return str(self._read()["threads"].get(thread_id, DEFAULT_ID))

    def for_thread(self, thread_id: str | None) -> dict[str, Any]:
        data = self._read()
        env_id = data["threads"].get(thread_id, DEFAULT_ID) if thread_id else DEFAULT_ID
        return {"id": env_id, **data["environments"][env_id]}

    def assignments(self) -> dict[str, str]:
        """Which environment each conversation chose; the others use Default."""
        return dict(self._read()["threads"])

    def create(self, fields: dict[str, Any], known: set[str]) -> dict[str, Any]:
        with _LOCK:
            data = self._read()
            env = _clean({"name": "", **fields}, blank(""), known)
            self._check_unique(data, env["name"], None)
            env_id = f"env_{uuid.uuid4().hex[:12]}"
            data["environments"][env_id] = env
            self._write(data)
        return {"id": env_id, **env}

    def update(self, env_id: str, fields: dict[str, Any], known: set[str]) -> dict[str, Any]:
        with _LOCK:
            data = self._read()
            current = data["environments"].get(env_id)
            if current is None:
                raise KeyError(env_id)
            env = _clean(fields, current, known)
            self._check_unique(data, env["name"], env_id)
            data["environments"][env_id] = env
            self._write(data)
        return {"id": env_id, **env}

    @staticmethod
    def _check_unique(data: dict[str, Any], name: str, own_id: str | None) -> None:
        for env_id, env in data["environments"].items():
            if env_id != own_id and env["name"].lower() == name.lower():
                raise EnvironmentRefused(f"There is already an environment named {env['name']}.")

    def delete(self, env_id: str) -> list[str]:
        """Removes `env_id`; its conversations move to Default. Returns them."""
        if env_id == DEFAULT_ID:
            raise EnvironmentRefused("Default can't be deleted.")
        with _LOCK:
            data = self._read()
            if env_id not in data["environments"]:
                raise KeyError(env_id)
            del data["environments"][env_id]
            moved = [t for t, e in data["threads"].items() if e == env_id]
            for thread_id in moved:
                del data["threads"][thread_id]
            self._write(data)
        return moved

    def assign(self, thread_id: str, env_id: str) -> None:
        with _LOCK:
            data = self._read()
            if env_id not in data["environments"]:
                raise KeyError(env_id)
            if env_id == DEFAULT_ID:
                data["threads"].pop(thread_id, None)
            else:
                data["threads"][thread_id] = env_id
            self._write(data)

    def add_folders(self, thread_id: str, folders: list[str], name: str) -> None:
        """Gives `thread_id`'s environment these folders, as allowed from the conversation. One on
        Default gets an environment of its own, named `name`, so a folder allowed in one
        conversation isn't opened to every other."""
        with _LOCK:
            data = self._read()
            env_id = data["threads"].get(thread_id, DEFAULT_ID)
            if env_id == DEFAULT_ID:
                env_id = f"env_{uuid.uuid4().hex[:12]}"
                own = {**data["environments"][DEFAULT_ID], "name": self._free_name(data, name)}
                data["environments"][env_id] = own
                data["threads"][thread_id] = env_id
            data["environments"][env_id]["folders"] = list(dict.fromkeys(folders))
            self._write(data)

    def with_folders(self, folders: list[str], name: str) -> str:
        """An environment that differs from a blank one only in these folders: one already there,
        else a new one named `name`."""
        with _LOCK:
            data = self._read()
            wanted = _settings_key({**blank(""), "folders": folders})
            for env_id, env in data["environments"].items():
                if env_id != DEFAULT_ID and _settings_key(env) == wanted:
                    return str(env_id)
            env_id = f"env_{uuid.uuid4().hex[:12]}"
            env = {**blank(self._free_name(data, name)), "folders": folders}
            data["environments"][env_id] = env
            self._write(data)
        return env_id

    def forget_thread(self, thread_id: str) -> None:
        with _LOCK:
            data = self._read()
            if data["threads"].pop(thread_id, None) is not None:
                self._write(data)

    def detach_secret(self, name: str) -> None:
        with _LOCK:
            data = self._read()
            for env in data["environments"].values():
                env["secrets"] = [s for s in env["secrets"] if s != name]
            self._write(data)

    def migrate(self, configs: dict[str, dict[str, Any]], titles: dict[str, str]) -> None:
        """The first start with environments: each conversation's own settings from before become
        an environment named after it, conversations with the same settings share one, and those
        with none stay on Default. Written once; later starts keep what is there."""
        with _LOCK:
            if self.exists():
                return
            data: dict[str, Any] = {"environments": {DEFAULT_ID: blank("Default")}, "threads": {}}
            by_settings: dict[str, str] = {}
            for thread_id in sorted(configs):
                env = {**blank(""), **configs[thread_id]}
                key = _settings_key(env)
                if key == _settings_key(blank("")):
                    continue
                if key not in by_settings:
                    env_id = f"env_{uuid.uuid4().hex[:12]}"
                    env["name"] = self._free_name(data, titles.get(thread_id) or "Conversation")
                    data["environments"][env_id] = env
                    by_settings[key] = env_id
                data["threads"][thread_id] = by_settings[key]
            self._write(data)

    @staticmethod
    def _free_name(data: dict[str, Any], wanted: str) -> str:
        base = " ".join(wanted.split())[:MAX_NAME].strip() or "Conversation"
        taken = {env["name"].lower() for env in data["environments"].values()}
        name, n = base, 2
        while name.lower() in taken:
            suffix = f" ({n})"
            name, n = f"{base[: MAX_NAME - len(suffix)]}{suffix}", n + 1
        return name
