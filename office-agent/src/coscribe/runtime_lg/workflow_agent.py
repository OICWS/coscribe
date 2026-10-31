"""The runner a workflow's agent step runs through (decision 0028): an agent
built like a sub-agent, kept in the conversation's checkpointer under the
step's key, so a run that pauses on one of its calls -- for an hour, or
across a restart -- goes on from that call instead of starting over.

Every call the agent makes comes to an interrupt, not only the gated ones:
the step's allowlist and its read-only rule are held there, with the same
fixed tool list every agent has (decision 0001), rather than by binding a
smaller one. A call that needs a person ends the pass as "waiting"; the
person's answer comes back on a later pass, named by the call's id, which
is the interrupt's id and the call's place in it, both kept in the
checkpoint."""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

from ..runtime import get_tool_metadata, redactor
from ..workflows import AgentCall, AgentReply, AgentRequest
from .agent import build_langgraph_agent, tool_name
from .audit import AuditLog, record_decision
from .audit import Reason as AuditReason
from .messages import extract_text
from .subagents import (
    LIMIT_MESSAGE_PREFIX,
    MODEL_STALL_SECONDS,
    ModelStalled,
    for_subagents,
    stream_turn,
    wrap_up,
)

logger = logging.getLogger(__name__)

# The code module never runs inside a workflow (decisions 0005, 0028), and
# nobody is there to answer a question.
_NOT_IN_WORKFLOWS = frozenset(
    {"run_code_task", "run_code_command", "ask_user_question", "create_scheduled_task"}
)
_DEFERRAL_TOOLS = frozenset({"search_tools", "use_tool"})


def workflow_agent_tools(
    tools: Sequence[Callable[..., Any] | BaseTool],
) -> list[Callable[..., Any] | BaseTool]:
    """The tools an agent step may be given: a sub-agent's, without the
    code module or anything that needs a person."""
    return [t for t in tools if for_subagents(t) and tool_name(t) not in _NOT_IN_WORKFLOWS]


class _Answers:
    """A person's answers to the calls of one pending interrupt that has
    more than one: each comes on its own pass, and all are needed before
    the agent can resume. Kept on disk because a pass can be a day apart."""

    def __init__(self, state_dir: Path, key: str) -> None:
        self.path = state_dir / "workflow_agents" / f"{key}.json"

    def load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, answers: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(answers), encoding="utf-8")

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def _activity(messages: Sequence[Any]) -> tuple[int, int, list[str]]:
    calls, tokens, used = 0, 0, set()
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        usage: dict[str, Any] = dict(message.usage_metadata or {})
        tokens += int(usage.get("total_tokens", 0) or 0)
        for call in message.tool_calls:
            if call["name"] not in _DEFERRAL_TOOLS:
                calls += 1
                used.add(call["name"])
    return calls, tokens, sorted(used)


@dataclass(frozen=True)
class _Decided:
    decision: dict[str, Any]
    # Why, for the audit log; None for a call nothing gates.
    reason: AuditReason | None

    @staticmethod
    def reject(reason: AuditReason, message: str) -> _Decided:
        return _Decided({"type": "reject", "message": message}, reason)


