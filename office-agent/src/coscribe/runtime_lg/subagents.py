"""Delegated sub-agents: spawn_agent (the parent waits for the reply) and
spawn_agent_background (it doesn't). Both run the child as its own
asyncio task, visible in the Sub Agents panel while it works.

A child's approvals go through its host's decide() -- the session's own
approval policy -- rather than being bridged up through the parent's
graph with interrupt(). So a child waiting on approval is answered from
the panel whether or not its parent is still waiting, and plan mode,
accept-edits, exec policy and hooks apply to it exactly as they do to the
parent.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from ..runtime.types import tool_metadata
from ..tools import QUESTION_TOOL_NAMES
from ..tools.scheduled_tasks import TASK_DRAFT_TOOL_NAMES
from ..tools.subagent_tasks import (
    SubAgentTask,
    SubAgentTaskStore,
    register_subagent_run,
)
from .agent import build_langgraph_agent
from .agent import tool_name as _tool_name
from .messages import extract_text

logger = logging.getLogger(__name__)

DEFAULT_SUBAGENT_INSTRUCTIONS = (
    "You are doing one task delegated to you by another assistant. Do the "
    "task with your tools, then reply with a short, complete report of what "
    "you did and found -- that reply is all the other assistant will see."
)

# Tools that need a person on the parent's side of the conversation, or
# would let a child start children of its own.
_NOT_FOR_SUBAGENTS = frozenset(
    {
        "spawn_agent",
        "spawn_agent_background",
        "draft_workflow",
        "revise_workflow",
        *QUESTION_TOOL_NAMES,
        *TASK_DRAFT_TOOL_NAMES,
    }
)


@dataclass
class SubAgentHost:
    """What the conversation that delegates provides to its sub-agents."""

    thread_id: str
    state_dir: Path
    # "" means the conversation's own model. Returns (model string, chat
    # model); raises ValueError for a model that isn't configured.
    resolve_model: Callable[[str], tuple[str, Any]]
    configured_models: Callable[[], list[str]]
    # Decides one pending action request -- approve, reject, or ask the
    # user -- for the given run.
    decide: Callable[[dict[str, Any], SubAgentTask], Awaitable[Any]]
    # The run's record changed; tell whoever is watching.
    changed: Callable[[SubAgentTask], Awaitable[None]]
    defer_tools: bool = False
    core_tool_names: frozenset[str] = frozenset()
    # Route every call through decide(), not only gated ones (a PreToolUse
    # hook needs to see them all).
    interrupt_all: bool = False
    max_turns: int | None = None


def _record_progress(task: SubAgentTask, update: Any, seen: set[str]) -> bool:
    """Folds one stream_mode="updates" chunk into the run's counters. The
    approval middleware re-emits the model's message in its own update, so
    each message is counted once, by `seen`."""
    if not isinstance(update, dict):
        return False
    changed = False
    for node_update in update.values():
        if not isinstance(node_update, dict):
            continue
        for message in node_update.get("messages", []) or []:
            if not isinstance(message, AIMessage):
                continue
            key = message.id or "|".join(
                [str(call.get("id")) for call in message.tool_calls] + [str(message.content)]
            )
            if key in seen:
                continue
            seen.add(key)
            usage: dict[str, Any] = dict(message.usage_metadata or {})
            task.tokens += int(usage.get("total_tokens", 0) or 0)
            if message.tool_calls:
                task.tool_uses += len(message.tool_calls)
                last = message.tool_calls[-1]
                task.last_tool = {"tool_name": last["name"], "arguments": last["args"]}
            changed = True
    return changed


async def _drive(
    host: SubAgentHost,
    store: SubAgentTaskStore,
    task: SubAgentTask,
    sub_agent: Any,
    child_config: dict[str, Any],
) -> None:
    async def save() -> None:
        store.save(task)
        await host.changed(task)

    turn_input: Any = {"messages": [{"role": "user", "content": task.prompt}]}
    seen: set[str] = set()
    try:
        while True:
            async for update in sub_agent.astream(
                turn_input, config=child_config, stream_mode="updates"
            ):
                if _record_progress(task, update, seen):
                    await save()
            state = await sub_agent.aget_state(child_config)
            if not state.next:
                break
            resume: dict[str, Any] = {}
            for pending in state.tasks:
                for interrupt in pending.interrupts:
                    decisions = []
                    for request in interrupt.value.get("action_requests", []):
                        decisions.append(await host.decide(request, task))
                        if task.pending_approval is not None or task.status != "running":
                            task.pending_approval = None
                            task.status = "running"
                            await save()
                    resume[interrupt.id] = {"decisions": decisions}
            if not resume:
                raise RuntimeError("the sub-agent stopped partway with nothing to resume")
            turn_input = Command(resume=resume)
    except asyncio.CancelledError:
        task.status = "stopped"
        task.pending_approval = None
        task.finished_at = datetime.now(UTC).isoformat()
        await save()
        raise
    except Exception as exc:  # noqa: BLE001 -- a failed child is reported, not raised into the parent
        logger.warning("sub-agent %s failed", task.task_id, exc_info=True)
        task.status = "failed"
        task.error = (str(exc) or type(exc).__name__)[:2000]
        task.pending_approval = None
        task.finished_at = datetime.now(UTC).isoformat()
        await save()
        return

    state = await sub_agent.aget_state(child_config)
    messages = state.values.get("messages", []) if state.values else []
    reply = extract_text(getattr(messages[-1], "content", None)) if messages else ""
    task.result = reply or "(the sub-agent gave no reply)"
    task.status = "succeeded"
    task.finished_at = datetime.now(UTC).isoformat()
    await save()


def _outcome(task: SubAgentTask) -> str:
    if task.status == "succeeded":
        return task.result or "(the sub-agent gave no reply)"
    if task.status == "stopped":
        return "(the user stopped this sub-agent before it finished)"
    return f"(the sub-agent failed: {task.error or 'unknown error'})"


def build_delegation_tools(
    host: SubAgentHost, available_tools: Sequence[Callable[..., Any] | BaseTool]
) -> list[Callable[..., Any]]:
    """spawn_agent and spawn_agent_background, bound to `host`."""
    tools_by_name = {
        _tool_name(t): t for t in available_tools if _tool_name(t) not in _NOT_FOR_SUBAGENTS
    }
    store = SubAgentTaskStore(host.state_dir)

    def start(
        description: str,
        prompt: str,
        instructions: str,
        model: str,
        tool_names: str,
        background: bool,
    ) -> tuple[SubAgentTask, asyncio.Task[None]]:
        requested = [n.strip() for n in tool_names.split(",") if n.strip()]
        unknown = [n for n in requested if n not in tools_by_name]
        if unknown:
            raise ValueError(f"Unknown tool for a sub-agent: {', '.join(unknown)}")
        selected = [tools_by_name[n] for n in requested] or list(tools_by_name.values())
        model_string, chat_model = host.resolve_model(model.strip())
        sub_agent = build_langgraph_agent(
            chat_model,
            selected,
            instructions.strip() or DEFAULT_SUBAGENT_INSTRUCTIONS,
            checkpointer=InMemorySaver(),
            extra_interrupt_tool_names=[_tool_name(t) for t in selected]
            if host.interrupt_all
            else (),
            max_turns=host.max_turns,
            # Only a child given the full set searches for tools; a hand-picked
            # set is small enough to bind as is.
            defer_tools=host.defer_tools and not requested,
            core_tool_names=host.core_tool_names,
        )
        task = SubAgentTask(
            task_id=uuid.uuid4().hex[:12],
            thread_id=host.thread_id,
            instructions=instructions,
            prompt=prompt,
            tool_names=tool_names,
            description=description.strip() or prompt.strip().splitlines()[0][:80],
            status="running",
            started_at=datetime.now(UTC).isoformat(),
            model=model_string,
            background=background,
        )
        child_config = {"configurable": {"thread_id": f"subagent-{task.task_id}"}}
        # A fresh context: LangChain keeps the running call's config in
        # contextvars, and a copied one would stream the child's messages
        # into the parent's own chat stream.
        runner = asyncio.create_task(
            _drive(host, store, task, sub_agent, child_config), context=contextvars.Context()
        )
        register_subagent_run(task.task_id, runner, sub_agent, child_config)
        # Saved only once registered: a record with no live runner reads as
        # cut off by a restart.
        store.save(task)
        return task, runner

    models_note = ", ".join(host.configured_models()) or "only this conversation's"

    async def spawn_agent(
        description: str,
        prompt: str,
        instructions: str = "",
        model: str = "",
        tool_names: str = "",
    ) -> str:
        """Hand a self-contained task to a sub-agent with its own context
        and wait for its report -- only that report comes back, so its
        steps don't crowd this conversation. The user watches it, and
        answers its approvals, in the Sub Agents panel.

        Args:
            description: a few plain words for the panel, e.g. "Check the
                Q3 figures in sales.xlsx".
            prompt: the complete task -- the sub-agent sees nothing of this
                conversation, so include every path, fact and constraint.
            instructions: its role, if it needs one beyond doing the task.
            model: which model runs it, as "provider:model"; empty for this
                conversation's own model.
            tool_names: comma-separated names of your tools to give it; empty
                gives it all of them (it finds the ones it needs).
        """
        task, runner = start(description, prompt, instructions, model, tool_names, False)
        try:
            await asyncio.shield(runner)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is None or not current.cancelling():
                # Only the sub-agent was stopped (from the panel): the parent
                # carries on with that as its report.
                return _outcome(task)
            # The parent's turn is ending. A Stop stops the sub-agent too (the
            # session cancels it); a dropped connection leaves it running as a
            # background run whose report waits in the panel.
            if not runner.done() and not runner.cancelling():
                task.background = True
                store.save(task)
            raise
        return _outcome(task)

    async def spawn_agent_background(
        description: str,
        prompt: str,
        instructions: str = "",
        model: str = "",
        tool_names: str = "",
    ) -> dict[str, Any]:
        """Like spawn_agent, but returns at once with a task_id instead of
        waiting, so this conversation can go on. Then either end your turn
        with wake_on_subagent(task_id, reason) to be resumed when it's done,
        or check on it with check_subagent_task(task_id).

        Args:
            description: a few plain words for the panel.
            prompt: the complete task -- it sees nothing of this conversation.
            instructions: its role, if it needs one beyond doing the task.
            model: which model runs it, as "provider:model"; empty for this
                conversation's own model.
            tool_names: comma-separated names of your tools to give it; empty
                gives it all of them.
        """
        task, _runner = start(description, prompt, instructions, model, tool_names, True)
        return {"task_id": task.task_id, "status": task.status, "description": task.description}

    for delegate in (spawn_agent, spawn_agent_background):
        head, _, args = (delegate.__doc__ or "").partition("        Args:")
        delegate.__doc__ = f"{head}        Configured models: {models_note}.\n\n        Args:{args}"
    return [
        tool_metadata(spawn_agent, risk_category="READ", category="subagents"),
        tool_metadata(spawn_agent_background, risk_category="READ", category="subagents"),
    ]


# build_review_work_tool's fixed Reviewer persona.
REVIEWER_INSTRUCTIONS = """\
You are a reviewer. You did not do the work being reviewed -- look at it \
with fresh eyes. Given the user's original request and a summary of what \
was done, check: does it actually satisfy the request? Is anything missing, \
wrong, or riskier than necessary? If you were given a file path, read it \
yourself with your own tools rather than trusting the summary alone -- the \
summary is what the acting agent believes it did, not independent \
confirmation. If an image is attached, it's a rendered preview of the \
actual output -- look at it critically, the way a human design reviewer \
would, not just for structural correctness: is text legible against \
whatever is behind it, does the layout look generic or like every \
slide/page uses the same template, are numbers/tables/formulas that are \
visible actually complete and not obviously wrong, do tracked changes or \
comments look like they landed on the right content. Reply with a short, \
direct assessment: either confirm it looks correct, or list specific, \
concrete problems to fix (not vague "could be better" -- name what's \
actually wrong and where). Do not redo the work yourself.
"""


# LibreOffice's own PNG export (render_pptx_preview et al) has no
# explicit width/height set (see tools/_thumbnail.py), so a slide preview
# commonly lands well past this on its long edge -- and vision APIs
# charge per-pixel-patch (Anthropic: ceil(w/28) * ceil(h/28) "visual
# tokens", verified live against Anthropic's own docs 2026-09-17), not a
# flat per-image fee, so an oversized image is real, avoidable cost, not
# a one-time flat cost. 1568px matches Claude's own documented "standard
# tier" long-edge cap -- Claude itself silently downscales to at most
# this before scoring an image, so sending anything larger only spends
# extra upload bytes/latency with zero fidelity benefit on that
# provider; for a provider that instead prices roughly by raw pixel
# count (most other vision APIs use a similar patch-tiling scheme),
# this directly cuts the real token cost. A reviewer needs to judge
# layout/legibility/color, not read every pixel at native slide
# resolution, so this tradeoff costs nothing quality-wise that matters
# for that job.
_REVIEW_PREVIEW_MAX_DIMENSION = 1568


def _downscale_preview_for_review(image_bytes: bytes) -> bytes:
    """Resize a rendered slide/page preview PNG so neither dimension
    exceeds `_REVIEW_PREVIEW_MAX_DIMENSION`, preserving aspect ratio --
    a no-op (returns the original bytes untouched) if it's already
    smaller, so a small preview never gets needlessly re-encoded.
    Real, live-reported cost problem this addresses: a real multi-slide
    review pass (render every slide, call review_work with all of them)
    sent every preview at LibreOffice's own uncapped export resolution,
    a meaningful and entirely avoidable slice of a run that measured
    ~9.3M tokens for one deck.

    Never raises: same best-effort contract render_thumbnail's own
    docstring establishes for preview generation -- a corrupt/
    unreadable/unusual-format image falls back to the original bytes
    unchanged (the review still proceeds, just without the token
    saving) rather than failing the whole review_work call over a
    preview-resizing problem that isn't the point of calling it."""
    from io import BytesIO

    from PIL import Image, UnidentifiedImageError

    max_dim = _REVIEW_PREVIEW_MAX_DIMENSION
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            if image.width <= max_dim and image.height <= max_dim:
                return image_bytes
            resized = image.copy()
            resized.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            resized.save(buffer, format="PNG")
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError):
        return image_bytes


