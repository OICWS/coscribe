"""A code conversation: every message is a turn of the code module (Codex,
see code_runtime/) in the conversation's folder.

It is a ChatSessionLG in everything around the turn -- the socket, the
permission modes and approval cards, Stop, titles, the sidebar -- and only
the turn itself differs: Codex runs it, and keeps the conversation's
context in its own thread. What the user saw is written into the
conversation's checkpoint afterwards, in the shape a chat turn leaves, so
history, the thread list and the transcript need nothing new.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import WebSocket
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.messages.ai import UsageMetadata

from ..code_runtime.launch import codex_model
from ..code_runtime.service import code_service
from ..code_runtime.thread import ApprovalRequest, CodexEvent, CodexThread, TurnResult
from ..runtime_lg.code_agent import (
    CODE_APPROVAL_RISKS,
    CODE_CHANGE_TOOL,
    CODE_COMMAND_TOOL,
    approval_call,
    command_args,
)
from .session import ChatSessionLG, _usage_event

logger = logging.getLogger(__name__)

# Must match frontend/src/lib/nav.ts's CODE_THREAD_PREFIX.
CODE_THREAD_PREFIX = "code-"

# A node after which the agent graph has nothing left to run: state written
# "as" it is a finished turn, not one waiting for the model.
_TURN_END_NODE = "HumanInTheLoopMiddleware.after_model"
_OUTPUT_CHARS = 4000

INSTRUCTIONS = """\
You are coscribe's code module, in a conversation with a user who doesn't \
write code. Work in the current folder. `python` has openpyxl, python-docx, \
python-pptx, pandas and pdfplumber. The user approves your commands as they \
come (plain reads of the folder don't ask); if one is declined, find \
another way or ask. Every figure or finding about the user's data must come \
from what a command printed, never typed in by hand. Ask before work that \
is hard to undo when the request leaves it open; ask in your reply, which \
ends your turn. Reply in plain words, in \
the user's language: what you did, the files you created or changed, and \
anything left undone."""

PLAN_NOTE = (
    "[Plan mode: read and look around only -- don't change files or run anything "
    "that does. End with the plan, and any questions, in your reply.]\n\n"
)


def is_code_thread(thread_id: str) -> bool:
    return thread_id.startswith(CODE_THREAD_PREFIX)


def codex_thread_path(state_dir: Path, thread_id: str) -> Path:
    return Path(state_dir) / f"{thread_id}.codex"


def _usage(last: dict[str, Any]) -> UsageMetadata:
    input_tokens = int(last.get("inputTokens") or 0)
    output_tokens = int(last.get("outputTokens") or 0)
    return UsageMetadata(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        input_token_details={"cache_read": int(last.get("cachedInputTokens") or 0)},
    )


class _Turn:
    """One message's Codex turn as the page sees it, and as it is kept."""

    def __init__(
        self, session: CodeSession, websocket: WebSocket, text: str, images: list[str]
    ) -> None:
        self.session = session
        self.websocket = websocket
        content: str | list[str | dict[str, Any]] = text
        if images:
            # As a chat turn keeps them, so history shows them again.
            content = [{"type": "text", "text": text}]
            content.extend({"type": "image_url", "image_url": {"url": url}} for url in images)
        self.messages: list[BaseMessage] = [HumanMessage(content=content)]
        self.streamed = ""
        self._commands: dict[str, str] = {}

    async def decide(self, request: ApprovalRequest) -> bool:
        decision = await self.session._decide_action_request(approval_call(request), self.websocket)
        return isinstance(decision, dict) and decision.get("type") == "approve"

    def _keep_call(
        self, call_id: str, name: str, args: dict[str, Any], result: Any, error: bool
    ) -> None:
        self.messages.append(
            AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])
        )
        self.messages.append(
            ToolMessage(
                content=json.dumps(result, ensure_ascii=False),
                tool_call_id=call_id,
                name=name,
                status="error" if error else "success",
            )
        )

    async def on_event(self, event: CodexEvent) -> None:
        data = event.data
        send = self.websocket.send_json
        if event.kind == "text" and data.get("text"):
            self.streamed += data["text"]
            await send({"type": "agent_delta", "text": data["text"]})
        elif event.kind == "message":
            self.streamed = ""
            if data.get("text"):
                self.messages.append(AIMessage(content=data["text"]))
        elif event.kind == "command_started":
            self._commands[str(data.get("id"))] = str(data.get("command", ""))
        elif event.kind == "auto_approved":
            args = command_args(str(data.get("command", "")))
            await send({"type": "tool_started", "tool_name": CODE_COMMAND_TOOL, "arguments": args})
        elif event.kind == "command_finished":
            if data.get("status") == "declined":
                return
            args = command_args(str(data.get("command", "")))
            exit_code = data.get("exit_code")
            result = {
                "exit_code": exit_code,
                "output": str(data.get("output") or "")[-_OUTPUT_CHARS:],
            }
            error = data.get("status") != "completed" or exit_code not in (0, None)
            self._keep_call(str(data.get("id")), CODE_COMMAND_TOOL, args, result, error)
            await send(
                {
                    "type": "tool_result",
                    "tool_name": CODE_COMMAND_TOOL,
                    "arguments": args,
                    "result": result,
                    "is_error": error,
                }
            )
        elif event.kind == "file_change":
            if data.get("status") == "declined":
                return
            args = {"paths": data.get("paths", [])}
            result = {"status": data.get("status")}
            error = data.get("status") != "completed"
            self._keep_call(str(data.get("id")), CODE_CHANGE_TOOL, args, result, error)
            await send(
                {
                    "type": "tool_result",
                    "tool_name": CODE_CHANGE_TOOL,
                    "arguments": args,
                    "result": result,
                    "is_error": error,
                }
            )
        elif event.kind == "usage":
            usage = _usage(data)
            self.session._last_usage_metadata = usage
            await send(_usage_event(usage))

    async def keep(self, ending: str | None = None) -> None:
        """Write the turn into the conversation. A reply cut short keeps
        what had streamed of it."""
        tail = self.streamed.strip()
        if ending:
            tail = f"{tail}\n\n{ending}" if tail else ending
        if tail:
            self.messages.append(AIMessage(content=tail))
        session = self.session
        await session.lg_agent.aupdate_state(
            session.config, {"messages": self.messages}, as_node=_TURN_END_NODE
        )