@dataclass
class WorkflowAgentRunner:
    """`workflows.AgentRunner` for a conversation's workflow runs: what
    that conversation gives its agent steps."""

    # The workflow run's thread, for the audit log.
    thread_id: str
    state_dir: Path
    checkpointer: Any
    tools: Sequence[Callable[..., Any] | BaseTool]
    # A step's model, or None for the task's; returns (model string, chat model).
    resolve_model: Callable[[str | None], tuple[str, Any]]
    defer_tools: bool = False
    core_tool_names: frozenset[str] = frozenset()
    fixed_tool_names: frozenset[str] = frozenset()
    keeps_tool_list_fixed: Callable[[str], bool] = lambda _model: False
    # The person's PreToolUse hooks: a reason to refuse the call, or None.
    pre_tool_use: Callable[[str, dict[str, Any]], Awaitable[str | None]] | None = None
    model_stall_seconds: float = MODEL_STALL_SECONDS

    def __post_init__(self) -> None:
        self._tools = workflow_agent_tools(self.tools)
        self._risks = {
            tool_name(t): get_tool_metadata(cast(Any, t)).risk_category for t in self._tools
        }

    async def __call__(self, request: AgentRequest) -> AgentReply:
        # A fresh context: run inside a workflow node, the agent's graph
        # would otherwise be taken for a subgraph of the workflow's, and its
        # interrupts would stop the workflow with the agent's payload.
        return await asyncio.create_task(self._run(request), context=contextvars.Context())

    async def _run(self, request: AgentRequest) -> AgentReply:
        model_string, chat_model = self.resolve_model(request.model)
        agent = self._build(request, model_string, chat_model)
        config = {"configurable": {"thread_id": request.key}}
        answers = _Answers(self.state_dir, request.key)
        state = await agent.aget_state(config)
        turn: Any = None
        if not state.values:
            turn = {"messages": [HumanMessage(request.task)]}
        running: set[str] = set()
        try:
            while True:
                if turn is not None:
                    await stream_turn(
                        agent, turn, config, _ignore, self.model_stall_seconds, running
                    )
                state = await agent.aget_state(config)
                if not state.next:
                    if _ended_silently(state) and not _was_nudged(state):
                        turn = {"messages": [HumanMessage(_SILENT_END_NUDGE)]}
                        continue
                    break
                resume, waiting = await self._decide(state, request, answers)
                if waiting is not None:
                    return self._reply("waiting", state, call=waiting)
                if not resume:
                    raise RuntimeError("the agent stopped partway with nothing to resume")
                answers.clear()
                turn = Command(resume=resume)
        except ModelStalled as exc:
            return self._reply("failed", state, error=str(exc))
        except Exception as exc:  # noqa: BLE001 -- the step fails with the agent's error
            logger.warning("workflow agent %s failed", request.key, exc_info=True)
            return self._reply("failed", state, error=(str(exc) or type(exc).__name__)[:2000])
        answers.clear()
        if _ended_silently(state):
            return self._reply("failed", state, error=_SILENT_END_ERROR)
        messages = list((state.values or {}).get("messages", []))
        text = extract_text(getattr(messages[-1], "content", None)) if messages else ""
        if text.startswith(LIMIT_MESSAGE_PREFIX):
            try:
                text = await wrap_up(chat_model, request.instructions, messages)
            except Exception:  # noqa: BLE001 -- the step's check sees what is missing
                logger.warning("workflow agent %s couldn't report", request.key, exc_info=True)
                text = f"(it used all {request.max_steps} of its steps before finishing)"
        return self._reply("done", state, text=text)

    def _build(self, request: AgentRequest, model_string: str, chat_model: Any) -> Any:
        core_names = self.core_tool_names
        if self.keeps_tool_list_fixed(model_string):
            core_names = core_names | self.fixed_tool_names
        return build_langgraph_agent(
            chat_model,
            self._tools,
            request.instructions,
            checkpointer=self.checkpointer,
            extra_interrupt_tool_names=list(self._risks),
            max_turns=request.max_steps,
            defer_tools=self.defer_tools,
            core_tool_names=core_names,
            redact=redactor(self.state_dir),
        )

    async def _decide(
        self, state: Any, request: AgentRequest, answers: _Answers
    ) -> tuple[dict[str, Any], AgentCall | None]:
        """The decisions for every pending call, or the first call still
        waiting for a person. Decisions are recorded only once they are
        sent, so a pass that stops to wait records nothing twice."""
        given = answers.load()
        answer = request.answer
        resume: dict[str, Any] = {}
        decided: list[tuple[AgentCall, _Decided]] = []
        waiting: AgentCall | None = None
        for task in state.tasks:
            for pending in task.interrupts:
                decisions = []
                for i, action in enumerate(pending.value.get("action_requests", [])):
                    name, args = action["name"], dict(action.get("args") or {})
                    call = AgentCall(f"{pending.id}:{i}", name, args, self._risks.get(name, "EXEC"))
                    if answer is not None and answer.get("call_id") == call.call_id:
                        given[call.call_id] = answer
                        answers.save(given)
                    outcome = await self._decision(call, request, given.get(call.call_id))
                    if outcome is None:
                        waiting = waiting or call
                        continue
                    decided.append((call, outcome))
                    decisions.append(outcome.decision)
                resume[pending.id] = {"decisions": decisions}
        if waiting is not None:
            return {}, waiting
        for call, outcome in decided:
            if outcome.reason is not None:
                self._audit(call, outcome)
        return resume, None

    async def _decision(
        self, call: AgentCall, request: AgentRequest, answer: dict[str, Any] | None
    ) -> _Decided | None:
        if not request.allows(call):
            return _Decided.reject("workflow_step", "This step isn't allowed to make that call.")
        if self.pre_tool_use is not None:
            reason = await self.pre_tool_use(call.tool, call.args)
            if reason is not None:
                return _Decided.reject("hook_veto", reason)
        if answer is not None:
            if answer.get("approved") is True:
                return _Decided({"type": "approve"}, "human")
            return _Decided.reject("human", str(answer.get("note") or "") or "Declined.")
        decision = request.decide(call)
        if decision == "approve":
            # A read the step allows is ungated, as in a conversation: nothing to record.
            return _Decided({"type": "approve"}, None if call.risk == "READ" else "workflow_step")
        if decision == "reject":
            return _Decided.reject("workflow_step", "Not allowed in this run.")
        return None

    def _audit(self, call: AgentCall, outcome: _Decided) -> None:
        assert outcome.reason is not None
        record_decision(
            AuditLog(self.state_dir),
            thread_id=self.thread_id,
            tool_name=call.tool,
            arguments=call.args,
            decision="approve" if outcome.decision["type"] == "approve" else "reject",
            reason=outcome.reason,
            detail=outcome.decision.get("message"),
        )

    def _reply(
        self,
        status: Any,
        state: Any,
        *,
        text: str = "",
        call: AgentCall | None = None,
        error: str | None = None,
    ) -> AgentReply:
        messages = list((getattr(state, "values", None) or {}).get("messages", []))
        calls, tokens, used = _activity(messages)
        return AgentReply(
            status, text=text, call=call, error=error, tool_calls=calls, tokens=tokens,
            tools_used=used,
        )  # fmt: skip


async def _ignore(_update: Any) -> None:
    return None


# A model can end a turn with no words and no call -- seen after a tool
# error -- and an empty report would pass for a finished step though the
# work may not be done. It is asked once to say where things stand.
_SILENT_END_NUDGE = (
    "Your last reply was empty. If a call failed, deal with it and finish the task. "
    "Then say what you did, and plainly what is not done and why."
)
_SILENT_END_ERROR = "The agent stopped without saying whether the task was done."


def _ended_silently(state: Any) -> bool:
    messages = list((getattr(state, "values", None) or {}).get("messages", []))
    if not messages or not isinstance(messages[-1], AIMessage):
        return False
    last = messages[-1]
    return not last.tool_calls and not extract_text(last.content).strip()


def _was_nudged(state: Any) -> bool:
    messages = (getattr(state, "values", None) or {}).get("messages", [])
    return any(isinstance(m, HumanMessage) and m.content == _SILENT_END_NUDGE for m in messages)
