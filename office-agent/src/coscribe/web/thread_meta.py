"""What the sidebar keeps about a conversation beyond its messages: which
group it sits in, whether it's archived, and whether it finished while
nobody was looking. A small JSON file per conversation, and one file for
the group names (so an empty group survives and groups keep their order)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

MAX_GROUP_NAME = 60


class ThreadMetaStore:
    def __init__(self, state_dir: str | Path) -> None:
        self.root = Path(state_dir)

    def _path(self, thread_id: str) -> Path:
        return self.root / f"{thread_id}.meta.json"

    @property
    def _groups_path(self) -> Path:
        return self.root / "thread_groups.json"

    @staticmethod
    def _write(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def get(self, thread_id: str) -> dict[str, Any]:
        try:
            data = json.loads(self._path(thread_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return {
            "group": data.get("group") or None,
            "archived": bool(data.get("archived", False)),
            "unseen": bool(data.get("unseen", False)),
        }

    def update(self, thread_id: str, **fields: Any) -> dict[str, Any]:
        meta = self.get(thread_id)
        meta.update(fields)
        if meta == {"group": None, "archived": False, "unseen": False}:
            self._path(thread_id).unlink(missing_ok=True)
        else:
            self._write(self._path(thread_id), meta)
        return meta

    def delete(self, thread_id: str) -> None:
        self._path(thread_id).unlink(missing_ok=True)

    def mark_finished(self, thread_id: str, watched: bool) -> None:
        """A turn ended: worth reviewing unless someone was looking."""
        if not watched and not self.get(thread_id)["unseen"]:
            self.update(thread_id, unseen=True)

    def mark_seen(self, thread_id: str) -> None:
        if self.get(thread_id)["unseen"]:
            self.update(thread_id, unseen=False)

    def groups(self) -> list[str]:
        try:
            data = json.loads(self._groups_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [g for g in data if isinstance(g, str)] if isinstance(data, list) else []

    def add_group(self, name: str) -> str:
        name = name.strip()[:MAX_GROUP_NAME]
        if not name:
            raise ValueError("A group needs a name.")
        groups = self.groups()
        existing = next((g for g in groups if g.casefold() == name.casefold()), None)
        if existing is not None:
            return existing
        self._write(self._groups_path, [*groups, name])
        return name

    def _members(self, group: str) -> list[str]:
        found = []
        for path in self.root.glob("*.meta.json"):
            thread_id = path.name.removesuffix(".meta.json")
            if self.get(thread_id)["group"] == group:
                found.append(thread_id)
        return found

    def rename_group(self, old: str, new: str) -> str:
        new = new.strip()[:MAX_GROUP_NAME]
        if not new:
            raise ValueError("A group needs a name.")
        groups = self.groups()
        if old not in groups:
            raise KeyError(old)
        clash = next((g for g in groups if g != old and g.casefold() == new.casefold()), None)
        if clash is not None:
            raise ValueError(f"There's already a group called {clash!r}.")
        self._write(self._groups_path, [new if g == old else g for g in groups])
        for thread_id in self._members(old):
            self.update(thread_id, group=new)
        return new

    def delete_group(self, name: str) -> None:
        """Its conversations become ungrouped."""
        groups = self.groups()
        if name not in groups:
            raise KeyError(name)
        self._write(self._groups_path, [g for g in groups if g != name])
        for thread_id in self._members(name):
            self.update(thread_id, group=None)
