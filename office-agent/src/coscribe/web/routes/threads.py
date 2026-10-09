"""Threads routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
)
from fastapi.responses import FileResponse, JSONResponse, Response

from ...conversation.activity import OPENABLE_EXTENSIONS, open_in_os
from ...runtime.secret_store import (
    SecretError,
    SecretStore,
    SessionEnvironments,
)
from ...runtime_lg import (
    extract_text,
    strip_mode_note,
)
from ...tools.background_tasks import (
    BackgroundTaskStore,
    forget_finished_background_tasks,
    read_background_log,
    stop_background_task,
)
from ...tools.scheduled_tasks import (
    SCHEDULED_THREAD_PREFIX,
)
from ...tools.subagent_tasks import (
    SubAgentTaskStore,
    forget_finished_subagents,
    get_subagent_transcript,
    stop_subagent_task,
)
from ...tools.tasks import TaskToolkit
from ...workflows.solidify import DraftFailed
from ..schemas import (
    GroupRename,
    OpenFileRequest,
    ThreadMetaPatch,
    ThreadRename,
    WorkflowDraftRequest,
)
from ..state import AppState


def router(state: AppState) -> APIRouter:
    router = APIRouter()
    settings = state.settings
    _read_workspace_sidecar = state.read_workspace_sidecar
    _title_sidecar_path = state.title_sidecar_path
    checkpointer_holder = state.checkpointer_holder
    meta_store = state.meta_store
    sessions = state.sessions
    _delete_thread_data = state.delete_thread_data
    _get_session = state.get_session
    _get_session_async = state.get_session_async


    @router.get("/api/threads")
    async def list_threads() -> list[dict[str, Any]]:
        # Direct port of web/app.py's identical endpoint would glob
        # settings.state_dir for *.json files -- wrong storage entirely
        # here: runtime_lg persists conversation history in the shared
        # AsyncSqliteSaver checkpointer (runtime_lg_checkpoints.sqlite),
        # never as one JSON file per thread, so that glob always came back
        # empty and the session-switcher UI (which calls this) had no
        # history to show, even though every thread's real state was
        # right there in the checkpoint database. Queries the checkpoints
        # table directly instead -- confirmed by inspecting AsyncSqliteSaver
        # (no built-in "list every thread" method exists on the class
        # itself, only per-thread aget_tuple/alist). setup() is idempotent
        # (a no-op once already run) and creates the checkpoints/writes
        # tables if they don't exist yet -- needed here since this query
        # goes around the checkpointer's own API, which normally triggers
        # setup() lazily on first real use; a brand-new app with no
        # conversations yet would otherwise 500 on "no such table".
        checkpointer = checkpointer_holder["checkpointer"]
        await checkpointer.setup()
        cursor = await checkpointer.conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id"
        )
        rows = await cursor.fetchall()
        # A fired Scheduled Task's own dedicated conversation has its own
        # surface (the frontend's Scheduled portal/detail/chat view) --
        # excluded here so it doesn't also show up in the ordinary chat
        # sidebar's session list, which would otherwise happen the moment
        # a trigger's first turn writes a checkpoint under its thread_id.
        rows = [row for row in rows if not row[0].startswith(SCHEDULED_THREAD_PREFIX)]
        waiting = await _threads_waiting_for_input()
        # Real per-thread metadata (Phase 2 of ROADMAP.md), not just bare
        # ids -- aget_tuple(thread_id) fetches each thread's *latest*
        # checkpoint directly off the checkpointer, without needing a
        # compiled Agent graph the way send_history's aget_state does
        # (that needs self.lg_agent; this needs nothing but a thread_id).
        # checkpoint["channel_values"]["messages"] is the exact same
        # channel send_history reads via state.values -- confirmed live,
        # not just by type signature, against a real checkpoint written
        # through AsyncSqliteSaver.aput.
        summaries: list[dict[str, Any]] = []
        for (thread_id,) in rows:
            tuple_ = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
            messages = (
                list(tuple_.checkpoint["channel_values"].get("messages", [])) if tuple_ else []
            )
            preview = ""
            for message in messages:
                if getattr(message, "type", None) == "human":
                    preview = strip_mode_note(extract_text(message.content))
                    break
            workspace_root = next(
                iter(_read_workspace_sidecar(thread_id)), str(settings.workspace_root)
            )
            title_sidecar = _title_sidecar_path(thread_id)
            if title_sidecar.is_file():
                preview = title_sidecar.read_text(encoding="utf-8").strip()
            meta = meta_store.get(thread_id)
            summaries.append(
                {
                    "thread_id": thread_id,
                    "updated_at": tuple_.checkpoint["ts"] if tuple_ else None,
                    "message_count": len(messages),
                    "preview": preview[:200],
                    "workspace_root": workspace_root,
                    "group": meta["group"],
                    "archived": meta["archived"],
                    "status": _thread_status(thread_id, waiting, meta),
                }
            )
        summaries.sort(key=lambda s: s["updated_at"] or "", reverse=True)
        return summaries

    async def _threads_waiting_for_input() -> set[str]:
        """Threads whose latest checkpoint holds an unanswered interrupt: an
        approval, a question or a plan waiting on a person -- also after a
        restart, when no session is in memory."""
        checkpointer = checkpointer_holder["checkpointer"]
        await checkpointer.setup()  # see list_threads' identical comment
        cursor = await checkpointer.conn.execute(
            "SELECT DISTINCT w.thread_id FROM writes w JOIN "
            "(SELECT thread_id, MAX(checkpoint_id) AS cid FROM checkpoints "
            "WHERE checkpoint_ns = '' GROUP BY thread_id) c "
            "ON w.thread_id = c.thread_id AND w.checkpoint_id = c.cid "
            "WHERE w.checkpoint_ns = '' AND w.channel = '__interrupt__'"
        )
        return {row[0] for row in await cursor.fetchall()}

    def _thread_status(thread_id: str, waiting: set[str], meta: dict[str, Any]) -> str:
        """"needs_input" | "working" | "ready" | "idle"."""
        session = sessions.get(thread_id)
        if thread_id in waiting:
            return "needs_input"
        if session is not None and session.turn_running:
            return "working"
        return "ready" if meta["unseen"] else "idle"

    @router.get("/api/threads/status")
    async def threads_status() -> dict[str, Any]:
        """Just each conversation's status, cheap enough to poll."""
        waiting = await _threads_waiting_for_input()
        known = set(sessions) | waiting
        known |= {p.name.removesuffix(".meta.json") for p in settings.state_dir.glob("*.meta.json")}
        return {
            "statuses": {
                thread_id: _thread_status(thread_id, waiting, meta_store.get(thread_id))
                for thread_id in known
                if not thread_id.startswith(SCHEDULED_THREAD_PREFIX)
            }
        }

    @router.get("/api/thread-groups")
    async def thread_groups() -> dict[str, Any]:
        return {"groups": meta_store.groups()}

    @router.post("/api/threads/{thread_id}/meta")
    async def patch_thread_meta(thread_id: str, payload: ThreadMetaPatch) -> JSONResponse:
        fields: dict[str, Any] = {}
        if payload.group is not None:
            try:
                named = payload.group.strip()
                fields["group"] = meta_store.add_group(named) if named else None
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
        if payload.archived is not None:
            fields["archived"] = payload.archived
        meta = meta_store.update(thread_id, **fields)
        return JSONResponse({"thread_id": thread_id, **meta, "groups": meta_store.groups()})

    @router.post("/api/thread-groups/{name}/rename")
    async def rename_thread_group(name: str, payload: GroupRename) -> JSONResponse:
        try:
            renamed = meta_store.rename_group(name, payload.name)
        except KeyError:
            return JSONResponse({"error": f"No group {name!r}"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"name": renamed, "groups": meta_store.groups()})

    @router.delete("/api/thread-groups/{name}")
    async def delete_thread_group(name: str) -> JSONResponse:
        try:
            meta_store.delete_group(name)
        except KeyError:
            return JSONResponse({"error": f"No group {name!r}"}, status_code=404)
        return JSONResponse({"groups": meta_store.groups()})

    @router.delete("/api/threads/{thread_id}")
    async def delete_thread(thread_id: str) -> JSONResponse:
        existed = await _delete_thread_data(thread_id)
        if not existed:
            return JSONResponse({"error": f"No thread {thread_id!r}"}, status_code=404)
        return JSONResponse({"deleted": thread_id})

    @router.post("/api/threads/{thread_id}/rename")
    async def rename_thread(thread_id: str, payload: ThreadRename) -> JSONResponse:
        title = payload.title.strip()
        if not title:
            return JSONResponse({"error": "title cannot be blank"}, status_code=400)
        checkpointer = checkpointer_holder["checkpointer"]
        await checkpointer.setup()  # see list_threads' identical comment
        cursor = await checkpointer.conn.execute(
            "SELECT 1 FROM checkpoints WHERE thread_id = ? LIMIT 1", (thread_id,)
        )
        if await cursor.fetchone() is None:
            return JSONResponse({"error": f"No thread {thread_id!r}"}, status_code=404)
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        _title_sidecar_path(thread_id).write_text(title[:200], encoding="utf-8")
        return JSONResponse({"thread_id": thread_id, "title": title[:200]})

    @router.get("/api/threads/{thread_id}/tasks")
    async def get_tasks(thread_id: str) -> list[dict[str, Any]]:
        return TaskToolkit(thread_id, settings.state_dir).list_tasks()

    @router.get("/api/threads/{thread_id}/activity")
    async def get_thread_activity(thread_id: str) -> dict[str, Any]:
        session = _get_session(thread_id)
        activity = await session.get_activity()
        return {"tasks": TaskToolkit(thread_id, settings.state_dir).list_tasks(), **activity}

    @router.post("/api/threads/{thread_id}/workflow-draft")
    async def draft_thread_workflow(
        thread_id: str, body: WorkflowDraftRequest | None = None
    ) -> JSONResponse:
        session = await _get_session_async(thread_id)
        try:
            draft = await session.draft_workflow((body.name if body else "").strip())
        except DraftFailed as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(draft.to_dict())

    def _thread_file(thread_id: str, path: str) -> Path | None:
        try:
            resolved = _get_session(thread_id).workspace_scope().resolve(path)
        except (PermissionError, OSError, ValueError):
            return None
        return resolved if resolved.is_file() else None

    @router.post("/api/threads/{thread_id}/files/open")
    async def open_thread_file(thread_id: str, body: OpenFileRequest) -> JSONResponse:
        resolved = _thread_file(thread_id, body.path)
        if resolved is None:
            return JSONResponse({"error": f"No file {body.path!r}"}, status_code=404)
        if not body.reveal and resolved.suffix.lower() not in OPENABLE_EXTENSIONS:
            return JSONResponse(
                {"error": f"{resolved.suffix or 'This'} files can't be opened from here"},
                status_code=400,
            )
        try:
            open_in_os(resolved, reveal=body.reveal)
        except FileNotFoundError:
            return JSONResponse(
                {"error": "No app on this machine can open it -- download it instead."},
                status_code=501,
            )
        except OSError as exc:
            return JSONResponse({"error": f"Couldn't open it: {exc}"}, status_code=501)
        return JSONResponse({"opened": body.path})

    @router.get("/api/threads/{thread_id}/files/download")
    async def download_thread_file(thread_id: str, path: str) -> Response:
        resolved = _thread_file(thread_id, path)
        if resolved is None:
            return JSONResponse({"error": f"No file {path!r}"}, status_code=404)
        return FileResponse(resolved, filename=resolved.name)

    @router.get("/api/threads/{thread_id}/context-breakdown")
    async def get_context_breakdown_endpoint(thread_id: str) -> dict[str, Any]:
        # Deliberately reuses _get_session (the same live-session cache
        # every WS-driven call goes through), not a fresh throwaway
        # ChatSessionLG -- the whole point is reporting *this* thread's
        # real, currently-bound tool/skill/MCP set and its actual last
        # usage report, not a generic recomputation from scratch.
        session = _get_session(thread_id)
        return await session.get_context_breakdown()

    # -- Sub Agents panel. Reads are polled; a running session also nudges
    # the tab with "subagents_changed". Stopping is the user's call, so it's
    # a REST action here, not a model tool.

    @router.get("/api/threads/{thread_id}/subagents")
    async def list_subagents_endpoint(thread_id: str) -> list[dict[str, Any]]:
        tasks = SubAgentTaskStore(settings.state_dir).list_for_thread(thread_id)
        return [t.to_dict() for t in tasks]

    @router.get("/api/subagents/{task_id}/transcript")
    async def get_subagent_transcript_endpoint(task_id: str) -> JSONResponse:
        task = SubAgentTaskStore(settings.state_dir).load(task_id)
        if task is None:
            return JSONResponse({"error": f"No sub-agent task {task_id!r}"}, status_code=404)
        transcript = get_subagent_transcript(task_id)
        entries = (transcript or {}).get("entries", [])
        return JSONResponse({"task": task.to_dict(), "entries": entries})

    @router.post("/api/subagents/{task_id}/stop")
    async def stop_subagent_endpoint(task_id: str) -> JSONResponse:
        try:
            return JSONResponse(stop_subagent_task(settings.state_dir, task_id))
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @router.delete("/api/threads/{thread_id}/subagents")
    async def forget_finished_subagents_endpoint(thread_id: str) -> dict[str, int]:
        return {"removed": forget_finished_subagents(settings.state_dir, thread_id)}

    # Background scripts share the panel with sub-agents; the same four
    # actions, over their own store.

    @router.get("/api/threads/{thread_id}/background-tasks")
    async def list_background_tasks_endpoint(thread_id: str) -> list[dict[str, Any]]:
        tasks = BackgroundTaskStore(settings.state_dir).list_for_thread(thread_id)
        return [t.to_dict() for t in tasks]

    @router.get("/api/background-tasks/{task_id}/log")
    async def get_background_task_log_endpoint(task_id: str) -> JSONResponse:
        log = read_background_log(settings.state_dir, task_id)
        if log is None:
            return JSONResponse({"error": f"No background task {task_id!r}"}, status_code=404)
        return JSONResponse(log)

    @router.post("/api/background-tasks/{task_id}/stop")
    async def stop_background_task_endpoint(task_id: str) -> JSONResponse:
        try:
            return JSONResponse(stop_background_task(settings.state_dir, task_id))
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @router.delete("/api/threads/{thread_id}/background-tasks")
    async def forget_finished_background_tasks_endpoint(thread_id: str) -> dict[str, int]:
        return {"removed": forget_finished_background_tasks(settings.state_dir, thread_id)}

    @router.get("/api/threads/{thread_id}/environment")
    async def get_session_environment(thread_id: str) -> dict[str, Any]:
        return SessionEnvironments(settings.state_dir).get(thread_id)

    @router.put("/api/threads/{thread_id}/environment")
    async def put_session_environment(thread_id: str, payload: dict[str, Any]) -> Any:
        try:
            return SessionEnvironments(settings.state_dir).set(
                thread_id,
                payload.get("variables", {}),
                payload.get("secrets", []),
                SecretStore(settings.state_dir).names(),
            )
        except SecretError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    return router
