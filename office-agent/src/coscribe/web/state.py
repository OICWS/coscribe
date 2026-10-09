"""What the route modules share: the objects and helpers `create_app_lg` builds
once and every router is built from (`routes/<area>.py`: `router(state)`)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from ..conversation.session import ChatSessionLG
from ..runtime import LLMClient
from ..tools import SkillInfo


@dataclass
class AppState:
    settings: Settings
    context_window_client: LLMClient
    get_session: Callable[..., ChatSessionLG]
    session_extra_tools: Callable[[], list[Any]]
    skills_by_name: Callable[[], dict[str, SkillInfo]]
    providers_info: Callable[..., dict[str, Any]]
