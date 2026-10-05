"""How coscribe starts Codex: its config, its environment, and one shared
app-server process that starts on first use and stops when idle.

Codex reads its own config.toml from CODEX_HOME, which lives under
state_dir so a user's own `~/.codex` (config, login, AGENTS.md) never
applies here. Features that duplicate something coscribe already does
(its browser, connectors, skills, sub-agents, memory) are switched off.
Codex speaks only the Responses API, so a coscribe model is usable here
only through a provider that serves `/responses`.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

from .. import __version__
from ..runtime.proxy import configured_proxy
from ..tools.script_env import venv_python
from .client import AppServer
from .install import CodexUnavailable, codex_path_dir

logger = logging.getLogger(__name__)

IDLE_SECONDS = 600.0

# `codex features list` (0.160.0): stable features on by default that
# coscribe covers itself or doesn't want, plus self-update so the pinned
# version stays the one that runs.
DISABLED_FEATURES = (
    "apps",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "code_mode_host",
    "goals",
    "image_generation",
    "in_app_browser",
    "in_app_chat",
    "in_app_dictation",
    "in_app_local_automation",
    "in_app_updates",
    "multi_agent",
    "plugin_sharing",
    "plugins",
    "realtime_conversation",
    "remote_plugin",
    "skill_mcp_dependency_install",
    "tool_suggest",
    "workspace_dependencies",
    "worktrees",
)

_OPENAI_BASE_URL = "https://api.openai.com/v1"


@dataclass(frozen=True)
class CodexModel:
    provider_id: str
    model: str


def _provider_id(name: str) -> str:
    return "coscribe_" + re.sub(r"[^A-Za-z0-9_]", "_", name)


_KEY_ENV_PREFIX = "COSCRIBE_CODEX_KEY_"


def _key_env(provider_id: str) -> str:
    return _KEY_ENV_PREFIX + provider_id.removeprefix("coscribe_").upper()


def codex_model(model: str, custom_providers: dict[str, dict[str, str]]) -> CodexModel:
    """The Codex provider and model for a coscribe "provider:model" string.
    Raises CodexUnavailable for providers Codex can't talk to."""
    provider, _, name = model.partition(":")
    if not name:
        raise CodexUnavailable(f'{model!r} isn\'t a "provider:model" string.')
    if provider in custom_providers or provider == "openai":
        return CodexModel(_provider_id(provider), name)
    raise CodexUnavailable(
        f"The code module can't use {provider} models: Codex only speaks OpenAI's "
        "Responses API. Pick an OpenAI model, or a provider that serves /responses "
        "(DeepSeek does)."
    )


def _toml_str(value: str) -> str:
    # A JSON string is a valid TOML basic string.
    return json.dumps(value, ensure_ascii=False)


def config_toml(custom_providers: dict[str, dict[str, str]], openai_key: bool) -> str:
    lines = [
        "[analytics]",
        "enabled = false",
        "",
        # Codex hands its whole environment to the commands it runs; the
        # keys this module adds for Codex itself stay with Codex.
        "[shell_environment_policy]",
        f"exclude = [{_toml_str(_KEY_ENV_PREFIX + '*')}]",
        "",
        "[features]",
    ]
    lines += [f"{feature} = false" for feature in DISABLED_FEATURES]
    providers = {name: config["base_url"] for name, config in sorted(custom_providers.items())}
    if openai_key:
        providers["openai"] = _OPENAI_BASE_URL
    for name, base_url in providers.items():
        provider_id = _provider_id(name)
        key_env = "OPENAI_API_KEY" if name == "openai" else _key_env(provider_id)
        lines += [
            "",
            f"[model_providers.{provider_id}]",
            f"name = {_toml_str(name)}",
            f"base_url = {_toml_str(base_url.rstrip('/'))}",
            f"env_key = {_toml_str(key_env)}",
            'wire_api = "responses"',
        ]
    return "\n".join(lines) + "\n"


def _prepend_path(env: dict[str, str], dirs: list[Path]) -> None:
    # Windows keys PATH case-insensitively but a dict doesn't; keep the one
    # spelling child processes will actually read.
    keys = [key for key in env if key.upper() == "PATH"]
    path_key = "Path" if "Path" in keys else (keys[-1] if keys else "PATH")
    for key in keys:
        if key != path_key:
            env.pop(key)
    wanted = [str(d) for d in dirs if d.is_dir()]
    rest = [p for p in env.get(path_key, "").split(os.pathsep) if p and p not in wanted]
    env[path_key] = os.pathsep.join([*wanted, *rest])


