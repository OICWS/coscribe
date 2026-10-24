"""The approval decision: what runs without asking, what is refused, and what
waits for a person. It lives apart from ChatSessionLG so the security-relevant
precedence can be read, owned and tested on its own; the session keeps the
pending-approval futures and their resolution, the socket and the hooks.
"""

from __future__ import annotations

import uuid
from asyncio import Future, get_running_loop
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from langchain_core.messages import HumanMessage

from ..code_runtime import CodePermissions
from ..config import Settings
from ..runtime import NetworkGuard
from ..runtime_lg import (
    CODE_APPROVAL_RISKS,
    EXEC_POLICY_TOOL_NAMES,
    AuditDecision,
    AuditLog,
    AuditReason,
    AutoApproveReason,
    ExecPolicy,
    Verdict,
    recent_user_requests,
    record_decision,
    review_action,
    strip_mode_note,
)
from ..runtime_lg import extract_text as _extract_text
from ..tools import (
    PLAN_CHOICE_AUTO,
    PLAN_CHOICE_MANUAL,
    PLAN_CHOICE_REJECT,
    PLAN_CHOICE_REVISE,
    QUESTION_TOOL_NAMES,
    TASK_DRAFT_TOOL_NAMES,
    ScheduledTriggerStore,
    edit_draft,
    format_question_answers,
    normalize_questions,
    parse_run_thread_id,
    sandbox_codex,
)
from .events import EventSink

_COMMAND_TOOL = "run_background_command"


def can_resolve_approvals(websocket: Any) -> bool:
    """True for a real client connection able to actually answer an
    approval_required prompt; False for a silent/background stand-in
    (runtime_lg/selfwake.py's SilentSocket, driving an unattended
    sleep/wake or Scheduled Task turn) where nobody is present to ever
    resolve one. Checked via a duck-typed attribute, not isinstance, so
    the selfwake module needs no import of this one. A real fastapi
    WebSocket has no such attribute, so it defaults True, unchanged.

    Calling _resolve_pending_approvals anyway when this is False would
    create a real asyncio.Future and await it, resolved only by a genuine
    incoming WS message that a silent caller will never send -- verified
    live, this hangs the calling coroutine forever, which for a poll-loop
    caller (poll_due_wakes/poll_due_scheduled_tasks) wedges the *entire*
    background poller on the very first unattended run that happens to
    touch a gated tool. Skipping the call instead leaves the interrupt
    durably paused in the checkpointer, exactly what
    resume_after_reconnect already expects to find and resolve once a
    real client opens that thread."""
    return getattr(websocket, "can_resolve_approvals", True)


class SubAgentChannel:
    """What the decision tells a sub-agent's approval channel from a
    conversation's own socket by; the session's channel class extends it."""

    @property
    def delegated_task(self) -> str:
        raise NotImplementedError

    @property
    def task_id(self) -> str:
        raise NotImplementedError

    @property
    def can_resolve_approvals(self) -> bool:
        raise NotImplementedError


class ApprovalHost(Protocol):
    """What the decision reads and calls on the session it decides for."""

    thread_id: str
    settings: Settings
    workspace_root: Path
    config: dict[str, Any]
    model: Any
    lg_agent: Any
    plan_mode: bool
    accept_edits: bool
    auto_mode: bool
    run_approval_mode: str | None
    preapproved_tools: frozenset[str]
    _gated_tool_risks: dict[str, str]
    _stop_requested: bool
    _auto_paused: bool
    _auto_blocks_in_row: int
    _auto_blocks_total: int
    _pending_approvals: dict[str, Future[bool]]
    _pending_questions: dict[str, Future[Any]]
    _subagent_request_ids: set[str]
    _code_requests: dict[str, tuple[str, str]]
    _code_task_allowances: set[tuple[str, str]]

    @property
    def folders(self) -> Sequence[Path]: ...

    def _toggle_mode(self, mode: str) -> None: ...

    async def send_state(self, websocket: EventSink, *, on_connect: bool = False) -> None: ...

    async def _send_tool_started(
        self, name: str, args: dict[str, Any], websocket: EventSink
    ) -> None: ...

    async def _run_pre_tool_use_hooks(self, name: str, args: dict[str, Any]) -> str | None: ...

    async def _build_document_edit_preview(
        self, tool_name_: str, args: dict[str, Any]
    ) -> tuple[str | None, str | None]: ...

    async def _auto_review(
        self, name: str, args: dict[str, Any], *, delegated_task: str = ""
    ) -> Verdict | None: ...

    def _load_exec_policy(self) -> ExecPolicy: ...


