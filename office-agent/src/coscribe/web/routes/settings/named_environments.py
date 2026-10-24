"""Named environments (#588): listed and edited from the environment picker below the message
box. An edit that changes folders reaches the open conversations using the environment at once,
so it waits until none of them is running a turn."""

from __future__ import annotations

import contextlib
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ....conversation import ChatSessionLG
from ....runtime import DEFAULT_ENVIRONMENT, EnvironmentRefused, Environments, SecretStore
from ....runtime_lg import AuditLog, record_decision
from ....tools import SCHEDULED_THREAD_PREFIX, ScheduledTriggerStore
from ...state import AppState

# What changes how far a conversation reaches: kept in the audit log like an approval.
_AUDITED = ("network", "scripts", "commands", "unisolated")


class _TabSink:
    """Passes what a session sends on to the tab watching it, if any, and keeps the refusals so
    they reach the caller."""

    def __init__(self, session: ChatSessionLG) -> None:
        self._tab = session._live_websocket
        self.errors: list[str] = []

    async def send_json(self, data: Any) -> None:
        if data.get("type") == "error":
            self.errors.append(str(data.get("message", "")))
        elif self._tab is not None:
            with contextlib.suppress(Exception):  # a closed tab catches up when it reconnects
                await self._tab.send_json(data)


def _refused(message: str, status: int = 422) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _open_sessions(state: AppState, env_ids: set[str]) -> list[ChatSessionLG]:
    """The open conversations using one of `env_ids`, scheduled runs aside (they keep the
    folders of their task)."""
    environments = Environments(state.settings.state_dir)
    return [
        session
        for thread_id, session in list(state.sessions.items())
        if environments.environment_of(thread_id) in env_ids
        and not thread_id.startswith(SCHEDULED_THREAD_PREFIX)
    ]


async def _move_folders(state_dir: Path, sessions: list[ChatSessionLG]) -> str | None:
    """Puts each session in its environment's folders and tells its tab; the first refusal,
    if any."""
    environments = Environments(state_dir)
    errors: list[str] = []
    for session in sessions:
        sink = _TabSink(session)
        folders = environments.for_thread(session.thread_id)["folders"]
        if [str(f) for f in session.folders] != folders:
            await session.set_folders(folders, sink)
        else:
            await session.send_state(sink)
        errors.extend(sink.errors)
    return errors[0] if errors else None


def _audit(
    state_dir: Path, env_id: str, before: dict[str, Any] | None, after: dict[str, Any]
) -> None:
    changed = {k: after[k] for k in _AUDITED if before is None or before[k] != after[k]}
    if changed:
        record_decision(
            AuditLog(state_dir),
            thread_id="",
            tool_name="set_environment",
            arguments={"environment": env_id, "name": after["name"], **changed},
            decision="approve",
            reason="human",
        )


def router(state: AppState) -> APIRouter:
    router = APIRouter()
    state_dir = state.settings.state_dir

    async def list_environments() -> dict[str, Any]:
        tasks = Counter(t.environment for t in ScheduledTriggerStore(state_dir).list_all())
        return {
            "environments": [
                {**env, "tasks": tasks[env["id"]]} for env in Environments(state_dir).all()
            ]
        }

    async def create_environment(payload: dict[str, Any]) -> Any:
        try:
            env = Environments(state_dir).create(payload, SecretStore(state_dir).names())
        except EnvironmentRefused as exc:
            return _refused(exc.reason)
        _audit(state_dir, env["id"], None, env)
        return env

    async def update_environment(env_id: str, payload: dict[str, Any]) -> Any:
        environments = Environments(state_dir)
        before = environments.get(env_id)
        if before is None:
            return _refused("No such environment.", 404)
        sessions = _open_sessions(state, {env_id})
        if "folders" in payload and any(s.turn_running for s in sessions):
            return _refused(
                "A conversation using this environment is replying. Save once it has finished.",
                409,
            )
        try:
            env = environments.update(env_id, payload, SecretStore(state_dir).names())
        except EnvironmentRefused as exc:
            return _refused(exc.reason)
        _audit(state_dir, env_id, before, env)
        problem = await _move_folders(state_dir, sessions)
        return {**env, "warning": problem} if problem else env

    async def delete_environment(env_id: str) -> Any:
        environments = Environments(state_dir)
        sessions = _open_sessions(state, {env_id})
        if any(s.turn_running for s in sessions):
            return _refused(
                "A conversation using this environment is replying. Delete it once it has "
                "finished.",
                409,
            )
        try:
            moved = environments.delete(env_id)
        except KeyError:
            return _refused("No such environment.", 404)
        except EnvironmentRefused as exc:
            return _refused(exc.reason)
        await _move_folders(state_dir, sessions)
        store = ScheduledTriggerStore(state_dir)
        for trigger in store.list_all():
            if trigger.environment == env_id:
                trigger.environment = DEFAULT_ENVIRONMENT
                store.save(trigger)
        return {"moved": len(moved)}

    # Registered by reference rather than decorated, so the dead-code check sees them used.
    router.add_api_route("/api/environments", list_environments, methods=["GET"])
    router.add_api_route("/api/environments", create_environment, methods=["POST"])
    router.add_api_route("/api/environments/{env_id}", update_environment, methods=["PUT"])
    router.add_api_route("/api/environments/{env_id}", delete_environment, methods=["DELETE"])
    return router
