"""ChatSessionLG: the web equivalent of cli.py's chat() loop body, built
on runtime_lg instead of runtime/ (see runtime_lg/README.md, Phase 3).

Mirrors web/session.py's ChatSession public shape (handle_user_message,
resolve_approval, send_state) closely enough that app.py's WS handler is
nearly identical to app.py's -- but the internals are native async
(agent.astream/aget_state) instead of the queue.Queue/asyncio.to_thread
bridging ChatSession needs only because Runner.run_sync is synchronous.

/plan, /accept-edits, /compact, /clear, /stop, model switching, and Hooks
are supported here (see runtime_lg/README.md's "Plan mode, accept-edits,
compact, and Hooks under runtime_lg" section for the design of the first
four; /stop and switch_model follow the same "port the contract, adapt the
mechanism to a compiled graph" approach -- /stop is cooperative, checked at
_stream_turn/_decide_action_request's own yield points, mirroring
runtime/runner.py's threading.Event-based stop_event; switch_model rebuilds
self.lg_agent against the same checkpointer/thread_id rather than mutating
a model string in place, since create_agent() bakes the model into a fixed
compiled graph at construction time). Threshold-triggered *automatic*
compaction (Settings.auto_compact_threshold) and a turn cap
(Settings.max_turns) *are* both wired in too -- see _build_lg_agent below,
which passes both through to build_langgraph_agent as
SummarizationMiddleware/ModelCallLimitMiddleware. Still narrower than
ChatSession in one documented way: Hooks configured on a top-level
session don't propagate into spawn_agent's/review_work's own nested
sub-agent graphs -- a sub-agent's own tool calls run without PreToolUse/
PostToolUse checks. spawn_agent/review_work delegation *is* wired in
(see runtime_lg/README.md's "spawn_agent's nested-interrupt bridge" section --
live-verified against Gemini before being added here, including that a
nested approval bridges up through _resolve_pending_approvals below with
zero changes needed, since the bridged interrupt's payload shape matches
HumanInTheLoopMiddleware's own exactly).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
import time
import uuid
from asyncio import Future, get_running_loop
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import WebSocket
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.ai import UsageMetadata
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph import END
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ..cli import INIT_PROMPT
from ..config import Settings
from ..coordinator import CORE_TOOL_NAMES, build_coordinator_agent
from ..runtime import (
    COMPACT_INSTRUCTIONS,
    LLMClient,
    empty_hooks_config,
    get_tool_metadata,
    run_hook,
)
from ..runtime.provider_config import load_custom_providers
from ..runtime_lg import (
    SkillSaveProposal,
    build_langgraph_agent,
    build_review_work_tool,
    build_spawn_agent_background_tool,
    build_spawn_agent_tool,
    propose_skill_save_lg,
    resolve_chat_model,
    serialize_history_for_ws_lg,
    tool_name,
    write_skill_lg,
)
from ..runtime_lg import extract_text as _extract_text
from ..runtime_lg import render_transcript_lg as _render_transcript_lg
from ..runtime_lg import tool_result_value as _tool_result_value
from ..runtime_lg.audit import AuditLog, AutoApproveReason, record_decision
from ..runtime_lg.exec_policy import EXEC_POLICY_TOOL_NAMES, load_exec_policy
from ..runtime_lg.messages import (
    ACCEPT_EDITS_MODE_NOTE,
    NORMAL_MODE_NOTE,
    PLAN_MODE_NOTE,
    current_date_note,
)
from ..tools import (
    QUESTION_TOOL_NAMES,
    format_skill_listing,
    load_builtin_skills,
    load_skills,
    slugify_skill_name,
)
from ..tools._thumbnail import render_single_page_preview
from ..tools._workspace import WorkspaceScope
from ..tools.documents import DocumentToolkit
from ..tools.presentations import PresentationToolkit
from ..tools.scheduled_tasks import (
    TASK_DRAFT_TOOL_NAMES,
    ScheduledTriggerStore,
    parse_run_thread_id,
)
from ..tools.spreadsheets import SpreadsheetToolkit
from ..workflows.engine import StepContext
from ..workflows.solidify import DraftFailed, WorkflowDraft, draft_workflow
from .activity import summarize_activity, summarize_workflow_run
from .context_usage import build_context_breakdown

logger = logging.getLogger(__name__)

# /saveworkflow <name>: the model distills the conversation into a draft
# scheduled task via create_scheduled_task, which the user then reviews.
SAVE_WORKFLOW_PROMPT = (
    'Save what we did in this conversation as a reusable workflow named "{name}": '
    'call create_scheduled_task with name="{name}", kind="manual" unless I asked '
    "for a schedule, and a prompt distilled from this conversation -- the steps and "
    "inputs that actually worked, pitfalls we ran into and how to avoid them, and "
    "the expected output. Leave out dead ends and unrelated chatter. If there's no "
    "repeatable task in this conversation yet, ask me what to save instead."
)

# How many rounds of clarifying question/answer /saveskill goes through
# before giving up.
MAX_SAVE_CLARIFICATION_ROUNDS = 3

# _build_document_edit_preview's dispatch table: which toolkit class
# owns an approval-gated edit tool, keyed by the target file's extension
# -- all three toolkits share the same (root, *, state_dir=,
# extra_readable=, extra_writable=) constructor shape (see that method's
# own docstring), which is what makes a single dict lookup enough instead
# of three near-duplicate preview-building methods.
_PREVIEWABLE_TOOLKITS_BY_EXTENSION: dict[str, type[Any]] = {
    ".pptx": PresentationToolkit,
    ".potx": PresentationToolkit,
    ".docx": DocumentToolkit,
    ".dotx": DocumentToolkit,
    ".xlsx": SpreadsheetToolkit,
    ".xlsm": SpreadsheetToolkit,
    ".xltx": SpreadsheetToolkit,
}


def _format_reply(text: str) -> str:
    if not text.strip():
        return "[no reply -- the model ended its turn without responding; try rephrasing]"
    return text


def _now_iso_lg() -> str:
    return datetime.now(UTC).isoformat()


_ORPHANED_TOOL_CALL_NOTE = "Stopped before this tool finished -- it produced no result."


async def _close_orphaned_tool_calls(agent: Any, config: dict[str, Any]) -> bool:
    """Give every tool call that never got a ToolMessage (a turn cancelled
    mid-tool: Stop, a dropped socket, a killed process) a placeholder one,
    and end the graph there. Every OpenAI-compatible provider rejects the
    whole history with a 400 otherwise, and a leftover pending "tools"
    node would re-run the cancelled call on the next resume.

    Skipped while an approval/question interrupt is pending: that tool
    call is legitimately unanswered and gets resumed, not patched.
    Rewrites the whole message list rather than appending, since a thread
    already corrupted by this bug has a newer HumanMessage after the
    orphan and the placeholder has to sit directly after its AIMessage.

    Also runs when leftover tasks exist but nothing needs a placeholder:
    aget_state folds in the pending writes of a tool that finished inside
    a superstep that never committed, but a fresh input restarts from the
    last *committed* checkpoint and drops those writes -- so the model
    would still see the orphan unless they're committed here."""
    state = await agent.aget_state(config)
    if not state.values or any(task.interrupts for task in state.tasks):
        return False
    messages = list(state.values.get("messages", []))
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    rebuilt: list[Any] = []
    patched = False
    index = 0
    while index < len(messages):
        message = messages[index]
        rebuilt.append(message)
        index += 1
        if not isinstance(message, AIMessage) or not message.tool_calls:
            continue
        # Placeholders go after any ToolMessages that did arrive (a
        # partially finished parallel batch), still before whatever
        # non-tool message follows.
        while index < len(messages) and isinstance(messages[index], ToolMessage):
            rebuilt.append(messages[index])
            index += 1
        for call in message.tool_calls:
            if call["id"] not in answered:
                patched = True
                rebuilt.append(
                    ToolMessage(
                        content=_ORPHANED_TOOL_CALL_NOTE,
                        tool_call_id=call["id"],
                        name=call["name"],
                    )
                )
    if not patched and not state.tasks:
        return False
    await agent.aupdate_state(
        config, {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *rebuilt]}, as_node="tools"
    )
    await agent.aupdate_state(config, None, as_node=END)
    return True


def _build_instructions(agent_instructions: str | None, *, defer_tools: bool) -> str:
    """Appends _SEARCH_TOOLS_NOTE (only when defer_tools is on) to
    whatever build_coordinator_agent already
    assembled -- shared by every one of this class's own call sites that
    (re)build self._instructions (__init__, select_workspace,
    set_enabled_skills -- switch_model doesn't, since it never rebuilds
    the coordinator Agent itself), so the search_tools note can never
    end up added in one place and forgotten in another."""
    notes: list[str] = []
    if defer_tools:
        notes.append(_SEARCH_TOOLS_NOTE)
    if not agent_instructions:
        return "\n\n".join(notes)
    return "\n\n".join([agent_instructions, *notes])


def _usage_event(usage: UsageMetadata) -> dict[str, Any]:
    """Build the "usage" WS event from a response's UsageMetadata,
    including prompt-cache stats when the provider reports them --
    real, user-reported gap: the token counter only ever showed
    total_tokens, with no way to tell whether prompt caching was
    actually reducing anything or a long, tool-heavy conversation was
    genuinely just that large. `input_token_details.cache_read` is
    langchain-core's own standard field for "how many of this call's
    input tokens were served from cache" -- populated for Anthropic
    (this project's own explicit cache_control breakpoints) *and*,
    unprompted by any coscribe code, for any OpenAI-compatible provider
    too (langchain_openai maps the raw API's own `prompt_tokens_details.
    cached_tokens` into this same field -- confirmed by reading its
    source directly). GLM specifically documents "implicit caching":
    automatic, no cache_control equivalent needed, so this field simply
    starts appearing once a call's prompt prefix repeats one already
    seen. Omitted (not sent as 0) when the provider didn't report it at
    all, so the frontend can distinguish "no cache activity reported"
    from "genuinely zero tokens were cached this call" -- the two read
    very differently to a user trying to tell whether caching is
    working at all. cache_hit_rate is computed here, not on the
    frontend, so every caller (there are three) gets the identical
    definition (cache_read / input_tokens, Codex CLI's own metric,
    chosen over total_tokens as the denominator since output tokens are
    never cacheable and would understate the real hit rate)."""
    event: dict[str, Any] = {"type": "usage", "total_tokens": usage["total_tokens"]}
    cache_read = usage.get("input_token_details", {}).get("cache_read")
    input_tokens = usage.get("input_tokens")
    if cache_read is not None and input_tokens:
        event["cache_read_tokens"] = cache_read
        event["input_tokens"] = input_tokens
        event["cache_hit_rate"] = cache_read / input_tokens
    return event


def _risks_of_gated_tools(lg_tools: list[Any]) -> dict[str, str]:
    """Every approval-gated tool's name -> its risk category, which the
    scheduled-run approval tiers key on (see _auto_approves)."""
    risks: dict[str, str] = {}
    for t in lg_tools:
        metadata = get_tool_metadata(cast(Any, t))
        if metadata.requires_approval:
            risks[tool_name(t)] = metadata.risk_category
    return risks


