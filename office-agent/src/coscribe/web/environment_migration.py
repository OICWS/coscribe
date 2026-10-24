"""The first start with named environments (#588): every conversation's own folders, network,
variables, secrets and "run scripts without isolation" become environments. The files they were
kept in are left in place, unread, so nothing is lost if this goes wrong."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Settings
from ..runtime import Environments
from ..runtime_lg import extract_text, strip_mode_note
from ..tools import SCHEDULED_THREAD_PREFIX, ScheduledTriggerStore
from .thread_files import ThreadFiles

_TITLE_LENGTH = 40


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _legacy_settings(settings: Settings) -> dict[str, dict[str, Any]]:
    """Each conversation's settings from before, as environment fields."""
    state_dir, files = settings.state_dir, ThreadFiles(settings)
    configs: dict[str, dict[str, Any]] = {}

    def of(thread_id: str) -> dict[str, Any]:
        return configs.setdefault(thread_id, {})

    for path in state_dir.glob("*.workspace"):
        folders = files.legacy_folders(path.stem)
        if folders:
            of(path.stem)["folders"] = folders
    for path in state_dir.glob("*.env.json"):
        raw = _json(path)
        if isinstance(raw, dict):
            thread = of(path.name.removesuffix(".env.json"))
            if isinstance(raw.get("variables"), dict):
                thread["variables"] = raw["variables"]
            if isinstance(raw.get("secrets"), list):
                thread["secrets"] = raw["secrets"]
    for path in state_dir.glob("*.network.json"):
        raw = _json(path)
        if isinstance(raw, dict) and isinstance(raw.get("mode"), str):
            domains = raw.get("domains") if isinstance(raw.get("domains"), list) else []
            of(path.name.removesuffix(".network.json"))["network"] = {
                "mode": raw["mode"],
                "domains": domains,
            }
    unisolated = _json(state_dir / "unisolated-conversations.json")
    for thread_id in unisolated if isinstance(unisolated, dict) else {}:
        of(str(thread_id))["unisolated"] = True
    return {t: c for t, c in configs.items() if not t.startswith(SCHEDULED_THREAD_PREFIX)}


async def _titles(settings: Settings, checkpointer: Any, thread_ids: list[str]) -> dict[str, str]:
    """A name for each conversation's environment: its title, else its first message."""
    files, titles = ThreadFiles(settings), {}
    for thread_id in thread_ids:
        sidecar = files.title_sidecar_path(thread_id)
        if sidecar.is_file():
            titles[thread_id] = sidecar.read_text(encoding="utf-8").strip()[:_TITLE_LENGTH]
            continue
        tuple_ = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
        messages = tuple_.checkpoint["channel_values"].get("messages", []) if tuple_ else []
        first = next((m for m in messages if getattr(m, "type", None) == "human"), None)
        if first is not None:
            titles[thread_id] = strip_mode_note(extract_text(first.content)).strip()[:_TITLE_LENGTH]
    return titles


def _migrate_scheduled_tasks(settings: Settings) -> None:
    """A task saved with its own folder runs in an environment with that folder, named after the
    task; tasks with the same folder share one."""
    store, environments = (
        ScheduledTriggerStore(settings.state_dir),
        Environments(settings.state_dir),
    )
    for trigger in store.list_all():
        if trigger.legacy_workspace:
            trigger.environment = environments.with_folders(
                [trigger.legacy_workspace], trigger.name
            )
            trigger.legacy_workspace = None
            store.save(trigger)


async def migrate_to_environments(settings: Settings, checkpointer: Any) -> None:
    environments = Environments(settings.state_dir)
    if not environments.exists():
        configs = _legacy_settings(settings)
        environments.migrate(configs, await _titles(settings, checkpointer, sorted(configs)))
    # Not only on the first start: tasks came to environments after conversations did.
    _migrate_scheduled_tasks(settings)
