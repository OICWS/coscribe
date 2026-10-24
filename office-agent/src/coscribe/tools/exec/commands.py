"""Starting a background command line: in Codex's sandbox where it is set up, else as the user
(decision 0022)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from ...runtime import NetworkGuard
from .capabilities import require_capability
from .command_sandbox import sandboxed_command


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
    require_capability(state, "commands", thread_id)
    online = NetworkGuard(state, thread_id).scripts_online()
    sandboxed = sandboxed_command(
        state, command, root, folders["writable"], folders["readable"], online
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