def _can_resolve_approvals(websocket: Any) -> bool:
    """True for a real client connection able to actually answer an
    approval_required prompt; False for a silent/background stand-in
    (runtime_lg/selfwake.py's _SilentSocket, driving an unattended
    sleep/wake or Scheduled Task turn) where nobody is present to ever
    resolve one. Checked via a duck-typed attribute, not isinstance --
    this module can't import runtime_lg back without a circular import
    (see runtime_lg/selfwake.py's own docstring). A real fastapi
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


# langchain_google_genai's gRPC channel accumulates something into the
# outgoing x-goog-api-client metadata header across repeated calls on the
# same long-lived channel -- an unresolved upstream bug in google-auth-
# library-python (see e.g. googleapis/python-aiplatform#3965,
# langchain-ai/langchain-google#306), not fixable from this codebase.
# Eventually the header outgrows grpc's default metadata soft limit and
# every further call on that channel fails with this exact message --
# waiting or retrying on the *same* channel never helps, since this
# session's ChatGoogleGenerativeAI client (and its channel) lives as long
# as the thread does. Forcing transport="rest" to dodge gRPC state
# entirely was already tried and reverted for a different real bug in
# this SDK version's async REST streaming path -- see providers.py's own
# comment. The narrow fix instead: recognize this one signature and
# recreate the client (a fresh channel starts the header at zero) before
# retrying.
_GRPC_METADATA_OVERFLOW_SIGNATURE = "metadata size exceeds soft limit"


# Shared between _handle_compact (which writes it as the first message of
# a fresh epoch) and send_history (which checks for it, below) -- lets
# send_history answer "might this thread have older history to page
# through" with an O(1) check on just the current checkpoint's own first
# message, instead of load_older_messages' own full backward walk (real,
# live-reported bug this avoids: the frontend used to offer "load older
# messages" unconditionally on *every* thread, including a brand new one
# with nothing to load).
_COMPACT_NOTE_PREFIX = "[Earlier conversation compacted to save context.]"

# Only appended when Settings.defer_tools is on (see _build_instructions
# below) -- the model has no other way to learn search_tools exists or
# when to reach for it, since most tools are hidden from its own tool
# list by default in that mode (see runtime_lg/tool_deferral.py).
_SEARCH_TOOLS_NOTE = (
    "Most tools beyond the basics aren't in your tool list yet -- this "
    "keeps this conversation's context small. If what you need isn't "
    "there (a PPTX/XLSX edit tool, a background script, a scheduled-task tool, "
    "etc.), call search_tools(query) first -- e.g. search_tools(\"pptx "
    "chart\") -- it makes any match callable by its real name starting "
    "with your very next tool call. Don't assume something can't be done "
    "just because you don't see a tool for it yet; search before giving up "
    "or falling back to a workaround."
)


class ChatSessionLG:
    def __init__(
        self,
        *,
        thread_id: str,
        settings: Settings,
        context_window_client: LLMClient,
        custom_providers: dict[str, dict[str, str]],
        extra_tools: list[Any],
        checkpointer: Any,
        hooks_config: dict[str, list[str]] | None = None,
        enabled_skill_names: set[str] | None = None,
        workspace_root: Path | None = None,
        workspace_explicit: bool = False,
    ) -> None:
        self.thread_id = thread_id
        self.settings = settings
        # None (the old default, still used by anything that hasn't been
        # taught about per-thread workspaces) falls back to the global
        # settings.workspace_root -- see coordinator.py's identical
        # fallback in build_coordinator_agent.
        self.workspace_root: Path = workspace_root or settings.workspace_root
        # True iff app.py's _resolve_workspace found a real per-thread
        # choice (sidecar file), not the settings.workspace_root fallback
        # -- select_workspace's "already set" guard needs this sentinel,
        # not a path comparison, since a user can legitimately choose the
        # same directory as the default (see _resolve_workspace's
        # docstring).
        self._workspace_explicit = workspace_explicit
        self._context_window_client = context_window_client
        self._context_window: int | None = None
        self._pending_approvals: dict[str, Future[bool]] = {}
        self._pending_questions: dict[str, Future[str]] = {}
        self.hooks_config: dict[str, list[str]] = hooks_config or empty_hooks_config()
        self.plan_mode = False
        self.accept_edits = False
        # Reset at the top of every handle_user_message call -- see
        # _stream_turn's docstring for why this exists (recovering a tool
        # call's arguments for the PostToolUse hook payload, which the
        # ToolMessage carrying its result doesn't itself include).
        self._pending_tool_args: dict[str, list[dict[str, Any]]] = defaultdict(list)
        # Cooperative /stop flag -- see request_stop's docstring. Reset at
        # the start of every handle_user_message call, same as
        # runtime/runner.py's threading.Event-based stop_event is cleared
        # at the start of every turn there.
        self._stop_requested = False
        # Most recent model-response segment's real usage_metadata (see
        # _stream_turn's _capture_segment_usage) -- not reset per-turn like
        # _pending_tool_args/_stop_requested above, since it's read once
        # after the whole turn (including any approval-resume continuation)
        # finishes, not checked mid-turn. None until the first real segment
        # completes, or if the active model never populates usage_metadata.
        self._last_usage_metadata: UsageMetadata | None = None
        # web/session.py's ws_endpoint dispatches each incoming
        # "user_message" via asyncio.create_task without awaiting it, so it
        # can go straight back to receiving the next one -- otherwise a
        # turn blocked on approval would freeze the whole socket, including
        # approval_response messages for that same turn (app.py's
        # ws_endpoint does the identical thing). Without this lock, a
        # second message sent while the first is still running would start
        # a fully concurrent second handle_user_message call against the
        # same self.lg_agent/self.config -- each mutating the same shared
        # instance state (_pending_tool_args, _stop_requested) and issuing
        # concurrent astream()/aget_state() calls against the identical
        # checkpointed thread, racing and interleaving their output. Same
        # lock ChatSession already has for the exact same reason; this
        # runtime just never had it ported over until a concurrency review
        # caught the gap. resolve_approval/request_stop are deliberately
        # NOT gated by this lock, same as ChatSession's identical methods
        # -- they have to be able to reach whichever turn is *currently*
        # in flight, not queue behind it.
        self._turn_lock = asyncio.Lock()
        # The task currently holding _turn_lock (handle_user_message or
        # resume_after_reconnect), if any -- see abandon_orphaned_turn's
        # docstring for why this exists: a turn lock alone would let an
        # orphaned task (one whose websocket died mid-approval-wait, so
        # nothing can ever resolve its pending Future) block every future
        # turn on this thread_id forever, including a genuine reconnect's
        # own attempt to redeliver that same pending approval.
        self._current_turn_task: asyncio.Task[None] | None = None
        # None = no /saveskill curation in progress; otherwise awaiting the
        # user's answer to a clarifying question or confirmation of a
        # proposed save -- see _handle_pending_skill_save_proposal.
        self.pending_save_skill_proposal: dict[str, Any] | None = None

        self.enabled_skill_names: set[str] = set(enabled_skill_names or ())
        agent = build_coordinator_agent(
            settings,
            thread_id,
            skill_names=self.enabled_skill_names,
            workspace_root=self.workspace_root,
        )
        # Unrestricted by enabled_skill_names deliberately -- a /<slug>
        # slash command is an explicit, one-off user request to follow
        # that skill for this message, distinct from the passive "is it
        # listed in instructions" toggle set_enabled_skills controls
        # below. Keyed by SkillInfo.slug (a single command-safe token,
        # e.g. "skill-creator"), not skill.name -- every current built-in
        # skill's display name has a space in it ("Skill Creator", "Word
        # Documents", ...), which a bare `.lower()` on the name can never
        # match against the single word _handle_user_message_locked below
        # actually parses out of "/<word> <rest>". Real, previously-
        # shipped-but-untested bug: the frontend's own autocomplete
        # (Composer.tsx's selectAutocomplete) inserted the literal display
        # name including its space, so selecting a multi-word skill from
        # the dropdown produced text this lookup could never match --
        # confirmed by reading through both sides together, not by a
        # live report. Distinct from web/app.py's *other*, separately-
        # scoped `skills_by_name` (the enable/disable toggle, correctly
        # keyed by display name -- that one was never broken).
        self.skills_by_slug = {
            skill.slug: skill
            for skill in load_builtin_skills() + load_skills(settings.skills_dir)
        }

        # Kept for switch_model, which needs to rebuild both the tool list
        # (spawn_agent/review_work are bound to a specific model) and the
        # compiled graph itself against a *new* model, reusing everything
        # else about this session unchanged (same checkpointer/thread_id,
        # same base tools, same instructions).
        self._checkpointer = checkpointer
        self._custom_providers = custom_providers
        self._base_tools: list[Callable[..., Any] | BaseTool] = list(agent.tools)
        # Kept separate from self._base_tools (not just concatenated once
        # here) because refresh_extra_tools below can replace this list
        # wholesale, independently, whenever an MCP connector connects/
        # disconnects live -- self._base_tools never needs rebuilding for
        # that. Combined back in by _build_lg_tools below (same treatment
        # spawn_agent/review_work already get).
        self._extra_tools: list[Callable[..., Any] | BaseTool] = list(extra_tools)
        self._instructions = _build_instructions(
            agent.instructions, defer_tools=settings.defer_tools
        )
        self._model_string = settings.default_model

        self.model = resolve_chat_model(self._model_string, custom_providers)
        lg_tools = self._build_lg_tools(self.model)
        # The *original* requires_approval set, computed independently of
        # what actually ends up in HumanInTheLoopMiddleware's interrupt_on
        # (which _build_lg_agent below may widen to include every tool, if
        # PreToolUse hooks are configured) -- plan mode needs to tell
        # "genuinely risky, blocked outright" apart from "only gated so a
        # hook gets a look," and this is the one place that distinction is
        # still visible. See _resolve_pending_approvals. Tool *names* don't
        # change across a model switch (same base tools, same spawn_agent/
        # review_work names every time), so this set is computed once here
        # and never needs recomputing anywhere else.
        self._gated_tool_risks = _risks_of_gated_tools(lg_tools)
        self.lg_agent = self._build_lg_agent(self.model, self._model_string, lg_tools)
        self.config = {"configurable": {"thread_id": thread_id}}
        # Pagination cursor for load_older_messages -- lazily set to
        # whatever is currently live the first time that method is
        # called, then advanced on every subsequent call. See that
        # method's own docstring for what these two actually track, and
        # why they're seeded lazily there rather than eagerly here.
        self._history_boundary_config: dict[str, Any] | None = None
        self._history_known_message_ids: set[str] | None = None
        # The currently-connected browser tab's websocket, if any -- set/
        # cleared by app.py's ws_endpoint around its own connection
        # lifetime, *not* threaded through every method call the way
        # every other websocket use in this class already is (see
        # notify_resync's own docstring for why this one specifically
        # needs a place to live outside any single call's stack).
        self._live_websocket: WebSocket | None = None
        # The prompt of a scheduled run executing on this thread right now
        # (set by runtime_lg/scheduled_tasks.py). A tab that opens the run
        # just after it starts can connect before that prompt is
        # checkpointed -- send_history fills it in so the run never shows
        # up without its opening message.
        self.active_run_prompt: str | None = None
        # The executing scheduled run's approval_mode, also set by
        # runtime_lg/scheduled_tasks.py -- see _auto_approves.
        self.run_approval_mode: str | None = None
        self._offer_task: asyncio.Task[None] | None = None

    def _build_lg_tools(self, model: Any) -> list[Callable[..., Any] | BaseTool]:
        # self._base_tools ("domain" tools) combined with self._extra_tools
        # (MCP connector tools -- see __init__'s comment on why these are
        # kept separate) *before* appending spawn_agent itself below -- a
        # sub-agent's own
        # selectable tool_names never includes spawn_agent, matching
        # tools/subagents.py's _DISALLOWED_SUBAGENT_TOOLS reasoning, and
        # does include MCP tools, same as the old runtime's cli.py (which
        # extends agent.tools with MCP tools before build_subagent_tools'
        # own available_tools=agent.tools call).
        combined_tools = [*self._base_tools, *self._extra_tools]
        spawn_agent_tool = build_spawn_agent_tool(model, combined_tools)
        spawn_agent_background_tool = build_spawn_agent_background_tool(
            model, combined_tools, self.thread_id, self.settings.state_dir
        )
        # The reviewer only ever gets read-only "documents" tools (read_docx/
        # read_pdf/search_pdf/read_xlsx/read_pptx today) -- independent
        # verification of a generated file's real content, never a way for
        # it to change anything itself. requires_approval is False for all
        # five, which is exactly why build_review_work_tool's own docstring
        # can promise its sub-agent never pauses on HumanInTheLoopMiddleware.
        reviewer_tools = [
            t
            for t in combined_tools
            if (metadata := get_tool_metadata(cast("Callable[..., Any]", t))).category
            == "documents"
            and not metadata.requires_approval
        ]
        review_work_tool = build_review_work_tool(model, reviewer_tools, self.settings.state_dir)
        return [
            *combined_tools,
            spawn_agent_tool,
            spawn_agent_background_tool,
            review_work_tool,
            self._build_draft_workflow_tool(),
        ]

    def _build_draft_workflow_tool(self) -> BaseTool:
        async def draft_workflow_tool(
            state: Annotated[dict[str, Any], InjectedState], name: str = ""
        ) -> str:
            # The graph's live state, not the checkpoint: mid-turn the
            # checkpoint doesn't hold this turn's tool calls yet.
            messages = list(state.get("messages", []))
            try:
                draft = await draft_workflow(
                    self.model, messages, self.workflow_context(None).tools, name.strip()
                )
            except DraftFailed as exc:
                return json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False)
            return json.dumps(
                {
                    "status": "drafted",
                    **draft.to_dict(),
                    "workspace": str(self.workspace_root) if self._workspace_explicit else None,
                    "next": "The user reviews this draft on a card and saves it as a task; "
                    "nothing is saved yet.",
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
                "conversation to have done the task with tools already. `name`: a short "
                "name for it, or empty to let the draft name itself."
            ),
        )

    def _build_lg_agent(
        self, model: Any, model_string: str, lg_tools: list[Callable[..., Any] | BaseTool]
    ) -> Any:
        # When PreToolUse hooks are configured, route *every* tool call
        # through the same interrupt point -- not just the ones that
        # already require human approval -- so a hook gets the chance to
        # veto a low-risk tool too. Mirrors runtime/policies.py's
        # HookToolPolicy, which wraps every tool call regardless of risk
        # level, not just RequireApprovalPolicy's gated subset.
        extra_interrupt_names = (
            [tool_name(t) for t in lg_tools] if self.hooks_config["PreToolUse"] else []
        )
        # `model_string` is taken as its own parameter rather than read from
        # self._model_string -- switch_model (below) calls this *before*
        # updating self._model_string (so a failed switch can revert
        # cleanly without ever having mutated it), so reading the field
        # here would silently compute auto_compact_tokens for the *old*
        # model on every switch. get_context_window is best-effort and
        # never raises (see LLMClient.get_context_window's own docstring),
        # so no try/except is needed around it here.
        auto_compact_tokens = max(
            1,
            int(
                self._context_window_client.get_context_window(model_string)
                * self.settings.auto_compact_threshold
            ),
        )
        return build_langgraph_agent(
            model,
            lg_tools,
            self._instructions,
            checkpointer=self._checkpointer,
            extra_interrupt_tool_names=extra_interrupt_names,
            question_tool_names=QUESTION_TOOL_NAMES | TASK_DRAFT_TOOL_NAMES,
            max_turns=self.settings.max_turns,
            auto_compact_tokens=auto_compact_tokens,
            defer_tools=self.settings.defer_tools,
            core_tool_names=CORE_TOOL_NAMES,
        )

    def resolve_approval(self, request_id: str, approved: bool) -> None:
        future = self._pending_approvals.get(request_id)
        if future is not None and not future.done():
            future.set_result(approved)

    def resolve_question(self, request_id: str, answer: str) -> None:
        future = self._pending_questions.get(request_id)
        if future is not None and not future.done():
            future.set_result(answer)

    def request_stop(self) -> None:
        """Called directly from ws_endpoint on a "stop" message, not
        awaited -- it has to reach whichever turn is *currently* running,
        same reasoning as resolve_approval. Mostly cooperative: sets a flag
        that _stream_turn/_decide_action_request check at their own natural
        yield points, mirroring runtime/runner.py's threading.Event-based
        stop_event (same cooperative-cancellation shape; this runtime's
        turn loop is a single async coroutine rather than a worker thread,
        so a plain flag serves the same purpose a thread-safe Event does
        there). Also immediately denies any tool call currently blocked on
        approval, so a stop can't get stuck waiting behind an approval
        prompt no one is going to answer -- the stop-flag checks elsewhere
        run between chunks/decisions, which a pending approval wait would
        otherwise never reach. A pending ask_user_question gets the same
        treatment, resolved with a placeholder answer rather than a bool --
        it's a "respond" decision, not approve/reject (see
        _decide_action_request's own docstring).

        Real, user-reported gap in the purely-cooperative design: none of
        the above helps when the turn isn't blocked on an approval/
        question at all, but stuck deep inside a single provider SDK call
        -- e.g. langchain_google_genai's own internal retry/backoff loop on
        a quota error, which can run for the better part of a minute
        without ever yielding a chunk for _stream_turn's stop-flag check to
        run against (see runtime_lg/providers.py's own comment on that same
        bug). Every other agent tool's Stop button reaches for the same
        fix in this situation -- bind directly to cancelling the actual
        in-flight call (AbortController in a JS fetch-based client,
        asyncio.Task.cancel() here) -- so this does too, but *only* when
        there's nothing pending to resolve instead: a pending approval/
        question means the turn is legitimately paused at a LangGraph
        interrupt() waiting on user input, and unblocking it via the
        futures above lets it unwind through its own normal
        Command(resume=...) path, preserving the checkpointer's state
        correctly. Hard-cancelling *that* case instead would skip straight
        past the resume path -- see abandon_orphaned_turn's own docstring,
        which reasons through exactly this for the WebSocket-disconnect
        case and deliberately avoids it there too."""
        self._stop_requested = True
        has_pending_interrupt = False
        for future in list(self._pending_approvals.values()):
            if not future.done():
                future.set_result(False)
                has_pending_interrupt = True
        for question_future in list(self._pending_questions.values()):
            if not question_future.done():
                question_future.set_result("(Stopped by user before answering.)")
                has_pending_interrupt = True
        if (
            not has_pending_interrupt
            and self._current_turn_task is not None
            and not self._current_turn_task.done()
        ):
            self._current_turn_task.cancel()

    def abandon_orphaned_turn(self) -> None:
        """Called from app.py's WebSocketDisconnect handler, not
        request_stop -- a real, previously-unhandled deadlock a concurrency
        review caught: _turn_lock (see __init__) means any turn still
        blocked on an approval when its websocket dies would otherwise hang
        onto that lock forever, since nothing can ever resolve a pending
        Future once the socket that would carry its approval_response is
        gone -- silently blocking every future turn on this thread_id too,
        including a genuine reconnect's own resume_after_reconnect trying
        to redeliver that exact same pending approval to a fresh
        connection.

        Deliberately does NOT call request_stop(): that denies the pending
        approval (a real decision, resumed against the checkpointer via
        Command(resume=...)) and would consume the very interrupt a
        reconnect is supposed to redeliver *untouched* -- see
        runtime_lg/README.md's reconnect/restart verdict, the whole reason
        a persistent checkpointer was chosen over InMemorySaver in the
        first place. Hard-cancels whichever task currently holds
        _turn_lock instead: cancellation unwinds that task without ever
        reaching a Command(resume=...) call, so the checkpointer's real
        pending state is left exactly as it was. The lock still gets
        released correctly either way, since `async with` releases on
        cancellation the same as on any other exception."""
        if self._current_turn_task is not None and not self._current_turn_task.done():
            self._current_turn_task.cancel()

    async def switch_model(self, model: str, websocket: WebSocket) -> None:
        """Change this thread's active model immediately, mid-session, no
        restart, no reconnect -- mirrors ChatSession.switch_model's public
        contract exactly, but the mechanism is necessarily different:
        Runner re-reads agent.model fresh every turn, so the old runtime
        just mutates a string in place. create_agent() bakes the model into
        a *compiled graph* once, so this rebuilds that graph instead
        (self.lg_agent), reusing everything else about the session
        unchanged -- same checkpointer, same thread_id/config, same base
        tools, same instructions. Rebinding a new compiled graph object to
        the *same* checkpointer/thread_id is safe even if a turn is
        mid-approval when this is called: HumanInTheLoopMiddleware's
        interrupt_on set is derived purely from tool *names*/hooks
        configuration, both unchanged by a model switch, and LangGraph
        resumes from checkpointed state, not from the compiled graph
        object's own identity.

        Only a basic "provider:model" shape check happens here; a
        genuinely bad choice (unknown provider prefix, or a provider whose
        SDK validates eagerly) is caught by resolve_chat_model/
        get_context_window below, both of which can raise -- reverted on
        that failure rather than leaving the thread pinned to a model that
        can never actually be used.

        self._custom_providers is refreshed from disk right here, not
        trusted as-is -- it's otherwise only a startup-time snapshot
        (_get_session in web/app.py loads it once, when a thread's session
        object is first created, and never again for that thread's
        lifetime). Without this, adding a custom provider via the
        Providers tab in an *already-open* thread and then immediately
        trying to switch to it here would raise resolve_chat_model's own
        "Unsupported provider" -- a real, live-reported confusion (the
        provider genuinely is configured; this session object just hadn't
        heard about it yet), not a sign DeepSeek/Kimi/GLM/etc. don't work.
        A plain small JSON read, cheap enough to redo on every switch
        rather than add a second cache to keep in sync."""
        if ":" not in model:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f'model must be a "provider:model" string, got {model!r}',
                }
            )
            return
        previous_model = self.model
        previous_lg_agent = self.lg_agent
        previous_model_string = self._model_string
        self._context_window = None
        try:
            if self.settings.providers_config_path is not None:
                self._custom_providers = load_custom_providers(self.settings.providers_config_path)
            new_model = resolve_chat_model(model, self._custom_providers)
            lg_tools = self._build_lg_tools(new_model)
            new_lg_agent = self._build_lg_agent(new_model, model, lg_tools)
            self.model = new_model
            self.lg_agent = new_lg_agent
            self._model_string = model
            await self.send_state(websocket)
        except Exception as exc:  # noqa: BLE001 -- a bad/misconfigured provider must not corrupt session state
            self.model = previous_model
            self.lg_agent = previous_lg_agent
            self._model_string = previous_model_string
            self._context_window = None
            await websocket.send_json(
                {"type": "error", "message": f"Could not switch to {model!r}: {exc}"}
            )

    async def refresh_extra_tools(self, extra_tools: list[Any]) -> None:
        """Rebuild self.lg_agent so this *already-open* thread picks up an
        MCP connector added/removed/version-bumped after this session was
        first created -- real, live-reported bug: __init__ takes a one-time
        snapshot of extra_tools_holder["tools"] (list(extra_tools), see its
        own comment), so before this method existed, connecting a new
        connector only ever affected the *next new* thread_id;
        already-open conversations kept whatever MCP tools existed at
        session-creation time until the process restarted. app.py calls
        this on every currently-open ChatSessionLG right after
        add_mcp_server/remove_mcp_server/bump_mcp_server_version succeeds,
        mirroring switch_model's "rebuild the compiled graph in place, same
        checkpointer/thread_id" shape.

        No websocket parameter, unlike switch_model: this
        runs from a REST endpoint handler, not in response to a message
        from *this* thread's own client, so there is nothing to reply to
        and no previous-state revert to report -- a failure here (e.g. a
        newly-added MCP tool with a schema create_agent rejects) is logged
        and this session's tools are simply left as they were, same
        "don't corrupt a working session" posture as switch_model's own
        except-and-revert branch, just without a websocket to notify.
        Guarded by _turn_lock so this can't race a concurrent
        handle_user_message call on the same session's self.lg_agent/
        self.config, same reasoning as resume_after_reconnect above."""
        async with self._turn_lock:
            self._extra_tools = list(extra_tools)
            try:
                lg_tools = self._build_lg_tools(self.model)
                new_lg_agent = self._build_lg_agent(self.model, self._model_string, lg_tools)
            except Exception:  # noqa: BLE001 -- a bad connector must not corrupt this session
                logger.exception(
                    "refresh_extra_tools: failed to rebuild lg_agent for thread %r, "
                    "keeping its previous tool set",
                    self.thread_id,
                )
                return
            self.lg_agent = new_lg_agent
            self._gated_tool_risks = _risks_of_gated_tools(lg_tools)

    async def select_workspace(self, path: str, websocket: WebSocket) -> bool:
        """The WS-message counterpart to passing ?workspace= at connect
        time (see app.py's ws_endpoint/_resolve_workspace) -- lets the
        new-session workspace picker fire *after* the socket is already
        open (the picker only shows once the first "state" event confirms
        this is a genuinely fresh thread, by which point _get_session
        already built this session against the fallback
        settings.workspace_root).

        Rebuilds self._base_tools/self.lg_agent from scratch via
        build_coordinator_agent with the new root, same revert-on-failure
        shape as set_enabled_skills below (a bad path -- e.g. one that
        can't be created -- must not corrupt an otherwise-working
        session).

        Returns whether the switch actually took effect -- unlike
        set_enabled_skills (whose caller in app.py's ws_endpoint always
        persists the sidecar unconditionally), a rejected workspace
        change must NOT have its (different, unapplied) path written to
        the sidecar --
        that would desync the sidecar from this session's actual
        in-memory workspace_root, and a later reconnect reading the
        sidecar fresh would try to rebuild against the bad/rejected path
        with no revert-on-failure safety net at that point (a startup
        crash, not a soft in-turn error). The caller only writes the
        sidecar when this returns True."""
        if self._workspace_explicit:
            await websocket.send_json(
                {"type": "error", "message": "This thread already has a workspace set."}
            )
            return False
        previous_workspace_root = self.workspace_root
        previous_instructions = self._instructions
        previous_base_tools = self._base_tools
        previous_lg_agent = self.lg_agent
        previous_gated_tool_risks = self._gated_tool_risks
        self._context_window = None
        try:
            # Unlike set_enabled_skills, build_coordinator_agent itself is
            # inside this try -- a folder a WorkspaceScope can't mkdir into
            # (permission denied, invalid path syntax) is a real, easily
            # user-triggered failure mode for a folder-picker-driven path
            # in a way a skill lookup rarely is, and it must be caught
            # here rather than crashing the WS message loop.
            self.workspace_root = Path(path)
            agent = build_coordinator_agent(
                self.settings,
                self.thread_id,
                skill_names=self.enabled_skill_names,
                workspace_root=self.workspace_root,
            )
            self._instructions = _build_instructions(
                agent.instructions, defer_tools=self.settings.defer_tools
            )
            self._base_tools = list(agent.tools)
            lg_tools = self._build_lg_tools(self.model)
            new_gated_tool_risks = _risks_of_gated_tools(lg_tools)
            new_lg_agent = self._build_lg_agent(self.model, self._model_string, lg_tools)
        except Exception as exc:  # noqa: BLE001 -- a bad workspace path must not corrupt session state
            self.workspace_root = previous_workspace_root
            self._instructions = previous_instructions
            self._base_tools = previous_base_tools
            self.lg_agent = previous_lg_agent
            self._gated_tool_risks = previous_gated_tool_risks
            self._context_window = None
            await websocket.send_json(
                {"type": "error", "message": f"Could not switch to workspace {path!r}: {exc}"}
            )
            return False
        self.lg_agent = new_lg_agent
        self._gated_tool_risks = new_gated_tool_risks
        self._workspace_explicit = True
        await self.send_state(websocket)
        return True

    async def set_enabled_skills(self, skill_names: set[str], websocket: WebSocket) -> None:
        """Toggle this thread's active built-in/local skills, live,
        mid-session -- deliberately with NO "once only" guard: the user
        asked for selectable-anytime toggling, like Claude Code's own
        skills. Every call fully replaces the enabled set and rebuilds
        self._instructions/self._base_tools from scratch via
        build_coordinator_agent, rather than accumulating skill text onto
        whatever was there from a previous toggle -- so turning a skill
        back off actually removes its listing, not just stops adding
        more.

        Same revert-on-failure shape as switch_model: a bad rebuild must
        not corrupt an otherwise-working session."""
        previous_instructions = self._instructions
        previous_base_tools = self._base_tools
        previous_lg_agent = self.lg_agent
        previous_gated_tool_risks = self._gated_tool_risks
        previous_enabled_skill_names = self.enabled_skill_names
        self.enabled_skill_names = set(skill_names)
        agent = build_coordinator_agent(
            self.settings, self.thread_id, skill_names=self.enabled_skill_names
        )
        self._instructions = _build_instructions(
            agent.instructions, defer_tools=self.settings.defer_tools
        )
        self._base_tools = list(agent.tools)
        self._context_window = None
        try:
            lg_tools = self._build_lg_tools(self.model)
            new_gated_tool_risks = _risks_of_gated_tools(lg_tools)
            new_lg_agent = self._build_lg_agent(self.model, self._model_string, lg_tools)
        except Exception as exc:  # noqa: BLE001 -- a bad rebuild must not corrupt session state
            self._instructions = previous_instructions
            self._base_tools = previous_base_tools
            self.lg_agent = previous_lg_agent
            self._gated_tool_risks = previous_gated_tool_risks
            self.enabled_skill_names = previous_enabled_skill_names
            self._context_window = None
            await websocket.send_json(
                {"type": "error", "message": f"Could not update skills: {exc}"}
            )
            return
        self.lg_agent = new_lg_agent
        self._gated_tool_risks = new_gated_tool_risks
        # Also re-scans disk for skills_by_slug (the /<slug> force-load
        # lookup, unrestricted by enabled_skill_names -- see its own
        # comment in __init__) -- cheap, and keeps it correctly in sync
        # with whatever's actually on disk rather than a stale __init__-
        # time snapshot. Exists mainly for /saveskill's own confirm
        # handler below, which calls this method specifically so a
        # freshly-saved skill is usable via /<slug> in this same session
        # immediately, without waiting for a reconnect.
        self.skills_by_slug = {
            skill.slug: skill
            for skill in load_builtin_skills() + load_skills(self.settings.skills_dir)
        }
        await self.send_state(websocket)

    async def send_state(self, websocket: WebSocket, *, on_connect: bool = False) -> None:
        if self._context_window is None:
            self._context_window = await asyncio.to_thread(
                self._context_window_client.get_context_window, self._model_string
            )
        state: dict[str, Any] = {
            "type": "state",
            "plan_mode": self.plan_mode,
            "accept_edits": self.accept_edits,
            "model": self._model_string,
            "context_window": self._context_window,
            "enabled_skills": sorted(self.enabled_skill_names),
            "workspace_root": str(self.workspace_root),
            "workspace_explicit": self._workspace_explicit,
        }
        if on_connect:
            # A tab opening mid-turn (most often: a scheduled run executing
            # in the background) needs to show that turn as running, with
            # Stop available. Connect-time only: a state sent from *inside*
            # a turn (/plan, a model switch) holds the lock itself.
            state["turn_in_flight"] = self._turn_lock.locked()
        await websocket.send_json(state)

    def workspace_scope(self) -> WorkspaceScope:
        return WorkspaceScope(
            self.workspace_root,
            extra_readable=self.settings.extra_readable_dirs,
            extra_writable=self.settings.extra_writable_dirs,
        )

    def workflow_context(self, model: str | None) -> StepContext:
        """What a workflow's steps run with: this thread's own tools and
        workspace scope, and `model` (else this session's) for model steps."""
        # A connector's tools are LangChain tools; the other LangChain tools
        # here (spawn_agent, review_work) need a running agent around them.
        tools: dict[str, Any] = {
            tool_name(t): t
            for t in [*self._base_tools, *self._extra_tools]
            if not isinstance(t, BaseTool)
            or (get_tool_metadata(cast(Any, t)).category or "").startswith("mcp:")
        }

        def make_model(step_model: str | None) -> Any:
            return resolve_chat_model(
                step_model or model or self._model_string, self._custom_providers
            )

        return StepContext(
            tools=tools,
            workspace_root=Path(self.workspace_root),
            state_dir=Path(self.settings.state_dir),
            make_model=make_model,
        )

    async def draft_workflow(self, name_hint: str = "") -> WorkflowDraft:
        """A workflow draft distilled from this conversation, for the person
        to review before it's saved anywhere."""
        if self.is_workflow_run():
            raise DraftFailed("This is already a workflow run.")
        if self._turn_lock.locked():
            raise DraftFailed("Wait for the current reply to finish first.")
        state = await self.lg_agent.aget_state(self.config)
        if state.next:
            raise DraftFailed("Resolve the pending approval first.")
        messages = list(state.values.get("messages", [])) if state.values else []
        return await draft_workflow(
            self.model, messages, self.workflow_context(None).tools, name_hint
        )

    @property
    def checkpointer(self) -> Any:
        return self._checkpointer

    def is_workflow_run(self) -> bool:
        """This thread's checkpoints belong to a workflow's graph, not this
        session's agent -- driving the agent on them (resuming a pending
        interrupt, a new turn) would feed one graph's state to another."""
        parsed = parse_run_thread_id(self.thread_id)
        if parsed is None:
            return False
        trigger = ScheduledTriggerStore(self.settings.state_dir).load(parsed[0])
        return trigger is not None and trigger.workflow is not None

    async def get_activity(self) -> dict[str, Any]:
        catalog = {
            tool_name(t): get_tool_metadata(cast(Any, t))
            for t in [*self._base_tools, *self._extra_tools]
        }
        parsed = parse_run_thread_id(self.thread_id)
        if parsed is not None:
            trigger = ScheduledTriggerStore(self.settings.state_dir).load(parsed[0])
            run = trigger.find_run(parsed[1]) if trigger is not None else None
            if trigger is not None and trigger.workflow is not None and run is not None:
                return summarize_workflow_run(
                    trigger.workflow, run, catalog, self.workspace_scope()
                )
        state = await self.lg_agent.aget_state(self.config)
        messages = list(state.values.get("messages", [])) if state.values else []
        return summarize_activity(messages, catalog, self.workspace_scope())

    async def get_context_breakdown(self) -> dict[str, Any]:
        """Where this thread's context-window budget actually goes --
        backs the context-usage panel (ContextRing.tsx's expanded view).
        See context_usage.py's own module docstring for the measurement
        approach and its documented approximation.

        Recomputes the currently-enabled skills' own listing text fresh
        (rather than trying to re-extract it from the already-concatenated
        self._instructions) via the exact same load-then-filter-by-name
        expression __init__/set_enabled_skills/switch_model already use
        to build self._instructions in the first place -- guaranteed to
        match, since self.enabled_skill_names is the one shared input
        every one of those call sites feeds into build_coordinator_agent's
        own identical filter."""
        if self._context_window is None:
            self._context_window = await asyncio.to_thread(
                self._context_window_client.get_context_window, self._model_string
            )
        skills = [
            skill
            for skill in load_builtin_skills() + load_skills(self.settings.skills_dir)
            if skill.name in self.enabled_skill_names
        ]
        skills_listing = format_skill_listing(skills) if skills else ""
        last_total = (
            self._last_usage_metadata["total_tokens"] if self._last_usage_metadata else None
        )
        return await asyncio.to_thread(
            build_context_breakdown,
            instructions=self._instructions,
            skills_listing=skills_listing,
            base_tools=list(self._base_tools),
            mcp_tools=list(self._extra_tools),
            context_window=self._context_window or 0,
            auto_compact_threshold=self.settings.auto_compact_threshold,
            last_total_tokens=last_total,
        )

    async def notify_resync(self) -> None:
        """Tell whichever browser tab is currently watching this thread
        (if any) that something changed server-side outside of its own
        turn -- a background wake/scheduled-task/sub-agent resuming this
        thread while nobody was actively typing into it. Real, previously-
        documented gap this closes: a poller-triggered resume wrote
        correctly to the checkpointer but never appeared in an already-
        open tab until its next reload/reconnect (ROADMAP.md).

        Deliberately just re-sends the exact same "history" event
        send_history already sends on every fresh connection, rather than
        inventing a new WS message type/frontend handler -- the frontend's
        "history" reducer case already replaces state.items wholesale
        with the authoritative replay (see reducer.ts), which is exactly
        "go re-fetch the now-correct state," and reusing it means zero
        new frontend wiring. This is the "stored state + resync" shape
        this feature's own design discussion found Claude Code's own
        Remote Control (stored transcript, reconnect/resync) and
        claude-code-best's goal service (plain status flag, no event
        emitter) both already use, rather than a full multi-listener
        broadcast bus -- this app only ever has one browser tab watching
        a given thread at a time in practice, so there's nothing more to
        fan out to.

        Silently does nothing if no tab is currently connected (the
        common case for an unattended wake) -- there's nothing to nudge.
        Best-effort: a dead/closing socket's send can raise, and a missed
        resync is recoverable (the next reconnect sees correct state
        anyway), not worth failing whatever background work triggered
        this over."""
        websocket = self._live_websocket
        if websocket is None:
            return
        try:
            await self.send_history(websocket)
        except Exception:
            logger.debug("notify_resync: failed to notify the live tab for %s", self.thread_id)

    async def send_history(self, websocket: WebSocket) -> None:
        """One-shot replay of this thread's checkpointed messages, sent
        once right after send_state on every fresh WS connection (see
        app.py's ws_endpoint) -- fixes a real, live-reported bug: the
        chat log was otherwise only ever built up from live events during
        the *current* connection, so switching to (or reconnecting to) an
        existing thread showed a blank pane even though the model still
        remembered the whole conversation. See serialize_history_for_ws_lg
        for the entry shape and what's deliberately not perfectly handled
        (a /compact'd thread's synthetic summary message).

        A brand-new thread has no checkpointed messages yet, so this is a
        harmless no-op "history" event with an empty list -- no special
        casing needed for the new-thread path."""
        state = await self.lg_agent.aget_state(self.config)
        messages = list(state.values.get("messages", [])) if state.values else []
        # O(1) proxy for "does load_older_messages have anything to find"
        # -- a real full-history walk (that method's own, correct way to
        # know for sure) is too expensive to do unconditionally on every
        # connect; see _COMPACT_NOTE_PREFIX's own comment for why this
        # check is safe and accurate in practice.
        first_message = messages[0] if messages else None
        has_older = isinstance(first_message, HumanMessage) and str(
            first_message.content
        ).startswith(_COMPACT_NOTE_PREFIX)
        entries = serialize_history_for_ws_lg(messages)
        prompt = self.active_run_prompt
        if prompt is not None and not any(
            e["kind"] == "user" and e["text"] == prompt for e in entries
        ):
            entries.append({"kind": "user", "text": prompt})
        await websocket.send_json({"type": "history", "entries": entries, "has_older": has_older})

    async def load_older_messages(self, websocket: WebSocket) -> None:
        """Scroll-up pagination for a thread that's been /compact'd: send_
        history above only ever returns the *current* checkpoint's message
        list, which right after a compaction is just the synthetic summary
        note -- real, live-reported complaint that the earlier conversation
        then looked permanently gone, with no way to scroll up and see it,
        unlike every other AI product the user had used. The underlying
        messages aren't actually gone: LangGraph's checkpointer is append-
        only/versioned (aupdate_state's RemoveMessage(id=REMOVE_ALL_
        MESSAGES) sentinel writes a *new* checkpoint, it doesn't delete the
        old ones) -- this was purely a read-side gap, nothing ever walked
        aget_state_history to read them back.

        Mechanism, verified empirically against a real LangGraph graph
        before wiring this in (a compacted thread's checkpoint sequence
        behaves exactly as follows): within one "epoch" (the stretch of
        checkpoints between two RemoveMessage(ALL) resets, or since the
        thread's start), the messages list only ever grows -- the
        add_messages reducer appends, so every older checkpoint's own id
        set is a *subset* of the current one's. The moment a walk backward
        crosses a RemoveMessage(ALL) boundary, that breaks: the checkpoint
        immediately on the other side holds the *previous* epoch's own
        final, fully-accumulated message list, entirely disjoint from
        anything already known. So: walk aget_state_history backward from
        `_history_boundary_config` and return the first checkpoint whose
        message ids aren't already a subset of `_history_known_message_
        ids` -- that's exactly the previous epoch's own full message
        list, in one shot, not a partial slice of it. Advances both the
        cursor and the known-ids set so a second "load older" click
        (multiple compactions in one thread) correctly skips back past
        the whole batch just revealed instead of re-finding it.

        The cursor is lazily initialized to *whatever is currently live*
        on this method's own first call, never to send_history's
        connect-time snapshot -- real bug caught by this fix's own tests:
        seeding it once in send_history left the cursor frozen at
        connect time (typically an empty thread), so every message sent
        *after* connecting -- the entire conversation a real user would
        ever want to page back through -- looked like it was "before"
        that stale cursor and never got walked at all. Recomputing here
        instead means the boundary always reflects everything the
        frontend has already rendered by the time the user actually
        clicks "load older" (send_history's own dump, plus every live
        turn since), which is the only version of "already known" that's
        actually correct.

        Real cost tradeoff, not free: for a thread that was *never*
        compacted, every earlier checkpoint is always a subset of the
        current one, so this walks the thread's *entire* checkpoint
        history before concluding there's nothing older -- acceptable
        because the frontend only calls this once per thread-view (on
        first scroll-to-top) and remembers has_more=False afterward, not
        because the walk itself is bounded."""
        if self._history_boundary_config is None or self._history_known_message_ids is None:
            current_state = await self.lg_agent.aget_state(self.config)
            self._history_boundary_config = current_state.config
            self._history_known_message_ids = (
                {m.id for m in current_state.values.get("messages", [])}
                if current_state.values
                else set()
            )

        older: list[Any] = []
        found_config: dict[str, Any] | None = None
        async for snapshot in self.lg_agent.aget_state_history(
            self.config, before=self._history_boundary_config
        ):
            snapshot_messages = list(snapshot.values.get("messages", [])) if snapshot.values else []
            snapshot_ids = {m.id for m in snapshot_messages}
            if not snapshot_ids <= self._history_known_message_ids:
                older = snapshot_messages
                found_config = snapshot.config
                break

        if found_config is not None:
            self._history_boundary_config = found_config
            self._history_known_message_ids |= {m.id for m in older}
        # has_more is a hint to keep offering "load older", not a real
        # lookahead -- true whenever this call itself found a batch, even
        # on the very last one (confirming there's truly nothing earlier
        # would mean walking one epoch further just to check, every time).
        # An empty result is the one unambiguous "nothing left" signal;
        # the frontend is expected to stop offering it only then, not on
        # has_more alone.
        await websocket.send_json(
            {
                "type": "older_messages",
                "entries": serialize_history_for_ws_lg(older),
                "has_more": bool(older),
            }
        )

    async def _stream_turn(self, turn_input: Any, websocket: WebSocket) -> str:
        """Runs one astream() call, forwarding the model's own narration as
        "agent_delta" events and each tool's return value as "tool_result",
        same wire shape as ChatSession's _on_delta/_post_tool_use. Returns
        only the *last* model response's own narration text -- not every
        response of this call concatenated -- reset (text_parts.clear())
        at each ToolMessage boundary, the same point _capture_segment_usage
        already resets `segment` at. A single astream() call can cover more
        than one model response when it includes an *ungated* tool call
        (the loop just keeps going past the ToolMessage); without the reset,
        this method's return value would glue the pre-tool narration onto
        the post-tool one with no separator, even though the pre-tool text
        already streamed live as its own "agent_delta" bubble.

        The gated case has the identical join done differently: this method's
        own astream() loop ends *without* a ToolMessage the moment the model
        proposes a call that needs approval (the interrupt fires first), so
        its return value here is already just the pre-tool narration, single-
        segment; the risk there is a *caller* joining this call's return
        value with a later _resolve_pending_approvals one -- see that
        method's own docstring for the matching half of this fix.

        Also recovers each tool call's arguments for the PostToolUse hook
        payload below -- a ToolMessage only carries a tool's result, not the
        arguments it was called with, so this accumulates the preceding
        AIMessageChunk stream (stream_mode=["messages"] yields incremental
        deltas, merged here via AIMessageChunk's own __add__) into a full
        AIMessage and reads its .tool_calls. Flushed into
        self._pending_tool_args in two places: right before a ToolMessage
        that arrived in the *same* call (the common, non-gated-tool case),
        and again unconditionally once the astream loop ends (the
        approval-gated case -- the AIMessage proposing a gated call is
        always fully streamed *before* the interrupt fires, since
        interrupt() runs in after_model once the AIMessage already exists,
        but the interrupt then ends this call's astream loop with no
        ToolMessage ever arriving in it; the flush at the end is what
        carries those args over to the *next* _stream_turn call --
        _resolve_pending_approvals's post-resume one -- where the
        ToolMessage for that call actually shows up). registered_ids
        prevents the shared end-of-loop flush from double-registering a
        call already flushed by the inline branch above it.

        Checks self._stop_requested after every chunk (see request_stop's
        docstring) -- cooperative, so it can't interrupt a single model
        call already in flight (same limitation the old runtime's
        stop_event has for any non-token-streaming provider), but it stops
        consuming *further* chunks/turns promptly. Explicitly closes the
        astream() generator on the way out (in a finally, whether the loop
        ended naturally or via a stop-triggered break) rather than just
        letting it fall out of scope -- an abandoned, ungarbage-collected
        async generator keeps running in the background instead of
        propagating GeneratorExit into LangGraph's own execution.

        Also tracks `self._last_usage_metadata`: a *separate* accumulator
        (`segment`, distinct from `accumulated` above) that resets at each
        ToolMessage boundary, so it only ever sums the chunks of the single
        most recent model response -- not the whole multi-response turn,
        which would double-count (each individual response's own
        usage_metadata.total_tokens already reflects the *cumulative*
        context size at that point, since Gemini/Anthropic/OpenAI all
        report "tokens in the whole prompt this call sent" as input_tokens,
        not just what's new since the last call). Verified live against
        real Gemini through this exact astream(stream_mode=["messages"])
        path, including a real tool call in between two model responses --
        confirmed each individual response's accumulated usage_metadata is
        correct (not the all-zero result an earlier, older
        langchain-google-genai version returned, see runtime_lg/README.md's
        "usage bar" section for the fix and how the earlier finding was
        superseded by an upstream fix in that package). Left as None (no
        WS event sent) if a response never populates usage_metadata at all
        -- some providers/configurations still don't; see that same
        section for langchain-openai's stream_usage caveat.

        _capture_segment_usage also sends a live "usage" WS event itself,
        at every boundary it runs at (each ToolMessage, plus once more at
        this method's own return) -- not only from the two post-turn sends
        the callers below already do once _stream_turn returns. Drives the
        composer's live running-token counter (ContextRing gets the same
        event) across a long, multi-tool-call turn instead of only
        updating once the whole turn is over."""
        agent = self.lg_agent
        config = self.config
        if not isinstance(turn_input, Command):
            await _close_orphaned_tool_calls(agent, config)
        text_parts: list[str] = []
        accumulated: AIMessageChunk | None = None
        segment: AIMessageChunk | None = None
        registered_ids: set[str] = set()

        def _flush_accumulated() -> None:
            nonlocal accumulated
            if accumulated is None:
                return
            for call in accumulated.tool_calls:
                call_id = call["id"]
                if call_id is not None and call_id not in registered_ids:
                    self._pending_tool_args[call["name"]].append(call["args"])
                    registered_ids.add(call_id)

        async def _capture_segment_usage() -> None:
            nonlocal segment
            if segment is not None and segment.usage_metadata:
                self._last_usage_metadata = segment.usage_metadata
                # Live, not just at turn-end: a multi-step turn (tool call,
                # then another model response) previously only told the
                # frontend the token count once the *whole* turn finished
                # (see the callers' own "usage" sends after _stream_turn
                # returns) -- nothing to drive a live-updating counter with
                # while a long turn is still running. Each individual
                # response's usage_metadata.total_tokens is already the
                # cumulative context size at that point (see this method's
                # own docstring), so sending it here, at every boundary this
                # function already runs at, is a correct running total, not
                # an approximation.
                await websocket.send_json(_usage_event(segment.usage_metadata))
            segment = None

        stream = agent.astream(turn_input, config=config, stream_mode=["messages"])
        try:
            async for _mode, chunk in stream:
                message, _metadata = chunk
                if isinstance(message, AIMessageChunk):
                    text = _extract_text(message.content)
                    if text:
                        text_parts.append(text)
                        await websocket.send_json({"type": "agent_delta", "text": text})
                    accumulated = message if accumulated is None else accumulated + message
                    segment = message if segment is None else segment + message
                elif isinstance(message, ToolMessage):
                    _flush_accumulated()
                    await _capture_segment_usage()
                    # A tool call (gated or not) ends the model's current
                    # response -- discard whatever narration text_parts
                    # accumulated for it, same boundary _capture_segment_usage
                    # already resets `segment` at. That narration already
                    # streamed live as its own "agent_delta" bubble; without
                    # this reset, this call's eventual return value would glue
                    # it onto whatever the model says *after* the tool result
                    # comes back, with no separator -- a real bug found live
                    # (see this method's own docstring).
                    text_parts.clear()
                    name = message.name or ""
                    result_value = _tool_result_value(message.content)
                    args = (
                        self._pending_tool_args[name].pop(0)
                        if self._pending_tool_args[name]
                        else {}
                    )
                    await self._run_post_tool_use_hooks(name, args, result_value)
                    # ask_user_question's own "question"/"answered" card (sent by
                    # _decide_question_request as question_required, resolved locally
                    # on answer -- see reducer.ts's "question_required"/
                    # "local_question_answered" cases) already fully represents this
                    # call; sending the normal tool_result here too would additionally
                    # fall into the reducer's tool_result fallback branch (no matching
                    # pending "tool" item exists, since question tools never got a
                    # "tool_call" one) and add a second, redundant "Asked: ..." row
                    # underneath the card for the same interaction.
                    if name not in QUESTION_TOOL_NAMES and name not in TASK_DRAFT_TOOL_NAMES:
                        # `args` (recovered above, same value the hook just got)
                        # rides along so a live turn's collapsed row can show the
                        # real filename/query -- without this the frontend only
                        # ever learns the actual arguments for a *gated* tool
                        # (via approval_required) or a *replayed* one (via
                        # history's own checkpointed entry.arguments); an
                        # ungated tool completing in a live turn had no event
                        # carrying arguments at all until now, so its row always
                        # fell back to generic phrasing ("Read a file" instead
                        # of "Read notes.txt").
                        await websocket.send_json(
                            {
                                "type": "tool_result",
                                "tool_name": name,
                                "arguments": args,
                                "result": result_value,
                                "is_error": message.status == "error",
                            }
                        )
                elif isinstance(message, AIMessage):
                    # Real, live-caught gap: a middleware-injected terminal
                    # message (e.g. ModelCallLimitMiddleware's
                    # exit_behavior="end" AIMessage, added directly via a
                    # before_model hook's return dict rather than an actual
                    # model call) never goes through _generate/_stream, so
                    # stream_mode=["messages"] emits it as one complete
                    # AIMessage instead of the usual AIMessageChunk deltas
                    # -- the `isinstance(message, AIMessageChunk)` branch
                    # above never matches it (AIMessageChunk is a subclass
                    # of AIMessage, not the reverse), so without this branch
                    # its content was silently dropped and the turn ended
                    # with an empty "[no reply -- ...]" instead of telling
                    # the user *why* it stopped. Confirmed live: printing
                    # the raw astream(stream_mode=["messages"]) output for a
                    # max_turns-capped run shows this exact message arriving
                    # as a plain AIMessage, not a chunk.
                    text = _extract_text(message.content)
                    if text:
                        text_parts.append(text)
                        await websocket.send_json({"type": "agent_delta", "text": text})
                if self._stop_requested:
                    break
        finally:
            await stream.aclose()
        _flush_accumulated()
        await _capture_segment_usage()
        return "".join(text_parts)

    async def _run_pre_tool_use_hooks(self, name: str, args: dict[str, Any]) -> str | None:
        """Returns a denial reason if any configured PreToolUse hook vetoes
        this call, None if all pass (or none are configured). run_hook is a
        blocking subprocess call, bridged off the event loop the same way
        every other blocking call in this module is."""
        if not self.hooks_config["PreToolUse"]:
            return None
        payload = {
            "event": "PreToolUse",
            "tool_name": name,
            "arguments": args,
            "agent_name": "coordinator",
        }
        for command in self.hooks_config["PreToolUse"]:
            result = await asyncio.to_thread(run_hook, command, payload)
            if not result.allowed:
                return result.reason
        return None

    async def _run_post_tool_use_hooks(self, name: str, args: dict[str, Any], result: Any) -> None:
        if not self.hooks_config["PostToolUse"]:
            return
        payload = {
            "event": "PostToolUse",
            "tool_name": name,
            "arguments": args,
            "agent_name": "coordinator",
            "result": result,
        }
        for command in self.hooks_config["PostToolUse"]:
            outcome = await asyncio.to_thread(run_hook, command, payload)
            if not outcome.allowed:
                logger.warning("PostToolUse hook failed: %s", outcome.reason)

    async def _run_observational_hooks(self, event: str, payload: dict[str, Any]) -> None:
        """Shared implementation for every hook event added in ROADMAP.md's
        Phase 7 item 3 (SessionEnd/UserPromptSubmit/PreCompact/PostCompact/
        Interrupt) -- none of them can veto the action they're reporting
        on, only PreToolUse can (see its own method above), so this is
        just "run every configured command, log a warning if one fails,"
        the same shape _run_post_tool_use_hooks already had before this
        got pulled out as a reusable helper (that one isn't routed through
        this, on purpose -- no reason to touch an already-working,
        already-tested code path for a pure refactor)."""
        for command in self.hooks_config.get(event, []):
            outcome = await asyncio.to_thread(run_hook, command, payload)
            if not outcome.allowed:
                logger.warning("%s hook failed: %s", event, outcome.reason)

    async def run_interrupt_hooks(self) -> None:
        """Called from app.py's ws_endpoint alongside request_stop() --
        not underscore-prefixed, unlike this class's other hook methods,
        since (like request_stop/abandon_orphaned_turn) it's meant to be
        called from outside this class."""
        await self._run_observational_hooks(
            "Interrupt", {"event": "Interrupt", "thread_id": self.thread_id}
        )

    async def run_session_end_hooks(self) -> None:
        """Called from app.py's ws_endpoint on WebSocketDisconnect, every
        time a connection for this thread_id drops -- not just the
        orphaned-turn case abandon_orphaned_turn exists for."""
        await self._run_observational_hooks(
            "SessionEnd", {"event": "SessionEnd", "thread_id": self.thread_id}
        )

    async def _build_document_edit_preview(
        self, tool_name_: str, args: dict[str, Any]
    ) -> tuple[str | None, str | None]:
        """Best-effort before/after preview for an approval-gated
        document edit (pptx/docx/xlsx) -- dry-runs the *exact* same tool
        call, with the *exact* same args, against a throwaway copy of the
        target file, so the approval card can show what the edit will
        actually produce instead of the raw JSON arguments dump it fell
        back to before (`{"shape_index": 3, "fill_color": "38BDF8"}`
        tells a user little about what's about to change; a picture
        does). Reusing the real edit method this way -- not a second,
        hand-written "what would this look like" implementation -- means
        the preview can never drift out of sync with what actually
        happens on approval.

        Deliberately generic, not a hardcoded per-tool dispatch table:
        `_PREVIEWABLE_TOOLKITS_BY_EXTENSION` below maps a file extension
        to its toolkit class (PresentationToolkit/DocumentToolkit/
        SpreadsheetToolkit -- all three share the same `(root, *,
        state_dir=, extra_readable=, extra_writable=)` constructor
        shape, which is what makes this dispatch table-driven instead of
        three near-duplicate copies of this method); applies to *any*
        tool name that happens to be a method on that class (private
        helpers are all `_`-prefixed, so this can't accidentally match
        one) whose `path` argument names an existing file with a
        recognized extension -- covers every current and future edit
        tool across all three formats with zero maintenance here, and
        naturally excludes a pure-create tool like `fill_pptx_template`
        (no existing file to diff against) without needing a denylist --
        `write_pptx`/`write_docx`/`write_xlsx` *do* get previewed when
        they're actually overwriting something that already exists,
        which is exactly the case worth previewing.

        Never raises and never touches the real file -- any failure
        (soffice/pdftoppm missing, a bad dry-run, a locked file, an
        unrecognized extension) just means one or both preview images
        come back None, and the approval flow proceeds exactly as it did
        before this existed.
        """
        path_arg = args.get("path")
        if not isinstance(path_arg, str):
            return None, None
        toolkit_cls = _PREVIEWABLE_TOOLKITS_BY_EXTENSION.get(Path(path_arg.lower()).suffix)
        if toolkit_cls is None:
            return None, None
        if tool_name_.startswith("_") or not hasattr(toolkit_cls, tool_name_):
            return None, None
        try:
            real_scope = WorkspaceScope(
                self.workspace_root,
                extra_readable=self.settings.extra_readable_dirs,
                extra_writable=self.settings.extra_writable_dirs,
            )
            real_path = real_scope.resolve(path_arg)
        except (PermissionError, OSError):
            return None, None
        if not real_path.is_file():
            return None, None

        state_dir = Path(self.settings.state_dir)
        # "slide" is pptx's own page-selector arg name -- docx/xlsx tool
        # calls never carry one, so they fall through to page 1, the
        # same default an argument-less pptx edit (e.g.
        # edit_pptx_theme_colors, which is deck-wide) already gets.
        slide = args.get("slide")
        page = slide if isinstance(slide, int) and slide > 0 else 1

        before_name, _ = await asyncio.to_thread(
            render_single_page_preview, real_path, state_dir, page
        )

        after_name: str | None = None
        tmp_dir = tempfile.mkdtemp(prefix="coscribe_preview_edit_")
        try:
            preview_path = Path(tmp_dir) / real_path.name
            shutil.copyfile(real_path, preview_path)
            preview_toolkit = toolkit_cls(tmp_dir)
            method = getattr(preview_toolkit, tool_name_)
            dry_run_args = {**args, "path": preview_path.name}
            try:
                await asyncio.to_thread(lambda: method(**dry_run_args))
            except Exception:
                logger.debug(
                    "approval preview: dry run of %s failed, showing 'before' only",
                    tool_name_,
                    exc_info=True,
                )
                return before_name, None
            after_name, _ = await asyncio.to_thread(
                render_single_page_preview, preview_path, state_dir, page
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return before_name, after_name

    async def _decide_action_request(self, request: dict[str, Any], websocket: WebSocket) -> Any:
        """Decide one pending action request, in precedence order: a
        configured PreToolUse hook can veto it outright (same precedence as
        runtime/policies.py's HookToolPolicy, which always runs before the
        policy it wraps); otherwise, a request for a tool in
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

        def _denied(message: str) -> dict[str, Any]:
            if is_question:
                return {"type": "respond", "message": message}
            return {"type": "reject", "message": message}

        if self._stop_requested:
            return _denied("Stopped by user.")
        args = request["args"]
        audit_log = AuditLog(self.settings.state_dir)
        hook_reason = await self._run_pre_tool_use_hooks(name, args)
        if hook_reason is not None:
            record_decision(
                audit_log,
                thread_id=self.thread_id,
                tool_name=name,
                arguments=args,
                decision="reject",
                reason="hook_veto",
                detail=hook_reason,
            )
            return _denied(hook_reason)
        if is_question:
            if not _can_resolve_approvals(websocket):
                message = (
                    "No one is available to review this draft -- this is an unattended run, "
                    "so no task was created."
                    if is_draft
                    else "No one is available to answer this question -- this is an unattended run."
                )
                record_decision(
                    audit_log,
                    thread_id=self.thread_id,
                    tool_name=name,
                    arguments=args,
                    decision="reject",
                    reason="unattended",
                    detail=message,
                )
                return _denied(message)
            if is_draft:
                return await self._decide_task_draft_request(args, websocket)
            return await self._decide_question_request(args, websocket)
        if name not in self._gated_tool_risks:
            return {"type": "approve"}
        exec_policy_decision, exec_policy_detail = "prompt", None
        if name in EXEC_POLICY_TOOL_NAMES:
            exec_policy = load_exec_policy(self.settings.exec_policy_path)
            exec_policy_decision, exec_policy_detail = exec_policy.decide(args.get("script", ""))
        if exec_policy_decision == "forbidden":
            message = exec_policy_detail or "Forbidden by exec policy."
            record_decision(
                audit_log,
                thread_id=self.thread_id,
                tool_name=name,
                arguments=args,
                decision="reject",
                reason="exec_policy",
                detail=message,
            )
            return {"type": "reject", "message": message}
        if self.plan_mode:
            message = (
                "Plan mode is active (read-only). Note this step with "
                "task_create instead; the user needs to turn plan mode "
                "off before it can run."
            )
            record_decision(
                audit_log,
                thread_id=self.thread_id,
                tool_name=name,
                arguments=args,
                decision="reject",
                reason="plan_mode",
                detail=message,
            )
            return {"type": "reject", "message": message}
        if exec_policy_decision == "allow":
            record_decision(
                audit_log,
                thread_id=self.thread_id,
                tool_name=name,
                arguments=args,
                decision="approve",
                reason="exec_policy",
                detail=exec_policy_detail,
            )
            return {"type": "approve"}
        auto_reason = self._auto_approves(name)
        if auto_reason is not None:
            record_decision(
                audit_log,
                thread_id=self.thread_id,
                tool_name=name,
                arguments=args,
                decision="approve",
                reason=auto_reason,
            )
            return {"type": "approve"}
        if not _can_resolve_approvals(websocket):
            # Reached only when this call is genuinely gated and neither
            # exec_policy nor accept_edits already decided it above --
            # creating the Future below would await a real approval nobody
            # is present to give. A normal unattended turn never gets here
            # (its caller skips _resolve_pending_approvals entirely, leaving
            # the interrupt durably paused in the checkpointer); this is the
            # backstop for an unattended run with accept_edits on, which
            # does resolve approvals and must not hang the poller forever.
            message = "No one is available to approve this -- this is an unattended run."
            record_decision(
                audit_log,
                thread_id=self.thread_id,
                tool_name=name,
                arguments=args,
                decision="reject",
                reason="unattended",
                detail=message,
            )
            return _denied(message)

        request_id = uuid.uuid4().hex
        future: Future[bool] = get_running_loop().create_future()
        self._pending_approvals[request_id] = future
        # Only worth building for a socket that can actually show it to
        # someone -- for an unattended selfwake/Scheduled Task turn (see
        # _can_resolve_approvals's own docstring), this would just spend a
        # real soffice conversion + dry-run edit on a prompt nobody is
        # ever going to see resolved live.
        before_preview, after_preview = (
            await self._build_document_edit_preview(name, args)
            if _can_resolve_approvals(websocket)
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
            }
        )
        try:
            approved = await future
        finally:
            self._pending_approvals.pop(request_id, None)
        record_decision(
            audit_log,
            thread_id=self.thread_id,
            tool_name=name,
            arguments=args,
            decision="approve" if approved else "reject",
            reason="human",
        )
        return {"type": "approve" if approved else "reject"}

    def _effective_run_approval_mode(self) -> str | None:
        """The scheduled-run approval tier in force here: the executing
        run's, or -- while a person finishes a run that parked on an
        approval -- that run's task's, so the rest of the run keeps the
        rules it started under. None for an ordinary conversation."""
        if self.run_approval_mode is not None:
            return self.run_approval_mode
        parsed = parse_run_thread_id(self.thread_id)
        if parsed is None:
            return None
        trigger = ScheduledTriggerStore(self.settings.state_dir).load(parsed[0])
        if trigger is None:
            return None
        run = trigger.find_run(parsed[1])
        if run is None or run.status != "needs_approval":
            return None
        return trigger.approval_mode

    def _auto_approves(self, name: str) -> AutoApproveReason | None:
        """Why a gated call may go ahead without asking anyone, or None.

        "skip" approves every risk tier. "auto" approves only WRITE_LOCAL:
        a local file edit stays on this machine and can be redone, while
        running code (EXEC) or acting outside this machine (EXTERNAL) can't
        be taken back, so those still wait for a person. A hook veto or an
        exec-policy "forbidden" rule is checked before this and still wins
        under either tier -- those are the user's own standing rules, not
        approvals."""
        if self.accept_edits:
            return "accept_edits"
        mode = self._effective_run_approval_mode()
        if mode == "skip":
            return "approval_mode_skip"
        if mode == "auto" and self._gated_tool_risks.get(name) == "WRITE_LOCAL":
            return "approval_mode_auto"
        return None

    def _auto_approval_active(self) -> bool:
        return self.accept_edits or self._effective_run_approval_mode() in ("auto", "skip")

    def _decidable_unattended(self, request: dict[str, Any]) -> bool:
        """Whether _decide_action_request can settle this request with
        nobody present other than by refusing for lack of a person. A gated
        call that can't must stay parked for someone to approve later,
        rather than be rejected and let the run carry on without it."""
        name = request["name"]
        if name in QUESTION_TOOL_NAMES or name in TASK_DRAFT_TOOL_NAMES:
            return True
        if name not in self._gated_tool_risks or self.plan_mode:
            return True
        if self._auto_approves(name) is not None:
            return True
        if name in EXEC_POLICY_TOOL_NAMES:
            exec_policy = load_exec_policy(self.settings.exec_policy_path)
            decision, _ = exec_policy.decide(request["args"].get("script", ""))
            return decision != "prompt"
        return False

    async def _decide_question_request(
        self, args: dict[str, Any], websocket: WebSocket
    ) -> dict[str, Any]:
        """The ask_user_question flow: send question_required, wait for a
        real person's answer, and return it as a "respond" decision --
        HumanInTheLoopMiddleware substitutes this message directly as the
        tool's own result, the tool's Python body never actually running
        (see tools/interaction.py's own docstring). Same pending-Future/
        request-id shape as the approval flow just above, kept as a
        separate dict (self._pending_questions) rather than reusing
        self._pending_approvals since the value type is different (a
        free-text answer, not a bool) and request_stop/reconnect need to
        treat the two independently."""
        request_id = uuid.uuid4().hex
        future: Future[str] = get_running_loop().create_future()
        self._pending_questions[request_id] = future
        options = [line for line in args.get("options", "").split("\n") if line.strip()]
        await websocket.send_json(
            {
                "type": "question_required",
                "id": request_id,
                "question": args.get("question", ""),
                "header": args.get("header", ""),
                "options": options,
                "multi_select": bool(args.get("multi_select", False)),
            }
        )
        try:
            answer = await future
        finally:
            self._pending_questions.pop(request_id, None)
        return {"type": "respond", "message": answer}

    async def _decide_task_draft_request(
        self, args: dict[str, Any], websocket: WebSocket
    ) -> dict[str, Any]:
        """create_scheduled_task's review flow: the frontend shows the
        draft, the user edits and saves it (through the ordinary REST
        create endpoint, with its full validation) or dismisses it, and
        answers with a sentence saying which -- substituted as the tool's
        result. Shares the pending-question plumbing, since the answer is
        likewise a string and stop/reconnect treat it the same way."""
        request_id = uuid.uuid4().hex
        future: Future[str] = get_running_loop().create_future()
        self._pending_questions[request_id] = future
        await websocket.send_json({"type": "task_draft_required", "id": request_id, "draft": args})
        try:
            answer = await future
        finally:
            self._pending_questions.pop(request_id, None)
        return {"type": "respond", "message": answer}

    async def _resolve_pending_approvals(self, websocket: WebSocket) -> str | None:
        """While the graph is paused, decide every pending action request
        and resume -- looping in case a resumed turn immediately hits
        another approval-gated call.

        Returns the *last* resumed `_stream_turn` call's own text, not a
        concatenation of every round -- each round already streamed its own
        narration live as its own "agent_delta" bubble (see reducer.ts's
        agent_delta/agent_message cases), so re-joining them here would
        duplicate that text in the final "agent_message" a caller sends
        with this return value: a real bug found live (DeepSeek/GLM, both
        of which narrate before a gated tool call, unlike this app's usual
        Gemini/Anthropic testing) -- "I'll create notes.txt...Done! I
        created **notes.txt**..." glued with no separator, because the
        pre-tool narration and the post-tool narration were joined as if
        they were one continuous reply. Returns None specifically (not
        "") when the loop never runs at all (nothing was pending) -- a
        caller needs to tell "nothing to resolve, keep whatever text you
        already had" apart from "resolved, and the model said nothing
        further," which an empty string can't distinguish on its own.

        More than one *task* can be pending at once -- concretely, two
        concurrent spawn_agent calls proposed in the same AIMessage, each
        independently bridging its own child's approval via interrupt()
        (see runtime_lg/subagents.py). LangGraph gives each task's
        interrupt its own id and, once more than one is pending
        simultaneously, *requires* resuming each individually via
        `Command(resume={interrupt_id: value, ...})` -- a single shared
        resume value (this method's own approach before this was found and
        fixed) raises `RuntimeError: When there are multiple pending
        interrupts, you must specify the interrupt id when resuming` the
        moment a second task is pending at the same time as the first.
        Live-verified against real Gemini: two concurrent spawn_agent
        calls, one approved and one rejected, resolved correctly and
        independently, with no cross-contamination between them -- see
        runtime_lg/README.md.

        Multiple pending tasks are decided *sequentially* here (one
        `_decide_action_request` await fully completes before the next
        task's own interrupt is even inspected) -- so a caller/client must
        answer the first approval_required before the second one is sent,
        not expect both up front. A test (or a future UI) that tries to
        `receive` two approval_required messages back-to-back before
        answering either one will deadlock against this method, not
        against LangGraph -- see test_concurrent_spawn_agent_approvals_
        resolve_independently's request/respond/request/respond shape."""
        agent = self.lg_agent
        config = self.config
        latest_text: str | None = None
        state = await agent.aget_state(config)
        while state.next:
            if not _can_resolve_approvals(websocket) and not all(
                self._decidable_unattended(request)
                for task in state.tasks
                for interrupt in task.interrupts
                for request in interrupt.value.get("action_requests", [])
            ):
                break
            resume_map: dict[str, Any] = {}
            for task in state.tasks:
                for interrupt in task.interrupts:
                    action_requests = interrupt.value.get("action_requests", [])
                    decisions = [
                        await self._decide_action_request(request, websocket)
                        for request in action_requests
                    ]
                    resume_map[interrupt.id] = {"decisions": decisions}

            latest_text = await self._stream_turn(Command(resume=resume_map), websocket)
            state = await agent.aget_state(config)
        return latest_text

    async def resume_after_reconnect(self, websocket: WebSocket) -> None:
        """If this session's graph is still paused on an approval from
        before a dropped connection or a process restart, redeliver it now
        instead of leaving the browser with nothing to resolve -- the
        checkpointer already durably persisted the pending state (that's
        the whole point of a persistent checkpointer over InMemorySaver, see
        runtime_lg/README.md's reconnect/restart verdict); this just
        re-runs the same delivery path a fresh turn's approval already
        uses. A no-op when nothing is pending.

        Guarded by _turn_lock, same reasoning as handle_user_message -- this
        runs as a backgrounded task right after connect (see ws_endpoint),
        so without the lock it could run fully concurrently with a
        user_message the client sends immediately after connecting, racing
        on the same self.lg_agent/self.config the way two concurrent
        handle_user_message calls would."""
        if self.is_workflow_run():
            return
        async with self._turn_lock:
            self._current_turn_task = asyncio.current_task()
            # A turn hard-cancelled mid-tool also leaves state.next set, but
            # with no interrupt to redeliver -- resuming it would silently
            # re-run that tool (possibly minutes of work the user just
            # stopped), so close it off instead.
            await _close_orphaned_tool_calls(self.lg_agent, self.config)
            state = await self.lg_agent.aget_state(self.config)
            if not state.next:
                return
            # state.next already confirmed truthy above, so
            # _resolve_pending_approvals's own while loop is guaranteed to
            # run at least once -- its None return (nothing was pending)
            # can't actually happen here; `or ""` only satisfies the type
            # checker, not a real runtime fallback.
            text = await self._resolve_pending_approvals(websocket) or ""
            await self._record_resumed_run_status()
            await websocket.send_json({"type": "agent_message", "text": _format_reply(text)})
            if self._last_usage_metadata is not None:
                await websocket.send_json(_usage_event(self._last_usage_metadata))
            await websocket.send_json({"type": "tasks_changed"})

    def offer_pending_approval_to_live_tab(self) -> None:
        """A background run that parked on an approval while someone was
        watching: hand the approval to that tab now, the same way a fresh
        connection would get it, instead of waiting for a reload."""
        websocket = self._live_websocket
        if websocket is None:
            return
        self._offer_task = asyncio.create_task(self.resume_after_reconnect(websocket))

    async def _record_resumed_run_status(self) -> None:
        """A scheduled run that parked on an approval ended as
        "needs_approval"; once someone resolves it here and the turn
        finishes, the run record should say how it actually ended."""
        parsed = parse_run_thread_id(self.thread_id)
        if parsed is None:
            return
        trigger_id, run_id = parsed
        store = ScheduledTriggerStore(self.settings.state_dir)
        trigger = store.load(trigger_id)
        run = trigger.find_run(run_id) if trigger is not None else None
        if run is None or run.status != "needs_approval":
            return
        state = await self.lg_agent.aget_state(self.config)
        if any(task.interrupts for task in state.tasks):
            return
        store.finish_run(trigger_id, run_id, "stopped" if self._stop_requested else "completed")

    async def _handle_compact(self, websocket: WebSocket) -> None:
        """Summarize this thread's checkpointed message history down to one
        note, freeing up context -- the runtime_lg counterpart to
        ChatSession's /compact. Reuses runtime/compaction.py's own
        COMPACT_INSTRUCTIONS (not a duplicate prompt) but can't reuse
        run_compaction/compact_state themselves: those work on RunState's
        plain dict messages, not LangGraph's checkpointed BaseMessage list,
        and collapse via FileStateStore.save_state rather than
        aupdate_state's RemoveMessage(id=REMOVE_ALL_MESSAGES) sentinel
        (verified live against real Gemini before wiring this in -- see
        runtime_lg/README.md).

        No system message to re-add after clearing: verified empirically
        that create_agent never stores one in checkpointed state at all
        (system_prompt is injected fresh at call time), unlike the old
        runtime's RunState.messages, which always keeps one at index 0.

        The pending-approval check below predates _turn_lock and is mostly
        unreachable through ordinary concurrent messaging now that the lock
        serializes every turn -- see _handle_clear's docstring for the one
        remaining window it still guards (a fresh process restart racing
        resume_after_reconnect for the lock)."""
        state = await self.lg_agent.aget_state(self.config)
        messages = list(state.values.get("messages", [])) if state.values else []
        if len(messages) < 4:
            await websocket.send_json(
                {"type": "error", "message": "Nothing much to compact yet."}
            )
            return
        if state.next:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "Resolve the pending approval before compacting.",
                }
            )
            return

        await self._run_observational_hooks(
            "PreCompact",
            {"event": "PreCompact", "thread_id": self.thread_id, "agent_name": "coordinator"},
        )
        start = time.perf_counter()
        summary_message = await self.model.ainvoke(
            [
                SystemMessage(content=COMPACT_INSTRUCTIONS),
                HumanMessage(content=_render_transcript_lg(messages)),
            ]
        )
        summary = _extract_text(summary_message.content) or "(no summary)"
        note = f"{_COMPACT_NOTE_PREFIX}\n\n{summary}"
        await self.lg_agent.aupdate_state(
            self.config,
            {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), HumanMessage(content=note)]},
        )
        after_state = await self.lg_agent.aget_state(self.config)
        after = len(after_state.values.get("messages", [])) if after_state.values else 0
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        await self._run_observational_hooks(
            "PostCompact",
            {
                "event": "PostCompact",
                "thread_id": self.thread_id,
                "agent_name": "coordinator",
                "before": len(messages),
                "after": after,
                "elapsed_ms": elapsed_ms,
            },
        )
        await websocket.send_json(
            {
                "type": "compacted",
                "before": len(messages),
                "after": after,
                "elapsed_ms": elapsed_ms,
            }
        )

    async def _handle_clear(self, websocket: WebSocket) -> None:
        """Wipe this thread's conversation history and start fresh -- unlike
        /compact (collapses down to a summary note), this discards it
        outright. Doesn't touch plan_mode/accept_edits (session-level UI
        toggles, not conversation content) -- matches ChatSession's
        _handle_clear.
        Same pending-approval guard as _handle_compact -- see that
        docstring for why an aupdate_state call shouldn't race a paused
        interrupt task. With _turn_lock now serializing every turn (see
        __init__), this specific check is unreachable through ordinary
        concurrent messaging -- a /clear sent while another turn is
        pending on approval queues behind _turn_lock instead of racing it,
        so by the time this method actually runs, that turn has already
        been resolved one way or another (see test_clear_queues_behind_a_
        pending_turn_instead_of_racing_it). Kept anyway as a defensive
        backstop for the one window that still isn't covered by the lock:
        a fresh process restart, where the checkpointer still shows a
        genuinely pending interrupt from before the restart but this new
        ChatSessionLG's own _turn_lock is unheld, so a /clear racing
        resume_after_reconnect for the lock could in principle win it
        first."""
        state = await self.lg_agent.aget_state(self.config)
        if state.next:
            await websocket.send_json(
                {"type": "error", "message": "Resolve the pending approval before clearing."}
            )
            return
        await self.lg_agent.aupdate_state(
            self.config, {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)]}
        )
        await websocket.send_json({"type": "cleared"})

    async def _current_messages(self) -> list[Any]:
        """This thread's checkpointed message history right now -- the
        runtime_lg counterpart to reading state.messages off a
        FileStateStore-persisted RunState."""
        state = await self.lg_agent.aget_state(self.config)
        return list(state.values.get("messages", [])) if state.values else []

    async def handle_edit_message(
        self,
        index: int,
        text: str,
        websocket: WebSocket,
        images: list[str] | None = None,
    ) -> None:
        # Same _turn_lock serialization as handle_user_message -- an edit
        # is a new turn like any other, just one that first rewrites
        # history before running it.
        async with self._turn_lock:
            self._current_turn_task = asyncio.current_task()
            await self._handle_edit_message_locked(index, text, websocket, images=images)

    async def _handle_edit_message_locked(
        self,
        index: int,
        text: str,
        websocket: WebSocket,
        images: list[str] | None = None,
    ) -> None:
        """Edit an earlier user turn and regenerate the conversation from
        there. `index` is 0-based, counting only HumanMessages -- the exact
        count the frontend's own `history` hydration already produces
        (every checkpointed HumanMessage maps 1:1 to one "user"-kind
        LogItem there), so no new id-plumbing between frontend and backend
        is needed to keep the two sides pointing at the same message.

        Truncates the checkpointed state back to (and including) the
        target HumanMessage via RemoveMessage/aupdate_state -- the same
        mechanism _handle_compact/_handle_clear already use, and safe to
        reuse here for the same reason: add_messages assigns every
        checkpointed message a real id on the way in (verified against
        langgraph.graph.message.add_messages's own source), so
        RemoveMessage(id=m.id) always targets a real, existing id. Once
        truncated, the edited text is re-run through the exact same turn
        path _handle_user_message_locked already uses for a freshly typed
        message -- an edit is not a special kind of turn, just one that
        starts from a rewound history."""
        await _close_orphaned_tool_calls(self.lg_agent, self.config)
        state = await self.lg_agent.aget_state(self.config)
        messages = list(state.values.get("messages", [])) if state.values else []
        if state.next:
            await websocket.send_json(
                {"type": "error", "message": "Resolve the pending approval before editing."}
            )
            return

        human_positions = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
        if index < 0 or index >= len(human_positions):
            await websocket.send_json(
                {"type": "error", "message": f"No such message to edit (index {index})."}
            )
            return
        # Editing an older message would silently discard every turn after
        # it, including work the agent already did on files.
        if index != len(human_positions) - 1:
            await websocket.send_json(
                {"type": "error", "message": "Only your most recent message can be edited."}
            )
            return

        to_remove = messages[human_positions[index] :]
        if to_remove:
            await self.lg_agent.aupdate_state(
                self.config, {"messages": [RemoveMessage(id=m.id) for m in to_remove]}
            )

        await self._handle_user_message_locked(text, websocket, images=images)

    async def handle_rewind_message(self, index: int, websocket: WebSocket) -> None:
        # Same _turn_lock serialization as handle_edit_message -- see that
        # method's own comment.
        async with self._turn_lock:
            self._current_turn_task = asyncio.current_task()
            await self._handle_rewind_message_locked(index, websocket)

    async def _handle_rewind_message_locked(self, index: int, websocket: WebSocket) -> None:
        """Undo the last question and its reply -- real, live-reported
        bug this fixes: the UI's "Rewind" button used to call
        handle_edit_message with the turn's own *unedited* text, which
        truncates and immediately reruns it -- that's retry (same
        question, new answer), not rewind. This only truncates; the
        frontend hands the original question text back to the composer
        instead of resubmitting it, so the user decides what happens next
        (edit it, discard it, or resend it unchanged). Same truncation
        mechanism and guards as _handle_edit_message_locked (pending-
        approval check, index range check), just without the rerun step
        at the end."""
        await _close_orphaned_tool_calls(self.lg_agent, self.config)
        state = await self.lg_agent.aget_state(self.config)
        messages = list(state.values.get("messages", [])) if state.values else []
        if state.next:
            await websocket.send_json(
                {"type": "error", "message": "Resolve the pending approval before rewinding."}
            )
            return

        human_positions = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
        if index < 0 or index >= len(human_positions):
            await websocket.send_json(
                {"type": "error", "message": f"No such message to rewind (index {index})."}
            )
            return

        to_remove = messages[human_positions[index] :]
        if to_remove:
            await self.lg_agent.aupdate_state(
                self.config, {"messages": [RemoveMessage(id=m.id) for m in to_remove]}
            )
        await websocket.send_json({"type": "rewound", "index": index})

    async def _handle_save_skill(self, name: str, websocket: WebSocket) -> None:
        """/saveskill's own entry point: validate the name, require a
        non-empty conversation, run one curator round
        (propose_skill_save_lg), present it."""
        name = name.strip()
        if not name:
            await websocket.send_json({"type": "error", "message": "Usage: /saveskill <name>"})
            return
        messages = await self._current_messages()
        if not messages:
            await websocket.send_json({"type": "error", "message": "Nothing to save yet."})
            return
        proposal = await propose_skill_save_lg(
            name, messages=messages, model=self.model, clarification_history=[]
        )
        self.pending_save_skill_proposal = await self._present_skill_save_proposal(
            name, proposal, [], 1, websocket
        )

    async def _present_skill_save_proposal(
        self,
        name: str,
        proposal: SkillSaveProposal,
        clarification_history: list[tuple[str, str]],
        rounds: int,
        websocket: WebSocket,
    ) -> dict[str, Any]:
        """Same "plain chat bubble, not a new WS event type" reasoning as
        _present_save_proposal -- the description/body preview is shown
        in full (not truncated) for the same reason a script's full text
        is shown before a run_python_script approval: this is exactly
        the kind of content worth reading before confirming a write, not
        something to skim a summary of."""
        if proposal.decision == "clarify":
            await websocket.send_json({"type": "agent_message", "text": proposal.question})
            return {
                "name": name,
                "stage": "clarify",
                "question": proposal.question,
                "clarification_history": clarification_history,
                "rounds": rounds,
            }
        slug = slugify_skill_name(name)
        overwrite_note = ""
        if (self.settings.skills_dir / slug / "SKILL.md").is_file():
            overwrite_note = " (this will overwrite the existing skill at this slug)"
        text = (
            f'Proposed skill "{name}" (/{slug}){overwrite_note}:\n'
            f"{proposal.description}\n\n"
            f"{proposal.body}\n\n"
            'Save this? Reply "yes" to save, anything else to discard.'
        )
        await websocket.send_json({"type": "agent_message", "text": text})
        return {
            "name": name,
            "stage": "confirm",
            "proposal": proposal,
            "clarification_history": clarification_history,
        }

    async def _handle_pending_skill_save_proposal(self, text: str, websocket: WebSocket) -> None:
        """While pending_save_skill_proposal is set, every incoming message
        answers the curator's clarifying question or confirms/discards its
        proposed save instead of running a normal turn."""
        proposal_state = self.pending_save_skill_proposal
        assert proposal_state is not None
        if proposal_state["stage"] == "clarify":
            answer = text.strip()
            if not answer:
                await websocket.send_json(
                    {
                        "type": "agent_message",
                        "text": "(please answer, or say 'cancel' to abandon this save)",
                    }
                )
                return
            if answer.lower() == "cancel":
                self.pending_save_skill_proposal = None
                await websocket.send_json({"type": "agent_message", "text": "Cancelled."})
                return
            name = proposal_state["name"]
            history = [
                *proposal_state["clarification_history"],
                (proposal_state["question"], answer),
            ]
            rounds = proposal_state["rounds"]
            if rounds >= MAX_SAVE_CLARIFICATION_ROUNDS:
                self.pending_save_skill_proposal = None
                await websocket.send_json(
                    {
                        "type": "agent_message",
                        "text": "Still unclear after a few tries -- try /saveskill again "
                        "with a more specific name, or once the conversation has a "
                        "clearer single task in it.",
                    }
                )
                return
            messages = await self._current_messages()
            proposal = await propose_skill_save_lg(
                name, messages=messages, model=self.model, clarification_history=history
            )
            self.pending_save_skill_proposal = await self._present_skill_save_proposal(
                name, proposal, history, rounds + 1, websocket
            )
            return

        # stage == "confirm"
        answer = text.strip().lower()
        name = proposal_state["name"]
        proposal = proposal_state["proposal"]
        self.pending_save_skill_proposal = None
        if answer not in {"y", "yes"}:
            await websocket.send_json(
                {
                    "type": "agent_message",
                    "text": "Discarded -- refine your request and try /saveskill again.",
                }
            )
            return
        slug = slugify_skill_name(name)
        # A slug colliding with a *built-in* skill would silently shadow
        # it for every future /<slug> lookup (skills_by_slug below keeps
        # whichever of load_builtin_skills()/load_skills() loads last for
        # a repeated key) -- refused outright rather than allowed to
        # overwrite-in-spirit a file this session doesn't even own.
        # Colliding with an existing *user* skill is fine (see
        # write_skill_lg's own docstring) -- that's this command's own
        # "update" path, same as Skill Creator's own guidance.
        if slug in {s.slug for s in load_builtin_skills()}:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"/{slug} is already a built-in skill -- choose a "
                    "different name for /saveskill.",
                }
            )
            return
        saved = write_skill_lg(
            name,
            proposal.description or "",
            proposal.body or "",
            skills_dir=self.settings.skills_dir,
        )
        await websocket.send_json(
            {"type": "skill_saved", "name": saved["name"], "slug": saved["slug"]}
        )
        # Refreshes self.lg_agent/instructions/tools AND self.skills_by_slug
        # from disk (set_enabled_skills' own rebuild already reloads both
        # load_builtin_skills()+load_skills(settings.skills_dir) fresh) --
        # without this, the just-saved skill wouldn't actually be usable
        # (either passively or via /<slug>) until this session reconnects,
        # which would make "yes" feel like it silently did nothing.
        await self.set_enabled_skills(self.enabled_skill_names | {saved["name"]}, websocket)

    async def handle_user_message(
        self,
        text: str,
        websocket: WebSocket,
        images: list[str] | None = None,
    ) -> None:
        # See _turn_lock's docstring in __init__: this serializes turns so a
        # message sent while one is still running waits its turn instead of
        # racing it.
        if self.is_workflow_run():
            await websocket.send_json(
                {"type": "error", "message": "A workflow run doesn't take messages."}
            )
            return
        async with self._turn_lock:
            self._current_turn_task = asyncio.current_task()
            await self._handle_user_message_locked(text, websocket, images=images)

    async def _handle_user_message_locked(
        self,
        text: str,
        websocket: WebSocket,
        images: list[str] | None = None,
    ) -> None:
        await self._run_observational_hooks(
            "UserPromptSubmit",
            {"event": "UserPromptSubmit", "thread_id": self.thread_id, "text": text},
        )
        stripped_lower = text.strip().lower()

        if self.pending_save_skill_proposal is not None:
            await self._handle_pending_skill_save_proposal(text, websocket)
            return

        if stripped_lower == "/plan":
            self.plan_mode = not self.plan_mode
            await self.send_state(websocket)
            return

        if stripped_lower == "/accept-edits":
            self.accept_edits = not self.accept_edits
            await self.send_state(websocket)
            return

        if stripped_lower == "/compact":
            await self._handle_compact(websocket)
            return

        if stripped_lower == "/clear":
            await self._handle_clear(websocket)
            return

        command, _, rest = text.strip().partition(" ")
        command_lower = command.lower()

        if command_lower == "/saveskill":
            await self._handle_save_skill(rest.strip(), websocket)
            return

        user_input = text
        if stripped_lower.startswith("/"):
            name = command[1:].lower()
            if name == "init":
                user_input = INIT_PROMPT
            elif name == "saveworkflow":
                if not rest.strip():
                    await websocket.send_json(
                        {"type": "error", "message": "Usage: /saveworkflow <name>"}
                    )
                    return
                user_input = SAVE_WORKFLOW_PROMPT.format(name=rest.strip())
            elif name in self.skills_by_slug:
                skill = self.skills_by_slug[name]
                fallback = (
                    "(no additional request given -- follow the skill and "
                    "proceed, or ask what is needed.)"
                )
                user_input = (
                    f"[Skill '{skill.name}' invoked directly via /{name} -- "
                    f"follow its instructions below for this request.]\n\n"
                    f"{skill.body}\n\n---\nUser request: {rest.strip() or fallback}"
                )
            else:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": f"Unknown command: {command}. Type a skill name or /init.",
                    }
                )
                return

        if self.plan_mode:
            mode_note = PLAN_MODE_NOTE
        elif self.accept_edits:
            mode_note = ACCEPT_EDITS_MODE_NOTE
        else:
            mode_note = NORMAL_MODE_NOTE
        # current_date_note() lives here, not in the system prompt --
        # see its own docstring in runtime_lg/messages.py for why that
        # matters for caching across every provider, not just Anthropic.
        model_input = current_date_note() + mode_note + user_input

        content: str | list[dict[str, Any]] = model_input
        if images:
            content = [{"type": "text", "text": model_input}]
            content.extend({"type": "image_url", "image_url": {"url": url}} for url in images)
        turn_input = {"messages": [{"role": "user", "content": content}]}

        # Reset for this turn -- see _stream_turn's docstring. A stale
        # entry from a previous turn popping for the wrong call is the only
        # real risk of *not* resetting; there's no other state to preserve
        # across turns here.
        self._pending_tool_args = defaultdict(list)
        # Fresh start for /stop each turn, same as runtime/runner.py's
        # stop_event.clear() -- a stop requested during a previous turn (or
        # left over from one that already finished) must not immediately
        # halt this new one.
        self._stop_requested = False

        retried_after_grpc_metadata_overflow = False
        reply_text = ""  # narrowing hint only -- always reassigned before use, see the loop below
        while True:
            try:
                # reply_text ends up as just the *last* model response's own
                # text, not every response of this turn concatenated -- see
                # _resolve_pending_approvals's docstring for why joining them
                # was a real bug (duplicated, glued-together narration on any
                # model that talks before a gated tool call). Overwritten
                # below only if approvals actually resolved something; None
                # means nothing was pending, so the initial call's own text
                # already *is* the whole (single-segment) reply.
                reply_text = await self._stream_turn(turn_input, websocket)
                # Unattended, only worth entering when something can
                # approve without a person; _resolve_pending_approvals
                # itself leaves anything else parked.
                if _can_resolve_approvals(websocket) or self._auto_approval_active():
                    resolved_text = await self._resolve_pending_approvals(websocket)
                    if resolved_text is not None:
                        reply_text = resolved_text
                break
            except asyncio.CancelledError:
                # request_stop() hard-cancels this task (see its own
                # docstring) whenever there's no pending approval/question to
                # resolve instead -- exactly the case where _stream_turn is
                # stuck inside a raw provider call with no chunk ever yielded
                # for its cooperative stop-flag check to run against. No
                # partial reply_text exists at this point (the cancellation
                # interrupts _stream_turn before it can return one), so this
                # sends just the marker -- same "agent_message" type and
                # "[stopped]" convention the cooperative-stop path below
                # already uses, so the frontend needs no changes to render it.
                try:
                    await websocket.send_json({"type": "agent_message", "text": "[stopped]"})
                except Exception:  # noqa: BLE001 -- the client is already gone
                    pass
                return
            except Exception as exc:  # noqa: BLE001 -- surface any provider/tool error to the client
                is_grpc_metadata_overflow = _GRPC_METADATA_OVERFLOW_SIGNATURE in str(exc)
                if not retried_after_grpc_metadata_overflow and is_grpc_metadata_overflow:
                    retried_after_grpc_metadata_overflow = True
                    try:
                        self.model = resolve_chat_model(
                            self._model_string, self._custom_providers
                        )
                        lg_tools = self._build_lg_tools(self.model)
                        self.lg_agent = self._build_lg_agent(
                            self.model, self._model_string, lg_tools
                        )
                    except Exception:  # noqa: BLE001 -- rebuild failure falls through to the original error below
                        pass
                    else:
                        continue
                try:
                    await websocket.send_json({"type": "error", "message": str(exc)})
                except Exception:  # noqa: BLE001 -- the client is already gone
                    # Live-hit: a client that drops mid-turn (network blip,
                    # tab closed) makes _stream_turn's own websocket.send_json
                    # raise WebSocketDisconnect, landing here -- but the
                    # socket is already closed by then, so THIS send fails
                    # too (Starlette raises RuntimeError('Cannot call "send"
                    # once a close message has been sent.') once it's
                    # recorded the close). Left uncaught, that second
                    # exception propagated out of handle_user_message, which
                    # ws_endpoint fires via asyncio.create_task with nothing
                    # ever awaiting/checking its result -- so it surfaced
                    # only as an alarming "asyncio: Task exception was never
                    # retrieved" log, not a real problem: the turn lock still
                    # releases correctly either way (handle_user_message's
                    # `async with self._turn_lock` runs its __aexit__ on any
                    # exception), and there's no one left to deliver an error
                    # message to.
                    pass
                return

        if self._stop_requested:
            # Mirrors ChatSession's _format_agent_reply for status ==
            # "stopped" -- same "[stopped]" note, appended rather than
            # replacing whatever text streamed through before the stop.
            reply_text = f"{reply_text}\n\n[stopped]" if reply_text.strip() else "[stopped]"
            await websocket.send_json({"type": "agent_message", "text": reply_text})
        else:
            await websocket.send_json(
                {"type": "agent_message", "text": _format_reply(reply_text)}
            )
        # self._last_usage_metadata is set by _stream_turn (see its
        # docstring) -- None if the model never populated usage_metadata at
        # all, in which case the event is omitted entirely rather than
        # showing a fabricated number.
        if self._last_usage_metadata is not None:
            await websocket.send_json(_usage_event(self._last_usage_metadata))
        await websocket.send_json({"type": "tasks_changed"})
