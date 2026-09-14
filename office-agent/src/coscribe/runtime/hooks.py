"""Hook execution: run a shell command as a lifecycle-event hook and
interpret its exit status.

See ARCHITECTURE.md's "精简版 Hooks" -- originally PreToolUse/PostToolUse/
SessionStart only, reusing ToolPolicy's own event point for PreToolUse (see
HookToolPolicy in policies.py) rather than inventing a separate mechanism.
ROADMAP.md's Phase 7 item 3 (comparing against openai/codex's own 12-event
hooks crate) added five more, all in web/session.py -- SessionEnd,
UserPromptSubmit, PreCompact, PostCompact, Interrupt -- everywhere with a
clear, already-existing async call site to fire from; SubagentStart/
SubagentStop were left out on purpose (see ROADMAP.md), since
runtime_lg/subagents.py has no access to a session's hooks_config today and
wiring that through is a new mechanism, not "one more call site" the way
every event actually added here is. Only PreToolUse can veto a call (the
model sees the denial reason); every other event here is observational --
a failing hook gets logged, never blocks the action it's reporting on,
same as PostToolUse/SessionStart always worked.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_HOOK_TIMEOUT = 10.0

HOOK_EVENTS: tuple[str, ...] = (
    "PreToolUse",
    "PostToolUse",
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreCompact",
    "PostCompact",
    "Interrupt",
)


def empty_hooks_config() -> dict[str, list[str]]:
    """The "no hooks configured" default -- every event present with an
    empty command list, matching what load_hooks_config itself produces
    for a config file that doesn't mention a given event. Every call site
    that used to spell out {"PreToolUse": [], "PostToolUse": [],
    "SessionStart": []} by hand now goes through this instead, so adding a
    future event only means editing HOOK_EVENTS once, not hunting down
    every duplicated literal."""
    return {event: [] for event in HOOK_EVENTS}


@dataclass
class HookResult:
    allowed: bool
    reason: str | None = None


def run_hook(
    command: str, payload: dict[str, Any], *, timeout: float = DEFAULT_HOOK_TIMEOUT
) -> HookResult:
    """Run `command` via the shell, feeding `payload` as JSON on stdin.

    Exit 0 = allowed; nonzero exit, timeout, or launch failure = not
    allowed, with a reason suitable for surfacing to the model (PreToolUse)
    or logging (PostToolUse/SessionStart).
    """
    try:
        completed = subprocess.run(
            command,
            shell=True,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return HookResult(allowed=False, reason=f"hook timed out after {timeout}s: {command!r}")
    except OSError as exc:
        return HookResult(allowed=False, reason=f"hook failed to start ({command!r}): {exc}")
    if completed.returncode == 0:
        return HookResult(allowed=True)
    reason = completed.stderr.strip() or f"hook {command!r} exited {completed.returncode}"
    return HookResult(allowed=False, reason=reason)


def load_hooks_config(path: str | Path) -> dict[str, list[str]]:
    """Parse {"PreToolUse": [...], "PostToolUse": [...], ...} (see
    HOOK_EVENTS for the full set) -- missing events default to an empty
    list, unknown keys are ignored."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {event: list(raw.get(event, [])) for event in HOOK_EVENTS}
