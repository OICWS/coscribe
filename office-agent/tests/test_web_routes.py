"""The server's route table, in registration order, against a checked-in list.

FastAPI matches routes in the order they were added (`/ws/browser` has to come
before `/ws/{thread_id}`), so a route dropped or reordered while `web/app.py` is
being split up changes behaviour without any other test noticing. A change that
adds, removes or moves a route edits `tests/web_routes.txt` in the same PR;
`UPDATE_ROUTES=1 pytest tests/test_web_routes.py` rewrites it.
"""

from __future__ import annotations

import os
from pathlib import Path

from starlette.routing import Mount, WebSocketRoute

from coscribe.config import Settings
from coscribe.web.app import create_app_lg

SNAPSHOT = Path(__file__).with_name("web_routes.txt")


def _route_table(tmp_path: Path) -> list[str]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        default_model="fake:model",
        workspace_root=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        skills_dir=tmp_path / "skills",
        memory_path=tmp_path / "MEMORY.md",
    )
    lines = []
    for route in create_app_lg(settings).routes:
        if isinstance(route, WebSocketRoute):
            lines.append(f"WS {route.path}")
        elif isinstance(route, Mount):
            lines.append(f"MOUNT {route.path}")
        else:
            methods = sorted(getattr(route, "methods", None) or [])
            lines.append(f"{','.join(methods)} {route.path}")
    return lines


def test_the_route_table_is_the_checked_in_one(tmp_path: Path) -> None:
    table = _route_table(tmp_path)
    if os.environ.get("UPDATE_ROUTES"):
        SNAPSHOT.write_text("\n".join(table) + "\n", encoding="utf-8")
    expected = SNAPSHOT.read_text(encoding="utf-8").splitlines()

    assert table == expected
