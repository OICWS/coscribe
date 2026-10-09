"""The code module as the app sees it: one Codex host per state_dir, made
ready on first use.

Getting ready can mean downloading Codex (~111 MB) and creating the script
env (a pip install); both block, so they run in a worker thread rather than
on the event loop every conversation shares.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..config import Settings
from ..runtime.provider_config import load_custom_providers
from ..tools.script_env import ensure_script_env
from ._pins import CODEX_VERSION
from .install import codex_executable, ensure_codex, installed, installed_size, remove_codex
from .launch import CodexHost, LaunchSpec, launch_spec, script_env_bin


def _custom_providers(settings: Settings) -> dict[str, dict[str, str]]:
    path = settings.providers_config_path
    return load_custom_providers(path) if path is not None else {}


class CodeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state_dir = Path(settings.state_dir)
        self.host = CodexHost(self._spec)
        self._ready = asyncio.Lock()
        # (bytes written, bytes in all) while Codex downloads; set from the
        # worker thread, read by status().
        self._progress: tuple[int, int] | None = None

    def _spec(self) -> LaunchSpec:
        # Read fresh, so a provider or key added in Settings reaches Codex
        # the next time it starts.
        return launch_spec(
            codex_executable(self.state_dir),
            self.state_dir,
            _custom_providers(self.settings),
            script_env_bin(self.state_dir),
        )

    def custom_providers(self) -> dict[str, dict[str, str]]:
        return _custom_providers(self.settings)

    def _report(self, done: int, total: int) -> None:
        self._progress = (done, total)

    async def prepare(self) -> CodexHost:
        async with self._ready:
            try:
                await asyncio.to_thread(
                    ensure_codex, self.state_dir, self.settings.codex_wheel_url, self._report
                )
            finally:
                self._progress = None
            await asyncio.to_thread(ensure_script_env, self.state_dir)
        return self.host

    def status(self) -> dict[str, Any]:
        progress = self._progress
        return {
            "version": CODEX_VERSION,
            "installed": installed(self.state_dir),
            "preparing": self._ready.locked(),
            "progress": None if progress is None else progress[0] / max(progress[1], 1),
            "size": installed_size(),
        }

    async def remove(self) -> bool:
        """Delete the downloaded Codex; False while it's working."""
        async with self._ready:
            await self.host.shutdown()
            if self.host.in_use:
                return False
            await asyncio.to_thread(remove_codex, self.state_dir)
        return True


_services: dict[Path, CodeService] = {}


def remove_stale_code_sidecars(state_dir: Path) -> int:
    """Code conversations once kept their Codex thread id in `code-<id>.codex`.
    Those conversations open as ordinary chats now and nothing reads the file;
    returns how many were removed."""
    removed = 0
    for path in state_dir.glob("code-*.codex"):
        if path.is_file():
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def code_service(settings: Settings) -> CodeService:
    key = Path(settings.state_dir).resolve()
    if key not in _services:
        _services[key] = CodeService(settings)
    return _services[key]


async def shutdown_code_services() -> None:
    for service in list(_services.values()):
        await service.host.shutdown(force=True)
    _services.clear()
