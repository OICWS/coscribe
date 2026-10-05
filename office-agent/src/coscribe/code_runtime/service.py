"""The code module as the app sees it: one Codex host per state_dir, made
ready on first use.

Getting ready can mean downloading Codex (~111 MB) and creating the script
env (a pip install); both block, so they run in a worker thread rather than
on the event loop every conversation shares.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..config import Settings
from ..runtime.provider_config import load_custom_providers
from ..tools.script_env import ensure_script_env
from .install import codex_executable, ensure_codex
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

    async def prepare(self) -> CodexHost:
        async with self._ready:
            await asyncio.to_thread(ensure_codex, self.state_dir, self.settings.codex_wheel_url)
            await asyncio.to_thread(ensure_script_env, self.state_dir)
        return self.host


_services: dict[Path, CodeService] = {}


def code_service(settings: Settings) -> CodeService:
    key = Path(settings.state_dir).resolve()
    if key not in _services:
        _services[key] = CodeService(settings)
    return _services[key]


async def shutdown_code_services() -> None:
    for service in list(_services.values()):
        await service.host.shutdown(force=True)
    _services.clear()
