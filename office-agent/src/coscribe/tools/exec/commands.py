"""Starting a background command line: in Codex's sandbox where it is set up, else as the user
(decision 0022)."""

from __future__ import annotations

import asyncio
import sys
import threading
from collections import Counter
from pathlib import Path

from ...runtime import NetworkGuard
from .capabilities import CapabilityOff, require_capability
from .command_sandbox import sandboxed_command

# Commands approved without the person because the sandbox was set up, per conversation; one that
# finds the sandbox gone when it starts is refused instead of running as the person.
_SANDBOX_ASSUMED: Counter[str] = Counter()
_lock = threading.Lock()


def expect_sandbox(thread_id: str) -> None:
    """The approval of `thread_id`'s next command counted on the sandbox (decision 0022)."""
    with _lock:
        _SANDBOX_ASSUMED[thread_id] += 1


def _took_sandbox_for_granted(thread_id: str) -> bool:
    with _lock:
        if _SANDBOX_ASSUMED[thread_id] <= 0:
            return False
        _SANDBOX_ASSUMED[thread_id] -= 1
        return True


async def start_command(
    state: Path,
    thread_id: str,
    root: Path,
    folders: dict[str, list[Path]],
    command: str,
    env: dict[str, str],
) -> asyncio.subprocess.Process:
    """`command` running in `root` with its output in stdout, once the person allowed commands;
    `folders` holds the conversation's "writable" and "readable" ones."""
    assumed = _took_sandbox_for_granted(thread_id)
    require_capability(state, "commands", thread_id)
    online = NetworkGuard(state, thread_id).scripts_online()
    sandboxed = sandboxed_command(
        state, command, root, folders["writable"], folders["readable"], online
    )
    if sandboxed is None and assumed:
        raise CapabilityOff(
            "The command sandbox was removed after this command was approved, so it did not "
            "run. Run it again to ask the person."
        )
    if sandboxed is not None:
        argv, sandbox_env = sandboxed
        return await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(root),
            env=env | sandbox_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    return await asyncio.create_subprocess_shell(
        command,
        cwd=str(root),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        # Its own process group, so a kill reaches what the shell started.
        start_new_session=sys.platform != "win32",
    )
