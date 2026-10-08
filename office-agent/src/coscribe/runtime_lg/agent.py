"""Phase 1 spike: build a LangGraph agent from coscribe's *existing*,
unmodified tool factories (tools/__init__.py) and ToolMetadata tagging
(runtime/types.py) -- proving those two things port over as-is, without
reinventing risk-level/approval tagging or rewriting any tool function.

Not wired into cli.py/web/coordinator.py. See runtime_lg/README.md (or the
plan file this Phase came from) for what this is and isn't proving yet.
"""

from __future__ import annotations

import errno
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    SummarizationMiddleware,
    hook_config,
)
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphBubbleUp

from ..runtime.types import get_tool_metadata
from .messages import starts_a_turn, steer_message
from .tool_calls import answer_every_tool_call
from .tool_deferral import (
    DeferredToolMiddleware,
    build_search_tools_tool,
    build_use_tool_tool,
)


def tool_name(tool: Callable[..., Any] | BaseTool) -> str:
    """A tool's name regardless of its underlying shape -- a plain function
    or aisuite's MCPToolWrapper has `__name__` but no `.name`; a LangChain
    `BaseTool` (spawn_agent/review_work, or an MCP tool converted by
    langchain-mcp-adapters) has `.name` but no `__name__`. Shared here
    rather than duplicated inline in both this module and subagents.py's
    build_delegation_tools, which needs the identical lookup for its own
    tool_names selection."""
    return str(getattr(tool, "name", None) or getattr(tool, "__name__", ""))


_LOCKED_WINERRORS = frozenset({5, 32, 33})  # access denied, sharing/lock violation


def _locked_file_message(exc: Exception) -> str | None:
    """On Windows a file open in Excel/Word/a PDF viewer can't be written or
    replaced, and the raw error ("[WinError 32] ... used by another
    process") sends the model off to chmod it or save somewhere else. Only a
    real OS error counts: the workspace guard's own PermissionError carries
    no errno and says its own thing."""
    if not isinstance(exc, OSError):
        return None
    locked = getattr(exc, "winerror", None) in _LOCKED_WINERRORS or exc.errno in (
        errno.EACCES,
        errno.EBUSY,
        errno.EPERM,
    )
    # os.replace reports the temp file as filename and the real target as
    # filename2.
    target = exc.filename2 or exc.filename
    if not locked or not target:
        return None
    name = Path(str(target)).name
    return (
        f"{name} can't be changed right now: it is probably open in another program "
        "(Excel, Word, a PDF viewer) or read-only. Ask the user to close it, then try "
        "again -- don't save a copy under another name unless they say so. "
        f"({exc})"
    )


_LEGACY_TARGETS = {".doc": "docx", ".xls": "xlsx", ".ppt": "pptx"}


def _unopenable_file_message(request: Any, exc: Exception) -> str | None:
    """The libraries say "File is not a zip file" or "Package not found" for
    a legacy .doc/.xls/.ppt, a damaged file and a password-protected one
    alike; say which it probably is and what to do."""
    kind = type(exc).__name__
    path = str((request.tool_call.get("args") or {}).get("path") or "")
    is_package_error = kind in {"BadZipFile", "PackageNotFoundError", "InvalidFileException"}
    if kind == "PdfiumError" and "password" in str(exc).lower():
        return (
            f"{path or 'This PDF'} is password-protected, so it can't be read. Ask the user "
            "for an unlocked copy."
        )
    if not is_package_error:
        return None
    legacy = _LEGACY_TARGETS.get(Path(path).suffix.lower())
    if legacy is not None:
        return (
            f"{path} is an old-format file ({Path(path).suffix}) this tool can't open. Call "
            f"convert_office_file with to_format='{legacy}' and work on the converted copy."
        )
    return (
        f"{path or 'The file'} isn't a valid Office file: it may be damaged, "
        "password-protected, or saved under the wrong extension. Tell the user rather than "
        "guessing at its contents."
    )


def _tool_error_message(request: Any, exc: Exception) -> ToolMessage:
    tool_call = request.tool_call
    return ToolMessage(
        content=_locked_file_message(exc) or _unopenable_file_message(request, exc) or str(exc),
        tool_call_id=tool_call["id"],
        name=tool_call["name"],
        status="error",
    )


