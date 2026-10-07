"""run_code_task: the conversation hands a programming task to the code
module (Codex, see code_runtime/) and gets back its report.

A run is a sub-agent like spawn_agent's -- the same task record, panel,
stop and approval path -- driven by Codex instead of a LangGraph child.
Codex's approval requests arrive as calls to two tools that exist only for
this, a command (EXEC) and a file change (WRITE_LOCAL), and are decided by
the host like any gated call: hooks, plan mode, the permission modes, a
scheduled task's approval tier and the audit log all apply. Its text and
tokens stay on its own record; nothing reaches the parent's chat stream.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..code_runtime.install import CodexUnavailable
from ..code_runtime.launch import codex_model
from ..code_runtime.service import CodeService
from ..code_runtime.thread import ApprovalRequest, CodexEvent, CodexThread, shell_script
from ..runtime.types import tool_metadata
from ..tools.subagent_tasks import (
    SubAgentTask,
    SubAgentTaskStore,
    register_subagent_run,
    take_stop_requester,
)
from .subagents import SubAgentHost, subagent_outcome

logger = logging.getLogger(__name__)

CODE_TASK_TOOL = "run_code_task"
CODE_COMMAND_TOOL = "run_code_command"
CODE_CHANGE_TOOL = "apply_code_change"
CODE_APPROVAL_RISKS = {CODE_COMMAND_TOOL: "EXEC", CODE_CHANGE_TOOL: "WRITE_LOCAL"}

_OUTPUT_CHARS = 4000

INSTRUCTIONS = """\
You are coscribe's code module, doing one task handed over by coscribe's \
office assistant, for a user who doesn't write code. Work in the current \
folder. `python` has openpyxl, python-docx, python-pptx, pandas and \
pdfplumber. Nobody can answer questions while you work: make reasonable \
assumptions and say what they were. Commands may need the user's approval; \
if one is declined, find another way or report what you couldn't do. Every \
figure or finding about the user's data must come from what a command \
printed, never typed in by hand. When done, reply with a short report in \
the language of the task: what you did, the files you created or changed, \
and anything left undone."""


@dataclass
class CodeTaskContext:
    """What the conversation lends a code run: the service, and its current
    model, folder and that model's context window."""

    service: Callable[[], CodeService]
    model: Callable[[], str]
    folder: Callable[[], Path]
    context_window: Callable[[], int | None]


def command_args(command: str, description: str = "") -> dict[str, Any]:
    # The shape run_python_script's approval card and transcript row show.
    return {"script": shell_script(command) or command, "description": description}


def approval_call(request: ApprovalRequest) -> dict[str, Any]:
    """Codex's approval request as the gated call the host decides."""
    if request.kind == "command":
        return {"name": CODE_COMMAND_TOOL, "args": command_args(request.command, request.reason)}
    return {
        "name": CODE_CHANGE_TOOL,
        "args": {"paths": list(request.paths), "description": request.reason},
    }


