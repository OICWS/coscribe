"""What coscribe may run on this computer, set in Settings › Capabilities (#588): scripts (on
unless turned off) and commands (off unless turned on). A conversation's environment can only
narrow these, and its mode decides whether the person is asked. The agent's tools stay in its
list either way, since that list is cached by the provider; a tool whose capability is off
refuses the call and says where to turn it on."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Literal

from ...runtime import Environments
from .command_sandbox import sandbox_codex

Capability = Literal["scripts", "commands"]

_FILE = "capabilities.json"
# "commands_without_sandbox" is the second confirmation (decision 0022): where the command
# sandbox is not set up, a command runs as the person only once they accepted that too.
_DEFAULTS: dict[str, bool] = {"scripts": True, "commands": False, "commands_without_sandbox": False}
_lock = threading.Lock()


class CapabilityOff(ValueError):
    """A tool was called whose capability is turned off in Settings › Capabilities."""


class Capabilities:
    def __init__(self, state_dir: Path) -> None:
        self._path = Path(state_dir) / _FILE

    def get(self) -> dict[str, bool]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        saved = raw if isinstance(raw, dict) else {}
        return {name: saved.get(name, on) is True for name, on in _DEFAULTS.items()}

    def set(self, name: str, on: bool) -> dict[str, bool]:
        if name not in _DEFAULTS:
            raise ValueError(f"No capability {name!r}")
        with _lock:
            current = self.get()
            current[name] = on
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(current), encoding="utf-8")
        return current


_REFUSALS: dict[Capability, str] = {
    "scripts": (
        "Scripts are turned off on this computer (Settings › Capabilities). Tell the person; "
        "they can turn them on there. Use the built-in tools meanwhile."
    ),
    "commands": (
        "Commands are turned off on this computer (Settings › Capabilities): a command runs "
        "outside coscribe's isolation. Tell the person if the task needs one; they can turn "
        "commands on there. Prefer a Python or Node script, which runs isolated."
    ),
}


_NO_SANDBOX = (
    "Commands are on, but the command sandbox isn't set up on this computer and the person "
    "hasn't allowed commands without it (Settings › Capabilities). Tell them if the task needs "
    "a command. Prefer a Python or Node script, which runs isolated."
)


def require_capability(state_dir: Path, name: Capability, thread_id: str | None = None) -> None:
    """Raises CapabilityOff, which the model reads, when `name` is turned off on this computer or
    in the environment of `thread_id`'s conversation."""
    allowed = Capabilities(state_dir).get()
    if not allowed[name]:
        raise CapabilityOff(_REFUSALS[name])
    if name == "commands" and not allowed["commands_without_sandbox"]:
        if sandbox_codex(state_dir) is None:
            raise CapabilityOff(_NO_SANDBOX)
    env = Environments(state_dir).for_thread(thread_id)
    if not env[name]:
        raise CapabilityOff(
            f"This conversation's environment ({env['name']}) doesn't allow {name}. Tell the "
            "person; they can allow them in the environment's settings, from the environment "
            "picker below the message box. Use the built-in tools meanwhile."
        )