class _CatchToolErrorsMiddleware(AgentMiddleware):
    """A live-reported bug, not a design choice: `create_agent`'s `ToolNode`
    only converts a tool's raised exception into an error `ToolMessage` (so
    the model can see it and self-correct) for its own `ToolInvocationError`
    -- anything else, including a plain `ValueError`/`FileNotFoundError`
    raised by a tool function's own body, is re-raised (confirmed by
    reading `langgraph.prebuilt.tool_node._default_handle_tool_errors`
    directly: `if isinstance(e, ToolInvocationError): return e.message;
    raise e`). Every built-in coscribe tool raises a plain `ValueError`
    for exactly this kind of expected, retryable failure (a missing file, a
    bad argument) -- `tools/files.py`'s `read_file` raising `ValueError(f"File
    does not exist: {path}")` is a completely ordinary code path there, not
    a crash. Under runtime_lg, before this fix, that `ValueError` propagated
    all the way out of `agent.astream()`/`.ainvoke()`, hit
    `_handle_user_message_locked`'s `except Exception` in web/session.py,
    and ended the turn immediately with a bare `{"type": "error", ...}` WS
    message -- no `agent_message`, no `tasks_changed`, nothing further, which
    looks exactly like the turn hanging forever from the user's side. Live-
    reported: `read_file("sap_login.md")` on a file that doesn't exist
    stopped a turn dead, mid-task.

    Fixed here as `wrap_tool_call`/`awrap_tool_call` middleware (LangChain's
    documented interception point for this -- `create_agent` itself has no
    public parameter to configure `ToolNode`'s own `handle_tool_errors`)
    rather than in every individual tool function: catches whatever the
    handler raises and turns it into an error `ToolMessage` the model can
    see and respond to, same contract `ToolInvocationError` already gets.

    **Both hooks are implemented, deliberately, not just the async one --
    a real bug caught by this project's own tests, not assumed away.**
    `build_langgraph_agent` (this module) is also how `runtime_lg/
    subagents.py`'s `spawn_agent` builds its *child* graph -- and
    `spawn_agent`'s own tool body drives that child graph *synchronously*
    (`sub_agent.invoke(...)`/`sub_agent.get_state(...)`), even though the
    top-level graph it's itself called from always runs async
    (`astream`/`ainvoke`). A first version of this fix defined only
    `awrap_tool_call` (via the single-function `@wrap_tool_call` decorator)
    -- which works for the top-level graph, but the moment a gated tool call
    resolves inside a *sub-agent's* own sync `.invoke()`, LangGraph needs the
    *sync* `wrap_tool_call` hook and raises `NotImplementedError` when only
    the async one exists. That `NotImplementedError`, raised from inside
    `spawn_agent`'s own tool body, then got caught by *this exact
    middleware* on the *parent* graph and silently turned into a bogus
    successful-looking tool result -- `test_concurrent_spawn_agent_
    approvals_resolve_independently` caught it immediately: no `error` WS
    message, no crash, just both sub-agents' files silently never written.
    Subclassing `AgentMiddleware` directly (rather than the decorator, which
    only ever generates one of the two hooks) fixes this properly.

    Critical, live-verified detail: **must not catch `GraphBubbleUp`
    (`GraphInterrupt`'s own base class)** -- `HumanInTheLoopMiddleware`'s
    approval pause is implemented as an exception that propagates up through
    this exact same handler call to signal "pause the graph here," and
    `GraphBubbleUp` is a subclass of `Exception`. A bare `except Exception`
    here would silently swallow a live approval pause and turn it into a
    bogus error tool result instead -- verified this doesn't happen with a
    real `HumanInTheLoopMiddleware` + approval-gated tool call: the
    interrupt still pauses (`state.next` populated) and resumes correctly
    with this middleware installed alongside it."""

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        try:
            return handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:  # noqa: BLE001 -- exactly the tools/*.py ValueError-for-
            # expected-failure convention this fix exists to feed back to the model, not lose.
            return _tool_error_message(request, exc)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:  # noqa: BLE001 -- same reasoning as wrap_tool_call above
            return _tool_error_message(request, exc)


_catch_tool_errors = _CatchToolErrorsMiddleware()


