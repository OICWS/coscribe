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


def _flatten(routes: list[Any]) -> list[Any]:
    """The routes in the order they are tried. A router added with include_router is one entry
    in `app.routes` that holds its routes."""
    flat: list[Any] = []
    for route in routes:
        if hasattr(route, "effective_route_contexts"):
            flat.extend(route.effective_route_contexts())
        else:
            flat.append(route)
    return flat


def _routes(tmp_path: Path) -> list[Any]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        default_model="fake:model",
        workspace_root=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        skills_dir=tmp_path / "skills",
        memory_path=tmp_path / "MEMORY.md",
    )
    return _flatten(list(create_app_lg(settings).routes))


def _path(route: Any) -> str:
    """An included WebSocket route reports an empty `path`; the real one is on the wrapped route."""
    return route.path or getattr(route, "original_route", route).path


def _describe(route: Any) -> str:
    if isinstance(getattr(route, "original_route", route), WebSocketRoute):
        return f"WS {_path(route)}"
    if isinstance(route, Mount):
        return f"MOUNT {_path(route)}"
    methods = sorted(getattr(route, "methods", None) or [])
    return f"{','.join(methods)} {_path(route)}"


def _methods(route: Any) -> set[str]:
    if isinstance(getattr(route, "original_route", route), WebSocketRoute):
        return {"WS"}
    return set(getattr(route, "methods", None) or {"WS"})


def test_the_routes_are_the_checked_in_ones(tmp_path: Path) -> None:
    table = sorted(_describe(route) for route in _routes(tmp_path))
    if os.environ.get("UPDATE_ROUTES"):
        SNAPSHOT.write_text("\n".join(table) + "\n", encoding="utf-8")

    assert table == SNAPSHOT.read_text(encoding="utf-8").splitlines()


def test_a_fixed_path_is_registered_before_a_pattern_that_would_match_it(tmp_path: Path) -> None:
    routes = [route for route in _routes(tmp_path) if not isinstance(route, Mount)]
    shadowed = [
        (_path(earlier), _path(later))
        for index, later in enumerate(routes)
        if "{" not in _path(later)
        for earlier in routes[:index]
        if "{" in _path(earlier)
        and _methods(earlier) & _methods(later)
        and earlier.path_regex.match(_path(later))
    ]

    assert shadowed == []


def test_the_static_files_mount_is_last(tmp_path: Path) -> None:
    routes = _routes(tmp_path)

    assert [i for i, route in enumerate(routes) if isinstance(route, Mount)] == [len(routes) - 1]