class ApprovalDecider:
    def __init__(self, host: ApprovalHost) -> None:
        self._host = host

    async def _decide_action_request(
        self, request: dict[str, Any], websocket: Any, *, honor_stop: bool = True
    ) -> Any:
        """Decide one pending action request, in precedence order: a
        configured PreToolUse hook can veto it outright (a hook runs before
        the approval policy it would otherwise reach); otherwise, a request for a tool in
        self._gated_tool_risks is genuinely gated. For
        run_python_script/run_node_script specifically (EXEC_POLICY_TOOL_NAMES),
        a configured exec policy (runtime_lg/exec_policy.py, ROADMAP.md
        Phase 7) can pre-decide the call: "forbidden" rejects outright,
        checked even before plan mode (same tier as a hook veto -- a
        categorical, human-authored "never do this" always wins); "allow"
        skips straight to approval, but only *after* plan mode's own check,
        so plan mode's read-only guarantee stays absolute regardless of any
        exec-policy rule. Otherwise ("prompt", or no policy configured --
        today's behavior, unconfigured) falls through unchanged: plan mode
        denies it outright (mirroring PlanModePolicy), accept-edits
        approves it without asking (mirroring AllowAllToolPolicy), and
        otherwise the user is actually asked (sending one approval_required
        WS message per request -- design gap noted in the Phase 3 plan: the
        middleware batches all of a task's requests into one interrupt, but
        the existing WS contract expects one message per gated call). A
        request for a tool *not* in that set only reached here because
        PreToolUse hooks are configured (see __init__'s
        extra_interrupt_tool_names) and already passed the hook check
        above, so it's auto-approved -- plan mode does not block it, since
        it was never risky enough to require approval in the first place.

        A name in QUESTION_TOOL_NAMES (ask_user_question) is a different
        shape of interrupt entirely -- see runtime_lg/agent.py's own
        `question_tool_names` docstring: its graph-level interrupt_on
        config only allows a "respond" decision, never "approve"/"reject",
        so every early-exit below that would normally deny a call routes
        through `_denied` instead of a bare `{"type": "reject", ...}` for
        this one tool -- `_denied` returns a "respond" decision carrying
        the same message as its content, which HumanInTheLoopMiddleware
        substitutes directly as the tool's own result (the model sees the
        denial reason as if it were the "answer"). Never audit-logged --
        it's READ risk, not an action that needed anyone's sign-off, same
        as any other ungated tool call.

        A stop already requested (see request_stop) short-circuits straight
        to reject, before ever prompting -- request_stop already denies any
        *existing* pending approval future directly, but this covers a
        request that's only decided *after* the stop flag was set (e.g. the
        next task in _resolve_pending_approvals's per-task loop).

        Audit logging (ROADMAP.md's Phase 4 "Audit logging" item, via
        runtime_lg/audit.py): every branch below that decides a genuinely
        risky action -- a hook veto (even for a tool outside
        self._gated_tool_risks, since a hook denying something is
        inherently security-relevant regardless of that tool's own base
        risk tier), an exec-policy rule forbidding or auto-allowing a
        script call, plan mode blocking a gated call, accept-edits
        auto-approving one with nobody actually looking, or a real human
        approving/denying one live -- gets a durable record, whether this
        turn was attended or a selfwake/scheduled-task run nobody was
        watching. The one branch deliberately *not* logged is the
        stop-requested short-circuit right below: that's always a live
        human's own explicit action with an obvious "why", not the
        was-anyone-watching gap this feature exists to close."""
        name = request["name"]
        is_draft = name in TASK_DRAFT_TOOL_NAMES
        is_question = name in QUESTION_TOOL_NAMES or is_draft
        if honor_stop and self._host._stop_requested:
            return self._denied(name, "Stopped by user.")
        args = request["args"]
        audit_log = AuditLog(self._host.settings.state_dir)
        hook_reason = await self._host._run_pre_tool_use_hooks(name, args)
        if hook_reason is not None:
            self._audit(audit_log, name, args, "reject", "hook_veto", hook_reason)
            return self._denied(name, hook_reason)
        if is_question:
            if not can_resolve_approvals(websocket):
                message = (
                    "No one is available to review this draft -- this is an unattended run, "
                    "so no task was created."
                    if is_draft
                    else "No one is available to answer this question -- this is an unattended run."
                )
                self._audit(audit_log, name, args, "reject", "unattended", message)
                return self._denied(name, message)
            if is_draft:
                return await self._decide_task_draft_request(name, args, websocket)
            if name == "exit_plan_mode":
                return await self._decide_plan_request(args, websocket)
            return await self._decide_question_request(args, websocket)
        if name not in self._host._gated_tool_risks:
            return {"type": "approve"}
        return await self._decide_gated_request(name, args, websocket, audit_log)

    def _denied(self, name: str, message: str) -> dict[str, Any]:
        if name in QUESTION_TOOL_NAMES or name in TASK_DRAFT_TOOL_NAMES:
            return {"type": "respond", "message": message}
        return {"type": "reject", "message": message}

    def _audit(
        self,
        audit_log: AuditLog,
        name: str,
        args: dict[str, Any],
        decision: AuditDecision,
        reason: AuditReason,
        detail: str | None = None,
    ) -> None:
        record_decision(
            audit_log,
            thread_id=self._host.thread_id,
            tool_name=name,
            arguments=args,
            decision=decision,
            reason=reason,
            detail=detail,
        )

    async def _approved(
        self,
        audit_log: AuditLog,
        name: str,
        args: dict[str, Any],
        websocket: Any,
        reason: AuditReason | AutoApproveReason,
        detail: str | None = None,
    ) -> dict[str, Any]:
        self._audit(audit_log, name, args, "approve", reason, detail)
        await self._host._send_tool_started(name, args, websocket)
        return {"type": "approve"}

    async def _decide_gated_request(
        self, name: str, args: dict[str, Any], websocket: Any, audit_log: AuditLog
    ) -> Any:
        """The precedence for a gated tool once no hook has vetoed it: exec
        policy forbids, plan mode, exec policy allows, a code-module
        allowance, accept-edits or auto mode, the auto reviewer, and last
        a person."""
        exec_policy_decision, exec_policy_detail = "prompt", None
        if name in EXEC_POLICY_TOOL_NAMES:
            exec_policy = self._host._load_exec_policy()
            exec_policy_decision, exec_policy_detail = exec_policy.decide(args.get("script", ""))
        if exec_policy_decision == "forbidden":
            message = exec_policy_detail or "Forbidden by exec policy."
            self._audit(audit_log, name, args, "reject", "exec_policy", message)
            return {"type": "reject", "message": message}
        if self._host.plan_mode:
            message = (
                "Plan mode is active (read-only). Note this step with "
                "task_create instead; the user needs to turn plan mode "
                "off before it can run."
            )
            self._audit(audit_log, name, args, "reject", "plan_mode", message)
            return {"type": "reject", "message": message}
        if name == _COMMAND_TOOL and self._command_needs_the_person():
            return await self._ask_person_only(audit_log, name, args, websocket)
        if exec_policy_decision == "allow":
            return await self._approved(
                audit_log, name, args, websocket, "exec_policy", exec_policy_detail
            )
        if self._code_allows(name, websocket):
            return await self._approved(audit_log, name, args, websocket, "code_allowed")
        auto_reason = self._auto_approves(name)
        if auto_reason is not None:
            return await self._approved(audit_log, name, args, websocket, auto_reason)
        reviewer_note: str | None = None
        if self._auto_review_active():
            decision, reviewer_note = await self._auto_review_decision(
                audit_log, name, args, websocket
            )
            if decision is not None:
                return decision
        return await self._ask_person_only(audit_log, name, args, websocket, reviewer_note)

    def _command_needs_the_person(self) -> bool:
        """Outside the sandbox a command runs as the person; inside it, it still reads most of
        their files, which an online command could send out. Either way only the person approves
        it, in every mode (decision 0022)."""
        state_dir = self._host.settings.state_dir
        if sandbox_codex(state_dir) is None:
            return True
        return NetworkGuard(state_dir, self._host.thread_id).scripts_online()

    async def _ask_person_only(
        self,
        audit_log: AuditLog,
        name: str,
        args: dict[str, Any],
        websocket: Any,
        reviewer_note: str | None = None,
    ) -> Any:
        if not can_resolve_approvals(websocket):
            # Reached only when this call is genuinely gated and neither
            # exec_policy nor accept_edits already decided it above --
            # creating the Future below would await a real approval nobody
            # is present to give. A normal unattended turn never gets here
            # (its caller skips _resolve_pending_approvals entirely, leaving
            # the interrupt durably paused in the checkpointer); this is the
            # backstop for an unattended run with accept_edits or auto mode
            # on, which does resolve approvals and must not hang the poller
            # forever.
            message = "No one is available to approve this -- this is an unattended run."
            self._audit(audit_log, name, args, "reject", "unattended", message)
            return self._denied(name, message)
        return await self._ask_person(audit_log, name, args, websocket, reviewer_note)

    async def _auto_review_decision(
        self, audit_log: AuditLog, name: str, args: dict[str, Any], websocket: Any
    ) -> tuple[dict[str, Any] | None, str | None]:
        """The auto reviewer's verdict: a decision to return now, or None
        to fall through to a person, with the note that explains why."""
        verdict = await self._host._auto_review(
            name, args, delegated_task=getattr(websocket, "delegated_task", "")
        )
        if verdict is None:
            return None, "Auto mode's reviewer gave no answer for this, so it's asking you."
        if verdict.allow:
            self._host._auto_blocks_in_row = 0
            return (
                await self._approved(
                    audit_log, name, args, websocket, "auto_review", verdict.reason
                ),
                None,
            )
        self._host._auto_blocks_in_row += 1
        self._host._auto_blocks_total += 1
        self._audit(audit_log, name, args, "reject", "auto_review_block", verdict.reason)
        if self._host._auto_blocks_in_row < 3 and self._host._auto_blocks_total < 20:
            return (
                self._denied(
                    name,
                    f"Auto mode blocked this: {verdict.reason} Find another way, or ask "
                    "the user to allow this specific action.",
                ),
                None,
            )
        self._host._auto_paused = True
        if self._host._auto_blocks_total >= 20:
            self._host._auto_blocks_total = 0
        return None, f"Auto mode kept blocking actions, so it's asking you: {verdict.reason}"

    async def _ask_person(
        self,
        audit_log: AuditLog,
        name: str,
        args: dict[str, Any],
        websocket: Any,
        reviewer_note: str | None,
    ) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        future: Future[bool] = get_running_loop().create_future()
        self._host._pending_approvals[request_id] = future
        if isinstance(websocket, SubAgentChannel):
            self._host._subagent_request_ids.add(request_id)
            if name in CODE_APPROVAL_RISKS:
                self._host._code_requests[request_id] = (websocket.task_id, name)
        # Only worth building for a socket that can actually show it to
        # someone -- for an unattended selfwake/Scheduled Task turn (see
        # _can_resolve_approvals's own docstring), this would just spend a
        # real office-program conversion + dry-run edit on a prompt nobody is
        # ever going to see resolved live.
        before_preview, after_preview = (
            await self._host._build_document_edit_preview(name, args)
            if can_resolve_approvals(websocket)
            else (None, None)
        )
        await websocket.send_json(
            {
                "type": "approval_required",
                "id": request_id,
                "tool_name": name,
                "arguments": args,
                "before_preview": before_preview,
                "after_preview": after_preview,
                "reviewer_note": reviewer_note,
            }
        )
        try:
            approved = await future
        finally:
            self._host._pending_approvals.pop(request_id, None)
            self._host._subagent_request_ids.discard(request_id)
            self._host._code_requests.pop(request_id, None)
        if approved and self._host._auto_paused:
            self._host._auto_paused = False
            self._host._auto_blocks_in_row = 0
        self._audit(audit_log, name, args, "approve" if approved else "reject", "human")
        return {"type": "approve" if approved else "reject"}

    async def _decide_question_request(
        self, args: dict[str, Any], websocket: EventSink
    ) -> dict[str, Any]:
        """The ask_user_question flow: send question_required, wait for a
        real person's answer, and return it as a "respond" decision --
        HumanInTheLoopMiddleware substitutes this message directly as the
        tool's own result, the tool's Python body never actually running
        (see tools/bookkeeping/interaction.py's own docstring). Same pending-Future/
        request-id shape as the approval flow just above, kept as a
        separate dict (self._pending_questions) rather than reusing
        self._pending_approvals since the value type is different (a
        free-text answer, not a bool) and request_stop/reconnect need to
        treat the two independently."""
        request_id = uuid.uuid4().hex
        future: Future[Any] = get_running_loop().create_future()
        self._host._pending_questions[request_id] = future
        questions = normalize_questions(args)
        await websocket.send_json(
            {"type": "question_required", "id": request_id, "questions": questions}
        )
        try:
            reply = await future
        finally:
            self._host._pending_questions.pop(request_id, None)
        return {"type": "respond", "message": format_question_answers(questions, reply)}

    async def _decide_plan_request(
        self, args: dict[str, Any], websocket: EventSink
    ) -> dict[str, Any]:
        """exit_plan_mode: the user approves the plan -- leaving plan mode for
        auto or manual approvals -- or keeps planning with feedback."""
        if not self._host.plan_mode:
            return {
                "type": "respond",
                "message": "Plan mode isn't on, so there's no plan to approve -- go ahead "
                "with the work; the usual approvals apply.",
            }
        request_id = uuid.uuid4().hex
        future: Future[str] = get_running_loop().create_future()
        self._host._pending_questions[request_id] = future
        await websocket.send_json(
            {"type": "plan_ready", "id": request_id, "plan": str(args.get("plan", ""))}
        )
        try:
            answer = await future
        finally:
            self._host._pending_questions.pop(request_id, None)
        if answer in (PLAN_CHOICE_AUTO, PLAN_CHOICE_MANUAL):
            self._host._toggle_mode("auto" if answer == PLAN_CHOICE_AUTO else "plan")
            await self._host.send_state(websocket)
            how = (
                "switched to auto mode"
                if answer == PLAN_CHOICE_AUTO
                else "will approve each change as you go"
            )
            return {
                "type": "respond",
                "message": f"The user approved the plan and {how}. Carry it out now.",
            }
        if answer == PLAN_CHOICE_REJECT:
            return {
                "type": "respond",
                "message": "The user rejected the plan. Don't carry it out and don't propose "
                "another one; stop and wait for their next message.",
            }
        if answer.startswith(PLAN_CHOICE_REVISE):
            feedback = answer[len(PLAN_CHOICE_REVISE) :].strip() or "(no details)"
            return {
                "type": "respond",
                "message": f"The user wants to keep planning: {feedback}. Revise the plan "
                "and call exit_plan_mode again.",
            }
        return {"type": "respond", "message": answer}

    async def _decide_task_draft_request(
        self, name: str, args: dict[str, Any], websocket: EventSink
    ) -> dict[str, Any]:
        """create_scheduled_task's review flow: the frontend shows the
        draft, the user edits and saves it (through the ordinary REST
        create endpoint, with its full validation) or dismisses it, and
        answers with a sentence saying which -- substituted as the tool's
        result. Shares the pending-question plumbing, since the answer is
        likewise a string and stop/reconnect treat it the same way."""
        draft = args
        if name == "edit_scheduled_task":
            # The whole task as it would be saved, so the review shows it
            # in the edit form rather than as a partial list of fields.
            try:
                draft = edit_draft(ScheduledTriggerStore(self._host.settings.state_dir), args)
            except KeyError as exc:
                message = f"{exc.args[0]} -- check list_scheduled_tasks."
                return {"type": "respond", "message": message}
        request_id = uuid.uuid4().hex
        future: Future[str] = get_running_loop().create_future()
        self._host._pending_questions[request_id] = future
        await websocket.send_json({"type": "task_draft_required", "id": request_id, "draft": draft})
        try:
            answer = await future
        finally:
            self._host._pending_questions.pop(request_id, None)
        return {"type": "respond", "message": answer}

    def _auto_approves(self, name: str) -> AutoApproveReason | None:
        """Why a gated call may go ahead without asking anyone, or None.

        "skip" approves every risk tier. "auto" approves only WRITE_LOCAL:
        a local file edit stays on this machine and can be redone, while
        running code (EXEC) or acting outside this machine (EXTERNAL) can't
        be taken back, so those still wait for a person. A hook veto or an
        exec-policy "forbidden" rule is checked before this and still wins
        under either tier -- those are the user's own standing rules, not
        approvals."""
        if name in self._host.preapproved_tools:
            return "preapproved"
        if self._host.accept_edits and self._host._gated_tool_risks.get(name) == "WRITE_LOCAL":
            return "accept_edits"
        mode = self._effective_run_approval_mode()
        if mode == "skip":
            return "approval_mode_skip"
        if mode == "auto" and self._host._gated_tool_risks.get(name) == "WRITE_LOCAL":
            return "approval_mode_auto"
        return None

    def _effective_run_approval_mode(self) -> str | None:
        """The scheduled-run approval tier in force here: the executing
        run's, or -- while a person finishes a run that parked on an
        approval -- that run's task's, so the rest of the run keeps the
        rules it started under. None for an ordinary conversation."""
        if self._host.run_approval_mode is not None:
            return self._host.run_approval_mode
        parsed = parse_run_thread_id(self._host.thread_id)
        if parsed is None:
            return None
        trigger = ScheduledTriggerStore(self._host.settings.state_dir).load(parsed[0])
        if trigger is None:
            return None
        run = trigger.find_run(parsed[1])
        if run is None or run.status != "needs_approval":
            return None
        return trigger.approval_mode

    def _code_allows(self, name: str, websocket: Any) -> bool:
        """Whether the user has allowed this kind of code-module action: on
        an earlier card for this task, or in Settings > Code for good."""
        if name not in CODE_APPROVAL_RISKS or not isinstance(websocket, SubAgentChannel):
            return False
        if (websocket.task_id, name) in self._host._code_task_allowances:
            return True
        # A switch set for the user at the keyboard doesn't reach a scheduled
        # run, whose task chose its own approval tier, or a turn nobody is
        # watching.
        if self._effective_run_approval_mode() is not None or not websocket.can_resolve_approvals:
            return False
        permissions = CodePermissions(self._host.settings.state_dir, tuple(CODE_APPROVAL_RISKS))
        return permissions.policy(name) == "allow"

    def _auto_review_active(self) -> bool:
        """A scheduled run keeps the approval tier its task was saved with;
        the conversation's Auto mode applies only outside one."""
        return (
            self._host.auto_mode
            and not self._host._auto_paused
            and self._effective_run_approval_mode() is None
        )

    async def _auto_review(
        self, name: str, args: dict[str, Any], *, delegated_task: str = ""
    ) -> Verdict | None:
        state = await self._host.lg_agent.aget_state(self._host.config)
        messages = list(state.values.get("messages", [])) if state.values else []
        return await review_action(
            self._host.model,
            tool_name=name,
            arguments=args,
            risk=self._host._gated_tool_risks.get(name, "WRITE_LOCAL"),
            folders=[str(folder) for folder in (self._host.folders or [self._host.workspace_root])],
            user_requests=recent_user_requests(
                [
                    HumanMessage(strip_mode_note(_extract_text(m.content)))
                    for m in messages
                    # A message from another conversation or a sub-agent's report is stored as a
                    # human message but is not what the user said.
                    if m.type == "human" and not m.additional_kwargs.get("notice")
                ]
            ),
            delegated_task=delegated_task,
        )

    def _auto_approval_active(self) -> bool:
        return (
            self._host.accept_edits
            or bool(self._host.preapproved_tools)
            or self._auto_review_active()
            or self._effective_run_approval_mode() in ("auto", "skip")
        )
