"""Workflow drafting: the tools through which the chat model drafts, revises and
tests a fixed workflow from what the conversation did, and the same draft for
the "save as workflow" button. It lives apart from ChatSessionLG because drafts
are one job with their own state (they live in the conversation's tool results);
the session keeps the model, the tools and the sockets.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Protocol, cast

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool
from langgraph.prebuilt import InjectedState

from ..config import Settings
from ..runtime import get_tool_metadata, tool_metadata
from ..runtime_lg import extract_text as _extract_text
from ..runtime_lg import serialize_history_for_ws_lg, tool_name
from ..tools import BROWSER_HOST, ScheduledTrigger, ScheduledTriggerStore, page_screenshot
from ..workflows import (
    DraftFailed,
    StepContext,
    StepRecord,
    WorkflowDraft,
    draft_workflow,
    parse_workflow,
    revise_workflow,
    run_test,
    uses_browser,
    walk,
)
from .events import EventSink
from .turn_lock import TurnLock

logger = logging.getLogger(__name__)

_PAGE_CHARS = 6000


def _draft_from(messages: list[Any], draft_id: str) -> dict[str, Any]:
    """The draft `draft_id` as its tool result reported it, or {} -- the
    conversation itself is where drafts live, so one survives a restart."""
    for message in reversed(messages):
        if not isinstance(message, ToolMessage) or message.name not in {
            "draft_workflow",
            "revise_workflow",
        }:
            continue
        try:
            data = json.loads(_extract_text(message.content))
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("draft_id") == draft_id:
            return data if isinstance(data.get("workflow"), dict) else {}
    return {}


async def _page_now(ctx: StepContext) -> str | None:
    snapshot = ctx.tools.get("browser_snapshot")
    if snapshot is None:
        return None
    try:
        text = str(await asyncio.to_thread(snapshot))
    except Exception as exc:  # noqa: BLE001 -- the failure itself is already reported
        return f"(couldn't read the page: {exc})"
    return text if len(text) <= _PAGE_CHARS else f"{text[:_PAGE_CHARS]}…"


class WorkflowDraftingHost(Protocol):
    """What drafting reads and calls on the session it drafts for."""

    thread_id: str
    settings: Settings
    workspace_root: Path
    extra_folders: list[Path]
    model: Any
    lg_agent: Any
    config: dict[str, Any]
    _base_tools: list[Callable[..., Any] | BaseTool]
    _extra_tools: list[Callable[..., Any] | BaseTool]
    _model_string: str
    _turn_lock: TurnLock
    _live_websocket: EventSink | None
    _turn_websocket: Any

    @property
    def environment(self) -> str: ...

    def is_workflow_run(self) -> bool: ...

    def _next_draft_id(self) -> str: ...

    def _resolve_model(self, model_string: str) -> Any: ...


class WorkflowDrafting:
    def __init__(self, host: WorkflowDraftingHost) -> None:
        self._host = host

    def _build_draft_workflow_tool(self) -> BaseTool:
        async def draft_workflow_tool(
            state: Annotated[dict[str, Any], InjectedState], name: str = ""
        ) -> str:
            # The graph's live state, not the checkpoint: mid-turn the
            # checkpoint doesn't hold this turn's tool calls yet.
            history = serialize_history_for_ws_lg(list(state.get("messages", [])))
            try:
                draft = await draft_workflow(
                    self._host.model, history, self.workflow_context(None).tools, name.strip()
                )
            except DraftFailed as exc:
                return json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False)
            return json.dumps(
                {
                    "status": "drafted",
                    "draft_id": self._host._next_draft_id(),
                    **draft.to_dict(),
                    "environment": self._host.environment,
                    "next": "Test it now with test_workflow, unless it only sends or deletes "
                    "things the user hasn't agreed to repeat -- then ask first. Nothing is "
                    "saved: the user reviews it on a card after testing.",
                },
                ensure_ascii=False,
            )

        return StructuredTool.from_function(
            coroutine=draft_workflow_tool,
            name="draft_workflow",
            description=(
                "Draft a fixed workflow from what this conversation did: the tool calls "
                "that worked become fixed steps (values that change become inputs), with "
                "checks, and a model step only where judgment was used. For a task the "
                "user wants to repeat exactly -- a fixed, stable workflow. Nothing is "
                "saved: the user reviews the draft on a card and saves it. Needs this "
                "conversation to have done the task with tools already. Call it after "
                "the work is finished -- a draft made before more changes is already "
                "out of date -- then test it: test_workflow, and on a failure "
                "revise_workflow with its draft_id and what failed, then test again, at "
                "most 3 rounds. Then tell the user whether the last test passed and, if "
                "not, what still fails. Call draft_workflow again only when the user asks "
                "for a new draft. If the task downloads files and the user hasn't said "
                "where those should be kept, ask them first: the draft makes that folder "
                "an input. `name`: a short name for it, or empty to let the draft name "
                "itself."
            ),
        )

    def _build_revise_workflow_tool(self) -> BaseTool:
        async def revise_workflow_tool(
            state: Annotated[dict[str, Any], InjectedState],
            request: str,
            task_id: str = "",
            draft_id: str = "",
        ) -> str:
            messages = list(state.get("messages", []))
            try:
                name, workflow, trigger = self._workflow_to_change(messages, task_id, draft_id)
                history = serialize_history_for_ws_lg(messages)
                tools = self.workflow_context(None).tools
                draft = await revise_workflow(
                    self._host.model, name, workflow, request, history, tools
                )
            except DraftFailed as exc:
                return json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False)
            if trigger is None:
                return json.dumps(
                    {
                        "status": "drafted",
                        "draft_id": self._host._next_draft_id(),
                        **draft.to_dict(),
                        "environment": _draft_from(messages, draft_id).get("environment"),
                        "next": "Test this version with test_workflow; the user reviews "
                        "it on a card after that.",
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "status": "drafted",
                    "draft_id": self._host._next_draft_id(),
                    **draft.to_dict(),
                    "trigger_id": trigger.trigger_id,
                    "environment": trigger.environment,
                    "next": "Test this version with test_workflow; the user reviews the "
                    "changes on a card after that, and the task is unchanged until they "
                    "save them.",
                },
                ensure_ascii=False,
            )

        return StructuredTool.from_function(
            coroutine=revise_workflow_tool,
            name="revise_workflow",
            description=(
                "Change a fixed workflow the way asked -- add, remove or edit steps, inputs "
                "or checks: a saved one (a scheduled task that runs steps; `task_id` from "
                "list_scheduled_tasks) or a draft in this conversation (`draft_id` from "
                "draft_workflow, revise_workflow or test_workflow). Nothing is saved: the "
                "user reviews the new version on a card, and a saved task keeps its "
                "schedule, runs and notes. `request`: the change, complete and specific -- "
                "the user's words, or after a failed test the step, its error and the fix. "
                "For a task that runs written instructions, or to change a task's name, "
                "schedule or approval setting, use edit_scheduled_task."
            ),
        )

    def _build_test_workflow_tool(self) -> BaseTool:
        async def test_workflow_tool(
            state: Annotated[dict[str, Any], InjectedState],
            tool_call_id: Annotated[str, InjectedToolCallId],
            draft_id: str = "",
            task_id: str = "",
            inputs: dict[str, Any] | None = None,
        ) -> str:
            messages = list(state.get("messages", []))
            try:
                _name, data, _trigger = self._workflow_to_change(messages, task_id, draft_id)
                workflow = parse_workflow(data)
            except (DraftFailed, ValueError, TypeError) as exc:
                return json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False)
            ctx = self.workflow_context(None)
            total = sum(1 for _ in walk(workflow.steps))
            finished: set[str] = set()

            async def report(record: StepRecord) -> None:
                if record.status != "running":
                    finished.add(record.step_id)
                await self._send_live(
                    {
                        "type": "workflow_test_progress",
                        "tool_call_id": tool_call_id,
                        "step": record.step_id,
                        "status": record.status,
                        "done": len(finished),
                        "total": total,
                    }
                )

            browser = uses_browser(workflow)
            if browser:
                # The steps' own download waits must not take a file this
                # conversation downloaded before the test.
                BROWSER_HOST.set_active(self._host.thread_id, True)
            result = await run_test(workflow, ctx, inputs, report)
            if browser:
                # For the reviewer, not the model: the page the test ended on.
                result["screenshot"] = await asyncio.to_thread(
                    page_screenshot, self._host.thread_id, Path(self._host.settings.state_dir)
                )
            if result["status"] == "failed" and browser:
                result["page"] = await _page_now(ctx)
            result["next"] = {
                "passed": "Tell the user it passed; they review it on the card.",
                "stopped_at_approval": "It ran up to an approval step, where a test stops; "
                "tell the user the steps before it passed.",
            }.get(
                result["status"],
                "Find the cause from the failed step, its error and the page, fix it with "
                "revise_workflow (draft_id, and what failed and how to fix it), then test "
                "again. After 3 failed tests, stop and tell the user what keeps failing.",
            )
            key = {"draft_id": draft_id} if draft_id else {"task_id": task_id}
            return json.dumps({**key, **result}, ensure_ascii=False, default=str)

        tool = StructuredTool.from_function(
            coroutine=test_workflow_tool,
            name="test_workflow",
            description=(
                "Run a fixed workflow once for real to check it works: every step, in "
                "order, with real effects -- files written, pages clicked, messages sent. "
                "Stops at the first failure, or before an approval step. Reports each "
                "step's status, time, output and error, and for a failed browser workflow "
                "the page at that moment. Nothing is saved to a task's runs. `draft_id`: "
                "a draft from draft_workflow or revise_workflow; or `task_id`: a saved "
                "task, to reproduce a failure. `inputs`: values for the workflow's inputs "
                "-- give realistic ones; an input left out takes its default."
            ),
        )
        tool_metadata(
            cast(Callable[..., Any], tool), risk_category="EXTERNAL", category="workflows"
        )
        return tool

    def _workflow_to_change(
        self, messages: list[Any], task_id: str, draft_id: str
    ) -> tuple[str, dict[str, Any], ScheduledTrigger | None]:
        """(name, workflow, the saved task or None for a draft) for a draft
        in this conversation or a saved task."""
        if draft_id:
            draft = _draft_from(messages, draft_id)
            if not draft:
                raise DraftFailed(f"There's no draft {draft_id!r} in this conversation.")
            return str(draft.get("name") or ""), draft["workflow"], None
        trigger = ScheduledTriggerStore(self._host.settings.state_dir).load(task_id)
        if trigger is None:
            raise DraftFailed(f"There's no scheduled task with id {task_id!r}.")
        if not trigger.workflow:
            raise DraftFailed(
                "That task runs written instructions, not fixed steps; "
                "change it with edit_scheduled_task."
            )
        return trigger.name, trigger.workflow, trigger

    async def _send_live(self, event: dict[str, Any]) -> None:
        socket = self._host._live_websocket or self._host._turn_websocket
        if socket is None:
            return
        try:
            await socket.send_json(event)
        except Exception:  # noqa: BLE001 -- a gone tab only misses progress
            logger.debug("could not send %s", event.get("type"), exc_info=True)

    def workflow_context(self, model: str | None) -> StepContext:
        """What a workflow's steps run with: this thread's own tools and
        workspace scope, and `model` (else this session's) for model steps."""
        # A connector's tools are LangChain tools; the other LangChain tools
        # here (spawn_agent, review_work) need a running agent around them.
        tools: dict[str, Any] = {
            tool_name(t): t
            for t in [*self._host._base_tools, *self._host._extra_tools]
            if not isinstance(t, BaseTool)
            or (get_tool_metadata(cast(Any, t)).category or "").startswith("mcp:")
        }

        def make_model(step_model: str | None) -> Any:
            return self._host._resolve_model(step_model or model or self._host._model_string)

        return StepContext(
            tools=tools,
            workspace_root=Path(self._host.workspace_root),
            state_dir=Path(self._host.settings.state_dir),
            make_model=make_model,
            extra_writable=[*self._host.settings.extra_writable_dirs, *self._host.extra_folders],
        )

    async def draft_workflow(self, name_hint: str = "") -> WorkflowDraft:
        """A workflow draft distilled from this conversation, for the person
        to review before it's saved anywhere."""
        if self._host.is_workflow_run():
            raise DraftFailed("This is already a workflow run.")
        if self._host._turn_lock.locked():
            raise DraftFailed("Wait for the current reply to finish first.")
        state = await self._host.lg_agent.aget_state(self._host.config)
        if state.next:
            raise DraftFailed("Resolve the pending approval first.")
        history = serialize_history_for_ws_lg((state.values or {}).get("messages", []))
        return await draft_workflow(
            self._host.model, history, self.workflow_context(None).tools, name_hint
        )