def _redact_value(value: Any, redact: Callable[[str], str]) -> Any:
    """Every string anywhere in a JSON-shaped value (a message's content blocks,
    an artifact). Keys are left as they are: two keys blanked to the same text
    would collapse into one entry and lose a value. Other objects pass through:
    nothing here makes one, and it cannot be walked without knowing its type."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_value(item, redact) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, redact) for item in value)
    if isinstance(value, dict):
        return {key: _redact_value(item, redact) for key, item in value.items()}
    return value


def _redacted(result: Any, redact: Callable[[str], str]) -> Any:
    if isinstance(result, ToolMessage):
        return result.model_copy(
            update={
                "content": _redact_value(result.content, redact),
                "artifact": _redact_value(result.artifact, redact),
            }
        )
    update = getattr(result, "update", None)
    if isinstance(update, dict) and isinstance(update.get("messages"), list):
        messages = [_redacted(m, redact) for m in update["messages"]]
        return replace(result, update={**update, "messages": messages})
    return result


class _RedactToolResultsMiddleware(AgentMiddleware):
    """Every tool's result -- and every error it raised -- passes here before
    it is written to the conversation, so the model, the transcript, the UI
    and the audit log all read the same blanked text. The one place secret
    values are kept out of what the model reads. Outermost, so it also sees
    the error messages `_catch_tool_errors` makes."""

    def __init__(self, redact: Callable[[str], str]) -> None:
        self._redact = redact

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        return _redacted(handler(request), self._redact)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        return _redacted(await handler(request), self._redact)


class _AnswerEveryToolCallMiddleware(AgentMiddleware):
    """Whatever left a tool call without a result (a stop, a killed app, an
    approval that was never answered), the provider sees every call
    answered: one unanswered call fails the whole request with a 400, and
    the conversation can't go on. Only the request is patched; the saved
    history is left as it is."""

    def _patched(self, request: Any) -> Any:
        messages, patched = answer_every_tool_call(list(request.messages))
        return request.override(messages=messages) if patched else request

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._patched(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._patched(request))


def model_calls_this_turn(messages: Sequence[Any]) -> int:
    """The model's replies since the user's last request -- a note added
    mid-turn or an auto-compact summary is a HumanMessage too, not one."""
    count = 0
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            count += 1
        elif starts_a_turn(message):
            break
    return count


class _TurnModelCallLimitMiddleware(AgentMiddleware):
    """Ends a turn after `limit` model calls, counted from the history.
    LangChain's ModelCallLimitMiddleware keeps its run count out of the
    checkpoint, so it starts again at 0 whenever a run resumes after an
    approval -- in Auto mode nearly every step -- and a looping turn never
    reaches it."""

    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        calls = model_calls_this_turn(state["messages"])
        if calls < self.limit:
            return None
        message = f"Model call limits exceeded: turn limit ({calls}/{self.limit})"
        return {"jump_to": "end", "messages": [AIMessage(content=message)]}

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.before_model(state, runtime)


class _SteerMiddleware(AgentMiddleware):
    """Puts the notes the user sent during the turn into the conversation
    just before the model's next step, so it reads them without waiting
    for the reply to finish."""

    def __init__(self, take_steers: Callable[[], Awaitable[list[str]]]) -> None:
        super().__init__()
        self.take_steers = take_steers

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        notes = await self.take_steers()
        return {"messages": [steer_message(note) for note in notes]} if notes else None


def build_langgraph_agent(
    model: Any,
    tools: Sequence[Callable[..., Any] | BaseTool],
    instructions: str,
    *,
    checkpointer: Any = None,
    extra_interrupt_tool_names: Iterable[str] = (),
    question_tool_names: Iterable[str] = (),
    max_turns: int | None = None,
    auto_compact_tokens: int | None = None,
    defer_tools: bool = False,
    core_tool_names: Iterable[str] = (),
    take_steers: Callable[[], Awaitable[list[str]]] | None = None,
    redact: Callable[[str], str] | None = None,
) -> Any:
    """Build a LangGraph agent reusing coscribe's own ToolMetadata.

    `model` is a LangChain chat model (see runtime_lg/providers.py). `tools`
    is usually the *same* list of plain functions build_coordinator_agent
    already assembles from tools/__init__.py's build_*_tools factories --
    passed straight through, no @tool wrapping needed (create_agent accepts
    plain callables directly). A `BaseTool` (e.g. runtime_lg/subagents.py's
    spawn_agent, which needs @tool for InjectedToolCallId) works too --
    `.__name__` doesn't exist on those, so `.name` is tried first below.

    Approval gating: every tool whose existing ToolMetadata.requires_approval
    is True is added to a HumanInTheLoopMiddleware interrupt_on map --
    this is the direct replacement for runtime/policies.py's
    RequireApprovalPolicy, reusing the *same* per-tool metadata rather than
    a parallel tagging system. A bare BaseTool with no coscribe
    ToolMetadata attached (spawn_agent today) just gets the all-tools-
    auto-approved default, same as any other untagged callable.

    `extra_interrupt_tool_names`: additional tool names to route through
    that same interrupt point even though their own ToolMetadata doesn't
    require approval -- web/session.py passes every tool name here when
    PreToolUse hooks are configured, since a hook needs the chance to veto a
    low-risk tool too (mirroring runtime/policies.py's HookToolPolicy, which
    wraps *every* tool call regardless of risk level, not just the ones
    RequireApprovalPolicy would already stop for). The caller is
    responsible for auto-approving these once resumed if no hook denies
    them and the tool doesn't otherwise require human approval -- this
    function only arranges for the interrupt to fire, it doesn't decide
    what to do with it.

    `question_tool_names`: tool names that always interrupt with only
    `"respond"` as an allowed decision (`tools/interaction.py`'s
    `ask_user_question`, today) -- unlike an approval-gated tool, which
    the human approves/rejects/edits and then the tool itself actually
    runs, a "respond"-only tool's own Python body never executes at all:
    the human's answer is substituted directly as the tool's result. See
    `HumanInTheLoopMiddleware._process_decision`'s own "Skip tool
    execution; the human answers on behalf of the tool" comment. The
    caller (`web/session.py`'s `_decide_action_request`) is responsible
    for only ever returning a `{"type": "respond", ...}` decision for a
    name in this set -- any other decision type raises inside the
    middleware, since `allowed_decisions=["respond"]` is the only one
    configured here.

    `max_turns`/`auto_compact_tokens` are the two settings this function
    was missing during the "audit + delete old runtime" pass (see
    runtime_lg/README.md's "One real gap" section) -- `Settings.max_turns`/
    `Settings.auto_compact_threshold` used to drive the old runtime's own
    `Runner` loop directly (a plain turn-count cap; `maybe_auto_compact`
    called after every turn) and had no runtime_lg equivalent wired up.
    Only `web/session.py`'s top-level `ChatSessionLG._build_lg_agent` passes
    these -- the (now-deleted) old `tools/subagents.py` confirms
    spawn_agent/review_work's own sub-agent `Runner`s always used their
    *own* independent turn cap (a `spawn_agent` function parameter, default
    6, unrelated to `Settings.max_turns`) and never ran auto-compact at
    all, so leaving both `None` (the default) at `runtime_lg/subagents.py`'s
    `build_delegation_tools`/`build_review_work_tool` call sites preserves
    that same scope exactly, rather than silently widening what these
    settings apply to.

    `max_turns`: caps the number of *model calls* per user turn (via
    `_TurnModelCallLimitMiddleware`, counted from the history so approvals
    don't reset it), ending the turn gracefully with an injected message
    instead of raising
    -- chosen over LangGraph's own `recursion_limit` config knob, which
    counts raw graph steps (not model calls) and raises `GraphRecursionError`
    instead of ending cleanly; see runtime_lg/README.md for the
    `recursion_limit = 2*max_turns + 1` formula this middleware replaced.

    `defer_tools`/`core_tool_names`: when `defer_tools` is True, every tool
    not named in `core_tool_names` is hidden from the model by default --
    a `search_tools` tool (see `tool_deferral.py`) is added so the model
    can discover and start calling any of them on demand. `tools` itself
    is unaffected either way (the full set is always given to
    `create_agent`, so `ToolNode` can execute anything once called --
    only what's *advertised* to the model per call changes); see
    `tool_deferral.py`'s own module docstring for why this is safe with
    approval gating and doesn't need `core_tool_names` to include
    anything already covered by `extra_interrupt_tool_names`/
    `question_tool_names` above. `core_tool_names` is ignored when
    `defer_tools` is False.

    `auto_compact_tokens`: when set, adds a `SummarizationMiddleware` using
    `trigger=("tokens", auto_compact_tokens)` rather than the middleware's
    own native `trigger=("fraction", X)` mode -- deliberately, not by
    oversight: `("fraction", X)` requires `model.profile["max_input_tokens"]`,
    which is empty (`{}`) for `ChatGoogleGenerativeAI` and any custom-
    `base_url` `ChatOpenAI` (i.e. Gemini and DeepSeek/GLM/NVIDIA-style
    custom providers -- confirmed live for all three), and
    `SummarizationMiddleware.__init__` raises immediately if a
    fraction-based trigger is requested without profile data. The caller
    computes `auto_compact_tokens` from this project's own already-working
    `LLMClient.get_context_window(model_string)` instead of depending on
    LangChain's incomplete model-profile registry -- see `web/session.py`'s
    `_build_lg_agent`.
    """
    approval_tool_names = {
        tool_name(t)
        for t in tools
        # get_tool_metadata's own signature only names Callable, but it's
        # really just getattr(func, TOOL_METADATA_ATTR, ...) underneath --
        # genuinely safe on a BaseTool too, this cast isn't hiding a real
        # type mismatch, just widening for a helper runtime_lg reuses as-is.
        if get_tool_metadata(cast(Callable[..., Any], t)).requires_approval
    }
    approval_tool_names.update(extra_interrupt_tool_names)
    question_tool_names_set = set(question_tool_names)
    # _catch_tool_errors always runs, regardless of approval gating --
    # every graph built here should feed a tool's own raised exception back
    # to the model instead of crashing the turn (see its own docstring for
    # the live-reported bug this fixes). Listed before HumanInTheLoopMiddleware
    # so it's the outermost wrapper; order doesn't change correctness here
    # (it re-raises GraphBubbleUp untouched either way), but outermost is
    # the natural place for a catch-all.
    middleware: list[Any] = [_catch_tool_errors]
    if redact is not None:
        middleware.insert(0, _RedactToolResultsMiddleware(redact))
    # Only for an Anthropic model: importing langchain_anthropic pulls in the
    # whole anthropic SDK, ~0.6s of every server start for people who never
    # use it. A model switch rebuilds this graph (ChatSessionLG.switch_model),
    # so the check is re-made on each switch. The middleware also checks the
    # model itself; unsupported_model_behavior="ignore" because a non-Anthropic
    # model is never the middleware's concern here.
    #
    # Gemini/OpenAI-compatible models get their own automatic prefix caching
    # from a stable system prompt (see current_date_note's docstring in
    # messages.py); this middleware re-tags the last system-prompt block and
    # the last tool definition with cache_control on every model call, which
    # is what Anthropic needs on top. It does NOT place a breakpoint on the
    # growing message history -- see ROADMAP.md's Phase 0.
    if any(c.__module__.startswith("langchain_anthropic") for c in type(model).__mro__):
        from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware

        middleware.append(AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"))
    if approval_tool_names or question_tool_names_set:
        interrupt_on: dict[str, Any] = dict.fromkeys(approval_tool_names, True)
        interrupt_on.update(
            dict.fromkeys(question_tool_names_set, {"allowed_decisions": ["respond"]})
        )
        middleware.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))
    if take_steers is not None:
        middleware.append(_SteerMiddleware(take_steers))
    if max_turns is not None:
        middleware.append(_TurnModelCallLimitMiddleware(max_turns))
    if auto_compact_tokens is not None:
        middleware.append(
            SummarizationMiddleware(model=model, trigger=("tokens", auto_compact_tokens))
        )

    middleware.append(_AnswerEveryToolCallMiddleware())

    final_tools = list(tools)
    if defer_tools:
        core_names = set(core_tool_names)
        deferred = [t for t in tools if tool_name(t) not in core_names]
        # Appended to the *full* tools list passed to create_agent below
        # (not a separate, restricted one) -- ToolNode must recognize a
        # deferred tool when the model runs it; only DeferredToolMiddleware's
        # wrap_model_call, added last so it sits innermost, narrows what a
        # request advertises and rewrites use_tool calls before the approval
        # middleware reads them.
        final_tools.append(build_search_tools_tool(deferred))
        final_tools.append(build_use_tool_tool())
        middleware.append(DeferredToolMiddleware(core_names))

    return create_agent(
        model,
        final_tools,
        system_prompt=instructions,
        middleware=middleware,
        checkpointer=checkpointer or InMemorySaver(),
    )
