"""What the route modules share: the objects and helpers `create_app_lg` builds
once and every router is built from (`routes/<area>.py`: `router(state)`)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings
from ..conversation.session import ChatSessionLG
from ..conversation.thread_meta import ThreadMetaStore
from ..runtime import LLMClient
from ..runtime_lg.mcp import McpServerConnection
from ..runtime_lg.mcp_oauth import LoopbackCallback, McpOAuth
from ..tools import SkillInfo
from ..tools.scheduled_tasks import ScheduledRun
from .background_events import BackgroundEventBus


@dataclass
class AppState:
    settings: Settings
    context_window_client: LLMClient
    meta_store: ThreadMetaStore
    sessions: dict[str, ChatSessionLG]
    checkpointer_holder: dict[str, Any]
    background_events: BackgroundEventBus
    mcp_connections: dict[str, McpServerConnection]
    mcp_oauth: McpOAuth
    app_callback: LoopbackCallback
    sign_in_tasks: set[asyncio.Task[None]]
    get_session: Callable[..., ChatSessionLG]
    get_session_async: Callable[[str], Coroutine[Any, Any, ChatSessionLG]]
    session_extra_tools: Callable[[], list[Any]]
    skills_by_name: Callable[[], dict[str, SkillInfo]]
    providers_info: Callable[..., dict[str, Any]]
    connector_tools: Callable[[str], list[dict[str, Any]]]
    connect_and_register_mcp_server_lg: Callable[..., Coroutine[Any, Any, Any]]
    disconnect_mcp_server_lg: Callable[[str], Coroutine[Any, Any, None]]
    refresh_all_sessions_extra_tools: Callable[[], Coroutine[Any, Any, None]]
    oauth_callback_response: Callable[..., Any]
    read_workspace_sidecar: Callable[[str], list[str]]
    write_workspace_sidecar: Callable[[str, list[str]], None]
    title_sidecar_path: Callable[[str], Path]
    delete_thread_data: Callable[[str], Coroutine[Any, Any, bool]]
    delete_run_threads: Callable[[list[ScheduledRun]], Coroutine[Any, Any, None]]
    execute_and_announce: Callable[[str, str], Coroutine[Any, Any, None]]
    announce_run: Callable[[str, ScheduledRun | None], Coroutine[Any, Any, None]]
    track_background: Callable[[asyncio.Task[Any]], None]
