"""A thread's files under the state dir: its title and workspace sidecars, and
which folders a new session opens in. Reads settings and the state dir, nothing
from FastAPI, so it runs without building the app."""

from __future__ import annotations

import json
from pathlib import Path

from ..config import Settings
from ..runtime import Environments
from ..tools import (
    SCHEDULED_THREAD_PREFIX,
    MessagingStore,
    ScheduledTriggerStore,
    WorkspaceScope,
    parse_run_thread_id,
)
from ..workflows import granted_by, parse_workflow


class ThreadFiles:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def title_sidecar_path(self, thread_id: str) -> Path:
        # Per-thread sidecar-file convention (plain text keyed by
        # thread_id under settings.state_dir) shared with
        # workspace_sidecar_path and the skills sidecar -- holds a
        # user-chosen rename, overriding list_threads' default
        # "preview = first human message" title. Unlike the workspace
        # sidecar, this one is never read to configure a live session --
        # it only affects how a thread is displayed in the nav rail's
        # session list.
        return self._settings.state_dir / f"{thread_id}.title"

    def workspace_sidecar_path(self, thread_id: str) -> Path:
        return self._settings.state_dir / f"{thread_id}.workspace"

    def write_workspace_sidecar(self, thread_id: str, folders: list[str]) -> None:
        """Keeps a scheduled run's folders; any other conversation's are its environment's."""
        if not thread_id.startswith(SCHEDULED_THREAD_PREFIX):
            return
        if not folders:
            self.workspace_sidecar_path(thread_id).unlink(missing_ok=True)
            return
        self._settings.state_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_sidecar_path(thread_id).write_text(json.dumps(folders), encoding="utf-8")

    def folders_of(self, thread_id: str) -> list[str]:
        """The folders a conversation works in, the main one first; none means the default
        workspace. Its environment's, except a scheduled task's conversations, which keep their
        own in a sidecar."""
        if not thread_id.startswith(SCHEDULED_THREAD_PREFIX):
            return list(Environments(self._settings.state_dir).for_thread(thread_id)["folders"])
        return self._read_sidecar(thread_id)

    def _read_sidecar(self, thread_id: str) -> list[str]:
        sidecar = self.workspace_sidecar_path(thread_id)
        if not sidecar.is_file():
            return []
        text = sidecar.read_text(encoding="utf-8").strip()
        # Threads from before multi-folder conversations hold one bare path.
        if not text.startswith("["):
            return [text] if text else []
        try:
            saved = json.loads(text)
        except json.JSONDecodeError:
            return []
        return [str(folder) for folder in saved if isinstance(folder, str) and folder]

    def legacy_folders(self, thread_id: str) -> list[str]:
        """A conversation's own folders from before environments, for the migration."""
        return self._read_sidecar(thread_id)

    def workspace_folders(self, thread_id: str) -> list[Path]:
        """The folders a conversation works in, the first being its root."""
        folders = [Path(f) for f in self.folders_of(thread_id)]
        return folders or [self._settings.workspace_root]

    def workspace_scope(self, thread_id: str) -> WorkspaceScope:
        """What a conversation may read and write, without starting its session."""
        root, *extra = self.workspace_folders(thread_id)
        return self.scope_of_folders(root, extra)

    def scope_of_folders(self, root: Path, extra: list[Path]) -> WorkspaceScope:
        return WorkspaceScope(
            root,
            extra_readable=[*self._settings.extra_readable_dirs, *extra],
            extra_writable=[*self._settings.extra_writable_dirs, *extra],
        )

    def delete(self, thread_id: str) -> None:
        """Every file that belongs to a thread -- otherwise a new thread later
        reusing the same id would inherit an old task list or workspace/skills
        choice."""
        (self._settings.state_dir / f"{thread_id}.tasks.json").unlink(missing_ok=True)
        self.workspace_sidecar_path(thread_id).unlink(missing_ok=True)
        self.title_sidecar_path(thread_id).unlink(missing_ok=True)
        Environments(self._settings.state_dir).forget_thread(thread_id)
        MessagingStore(self._settings.state_dir).delete(thread_id)

    def run_folders(self, thread_id: str) -> list[str]:
        """A scheduled run's folders: its task's environment's, then the existing ones its
        workflow names or the person allowed while a run waited."""
        parsed = parse_run_thread_id(thread_id)
        trigger = (
            ScheduledTriggerStore(self._settings.state_dir).load(parsed[0]) if parsed else None
        )
        if trigger is None:
            return []
        environment = Environments(self._settings.state_dir).get(trigger.environment)
        own = list(environment["folders"]) if environment else []
        extra = list(trigger.permissions.get("folders", []))
        if trigger.workflow is not None:
            extra = [*granted_by(parse_workflow(trigger.workflow))["folders"], *extra]
        extra = [f for f in dict.fromkeys(extra) if Path(f).is_dir() and f not in own]
        if not extra:
            return own
        return [*(own or [str(self._settings.workspace_root)]), *extra]

    def resolve_folders(self, thread_id: str) -> list[Path]:
        """The folders a session opens in. A scheduled run's come from its task the first time
        it is seen; after that its sidecar (changed by a folder allowed during a run) wins."""
        if not thread_id.startswith(SCHEDULED_THREAD_PREFIX):
            return [Path(folder) for folder in self.folders_of(thread_id)]
        if self.workspace_sidecar_path(thread_id).is_file():
            return [Path(folder) for folder in self._read_sidecar(thread_id)]
        run_folders = self.run_folders(thread_id)
        self.write_workspace_sidecar(thread_id, run_folders)
        return [Path(folder) for folder in run_folders]