def build_review_work_tool(
    model: Any,
    reviewer_tools: Sequence[Callable[..., Any] | BaseTool],
    state_dir: Path,
) -> Callable[..., Any]:
    """Return a review_work tool bound to `model` -- spawn_agent's fixed-
    Reviewer sibling (REVIEWER_INSTRUCTIONS above, reused
    unmodified rather than duplicated, same "reuse coscribe's own
    definitions" principle agent.py already follows for ToolMetadata).

    `reviewer_tools` should be read-only (the caller is expected to filter
    to exactly that, e.g. by ToolMetadata.requires_approval) -- none of
    spawn_agent's nested-interrupt machinery is needed here specifically
    *because* of that: a Reviewer with only read-only tools can never
    pause on HumanInTheLoopMiddleware, so a plain single-turn `.invoke()`
    (which itself may loop through several tool calls internally before
    returning) always runs straight to completion without ever raising
    interrupt(). No InjectedToolCallId/thread_id tracking either, for the
    same reason spawn_agent needs it (surviving the parent node's own
    resume-replay) never applies: there's nothing to resume.

    `state_dir` resolves a bare preview filename (exactly what a write
    tool's own `preview_path` field returns) to the real file under
    `state_dir/previews/`, the same lookup `GET /api/previews/{name}`
    already does -- no path-traversal surface, it's a single-directory
    filename match, not a caller-supplied path.
    """

    def review_work(
        original_request: str,
        summary_of_work: str,
        file_path: str = "",
        preview_name: str = "",
    ) -> str:
        """Ask a fresh reviewer (who didn't do the work) to check it against
        the original request before you finalize or act on it -- use this
        after a sub-agent finishes, after writing a multi-page/multi-slide
        or heavily formatted document, or before a high-risk/irreversible
        action, to catch what you might have missed. The reviewer has its
        own read-only tools and will independently read file_path itself
        rather than just trusting summary_of_work -- and if preview_name is
        given, it looks at the actual rendered image(s), not just a
        description of them.

        Args:
            original_request: what the user actually asked for
            summary_of_work: what was done, or is about to be done
            file_path: the file to independently check, relative to the
                workspace root -- the reviewer will read it with its own
                tools rather than trusting summary_of_work alone
            preview_name: one or more of a write tool's own `preview_path`
                value(s), comma-separated -- e.g. render_pptx_preview's
                `preview_paths_csv` for a full multi-slide check. Lets the
                reviewer see the actual rendered page(s)/slide(s), not just
                a text description of them; a single-slide check that only
                looks at slide 1 can miss real problems on the rest of a
                deck.
        """
        reviewer = build_langgraph_agent(model, reviewer_tools, REVIEWER_INSTRUCTIONS)
        prompt_text = (
            f"Original request:\n{original_request}\n\nWork summary:\n{summary_of_work}\n\n"
        )
        if file_path:
            prompt_text += (
                f"File to independently check: {file_path} -- read it yourself, "
                "don't just trust the summary above.\n\n"
            )

        content: str | list[dict[str, Any]] = prompt_text
        preview_names = [name.strip() for name in preview_name.split(",") if name.strip()]
        if preview_names:
            blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]
            for name in preview_names:
                try:
                    image_bytes = (state_dir / "previews" / name).read_bytes()
                except OSError:
                    continue
                image_bytes = _downscale_preview_for_review(image_bytes)
                encoded = base64.b64encode(image_bytes).decode("ascii")
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{encoded}"},
                    }
                )
            if len(blocks) > 1:
                content = blocks

        # build_langgraph_agent always attaches a checkpointer (defaults to
        # a fresh InMemorySaver()), which requires a thread_id in .invoke()'s
        # config regardless of whether anything actually needs to be
        # resumed later -- a real, previously-latent bug this call
        # surfaced (review_work had never actually been exercised against
        # a real .invoke() before). Each call is one-shot, so a fresh
        # thread_id per call is correct, not a workaround.
        thread_id = uuid.uuid4().hex
        result = reviewer.invoke(
            {"messages": [{"role": "user", "content": content}]},
            config={"configurable": {"thread_id": thread_id}},
        )
        last = result["messages"][-1]
        # extract_text, not raw .content: a Gemini "thinking" reply's content
        # carries a large signature blob (confirmed live).
        reply = extract_text(getattr(last, "content", None))
        return reply or "(reviewer produced no text reply)"

    return review_work
