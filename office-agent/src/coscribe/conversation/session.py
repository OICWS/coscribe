"""ChatSessionLG: the web equivalent of cli.py's chat() loop body, built
on runtime_lg instead of runtime/ (see runtime_lg/README.md, Phase 3).

Mirrors conversation/session.py's ChatSession public shape (handle_user_message,
resolve_approval, send_state) closely enough that app.py's WS handler is
nearly identical to app.py's -- but the internals are native async
(agent.astream/aget_state) instead of the queue.Queue/asyncio.to_thread
bridging ChatSession needs only because Runner.run_sync is synchronous.

/plan, /accept-edits, /compact, /clear, /stop, model switching, and Hooks
are supported here (see runtime_lg/README.md's "Plan mode, accept-edits,
compact, and Hooks under runtime_lg" section for the design of the first
four; /stop and switch_model follow the same "port the contract, adapt the
mechanism to a compiled graph" approach -- /stop is cooperative, checked at
_stream_turn/_decide_action_request's own yield points; switch_model rebuilds
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
import contextlib
import logging
import uuid
from asyncio import Future
from collections import defaultdict
from collections.abc import Callable
from functools import cached_property
from pathlib import Path
from typing import Any, cast

from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)
from langchain_core.messages.ai import UsageMetadata
from langchain_core.tools import BaseTool
from langgraph.constants import TAG_NOSTREAM

from ..config import Settings
from ..coordinator import build_coordinator_agent, conversation_context
from ..runtime import (
    Environments,
    LLMClient,
    empty_hooks_config,
    get_tool_metadata,
    load_custom_providers,
)
from ..runtime_lg import (
    ORPHANED_TOOL_CALL_NOTE,
    AutoApproveReason,
    CheckpointConversations,
    ExecPolicy,
    SilentSocket,
    SubAgentHost,
    Verdict,
    artifact_note,
    context_note,
    current_date_note,
    for_conversation,
    language_note,
    last_conversation_context,
    load_exec_policy,
    resolve_chat_model,
    strip_mode_note,
    tool_name,
)
from ..runtime_lg import extract_text as _extract_text
from ..tools import (
    BROWSER_HOST,
    QUESTION_TOOL_NAMES,
    TASK_DRAFT_TOOL_NAMES,
    BackgroundTask,
    ScheduledTriggerStore,
    SubAgentTask,
    WorkspaceScope,
    enabled_skill_names,
    load_builtin_skills,
    load_skills,
    parse_run_thread_id,
    request_detach,
    stop_commands,
)
from ..workflows import StepContext, WorkflowDraft
from . import tool_hooks
from .activity import summarize_activity, summarize_workflow_run
from .agent_assembly import AgentAssembly, risks_of_gated_tools
from .approval_resume import ApprovalResumer
from .approvals import ApprovalDecider, can_resolve_approvals
from .edit_preview import build_document_edit_preview
from .events import EventSink
from .history import ConversationHistory
from .prompts import INIT_PROMPT
from .reconfigure import SessionReconfigurer
from .skill_save import SkillSave
from .subagents import _MAX_REPORT_TURNS_IN_ROW as _MAX_REPORT_TURNS_IN_ROW
from .subagents import SubAgentApprovalChannel, SubAgentReporter
from .suggestions import offer_next_prompt
from .thread_meta import ThreadMetaStore
from .turn_lock import TurnLock
from .turn_stream import TurnStreamer
from .turn_text import (
    TITLE_INSTRUCTIONS,
    clean_title,
    design_system_for_start,
    format_reply,
    turn_mode_note,
    usage_event,
)
from .user_edits import user_edits_note
from .workflow_drafting import WorkflowDrafting

logger = logging.getLogger(__name__)

# Tests construct the channel by this name.
_SubAgentApprovalChannel = SubAgentApprovalChannel

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

_ORPHANED_TOOL_CALL_NOTE = ORPHANED_TOOL_CALL_NOTE


def _new_draft_id() -> str:
    return uuid.uuid4().hex[:8]


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
        extra_folders: list[Path] | None = None,
        configured_models: Callable[[], list[str]] | None = None,
    ) -> None:
        self.thread_id = thread_id
        # "provider:model" for every configured provider -- what a sub-agent
        # may be run on.
        self._configured_models = configured_models or (lambda: [self._model_string])
        # The socket of the turn now (or last) running: where a sub-agent's
        # approval goes when no browser tab is watching (the CLI).
        self._turn_websocket: Any = None
        # Approval request ids that belong to sub-agents, which a Stop of
        # the parent's turn must not deny.
        self._subagent_request_ids: set[str] = set()
        # Background sub-agent runs whose end is being reported, and the
        # tasks doing it (held so they aren't garbage-collected mid-turn).
        self._reporting_subagents: set[str] = set()
        self._report_tasks: set[asyncio.Task[None]] = set()
        # Report turns since the user last spoke: each can start another
        # sub-agent, whose report starts another turn, with nobody there.
        self._report_turns_in_row = 0
        self._delivering_inbox = False
        self._loop: asyncio.AbstractEventLoop | None = None
        with contextlib.suppress(RuntimeError):
            self._loop = asyncio.get_running_loop()
        self.settings = settings
        # None (the old default, still used by anything that hasn't been
        # taught about per-thread workspaces) falls back to the global
        # settings.workspace_root -- see coordinator.py's identical
        # fallback in build_coordinator_agent.
        self.workspace_root: Path = workspace_root or settings.workspace_root
        # A flag, not a comparison with settings.workspace_root: a user can
        # deliberately choose the default folder, and the UI should show it
        # as chosen.
        self._workspace_explicit = workspace_explicit
        # Folders added after the first; readable and writable like it.
        self.extra_folders: list[Path] = list(extra_folders or [])
        self._context_window_client = context_window_client
        self._context_window: int | None = None
        self._pending_approvals: dict[str, Future[bool]] = {}
        # The code module's approvals: which (task, action) a pending card
        # is for, and what the person has allowed for a whole task.
        self._code_requests: dict[str, tuple[str, str]] = {}
        self._code_task_allowances: set[tuple[str, str]] = set()
        # A question's answer is one entry per question, or None when the
        # user closed them; the other requests (plans, drafts) and a stop
        # answer with a string.
        self._pending_questions: dict[str, Future[Any]] = {}
        self.hooks_config: dict[str, list[str]] = hooks_config or empty_hooks_config()
        self.plan_mode = False
        self.accept_edits = False
        # Auto mode: a reviewer model decides risky calls in the user's
        # place. After 3 blocks in a row or 20 in all it pauses and the
        # user is asked, as in Claude Code; the user approving one resumes it.
        self.auto_mode = False
        self._auto_blocks_in_row = 0
        self._auto_blocks_total = 0
        self._auto_paused = False
        if settings.default_permission_mode != "manual" and parse_run_thread_id(thread_id) is None:
            self._toggle_mode(settings.default_permission_mode)
        # Reset at the top of every handle_user_message call -- see
        # _stream_turn's docstring for why this exists (recovering a tool
        # call's arguments for the PostToolUse hook payload, which the
        # ToolMessage carrying its result doesn't itself include).
        self._pending_tool_args: dict[str, list[dict[str, Any]]] = defaultdict(list)
        # Cooperative /stop flag -- see request_stop's docstring. Reset at
        # the start of every handle_user_message call.
        self._stop_requested = False
        # Most recent model-response segment's real usage_metadata (see
        # turn_stream's _capture_segment_usage) -- not reset per-turn like
        # _pending_tool_args/_stop_requested above, since it's read once
        # after the whole turn (including any approval-resume continuation)
        # finishes, not checked mid-turn. None until the first real segment
        # completes, or if the active model never populates usage_metadata.
        self._last_usage_metadata: UsageMetadata | None = None
        # The socket starts each "user_message" as a task without awaiting it, so a turn
        # waiting on an approval can't freeze the socket. Without this lock a second message
        # would start a concurrent turn on the same agent and checkpointed thread, racing on
        # shared state (_pending_tool_args, _stop_requested). resolve_approval/request_stop
        # stay outside it: they must reach the turn in flight, not queue behind it.
        self._turn_lock = TurnLock(self._turn_lock_changed)
        # Notes the user sent during a turn, (id, text), not yet read by
        # the model.
        self._steers: list[tuple[str, str]] = []
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
        self._checkpointer = checkpointer
        agent = self._build_agent()
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
            skill.slug: skill for skill in load_builtin_skills() + load_skills(settings.skills_dir)
        }

        # Kept for switch_model, which rebuilds the tool list (spawn_agent/review_work
        # are bound to a model) and the compiled graph against a *new* model, reusing
        # everything else unchanged (checkpointer/thread_id, base tools, instructions).
        self._custom_providers = custom_providers
        self._base_tools: list[Callable[..., Any] | BaseTool] = list(agent.tools)
        # Kept separate from self._base_tools (not just concatenated once
        # here) because refresh_extra_tools below can replace this list
        # wholesale, independently, whenever an MCP connector connects/
        # disconnects live -- self._base_tools never needs rebuilding for
        # that. Combined back in by _build_lg_tools below (same treatment
        # spawn_agent/review_work already get).
        self._extra_tools: list[Callable[..., Any] | BaseTool] = list(extra_tools)
        self._title_task: asyncio.Task[None] | None = None
        self._suggestion_task: asyncio.Task[None] | None = None
        self._instructions = agent.instructions or ""
        self._model_string = settings.default_model
        self._effort: str = settings.default_effort
        self.model, self._effort_runs_as = self._conversation_model(
            self._model_string, self._effort
        )
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
        self._gated_tool_risks = risks_of_gated_tools(lg_tools)
        self.lg_agent = self._build_lg_agent(self.model, self._model_string, lg_tools)
        self.config = {"configurable": {"thread_id": thread_id}}
        # The currently-connected browser tab's websocket, if any -- set/
        # cleared by app.py's ws_endpoint around its own connection
        # lifetime, *not* threaded through every method call the way
        # every other websocket use in this class already is (see
        # notify_resync's own docstring for why this one specifically
        # needs a place to live outside any single call's stack).
        self._live_websocket: EventSink | None = None
        self._subagents.listen()
        # A reply is being worked on -- unlike the turn lock, not held for the
        # moments a reconnect or a folder change takes it.
        self.turn_running = False
        # The prompt of a scheduled run executing on this thread right now
        # (set by runtime_lg/scheduled_tasks.py). A tab that opens the run
        # just after it starts can connect before that prompt is
        # checkpointed -- send_history fills it in so the run never shows
        # up without its opening message.
        self.active_run_prompt: str | None = None
        # The executing scheduled run's approval_mode, also set by
        # runtime_lg/scheduled_tasks.py -- see _auto_approves.
        self.run_approval_mode: str | None = None
        # Gated tools the current turn may call without asking, set by
        # whoever started it for someone who isn't there to be asked.
        self.preapproved_tools: frozenset[str] = frozenset()
        self._offer_task: asyncio.Task[None] | None = None

    def _conversation_model(self, model_string: str, effort: str) -> tuple[Any, str | None]:
        return for_conversation(
            resolve_chat_model(model_string, self._custom_providers), self.thread_id, effort
        )

    def _build_lg_tools(self, model: Any) -> list[Callable[..., Any] | BaseTool]:
        return self._assembly.build_lg_tools(model)

    @cached_property
    def _assembly(self) -> AgentAssembly:
        return AgentAssembly(self)

    @cached_property
    def _subagents(self) -> SubAgentReporter:
        return SubAgentReporter(self)

    def _subagent_host(self, model: Any) -> SubAgentHost:
        return self._subagents._subagent_host(model)

    async def _subagent_changed(self, task: SubAgentTask) -> None:
        await self._subagents._subagent_changed(task)

    def _background_task_changed(self, task: BackgroundTask) -> None:
        self._subagents._background_task_changed(task)

    async def _report_subagent(self, task_id: str, reasons: list[str]) -> None:
        await self._subagents._report_subagent(task_id, reasons)

    def schedule_inbox_delivery(self) -> None:
        self._subagents.schedule_inbox_delivery()

    def _stop_waited_on_subagents(self) -> None:
        self._subagents._stop_waited_on_subagents()

    def _title_path(self) -> Path:
        # The same sidecar app.py's rename endpoint writes and list_threads
        # reads.
        return Path(self.settings.state_dir) / f"{self.thread_id}.title"

    def _wants_title(self) -> bool:
        return (
            self.settings.auto_title_threads
            and not self._stop_requested
            and parse_run_thread_id(self.thread_id) is None
            and not self._title_path().exists()
            and (self._title_task is None or self._title_task.done())
        )

    def _offer_suggestion(self, websocket: EventSink, user_text: str, reply_text: str) -> None:
        if (
            self.settings.prompt_suggestions
            and reply_text.strip()
            and not self._stop_requested
            and parse_run_thread_id(self.thread_id) is None
            and not self._pending_approvals
            and not self._pending_questions
            and not self._steers
            and self._report_turns_in_row == 0
        ):
            self._suggestion_task = asyncio.create_task(
                offer_next_prompt(self.model, websocket, user_text, reply_text)
            )

    async def _name_thread(self, websocket: EventSink, first_message: str | None = None) -> None:
        """A short title from the conversation's first message, asked for as
        soon as it is sent so that a long first turn (or one waiting on the
        user) doesn't leave the raw message as the label; the end of the first
        turn asks again, with the reply, if that one got nothing."""
        try:
            first_user = first_message or ""
            last_reply = ""
            if first_message is None:
                state = await self.lg_agent.aget_state(self.config)
                messages = list(state.values.get("messages", [])) if state.values else []
                first_user = next(
                    (
                        strip_mode_note(_extract_text(m.content))
                        for m in messages
                        if m.type == "human"
                    ),
                    "",
                )
                last_reply = next(
                    (
                        _extract_text(m.content)
                        for m in reversed(messages)
                        if m.type == "ai" and _extract_text(m.content)
                    ),
                    "",
                )
            if not first_user.strip():
                return
            reply = await self.model.ainvoke(
                [
                    SystemMessage(content=TITLE_INSTRUCTIONS),
                    HumanMessage(
                        content=f"User: {first_user[:1500]}"
                        + (f"\n\nAssistant: {last_reply[:1500]}" if last_reply else "")
                    ),
                ],
                config={"tags": [TAG_NOSTREAM]},
            )
            title = clean_title(_extract_text(reply.content))
            if not title or self._title_path().exists():
                return
            self._title_path().parent.mkdir(parents=True, exist_ok=True)
            self._title_path().write_text(title, encoding="utf-8")
            await websocket.send_json({"type": "thread_titled", "title": title})
        except Exception:  # noqa: BLE001 -- a missing title is harmless; the first message stays the label
            logger.info("could not name thread %s", self.thread_id, exc_info=True)

    @cached_property
    def _drafting(self) -> WorkflowDrafting:
        return WorkflowDrafting(self)

    def _next_draft_id(self) -> str:
        return _new_draft_id()

    def _resolve_model(self, model_string: str) -> Any:
        return resolve_chat_model(model_string, self._custom_providers)

    def _build_draft_workflow_tool(self) -> BaseTool:
        return self._drafting._build_draft_workflow_tool()

    def _build_revise_workflow_tool(self) -> BaseTool:
        return self._drafting._build_revise_workflow_tool()

    def _build_test_workflow_tool(self) -> BaseTool:
        return self._drafting._build_test_workflow_tool()

    def _build_lg_agent(
        self, model: Any, model_string: str, lg_tools: list[Callable[..., Any] | BaseTool]
    ) -> Any:
        return self._assembly.build_lg_agent(model, model_string, lg_tools)

    def resolve_approval(self, request_id: str, approved: bool, scope: str | None = None) -> None:
        # "task": the person allowed this kind of action for the rest of the
        # code task that asked, not only this once.
        code_request = self._code_requests.get(request_id)
        if approved and scope == "task" and code_request is not None:
            self._code_task_allowances.add(code_request)
        future = self._pending_approvals.get(request_id)
        if future is not None and not future.done():
            future.set_result(approved)

    def resolve_question(self, request_id: str, answer: str | list[str | None] | None) -> None:
        future = self._pending_questions.get(request_id)
        if future is not None and not future.done():
            future.set_result(answer)

    def add_steer(self, steer_id: str, text: str | None) -> None:
        """A note for the turn in progress, read before its next step; `None` takes it back. With
        no turn running (it ended as the note was sent), it is sent as a message instead."""
        self._steers = [s for s in self._steers if s[0] != steer_id]
        if text is not None:
            self._steers.append((steer_id, text))
            if not self._turn_lock.locked():
                asyncio.get_running_loop().create_task(self._send_unread_steers())

    async def _take_steers(self) -> list[str]:
        if not self._steers:
            return []
        steers, self._steers = self._steers, []
        socket = self._live_websocket or self._turn_websocket
        if socket is not None:
            with contextlib.suppress(Exception):  # the tab went away; the model still has them
                await socket.send_json({"type": "steers_delivered", "ids": [i for i, _ in steers]})
        return [text for _, text in steers]

    def _turn_lock_changed(self, active: bool) -> None:
        BROWSER_HOST.set_active(self.thread_id, active)
        # The turn ended before its next model step could read them.
        if not active and self._steers:
            asyncio.get_running_loop().create_task(self._send_unread_steers())

    async def _send_unread_steers(self) -> None:
        if not self._steers or self._turn_lock.locked():
            return
        steers, self._steers = self._steers, []
        text = "\n\n".join(note for _, note in steers)
        websocket: Any = self._live_websocket
        if websocket is not None:
            try:
                await websocket.send_json(
                    {"type": "steers_requeued", "ids": [i for i, _ in steers], "text": text}
                )
            except Exception:  # noqa: BLE001 -- the tab went away; run unwatched
                websocket = None
        socket: Any = websocket or SilentSocket()
        await self.handle_user_message(text, socket)

    def detach_command(self, script: str) -> bool:
        """From the running loop: the background task announces itself to this loop from the
        tool's worker thread."""
        return request_detach(self.thread_id, script, asyncio.get_running_loop())

    def request_stop(self) -> None:
        """Called directly from ws_endpoint on a "stop" message, not
        awaited -- it has to reach whichever turn is *currently* running,
        same reasoning as resolve_approval. Mostly cooperative: sets a flag
        that _stream_turn/_decide_action_request check at their own natural
        yield points (the turn loop is a single async coroutine rather than a
        worker thread, so a plain flag is enough). Also immediately denies any
        tool call currently blocked on approval, so a stop can't get stuck waiting
        behind an approval prompt no one is going to answer -- the stop-flag checks
        elsewhere
        run between chunks/decisions, which a pending approval wait would
        otherwise never reach. A pending ask_user_question gets the same
        treatment, resolved with a placeholder answer rather than a bool --
        it's a "respond" decision, not approve/reject (see
        _decide_action_request's own docstring).

        A turn stuck inside one provider SDK call (langchain_google_genai's
        retry loop on a quota error can run for most of a minute, see
        runtime_lg/providers.py) never reaches the flag check, so the
        in-flight task is cancelled too, but *only* when there's nothing
        pending to resolve instead: a pending approval/
        question means the turn is legitimately paused at a LangGraph
        interrupt() waiting on user input, and unblocking it via the
        futures above lets it unwind through its own normal
        Command(resume=...) path, preserving the checkpointer's state
        correctly. Hard-cancelling *that* case instead would skip straight
        past the resume path -- see abandon_orphaned_turn's own docstring,
        which reasons through exactly this for the WebSocket-disconnect
        case and deliberately avoids it there too."""
        self._stop_requested = True
        # The page puts the unread notes back in the composer.
        self._steers.clear()
        self._stop_waited_on_subagents()
        # A browser step can be waiting out a slow page for an hour, in the
        # desktop app where cancelling this turn doesn't reach.
        BROWSER_HOST.cancel(self.thread_id)
        stop_commands(self.thread_id)
        has_pending_interrupt = False
        for request_id, future in list(self._pending_approvals.items()):
            if request_id in self._subagent_request_ids:
                continue
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

    async def stop_and_wait(self, timeout: float = 15.0) -> None:
        """Stops the running turn and waits for it to end: one ending after its conversation was
        deleted would write a checkpoint and bring the conversation back."""
        if self._turn_lock.locked():
            self.request_stop()
            await self._turn_lock.wait_released(timeout)

    def abandon_orphaned_turn(self, websocket: Any) -> None:
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
        cancellation the same as on any other exception.

        Only a turn driven by `websocket`, the connection that went away: a
        background turn (a scheduled run, a run being looked into) only
        shows in whichever tab is open, and must outlive one closing."""
        if self._turn_websocket is not websocket:
            return
        if self._current_turn_task is not None and not self._current_turn_task.done():
            self._current_turn_task.cancel()

    async def switch_model(
        self, model: str, websocket: EventSink, effort: str | None = None
    ) -> None:
        """Change this thread's model, its effort level, or both, from the next request on.
        create_agent() bakes the model into a compiled graph, so the graph is rebuilt around the
        new model object; the same checkpointer and thread keep the history, which is safe even
        mid-approval (the interrupt set comes from tool names and hooks, which a switch does not
        change). A failure (an unknown provider, an SDK that validates eagerly, an unknown effort
        level) leaves everything as it was.

        The custom providers are re-read from disk: the session's copy is a startup snapshot,
        and a provider added in Settings since must be usable without reopening the thread."""
        if ":" not in model:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f'model must be a "provider:model" string, got {model!r}',
                }
            )
            return
        previous = (
            self.model,
            self.lg_agent,
            self._model_string,
            self._effort,
            self._effort_runs_as,
        )
        self._context_window = None
        try:
            if self.settings.providers_config_path is not None:
                self._custom_providers = load_custom_providers(self.settings.providers_config_path)
            effort = effort or self._effort
            new_model, runs_as = self._conversation_model(model, effort)
            lg_tools = self._build_lg_tools(new_model)
            new_lg_agent = self._build_lg_agent(new_model, model, lg_tools)
            self.model, self.lg_agent, self._model_string = new_model, new_lg_agent, model
            self._effort, self._effort_runs_as = effort, runs_as
            await self.send_state(websocket)
        except Exception as exc:  # noqa: BLE001 -- a bad/misconfigured provider must not corrupt session state
            (self.model, self.lg_agent, self._model_string, self._effort, self._effort_runs_as) = (
                previous
            )
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
            self._gated_tool_risks = risks_of_gated_tools(lg_tools)

    def _build_agent(self) -> Any:
        return build_coordinator_agent(
            self.settings,
            self.thread_id,
            skill_names=self.enabled_skill_names,
            workspace_root=self.workspace_root,
            extra_folders=self.extra_folders,
            conversation_source=CheckpointConversations(self._checkpointer),
        )

    @property
    def folders(self) -> list[Path]:
        """The folders this conversation works in, the main one first;
        empty while it uses the app's default workspace."""
        return [self.workspace_root, *self.extra_folders] if self._workspace_explicit else []

    @cached_property
    def _reconfigurer(self) -> SessionReconfigurer:
        return SessionReconfigurer(self)

    def _build_lg_agent_with_risks(self) -> tuple[Any, dict[str, str]]:
        lg_tools = self._build_lg_tools(self.model)
        return (
            self._build_lg_agent(self.model, self._model_string, lg_tools),
            risks_of_gated_tools(lg_tools),
        )

    async def set_folders(self, paths: list[str], websocket: EventSink) -> bool:
        return await self._reconfigurer.set_folders(paths, websocket)

    async def add_folder(self, path: str, websocket: EventSink) -> bool:
        return await self._reconfigurer.add_folder(path, websocket)

    async def set_environment(self, env_id: str, websocket: EventSink) -> bool:
        return await self._reconfigurer.set_environment(env_id, websocket)

    @property
    def environment(self) -> str:
        return Environments(self.settings.state_dir).environment_of(self.thread_id)

    async def set_enabled_skills(
        self, skill_names: set[str], websocket: EventSink, *, announce: bool = True
    ) -> None:
        await self._reconfigurer.set_enabled_skills(skill_names, websocket, announce=announce)

    async def send_state(self, websocket: EventSink, *, on_connect: bool = False) -> None:
        if self._context_window is None:
            self._context_window = await asyncio.to_thread(
                self._context_window_client.get_context_window, self._model_string
            )
        state: dict[str, Any] = {
            "type": "state",
            "plan_mode": self.plan_mode,
            "accept_edits": self.accept_edits,
            "auto_mode": self.auto_mode,
            "model": self._model_string,
            "effort": self._effort,
            "effort_runs_as": self._effort_runs_as,
            "context_window": self._context_window,
            "enabled_skills": sorted(self.enabled_skill_names),
            "workspace_root": str(self.workspace_root),
            "workspace_explicit": self._workspace_explicit,
            "folders": [str(folder) for folder in self.folders],
            "environment": self.environment,
            "connector_tools": sorted(
                tool_name(t)
                for t in self._extra_tools
                if (get_tool_metadata(cast(Any, t)).category or "").startswith("mcp:")
            ),
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
            extra_readable=[*self.settings.extra_readable_dirs, *self.extra_folders],
            extra_writable=[*self.settings.extra_writable_dirs, *self.extra_folders],
        )

    def workflow_context(self, model: str | None) -> StepContext:
        return self._drafting.workflow_context(model)

    async def draft_workflow(self, name_hint: str = "") -> WorkflowDraft:
        return await self._drafting.draft_workflow(name_hint)

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

    def tool_catalog(self) -> dict[str, Any]:
        return {
            tool_name(t): get_tool_metadata(cast(Any, t))
            for t in [*self._base_tools, *self._extra_tools]
        }

    async def get_activity(self) -> dict[str, Any]:
        catalog = self.tool_catalog()
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
        """Where this thread's context-window budget goes, for the context panel."""
        return await self._assembly.context_breakdown()

    async def notify_resync(self) -> None:
        """Tell the tab watching this thread, if any, that something changed
        outside its own turn (a wake, scheduled task or sub-agent resumed it).
        It re-sends the "history" event a fresh connection gets, so the
        frontend's existing reducer replaces its items with the replay.

        Best-effort: a dead socket can raise, and a missed resync is fixed by
        the next reconnect, not worth failing the background work over."""
        websocket = self._live_websocket
        if websocket is None:
            return
        try:
            await self.send_history(websocket)
        except Exception:
            logger.debug("notify_resync: failed to notify the live tab for %s", self.thread_id)

    @cached_property
    def _history(self) -> ConversationHistory:
        return ConversationHistory(self)

    async def send_history(self, websocket: EventSink) -> None:
        await self._history.send_history(websocket)

    async def load_older_messages(self, websocket: EventSink) -> None:
        await self._history.load_older_messages(websocket)

    @cached_property
    def _turn_streamer(self) -> TurnStreamer:
        return TurnStreamer(self)

    async def _stream_turn(self, turn_input: Any, websocket: EventSink) -> str:
        return await self._turn_streamer.stream_turn(turn_input, websocket)

    async def _send_tool_started(
        self, name: str, args: dict[str, Any], websocket: EventSink
    ) -> None:
        if name in QUESTION_TOOL_NAMES or name in TASK_DRAFT_TOOL_NAMES:
            return
        await websocket.send_json({"type": "tool_started", "tool_name": name, "arguments": args})

    async def _run_pre_tool_use_hooks(self, name: str, args: dict[str, Any]) -> str | None:
        return await tool_hooks.run_pre_tool_use(self.hooks_config, name, args)

    async def _run_post_tool_use_hooks(self, name: str, args: dict[str, Any], result: Any) -> None:
        await tool_hooks.run_post_tool_use(self.hooks_config, name, args, result)

    async def _run_observational_hooks(self, event: str, payload: dict[str, Any]) -> None:
        await tool_hooks.run_observational(self.hooks_config, event, payload)

    async def run_interrupt_hooks(self) -> None:
        await self._run_observational_hooks(
            "Interrupt", {"event": "Interrupt", "thread_id": self.thread_id}
        )

    async def run_session_end_hooks(self) -> None:
        await self._run_observational_hooks(
            "SessionEnd", {"event": "SessionEnd", "thread_id": self.thread_id}
        )

    async def _build_document_edit_preview(
        self, tool_name_: str, args: dict[str, Any]
    ) -> tuple[str | None, str | None]:
        return await build_document_edit_preview(
            tool_name_,
            args,
            self.workspace_root,
            self.settings.extra_readable_dirs,
            self.settings.extra_writable_dirs,
            Path(self.settings.state_dir),
        )

    async def _decide_action_request(
        self, request: dict[str, Any], websocket: Any, *, honor_stop: bool = True
    ) -> Any:
        return await self._approvals._decide_action_request(
            request, websocket, honor_stop=honor_stop
        )

    def _effective_run_approval_mode(self) -> str | None:
        return self._approvals._effective_run_approval_mode()

    def _code_allows(self, name: str, websocket: Any) -> bool:
        return self._approvals._code_allows(name, websocket)

    def _auto_approves(self, name: str) -> AutoApproveReason | None:
        return self._approvals._auto_approves(name)

    def _toggle_mode(self, mode: str) -> None:
        """Plan, Accept Edits and Auto are one mode at a time; none of them is
        Manual. Toggling the current one returns to Manual."""
        turning_on = not {
            "plan": self.plan_mode,
            "accept-edits": self.accept_edits,
            "auto": self.auto_mode,
        }[mode]
        self.plan_mode = turning_on and mode == "plan"
        self.accept_edits = turning_on and mode == "accept-edits"
        self.auto_mode = turning_on and mode == "auto"
        if self.auto_mode:
            self._auto_blocks_in_row = 0
            self._auto_paused = False

    def _auto_review_active(self) -> bool:
        return self._approvals._auto_review_active()

    async def _auto_review(
        self, name: str, args: dict[str, Any], *, delegated_task: str = ""
    ) -> Verdict | None:
        return await self._approvals._auto_review(name, args, delegated_task=delegated_task)

    def _auto_approval_active(self) -> bool:
        return self._approvals._auto_approval_active()

    @cached_property
    def _approvals(self) -> ApprovalDecider:
        return ApprovalDecider(self)

    def _load_exec_policy(self) -> ExecPolicy:
        return load_exec_policy(self.settings.exec_policy_path)

    async def _decide_question_request(
        self, args: dict[str, Any], websocket: EventSink
    ) -> dict[str, Any]:
        return await self._approvals._decide_question_request(args, websocket)

    async def _decide_plan_request(
        self, args: dict[str, Any], websocket: EventSink
    ) -> dict[str, Any]:
        return await self._approvals._decide_plan_request(args, websocket)

    async def _decide_task_draft_request(
        self, name: str, args: dict[str, Any], websocket: EventSink
    ) -> dict[str, Any]:
        return await self._approvals._decide_task_draft_request(name, args, websocket)

    @cached_property
    def _approval_resumer(self) -> ApprovalResumer:
        return ApprovalResumer(self)

    async def _resolve_pending_approvals(self, websocket: EventSink) -> str | None:
        return await self._approval_resumer.resolve_pending_approvals(websocket)

    async def resume_after_reconnect(self, websocket: EventSink) -> None:
        await self._approval_resumer.resume_after_reconnect(websocket)

    def offer_pending_approval_to_live_tab(self) -> None:
        """A background run that parked on an approval while someone was
        watching: hand the approval to that tab now, the same way a fresh
        connection would get it, instead of waiting for a reload."""
        websocket = self._live_websocket
        if websocket is None:
            return
        self._offer_task = asyncio.create_task(self.resume_after_reconnect(websocket))

    async def _handle_compact(self, websocket: EventSink) -> None:
        await self._history._handle_compact(websocket)

    async def _handle_clear(self, websocket: EventSink) -> None:
        await self._history._handle_clear(websocket)

    async def _current_messages(self) -> list[Any]:
        return await self._history._current_messages()

    async def handle_edit_message(
        self,
        index: int,
        text: str,
        websocket: EventSink,
        images: list[str] | None = None,
    ) -> None:
        await self._history.handle_edit_message(index, text, websocket, images=images)

    async def handle_rewind_message(self, index: int, websocket: EventSink) -> None:
        await self._history.handle_rewind_message(index, websocket)

    @cached_property
    def _skill_save(self) -> SkillSave:
        return SkillSave(self)

    async def _handle_save_skill(self, name: str, websocket: EventSink) -> None:
        await self._skill_save._handle_save_skill(name, websocket)

    async def _handle_pending_skill_save_proposal(self, text: str, websocket: EventSink) -> None:
        await self._skill_save._handle_pending_skill_save_proposal(text, websocket)

    async def handle_user_message(
        self,
        text: str,
        websocket: EventSink,
        images: list[str] | None = None,
        artifact: str | None = None,
        design_system: str | None = None,
    ) -> None:
        # See _turn_lock's docstring in __init__: this serializes turns so a
        # message sent while one is still running waits its turn instead of
        # racing it.
        if self.is_workflow_run():
            await websocket.send_json(
                {"type": "error", "message": "A workflow run doesn't take messages."}
            )
            return
        meta = ThreadMetaStore(self.settings.state_dir)
        if meta.get(self.thread_id)["archived"]:
            # Talking to an archived conversation brings it back.
            meta.update(self.thread_id, archived=False)
        try:
            async with self._turn_lock:
                self._current_turn_task = asyncio.current_task()
                if self._suggestion_task is not None:
                    self._suggestion_task.cancel()
                self._report_turns_in_row = 0
                self.turn_running = True
                await self._handle_user_message_locked(
                    text,
                    websocket,
                    images=images,
                    artifact=artifact,
                    design_system=design_system_for_start(
                        self.settings.state_dir, self.thread_id, artifact, design_system
                    ),
                )
        finally:
            self.turn_running = False
            # Worth a look in the sidebar unless someone had the
            # conversation open when it ended (a stop counts as ended).
            ThreadMetaStore(self.settings.state_dir).mark_finished(
                self.thread_id, watched=self._live_websocket is not None
            )
            # Messages that came in during the turn, or waited on the cap.
            self.schedule_inbox_delivery()

    async def _handle_user_message_locked(
        self,
        text: str,
        websocket: EventSink,
        images: list[str] | None = None,
        *,
        notice: bool = False,
        artifact: str | None = None,
        design_system: dict[str, Any] | None = None,
    ) -> None:
        await self._run_observational_hooks(
            "UserPromptSubmit",
            {"event": "UserPromptSubmit", "thread_id": self.thread_id, "text": text},
        )
        # Skills are switched on and off globally in Settings; a conversation
        # that's already open picks the change up at its next turn.
        wanted_skills = enabled_skill_names(self.settings.skills_dir, self.settings.state_dir)
        if wanted_skills != self.enabled_skill_names:
            await self.set_enabled_skills(wanted_skills, websocket, announce=False)
        # A notice isn't the user's reply to anything, nor a command.
        stripped_lower = "" if notice else text.strip().lower()

        if self.pending_save_skill_proposal is not None and not notice:
            await self._handle_pending_skill_save_proposal(text, websocket)
            return

        if stripped_lower in ("/plan", "/accept-edits", "/auto"):
            self._toggle_mode(stripped_lower[1:])
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

        mode_note = turn_mode_note(self.plan_mode, self.accept_edits, self.auto_mode)
        # current_date_note() lives here, not in the system prompt --
        # see its own docstring in runtime_lg/messages.py for why that
        # matters for caching across every provider, not just Anthropic.
        context = conversation_context(self.settings, self.workspace_root, self.extra_folders)
        state = await self.lg_agent.aget_state(self.config)
        history = list(state.values.get("messages", [])) if state.values else []
        told = last_conversation_context(history) == context
        # What the user changed in a deck or a document in the app, told once, with the other notes.
        deck_note = await asyncio.to_thread(user_edits_note, self, history)
        model_input = (
            current_date_note()
            + mode_note
            + artifact_note(artifact, design_system)
            + language_note(text)
            + deck_note
            + ("" if told else context_note(context))
            + user_input
        )

        content: str | list[str | dict[Any, Any]] = model_input
        if images:
            content = [{"type": "text", "text": model_input}]
            content.extend({"type": "image_url", "image_url": {"url": url}} for url in images)
        turn_input = {"messages": [HumanMessage(content, additional_kwargs={"notice": notice})]}

        # Reset for this turn -- see _stream_turn's docstring. A stale
        # entry from a previous turn popping for the wrong call is the only
        # real risk of *not* resetting; there's no other state to preserve
        # across turns here.
        self._pending_tool_args = defaultdict(list)
        # Fresh start for /stop each turn: a stop requested during a previous turn (or
        # left over from one that already finished) must not immediately
        # halt this new one.
        self._stop_requested = False
        if not history and self._wants_title():
            self._title_task = asyncio.create_task(self._name_thread(websocket, text))

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
                if can_resolve_approvals(websocket) or self._auto_approval_active():
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
                    # The task panel refreshes on this, as after any other turn.
                    await websocket.send_json({"type": "tasks_changed"})
                except Exception:  # noqa: BLE001 -- the client is already gone
                    pass
                return
            except Exception as exc:  # noqa: BLE001 -- surface any provider/tool error to the client
                is_grpc_metadata_overflow = _GRPC_METADATA_OVERFLOW_SIGNATURE in str(exc)
                if not retried_after_grpc_metadata_overflow and is_grpc_metadata_overflow:
                    retried_after_grpc_metadata_overflow = True
                    try:
                        self.model, self._effort_runs_as = self._conversation_model(
                            self._model_string, self._effort
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
            await websocket.send_json({"type": "agent_message", "text": format_reply(reply_text)})
        # self._last_usage_metadata is set by _stream_turn (see its
        # docstring) -- None if the model never populated usage_metadata at
        # all, in which case the event is omitted entirely rather than
        # showing a fabricated number.
        if self._last_usage_metadata is not None:
            await websocket.send_json(usage_event(self._last_usage_metadata))
        await websocket.send_json({"type": "tasks_changed"})
        self._offer_suggestion(websocket, text, reply_text)
        if self._wants_title():
            # Off the turn's own path: the reply is done, the title can lag.
            self._title_task = asyncio.create_task(self._name_thread(websocket))