class CodeSession(ChatSessionLG):
    def __init__(self, **kwargs: Any) -> None:
        settings = kwargs["settings"]
        kwargs.setdefault("model", settings.code_model or settings.default_model)
        super().__init__(**kwargs)
        # Codex's actions are decided like any gated call, so they must be
        # known as gated whatever the chat's own tool list holds.
        self._gated_tool_risks.update(CODE_APPROVAL_RISKS)

    @property
    def _codex_thread_path(self) -> Path:
        return codex_thread_path(Path(self.settings.state_dir), self.thread_id)

    def _codex_thread_id(self) -> str | None:
        try:
            return self._codex_thread_path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    @property
    def asking(self) -> bool:
        """Waiting on the user: a code turn's approvals aren't checkpointed
        interrupts, so the thread list can't see them there."""
        return any(
            request_id not in self._subagent_request_ids for request_id in self._pending_approvals
        )

    def request_stop(self) -> None:
        super().request_stop()
        # A declined approval doesn't end a Codex turn; only interrupting it
        # does, which cancelling the turn's task brings about.
        task = self._current_turn_task
        if task is not None and not task.done() and not task.cancelling():
            task.cancel()

    # Codex keeps the conversation's context in its own thread, which can't
    # be cut back to an earlier message.
    async def handle_edit_message(
        self,
        index: int,
        text: str,
        websocket: WebSocket,
        images: list[str] | None = None,
    ) -> None:
        await websocket.send_json(
            {
                "type": "error",
                "message": "Editing a message isn't available in a code conversation.",
            }
        )

    async def handle_rewind_message(self, index: int, websocket: WebSocket) -> None:
        await websocket.send_json(
            {"type": "error", "message": "Rewinding isn't available in a code conversation."}
        )

    async def _handle_user_message_locked(
        self,
        text: str,
        websocket: WebSocket,
        images: list[str] | None = None,
        *,
        notice: bool = False,
    ) -> None:
        await self._run_observational_hooks(
            "UserPromptSubmit",
            {"event": "UserPromptSubmit", "thread_id": self.thread_id, "text": text},
        )
        command = text.strip().lower()
        if command in ("/plan", "/accept-edits", "/auto"):
            self._toggle_mode(command[1:])
            await self.send_state(websocket)
            return
        if command.startswith("/"):
            await websocket.send_json(
                {"type": "error", "message": "Commands aren't available in a code conversation."}
            )
            return
        self._stop_requested = False
        self._turn_websocket = websocket
        turn = _Turn(self, websocket, text, images or [])
        try:
            result = await self._run_codex(
                turn, (PLAN_NOTE if self.plan_mode else "") + text, images or []
            )
        except asyncio.CancelledError:
            try:
                await turn.keep("[stopped]")
                await websocket.send_json({"type": "agent_message", "text": "[stopped]"})
                await websocket.send_json({"type": "tasks_changed"})
            except Exception:  # noqa: BLE001 -- the client is already gone
                pass
            return
        except Exception as exc:  # noqa: BLE001 -- surfaced to the client, as a chat turn's error is
            logger.warning("code turn on %s failed", self.thread_id, exc_info=True)
            await turn.keep()
            await websocket.send_json({"type": "error", "message": str(exc) or type(exc).__name__})
            return
        await self._finish(turn, result, websocket)

    async def _run_codex(self, turn: _Turn, prompt: str, images: list[str]) -> TurnResult:
        service = code_service(self.settings)
        model = codex_model(self._model_string, service.custom_providers())
        host = await service.prepare()
        if self._context_window is None:
            self._context_window = await asyncio.to_thread(
                self._context_window_client.get_context_window, self._model_string
            )
        thread = CodexThread(
            host,
            model,
            Path(self.workspace_root),
            thread_id=self._codex_thread_id(),
            developer_instructions=INSTRUCTIONS,
            context_window=self._context_window,
        )
        try:
            return await thread.run_turn(prompt, turn.decide, turn.on_event, images)
        finally:
            if thread.thread_id is not None and thread.thread_id != self._codex_thread_id():
                self._codex_thread_path.parent.mkdir(parents=True, exist_ok=True)
                self._codex_thread_path.write_text(thread.thread_id, encoding="utf-8")

    async def _finish(self, turn: _Turn, result: TurnResult, websocket: WebSocket) -> None:
        if result.status == "completed":
            turn.streamed = ""
            reply = result.text or "(no reply)"
            if not result.text:
                turn.messages.append(AIMessage(content=reply))
            await turn.keep()
            await websocket.send_json({"type": "agent_message", "text": reply})
        elif result.status == "interrupted":
            await turn.keep("[stopped]")
            await websocket.send_json({"type": "agent_message", "text": "[stopped]"})
        else:
            error = result.error or "The code module's turn failed."
            await turn.keep(f"[failed: {error}]")
            await websocket.send_json({"type": "error", "message": error})
        if self._last_usage_metadata is not None:
            await websocket.send_json(_usage_event(self._last_usage_metadata))
        await websocket.send_json({"type": "tasks_changed"})
        if self._wants_title():
            self._title_task = asyncio.create_task(self._name_thread(websocket))
