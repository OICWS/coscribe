"""Settings > Code: whether the code module's commands and file changes ask
first. "ask" (the default) leaves them to the permission mode and the
approval cards; "allow" runs them without a card, once a plan-mode check
and the user's hooks and exec policy have had their say.

Kept in the state folder, as connector permissions are, so a stray key can't
break a config file the user edits by hand.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Literal, cast

Policy = Literal["ask", "allow"]
POLICIES: tuple[Policy, ...] = ("ask", "allow")
DEFAULT_POLICY: Policy = "ask"

_LOCK = threading.Lock()


class CodePermissions:
    """{approval tool name: policy} in one JSON file."""

    def __init__(self, state_dir: str | Path, actions: tuple[str, ...]) -> None:
        self.path = Path(state_dir) / "code_permissions.json"
        self.actions = actions

    def load(self) -> dict[str, Policy]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return dict.fromkeys(self.actions, DEFAULT_POLICY)
        stored = raw if isinstance(raw, dict) else {}
        return {
            action: cast(Policy, stored[action])
            if stored.get(action) in POLICIES
            else DEFAULT_POLICY
            for action in self.actions
        }

    def policy(self, action: str) -> Policy:
        return self.load().get(action, DEFAULT_POLICY)

    def update(self, policies: dict[str, str]) -> dict[str, Policy]:
        unknown = [name for name in policies if name not in self.actions]
        bad = [f"{name}: {policy!r}" for name, policy in policies.items() if policy not in POLICIES]
        if unknown or bad:
            problems = ", ".join([*unknown, *bad])
            raise ValueError(
                f"Expected {'/'.join(self.actions)} set to {'/'.join(POLICIES)}: {problems}"
            )
        with _LOCK:
            data = self.load()
            data.update({name: cast(Policy, policy) for name, policy in policies.items()})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return data
