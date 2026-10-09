"""The server's routes: which there are, and the one order that matters.

A route dropped or added by mistake while `web/app.py` is split into routers
changes behaviour without any other test noticing, so the table is compared with
a checked-in list (`tests/web_routes.txt`; a change that adds, removes or moves a
route edits it in the same PR, and `UPDATE_ROUTES=1 pytest tests/test_web_routes.py`
rewrites it). The list is sorted: which module registers a route first does not
matter. What does matter, because FastAPI takes the first route that matches, is
that a fixed path comes before a pattern that would also match it (`/ws/browser`
before `/ws/{thread_id}`), and the static-files mount comes last.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from starlette.routing import Mount, WebSocketRoute

from coscribe.config import Settings
from coscribe.web.app import create_app_lg

SNAPSHOT = Path(__file__).with_name("web_routes.txt")


def _routes(tmp_path: Path) -> list[Any]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        default_model="fake:model",
        workspace_root=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        skills_dir=tmp_path / "skills",
        memory_path=tmp_path / "MEMORY.md",
    )
    return list(create_app_lg(settings).routes)


def _describe(route: Any) -> str:
    if isinstance(route, WebSocketRoute):
        return f"WS {route.path}"
    if isinstance(route, Mount):
        return f"MOUNT {route.path}"
    methods = sorted(getattr(route, "methods", None) or [])
    return f"{','.join(methods)} {route.path}"


def _methods(route: Any) -> set[str]:
    return set(getattr(route, "methods", None) or {"WS"})


def test_the_routes_are_the_checked_in_ones(tmp_path: Path) -> None:
    table = sorted(_describe(route) for route in _routes(tmp_path))
    if os.environ.get("UPDATE_ROUTES"):
        SNAPSHOT.write_text("\n".join(table) + "\n", encoding="utf-8")

    assert table == SNAPSHOT.read_text(encoding="utf-8").splitlines()


def test_a_fixed_path_is_registered_before_a_pattern_that_would_match_it(tmp_path: Path) -> None:
    routes = [route for route in _routes(tmp_path) if not isinstance(route, Mount)]
    shadowed = [
        (earlier.path, later.path)
        for index, later in enumerate(routes)
        if "{" not in later.path
        for earlier in routes[:index]
        if "{" in earlier.path
        and _methods(earlier) & _methods(later)
        and earlier.path_regex.match(later.path)
    ]

    assert shadowed == []


def test_the_static_files_mount_is_last(tmp_path: Path) -> None:
    routes = _routes(tmp_path)

    assert [i for i, route in enumerate(routes) if isinstance(route, Mount)] == [len(routes) - 1]
