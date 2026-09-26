"""Deferred tool loading for the top-level coordinator graph: a small,
always-bound "core" tool set plus a `search_tools(query)` gateway that
discovers the rest on demand -- the fix for the real, measured cost
`runtime_lg/README.md`'s "Tool-loading context cost" section already
found (~27k tokens of tool JSON schema alone, out of coscribe's own 95
built-in tools) and the design ROADMAP.md's Phase 8ap already grounded
against real prior art before any of this was written: Anthropic's own
Tool Search Tool, this environment's own `ToolSearch`, and
`claude-code-best/claude-code`'s real (cloned, read) `CORE_TOOLS` +
`SearchExtraTools`/`ExecuteExtraTool` pair all independently converge on
the identical two-tier shape.

**Why this restricts `ModelRequest.tools`, not `create_agent`'s own
`tools=` list.** `create_agent`'s `tools` parameter fixes the *complete*
set `ToolNode` will ever recognize and execute for this graph -- pass
the full 95-tool set there, always, or a genuinely deferred tool could
never run once discovered. `DeferredToolMiddleware.wrap_model_call`
instead narrows `request.tools` -- what actually gets sent to the
model/provider for one specific call -- via `request.override(tools=...)`,
LangChain's own documented mechanism for exactly this (the same one
`langchain.agents.middleware.ProviderToolSearchMiddleware`/
`LLMToolSelectorMiddleware` both already use, confirmed by reading their
real source in this installed langchain version). Verified live before
writing this file for real (a throwaway script, not assumed): a tool
excluded from `request.tools` really is hidden from `model.bind_tools`
on the first call, really does reappear once `search_tools` finds it,
and an excluded-then-discovered tool that also requires approval still
correctly pauses `HumanInTheLoopMiddleware` -- interrupt gating is
computed once from the *full* tool list at graph-build time
(`build_langgraph_agent`'s own `approval_tool_names`), entirely
independent of what any single request happened to advertise.

**Why "discovered so far" is derived fresh from message history, not
tracked in mutable middleware state.** A custom `AgentMiddleware.
state_schema` field updated imperatively would raise the exact replay-
safety question this session already got burned by once
(`runtime_lg/subagents.py`'s `spawn_agent` child_checkpointer bug, see
that module's own docstring) -- LangGraph's documented re-run-from-the-
node-start semantics on a resume make "did this mutation already
happen" a real question for anything stateful. Scanning `request.
messages` for every completed `search_tools` `ToolMessage` and unioning
their own JSON results instead is a *pure* function of data LangGraph
already checkpoints (the message history itself), so it's automatically
correct across every replay/resume/reconnect with no new checkpointing
concern to reason about at all.

**Why lexical scoring, not embeddings.** At coscribe's real scale (95
built-in tools total; ~15-20 stay in `coordinator.py`'s `CORE_TOOL_NAMES`,
the rest deferred), an embedding model/vector store is real added
infrastructure for no measurable benefit over plain term overlap --
`claude-code-best`'s own real, shipped implementation (TF-IDF, read live
from its source) reaches the identical conclusion at a comparable scale.
coscribe's own tool names are also unusually literal/prefixed
(`add_pptx_*`, `edit_pptx_*`, `read_*`/`write_*`) -- a query like "pptx"
or "add a chart" hits a name substring directly far more often than it
would in a less regularly-named API, so a substring-match bonus on top
of plain token overlap (mirroring `claude-code-best`'s own name-weighted-
3x-over-description scoring) covers the common case well without needing
IDF/BM25's extra bookkeeping.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool

# The tool this whole module hangs off of -- always allowed, alongside
# whatever CORE_TOOL_NAMES the caller passes in, regardless of what
# has been discovered yet (there would be no way to discover anything
# else if this one were ever itself deferred).
SEARCH_TOOLS_NAME = "search_tools"

# Name tokens count 3x a description token's weight -- same ratio
# `claude-code-best`'s own real `SearchExtraToolsTool` search index uses
# (name=3.0, description=1.0 in its field-weight table), read live from
# its source before choosing this.
_NAME_TOKEN_WEIGHT = 3.0
_DESCRIPTION_TOKEN_WEIGHT = 1.0
# A query matching a tool's name as a literal substring (e.g. "pptx"
# inside "add_pptx_shape") is a much stronger signal than incidental
# token overlap -- weighted well above what pure token-overlap scoring
# could produce for a single short query, so it reliably wins ties.
_NAME_SUBSTRING_BONUS = 5.0
_MAX_RESULTS = 8
# Every tool a search returns is bound for the rest of the conversation,
# so a weak match costs its schema on every later request, and binding it
# changes the tool list, which makes the provider re-read the whole
# conversation uncached. Measured on the real catalog: without these, a
# search for "run python script" also bound add_pptx_hyperlink and
# delete_file. Matches under this share of the best match's score, or
# under the floor, are left out.
_MIN_SHARE_OF_BEST = 0.4
_MIN_SCORE = 2.0

_STOPWORDS = frozenset(
    {"a", "an", "the", "to", "of", "for", "and", "or", "in", "on", "with", "this", "that",
     "is", "are", "it", "as", "at", "be", "by", "from"}
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [w for w in _TOKEN_RE.findall(text.lower()) if len(w) > 1 and w not in _STOPWORDS]


def _tool_name(tool: Callable[..., Any] | BaseTool) -> str:
    # Deliberately a local copy of agent.py's identical helper, not an
    # import from it -- agent.py imports *this* module (to attach
    # DeferredToolMiddleware/build_search_tools_tool), so importing back
    # from it here would be circular. Same trivial lookup either way: a
    # plain function's `__name__` vs. a BaseTool's `.name`.
    return str(getattr(tool, "name", None) or getattr(tool, "__name__", ""))


@dataclass(frozen=True)
class _ToolEntry:
    name: str
    description: str
    name_tokens: tuple[str, ...]
    description_tokens: tuple[str, ...]


def _tool_description(tool: Callable[..., Any] | BaseTool) -> str:
    description = getattr(tool, "description", None)
    if description:
        return str(description).strip()
    return (getattr(tool, "__doc__", None) or "").strip()


def _build_entry(tool: Callable[..., Any] | BaseTool) -> _ToolEntry:
    name = _tool_name(tool)
    description = _tool_description(tool)
    return _ToolEntry(
        name=name,
        description=description,
        name_tokens=tuple(_tokenize(name)),
        description_tokens=tuple(_tokenize(description)),
    )


def _score(entry: _ToolEntry, query_tokens: Sequence[str], query_lower: str) -> float:
    # Presence, not counts: a long description that repeats "pptx" or
    # "cells" ten times isn't ten times more relevant, and counting ranked
    # edit_pptx_xml above recalc_xlsx for "edit xlsx cells format".
    score = 0.0
    if query_lower and query_lower in entry.name.lower():
        score += _NAME_SUBSTRING_BONUS
    for token in set(query_tokens):
        score += _NAME_TOKEN_WEIGHT * (token in entry.name_tokens)
        score += _DESCRIPTION_TOKEN_WEIGHT * (token in entry.description_tokens)
    return score


def build_search_tools_tool(
    deferred_tools: Sequence[Callable[..., Any] | BaseTool],
) -> Callable[..., Any]:
    """Return the `search_tools` tool, indexed once over `deferred_tools`
    (everything *not* in `CORE_TOOL_NAMES` -- see `build_langgraph_agent`'s
    own `defer_tools` wiring for how the split is made). The index is
    built once per call, not per search -- cheap either way at this
    tool count, but no reason to re-tokenize every description on every
    query within one conversation."""
    entries = [_build_entry(tool) for tool in deferred_tools]
    index = _catalog_index(deferred_tools)

    def search_tools(query: str) -> str:
        """Find a tool that isn't currently available by keyword -- most
        tools start hidden to keep this conversation's context small;
        this searches the full catalog and makes any match available for
        you to call directly (by its real name, with its real arguments)
        starting with your very next tool call. Always try this before
        assuming something can't be done -- it almost certainly has a
        tool, just not one bound yet.

        Args:
            query: keywords describing what you need, e.g. "pptx chart"
                or "background script". A tool name substring (e.g.
                "pptx") works well since most tool names are literal
                about what they do.
        """
        query_tokens = _tokenize(query)
        query_lower = query.strip().lower()
        scored = [(_score(entry, query_tokens, query_lower), entry) for entry in entries]
        best = max((score for score, _ in scored), default=0.0)
        floor = max(_MIN_SCORE, best * _MIN_SHARE_OF_BEST)
        matches = sorted(
            (pair for pair in scored if pair[0] >= floor), key=lambda pair: pair[0], reverse=True
        )
        top = matches[:_MAX_RESULTS]
        return json.dumps(
            [{"name": entry.name, "description": entry.description} for _, entry in top]
        )

    if index:
        # The model can't search for what it doesn't know exists; a short
        # map of what's hidden (not the schemas) costs little context.
        search_tools.__doc__ = (search_tools.__doc__ or "") + "\n\n" + index
    return search_tools


_INDEX_EXAMPLES = 6


def _catalog_index(tools: Sequence[Callable[..., Any] | BaseTool]) -> str:
    """What the hidden tools are, by group: each built-in category and
    each connector, with a count and a few names to search by."""
    from ..runtime.types import get_tool_metadata

    groups: dict[str, list[str]] = {}
    for tool in tools:
        category = get_tool_metadata(tool).category or "other"  # type: ignore[arg-type]
        connector = category.removeprefix("mcp:")
        label = f"connector {connector}" if connector != category else category
        groups.setdefault(label, []).append(_tool_name(tool))
    if not groups:
        return ""
    lines = ["Hidden tools you can find with this, by group:"]
    for label, names in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        step = max(1, len(names) // _INDEX_EXAMPLES)
        examples = ", ".join(names[::step][:_INDEX_EXAMPLES])
        count = f"{len(names)} tool" + ("" if len(names) == 1 else "s")
        lines.append(f"- {label} ({count}), e.g. {examples}")
    return "\n".join(lines)


def bound_tool_names(core_tool_names: Iterable[str], messages: Sequence[BaseMessage]) -> set[str]:
    """The tools whose schemas a request actually sends: the core set,
    search_tools itself, and whatever search_tools has surfaced so far."""
    return {*core_tool_names, SEARCH_TOOLS_NAME, *_discovered_tool_names(messages)}


def _discovered_tool_names(messages: Sequence[BaseMessage]) -> list[str]:
    """Every tool name any `search_tools` call in this conversation has
    ever surfaced, in the order first surfaced, derived fresh from the
    message history each time -- see this module's own docstring for why
    that's the replay-safe choice over tracking it as mutable middleware
    state."""
    found: dict[str, None] = {}
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != SEARCH_TOOLS_NAME:
            continue
        content = message.content
        if not isinstance(content, str):
            continue
        try:
            entries = json.loads(content)
        except json.JSONDecodeError:
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                found.setdefault(entry["name"], None)
    return list(found)


def _request_tool_allowed(tool: Any, allowed_names: set[str]) -> bool:
    # A plain dict entry in request.tools is a provider-native tool spec
    # (e.g. a vendor's own built-in web-search/code-interpreter tool
    # dict), not one of coscribe's own callables -- always passed
    # through unfiltered rather than guessed at, since it has no `.name`
    # this module's own CORE_TOOL_NAMES/deferred split could ever have
    # been written against in the first place.
    if isinstance(tool, dict):
        return True
    return getattr(tool, "name", None) in allowed_names


class DeferredToolMiddleware(AgentMiddleware):
    """Narrows `request.tools` to `core_tool_names` plus whatever
    `search_tools` has discovered so far in this conversation -- see
    this module's own docstring for the full design and what was
    verified live before writing it. Filters whatever `request.tools`
    already contains rather than holding its own copy of the full tool
    list, so it can never drift from what `create_agent` was actually
    given."""

    def __init__(self, core_tool_names: Iterable[str]) -> None:
        super().__init__()
        self._core_names = set(core_tool_names) | {SEARCH_TOOLS_NAME}

    def _filtered_tools(self, request: ModelRequest[Any]) -> list[Any]:
        # Providers cache the prompt by prefix, and the tool list comes
        # before the conversation. A newly found tool goes after every tool
        # already sent, so what was sent before stays byte-identical.
        core = [tool for tool in request.tools if _request_tool_allowed(tool, self._core_names)]
        by_name = {getattr(tool, "name", None): tool for tool in request.tools}
        found = [
            by_name[name]
            for name in _discovered_tool_names(request.messages)
            if name in by_name and name not in self._core_names
        ]
        return core + found

    def wrap_model_call(
        self, request: ModelRequest[Any], handler: Callable[[ModelRequest[Any]], ModelResponse[Any]]
    ) -> ModelResponse[Any] | AIMessage:
        return handler(request.override(tools=self._filtered_tools(request)))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        return await handler(request.override(tools=self._filtered_tools(request)))