def codex_home(state_dir: Path) -> Path:
    return Path(state_dir) / "codex" / "home"


@dataclass(frozen=True)
class LaunchSpec:
    argv: tuple[str, ...]
    env: dict[str, str]
    config: str
    home: Path
    log_path: Path

    def fingerprint(self) -> str:
        keys = sorted((k, v) for k, v in self.env.items() if k.startswith(_KEY_ENV_PREFIX))
        payload = json.dumps([self.argv, self.config, keys, str(self.home)])
        return hashlib.sha256(payload.encode()).hexdigest()


def launch_spec(
    executable: Path,
    state_dir: Path,
    custom_providers: dict[str, dict[str, str]],
    script_env_bin: Path | None,
) -> LaunchSpec:
    env = dict(os.environ)
    openai_key = bool(env.get("OPENAI_API_KEY"))
    for name, config in custom_providers.items():
        env[_key_env(_provider_id(name))] = config["api_key"]
    home = codex_home(state_dir)
    env["CODEX_HOME"] = str(home)
    # Codex's commands get coscribe's script env first, so `python` is the
    # one with openpyxl/pandas/python-docx and what Codex installs lands in
    # the Environment tab -- an office PC often has no Python on PATH.
    dirs = [codex_path_dir(state_dir)]
    if script_env_bin is not None:
        dirs.insert(0, script_env_bin)
    _prepend_path(env, dirs)
    proxy = configured_proxy()
    if proxy and not any(k.upper() == "HTTPS_PROXY" for k in env):
        env["HTTPS_PROXY"] = proxy
    return LaunchSpec(
        argv=(str(executable), "app-server"),
        env=env,
        config=config_toml(custom_providers, openai_key),
        home=home,
        log_path=Path(state_dir) / "codex" / "app-server.log",
    )


class CodexHost:
    """The one app-server all code-module threads share. `use()` starts it
    when needed and holds it; once nothing holds it for `idle_seconds` it
    stops. A changed spec (a provider or key added) restarts it, but only
    while nothing is using it."""

    def __init__(self, spec: Callable[[], LaunchSpec], idle_seconds: float = IDLE_SECONDS) -> None:
        self._spec = spec
        self._idle_seconds = idle_seconds
        self._server: AppServer | None = None
        self._fingerprint = ""
        self._users = 0
        self._idle: asyncio.TimerHandle | None = None
        self._lock = asyncio.Lock()
        self._stopping: set[asyncio.Task[None]] = set()

    @property
    def server(self) -> AppServer | None:
        return self._server

    async def _ensure(self) -> AppServer:
        spec = self._spec()
        fingerprint = spec.fingerprint()
        server = self._server
        if server is not None and server.running:
            if fingerprint == self._fingerprint or self._users > 0:
                return server
        if server is not None:
            await server.close()
        spec.home.mkdir(parents=True, exist_ok=True)
        (spec.home / "config.toml").write_text(spec.config, encoding="utf-8")
        server = AppServer(spec.argv, spec.env, spec.log_path)
        try:
            await server.start("coscribe", __version__)
        except BaseException:
            await server.close()
            raise
        self._server = server
        self._fingerprint = fingerprint
        return server

    @contextlib.asynccontextmanager
    async def use(self) -> AsyncIterator[AppServer]:
        async with self._lock:
            if self._idle is not None:
                self._idle.cancel()
                self._idle = None
            server = await self._ensure()
            self._users += 1
        try:
            yield server
        finally:
            self._users -= 1
            if self._users == 0:
                self._idle = asyncio.get_running_loop().call_later(
                    self._idle_seconds, self._stop_if_idle
                )

    def _stop_if_idle(self) -> None:
        self._idle = None
        if self._users == 0 and self._server is not None:
            task = asyncio.get_running_loop().create_task(self.shutdown())
            self._stopping.add(task)
            task.add_done_callback(self._stopping.discard)

    async def shutdown(self, force: bool = False) -> None:
        """Stop Codex. Without `force`, only when nothing is using it."""
        async with self._lock:
            if self._users > 0 and not force:
                return
            if self._idle is not None:
                self._idle.cancel()
                self._idle = None
            server, self._server = self._server, None
            if server is not None:
                await server.close()


def script_env_bin(state_dir: Path) -> Path | None:
    venv = Path(state_dir) / "script-env"
    python = venv_python(venv)
    return python.parent if python.is_file() else None