class _Run:
    def __init__(
        self,
        host: SubAgentHost,
        store: SubAgentTaskStore,
        record: SubAgentTask,
        context: CodeTaskContext,
    ) -> None:
        self.host = host
        self.store = store
        self.record = record
        self.context = context
        self.entries: list[dict[str, Any]] = [{"kind": "user", "text": record.prompt}]

    async def save(self) -> None:
        self.store.save(self.record)
        await self.host.changed(self.record)

    async def decide(self, request: ApprovalRequest) -> bool:
        decision = await self.host.decide(approval_call(request), self.record)
        if self.record.pending_approval is not None or self.record.status != "running":
            self.record.pending_approval = None
            self.record.status = "running"
            await self.save()
        return isinstance(decision, dict) and decision.get("type") == "approve"

    async def on_event(self, event: CodexEvent) -> None:
        data = event.data
        if event.kind == "message" and data.get("text"):
            self.entries.append({"kind": "agent", "text": data["text"]})
        elif event.kind == "command_started":
            self.record.tool_uses += 1
            self.record.last_tool = {
                "tool_name": CODE_COMMAND_TOOL,
                "arguments": command_args(str(data.get("command", ""))),
            }
            await self.save()
        elif event.kind == "command_finished":
            exit_code = data.get("exit_code")
            self.entries.append(
                {
                    "kind": "tool",
                    "tool_name": CODE_COMMAND_TOOL,
                    "arguments": command_args(str(data.get("command", ""))),
                    "result": {
                        "exit_code": exit_code,
                        "output": str(data.get("output") or "")[-_OUTPUT_CHARS:],
                    },
                    "is_error": data.get("status") != "completed" or exit_code not in (0, None),
                }
            )
        elif event.kind == "file_change":
            self.entries.append(
                {
                    "kind": "tool",
                    "tool_name": CODE_CHANGE_TOOL,
                    "arguments": {"paths": data.get("paths", [])},
                    "result": {"status": data.get("status")},
                    "is_error": data.get("status") != "completed",
                }
            )
        elif event.kind == "usage":
            self.record.tokens += int(data.get("totalTokens") or 0)
            await self.save()

    async def drive(self) -> None:
        record = self.record
        try:
            service = self.context.service()
            model = codex_model(self.context.model(), service.custom_providers())
            codex = await service.prepare()
            folder = self.context.folder()
            thread = CodexThread(
                codex,
                model,
                folder,
                developer_instructions=INSTRUCTIONS,
                context_window=await asyncio.to_thread(self.context.context_window),
            )
            result = await thread.run_turn(record.prompt, self.decide, self.on_event)
        except asyncio.CancelledError:
            record.status = "stopped"
            record.stopped_by = take_stop_requester(record.task_id)
            record.pending_approval = None
            record.finished_at = datetime.now(UTC).isoformat()
            await self.save()
            raise
        except Exception as exc:  # noqa: BLE001 -- a failed run is reported, not raised into the parent
            logger.warning("code run %s failed", record.task_id, exc_info=True)
            record.status = "failed"
            record.error = (str(exc) or type(exc).__name__)[:2000]
            record.finished_at = datetime.now(UTC).isoformat()
            await self.save()
            return
        if result.status == "completed":
            report = result.text or "(the code module gave no reply)"
            if result.files_changed:
                report += f"\n\nFiles created or changed in {folder}: " + ", ".join(
                    result.files_changed
                )
            record.result = report
            record.status = "succeeded"
        else:
            record.status = "failed"
            record.error = result.error or f"the run ended {result.status}"
        record.finished_at = datetime.now(UTC).isoformat()
        await self.save()


def build_code_task_tool(host: SubAgentHost, context: CodeTaskContext) -> Callable[..., Any]:
    store = SubAgentTaskStore(host.state_dir)

    async def run_code_task(description: str, task: str) -> str:
        """Hand a programming task to the code module -- a coding agent
        that writes and runs code in this conversation's folder -- and wait
        for its report. Use it for work that needs real code: processing
        data across files in several steps, writing and testing a script
        the user will reuse, fixing a script that fails, anything that
        takes many commands. Don't use it for what your own tools do in a
        call or two (reading or writing one document, one short script).
        It sees nothing of this conversation, so `task` must be complete:
        the files, the rules, what to produce and where. The user approves
        its commands as they come (plain reads of the folder don't ask);
        the user watches it, and can stop it, in the Sub Agents panel. Only
        its report comes back, with the files it created or changed.

        Args:
            description: a few plain words for the panel, e.g. "Clean the
                order export and total by region".
            task: the complete task -- files, rules, outputs.
        """
        try:
            model = codex_model(context.model(), context.service().custom_providers())
        except CodexUnavailable as exc:
            return (
                f"The code module can't take this task: {exc} The user can pick a model "
                "Codex can use in Settings > Code > Model for code."
            )
        record = SubAgentTask(
            task_id=uuid.uuid4().hex[:12],
            thread_id=host.thread_id,
            instructions="",
            prompt=task,
            tool_names="",
            description=description.strip() or task.strip().splitlines()[0][:80],
            status="running",
            started_at=datetime.now(UTC).isoformat(),
            model=f"codex:{model.model}",
        )
        run = _Run(host, store, record, context)
        # A fresh context, as spawn_agent's runner gets: LangChain keeps the
        # running call's config in contextvars.
        runner = asyncio.create_task(run.drive(), context=contextvars.Context())
        register_subagent_run(record.task_id, runner, lambda: list(run.entries))
        store.save(record)
        try:
            await asyncio.shield(runner)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is None or not current.cancelling():
                return subagent_outcome(record)
            # The parent's turn is ending: a Stop stops this run too (the
            # session cancels it); a dropped connection leaves it running as
            # a background run whose report waits in the panel.
            if not runner.done() and not runner.cancelling():
                record.background = True
                store.save(record)
            raise
        return subagent_outcome(record)

    return tool_metadata(run_code_task, risk_category="READ", category="subagents")
