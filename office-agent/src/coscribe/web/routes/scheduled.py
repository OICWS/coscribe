"""Scheduled routes."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import (
    APIRouter,
    Body,
)
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ...coordinator import build_coordinator_agent
from ...runtime.types import get_tool_metadata
from ...runtime_lg import (
    continue_workflow_run,
    start_investigation,
    stop_run,
)
from ...tools.scheduled_tasks import (
    ScheduledTriggerStore,
    compute_next_run_at,
    create_trigger,
    patch_trigger,
    record_draft,
    update_trigger,
)
from ...workflows.permissions import summarize
from ...workflows.spec import BranchStep, LoopStep, parse_workflow, walk, workflow_error
from ..schemas import (
    InvestigateRequest,
    PermissionsRequest,
    RunNowRequest,
    ScheduledTaskCreate,
    TaskNotesUpdate,
    WorkflowAnswer,
    WorkflowCheck,
    WorkflowRetry,
)
from ..state import AppState


class _NoSocket:
    """Stands in for a websocket where nobody is listening."""

    async def send_json(self, data: dict[str, Any]) -> None:
        return None


def router(state: AppState) -> APIRouter:
    router = APIRouter()
    settings = state.settings
    _get_session_async = state.get_session_async
    _session_extra_tools = state.session_extra_tools
    _delete_run_threads = state.delete_run_threads
    _execute_and_announce = state.execute_and_announce
    _track_background = state.track_background
    _announce_run = state.announce_run
    _write_workspace_sidecar = state.write_workspace_sidecar


    @router.post("/api/workflows/validate")
    async def validate_workflow(body: WorkflowCheck) -> JSONResponse:
        try:
            workflow = parse_workflow(body.workflow)
        except ValidationError as exc:
            return JSONResponse({"error": workflow_error(exc, body.workflow)}, status_code=400)
        return JSONResponse({"workflow": workflow.model_dump(mode="json")})

    def _tool_risks() -> dict[str, str]:
        risks: dict[str, str] = {}
        for connector_tool in _session_extra_tools():
            metadata = get_tool_metadata(connector_tool)
            if (metadata.category or "").startswith("mcp:"):
                risks[connector_tool.name] = metadata.risk_category
        for tool in build_coordinator_agent(settings, thread_id="__tools_probe__").tools:
            risks[tool.__name__] = get_tool_metadata(tool).risk_category
        return risks

    @router.post("/api/workflows/permissions")
    async def workflow_permissions(body: PermissionsRequest) -> JSONResponse:
        """What a workflow will touch -- shown where it's reviewed -- and,
        given an earlier version, what this one adds to it."""
        try:
            workflow = parse_workflow(body.workflow)
            earlier = parse_workflow(body.previous) if body.previous is not None else None
        except ValidationError as exc:
            return JSONResponse({"error": workflow_error(exc, body.workflow)}, status_code=400)
        trigger = (
            ScheduledTriggerStore(settings.state_dir).load(body.trigger_id)
            if body.trigger_id
            else None
        )
        granted = trigger.permissions if trigger is not None else None
        if earlier is None and body.against_saved and trigger is not None and trigger.workflow:
            earlier = parse_workflow(trigger.workflow)
        risks = _tool_risks()
        summary = summarize(workflow, risks, granted)
        added: dict[str, Any] | None = None
        if earlier is not None:
            before = summarize(earlier, risks, granted)
            added = {
                "folders": [f for f in summary["folders"] if f not in before["folders"]],
                "sites": [x for x in summary["sites"] if x not in before["sites"]],
                "scripts": [t for t in summary["scripts"] if t not in before["scripts"]],
                "notable": [
                    n
                    for n in summary["notable"]
                    if n["tool"] not in {b["tool"] for b in before["notable"]}
                ],
            }
        return JSONResponse({"permissions": summary, "added": added})

    # -- /api/scheduled-tasks -- the Settings > Scheduled Tasks panel's
    # create-without-a-conversation entry point; direct ScheduledTriggerStore
    # reads/writes, no session/graph involved. POST reuses create_trigger
    # (tools/scheduled_tasks.py) -- the exact same validation
    # create_scheduled_task (the model tool) uses, so the two creation
    # paths can't silently drift apart.

    @router.get("/api/scheduled-tasks")
    async def list_scheduled_tasks_endpoint() -> list[dict[str, Any]]:
        return [t.to_dict() for t in ScheduledTriggerStore(settings.state_dir).list_all()]

    @router.post("/api/scheduled-tasks")
    async def create_scheduled_task_endpoint(payload: ScheduledTaskCreate) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        try:
            trigger = create_trigger(
                store,
                name=payload.name,
                kind=payload.kind,
                at=payload.at,
                prompt=payload.prompt,
                weekday=payload.weekday,
                day_of_month=payload.day_of_month,
                start_date=payload.start_date,
                model=payload.model,
                approval_mode=payload.approval_mode,
                notes_enabled=payload.notes_enabled,
                workflow=payload.workflow,
                workspace=payload.workspace,
            )
        except (ValueError, KeyError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if payload.from_thread:
            trigger.source_thread = payload.from_thread
            store.save(trigger)
        if payload.from_draft:
            record_draft(store, trigger, payload.from_draft)
        return JSONResponse(trigger.to_dict())

    @router.put("/api/scheduled-tasks/{trigger_id}")
    async def update_scheduled_task_endpoint(
        trigger_id: str, payload: ScheduledTaskCreate
    ) -> JSONResponse:
        try:
            trigger = update_trigger(
                ScheduledTriggerStore(settings.state_dir),
                trigger_id,
                name=payload.name,
                kind=payload.kind,
                at=payload.at,
                prompt=payload.prompt,
                weekday=payload.weekday,
                day_of_month=payload.day_of_month,
                start_date=payload.start_date,
                model=payload.model,
                approval_mode=payload.approval_mode,
                notes_enabled=payload.notes_enabled,
                workflow=payload.workflow,
                workspace=payload.workspace,
            )
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(trigger.to_dict())

    @router.patch("/api/scheduled-tasks/{trigger_id}")
    async def patch_scheduled_task_endpoint(
        trigger_id: str, changes: Annotated[dict[str, Any], Body()]
    ) -> JSONResponse:
        try:
            trigger = patch_trigger(ScheduledTriggerStore(settings.state_dir), trigger_id, changes)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(trigger.to_dict())

    @router.post("/api/scheduled-tasks/{trigger_id}/run")
    async def run_scheduled_task_now_endpoint(
        trigger_id: str, payload: RunNowRequest | None = None
    ) -> JSONResponse:
        # Returns as soon as the run is recorded, not when it finishes: the
        # browser opens the run's own conversation right away and watches
        # it stream there (see runtime_lg/scheduled_tasks.py's
        # _RelaySocket). How it ended lands on the run record.
        store = ScheduledTriggerStore(settings.state_dir)
        try:
            trigger, run, pruned = store.start_run(
                trigger_id, "manual", payload.inputs if payload else None
            )
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        await _delete_run_threads(pruned)
        _track_background(asyncio.create_task(_execute_and_announce(trigger_id, run.run_id)))
        return JSONResponse({"task": trigger.to_dict(), "run": run.to_dict()})

    def _reopen_workflow_run(trigger_id: str, run_id: str) -> tuple[Any, Any] | JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        trigger = store.load(trigger_id)
        if trigger is None or trigger.workflow is None:
            return JSONResponse({"error": f"No workflow task {trigger_id!r}"}, status_code=404)
        existing = trigger.find_run(run_id)
        if existing is None:
            return JSONResponse({"error": f"No run {run_id!r}"}, status_code=404)
        try:
            run = store.reopen_run(trigger_id, run_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return existing, run

    def _continue_in_background(trigger_id: str, run_id: str, action: Any) -> None:
        async def go() -> None:
            run = await continue_workflow_run(
                settings.state_dir, trigger_id, run_id, _get_session_async, action
            )
            await _announce_run(trigger_id, run)

        _track_background(asyncio.create_task(go()))

    @router.post("/api/scheduled-tasks/{trigger_id}/runs/{run_id}/stop")
    async def stop_workflow_run(trigger_id: str, run_id: str) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        trigger = store.load(trigger_id)
        run = trigger.find_run(run_id) if trigger is not None else None
        if trigger is None or run is None:
            return JSONResponse({"error": f"No run {run_id!r}"}, status_code=404)
        if run.status != "running" or not stop_run(run_id, run.thread_id):
            return JSONResponse({"error": "This run isn't going"}, status_code=409)
        return JSONResponse({"ok": True})

    @router.post("/api/scheduled-tasks/{trigger_id}/runs/{run_id}/investigate")
    async def investigate_workflow_run(
        trigger_id: str, run_id: str, payload: InvestigateRequest | None = None
    ) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        trigger = store.load(trigger_id)
        run = trigger.find_run(run_id) if trigger is not None else None
        if trigger is None or trigger.workflow is None or run is None:
            return JSONResponse({"error": f"No workflow run {run_id!r}"}, status_code=404)
        if run.status != "failed":
            return JSONResponse({"error": "Only a failed run can be looked into"}, status_code=409)
        thread_id = uuid.uuid4().hex[:8]
        _track_background(
            asyncio.create_task(
                start_investigation(
                    settings.state_dir,
                    trigger_id,
                    run_id,
                    thread_id,
                    payload.model if payload else None,
                    _get_session_async,
                    attended=True,
                )
            )
        )
        return JSONResponse({"thread_id": thread_id})

    def _waiting_permission(run: Any) -> dict[str, Any] | None:
        """What the run's waiting step asks to be allowed, if it is asking."""
        for record in reversed(run.steps):
            if record.get("status") == "waiting":
                asked = record.get("permission")
                return asked if isinstance(asked, dict) else None
        return None

    async def _allow_for_run(
        store: ScheduledTriggerStore, trigger_id: str, thread_id: str, permission: dict[str, Any]
    ) -> str | None:
        """Record the answer on the task, so later runs don't ask again, and
        open the folder to the run's session. A problem, or None."""
        target = str(permission.get("target") or "")
        if permission.get("kind") == "folder":
            session = await _get_session_async(thread_id)
            if not await session.add_folder(target, cast(Any, _NoSocket())):
                return f"Couldn't add {target} -- is it still there?"
            _write_workspace_sidecar(thread_id, [str(f) for f in session.folders])
            store.grant(trigger_id, "folders", target)
        elif permission.get("kind") == "site":
            store.grant(trigger_id, "sites", target)
        return None

    @router.post("/api/scheduled-tasks/{trigger_id}/runs/{run_id}/answer")
    async def answer_workflow_approval(
        trigger_id: str, run_id: str, payload: WorkflowAnswer
    ) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        trigger = store.load(trigger_id)
        before = trigger.find_run(run_id) if trigger is not None else None
        if before is not None and before.status != "needs_approval":
            return JSONResponse({"error": "This run isn't waiting for approval"}, status_code=409)
        permission = _waiting_permission(before) if before is not None else None
        if payload.approved and permission is not None and before is not None:
            problem = await _allow_for_run(store, trigger_id, before.thread_id, permission)
            if problem:
                return JSONResponse({"error": problem}, status_code=409)
        reopened = _reopen_workflow_run(trigger_id, run_id)
        if isinstance(reopened, JSONResponse):
            return reopened
        _continue_in_background(
            trigger_id, run_id, lambda wf: wf.answer(payload.approved, payload.note)
        )
        return JSONResponse({"run": reopened[1].to_dict()})

    @router.post("/api/scheduled-tasks/{trigger_id}/runs/{run_id}/retry")
    async def retry_workflow_run(
        trigger_id: str, run_id: str, payload: WorkflowRetry
    ) -> JSONResponse:
        reopened = _reopen_workflow_run(trigger_id, run_id)
        if isinstance(reopened, JSONResponse):
            return reopened
        before, run = reopened
        trigger = ScheduledTriggerStore(settings.state_dir).load(trigger_id)
        workflow = parse_workflow(trigger.workflow) if trigger and trigger.workflow else None
        # A loop or branch around the failed step is recorded as failed
        # too; retrying means the step itself, on the pass that failed.
        blocks = (
            {p.step.id for p in walk(workflow.steps) if isinstance(p.step, (BranchStep, LoopStep))}
            if workflow
            else set()
        )
        step_id = payload.step_id or next(
            (
                s["step_id"]
                for s in reversed(before.steps)
                if s.get("status") == "failed" and s["step_id"] not in blocks
            ),
            None,
        )
        if step_id is None:
            _continue_in_background(trigger_id, run_id, lambda wf: wf.resume())
        else:
            _continue_in_background(trigger_id, run_id, lambda wf: wf.retry_from(step_id))
        return JSONResponse({"run": run.to_dict()})

    @router.get("/api/scheduled-tasks/{trigger_id}/notes")
    async def get_scheduled_task_notes(trigger_id: str) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        if store.load(trigger_id) is None:
            return JSONResponse({"error": f"No scheduled task {trigger_id!r}"}, status_code=404)
        return JSONResponse({"notes": store.read_notes(trigger_id)})

    @router.put("/api/scheduled-tasks/{trigger_id}/notes")
    async def put_scheduled_task_notes(trigger_id: str, payload: TaskNotesUpdate) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        if store.load(trigger_id) is None:
            return JSONResponse({"error": f"No scheduled task {trigger_id!r}"}, status_code=404)
        try:
            store.write_notes(trigger_id, payload.notes.strip())
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"notes": store.read_notes(trigger_id)})

    @router.post("/api/scheduled-tasks/{trigger_id}/pause")
    async def pause_scheduled_task_endpoint(trigger_id: str) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        trigger = store.load(trigger_id)
        if trigger is None:
            return JSONResponse({"error": f"No scheduled task {trigger_id!r}"}, status_code=404)
        trigger.enabled = False
        store.save(trigger)
        return JSONResponse(trigger.to_dict())

    @router.post("/api/scheduled-tasks/{trigger_id}/resume")
    async def resume_scheduled_task_endpoint(trigger_id: str) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        trigger = store.load(trigger_id)
        if trigger is None:
            return JSONResponse({"error": f"No scheduled task {trigger_id!r}"}, status_code=404)
        trigger.enabled = True
        trigger.next_run_at = compute_next_run_at(trigger.schedule, datetime.now())
        store.save(trigger)
        return JSONResponse(trigger.to_dict())

    @router.delete("/api/scheduled-tasks/{trigger_id}")
    async def delete_scheduled_task_endpoint(trigger_id: str) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        trigger = store.load(trigger_id)
        if trigger is None or not store.delete(trigger_id):
            return JSONResponse({"error": f"No scheduled task {trigger_id!r}"}, status_code=404)
        await _delete_run_threads(trigger.runs)
        return JSONResponse({"deleted": trigger_id})

    return router
