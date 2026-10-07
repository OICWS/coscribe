"""Deferred tool loading for the top-level coordinator graph: a small,
always-bound "core" tool set plus a `search_tools(query)` gateway and a
`use_tool(name, arguments)` runner for the rest -- the fix for the real,
measured cost `runtime_lg/README.md`'s "Tool-loading context cost" section
found (~27k tokens of tool JSON schema alone, out of coscribe's own 95
built-in tools). The two-tier shape follows Anthropic's Tool Search Tool,
this environment's own `ToolSearch`, and `claude-code-best/claude-code`'s
`CORE_TOOLS` + `SearchExtraTools`/`ExecuteExtraTool` pair.

**Why the tool list never changes mid-conversation.** Providers cache the
prompt by prefix and the tool list comes before the messages, so binding a
tool that `search_tools` found made the provider re-read the whole
conversation uncached (22-56% of a long DeepSeek task, ROADMAP Phase 8cj).
So `search_tools` returns the found tools' descriptions *and parameter
schemas* as its result, and the model runs one through `use_tool`. What a
request sends is the core set plus those two tools, always.

**Why every other layer still sees the real tool name.** Approvals, hooks,
the audit log and the chat's tool rows are all keyed on the name of the
call that ends up in the message history. The middleware therefore does
two translations around each model call: the model's `use_tool(name, args)`
becomes `name(args)` as it comes back (before the approval middleware
reads it), and a stored call to a tool that isn't bound is shown to the
model as `use_tool(name, args)` again, so what it reads back matches what
it wrote. Both are pure functions of the message list, so they hold across
replays, resumes and reconnects with no new state to checkpoint.

**Why this restricts `ModelRequest.tools`, not `create_agent`'s own
`tools=` list.** `create_agent`'s `tools` fixes the *complete* set
`ToolNode` will recognize and run; a deferred tool must be in it or it could
never execute. `request.override(tools=...)` narrows what one request
advertises -- the mechanism `langchain.agents.middleware`'s own
`LLMToolSelectorMiddleware` uses. Interrupt gating is computed once from the
full tool list at graph-build time (`build_langgraph_agent`'s
`approval_tool_names`), independent of what a request advertised.

**Why lexical scoring, not embeddings.** At ~115 built-in tools, with names
unusually literal (`add_pptx_*`, `read_*`), term overlap plus a
name-substring bonus finds the right tool; `claude-code-best`'s shipped
TF-IDF implementation reaches the same conclusion at a similar scale.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool

# Always sent, alongside the caller's core tools: without them nothing
# deferred could be found or run.
SEARCH_TOOLS_NAME = "search_tools"
USE_TOOL_NAME = "use_tool"

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
# A result carries each match's full parameter schema, so a weak match costs
# tokens for nothing. Measured on the real catalog: without these, a search
# for "run python script" also returned add_pptx_hyperlink and delete_file.
# Matches under this share of the best match's score, or under the floor,
# are left out.
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


def _parameters_schema(tool: Callable[..., Any] | BaseTool) -> dict[str, Any]:
    """The tool's argument schema the way the model would have been sent it
    as a bound tool: ToolNode wraps a plain function with `tool()` too."""
    from langchain_core.tools import tool as create_tool
    from langchain_core.utils.function_calling import convert_to_openai_tool

    wrapped = tool if isinstance(tool, BaseTool) else create_tool(tool)
    return dict(convert_to_openai_tool(wrapped)["function"].get("parameters", {}))


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
    tools_by_name = {_tool_name(tool): tool for tool in deferred_tools}
    index = _catalog_index(deferred_tools)

    def search_tools(query: str) -> str:
        """Find a tool that isn't in your tool list by keyword -- most
        tools start hidden to keep this conversation's context small. Each
        match comes back with its description and parameter schema; run it
        with use_tool(name, arguments). Always try this before assuming
        something can't be done -- it almost certainly has a tool, just
        not one listed yet.

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
        return json.dumps(
            [
                {
                    "name": entry.name,
                    "description": entry.description,
                    "parameters": _parameters_schema(tools_by_name[entry.name]),
                }
                for _, entry in matches[:_MAX_RESULTS]
            ]
        )

    if index:
        # The model can't search for what it doesn't know exists; a short
        # map of what's hidden (not the schemas) costs little context.
        search_tools.__doc__ = (search_tools.__doc__ or "") + "\n\n" + index
    return search_tools


def build_use_tool_tool() -> Callable[..., Any]:
    """The `use_tool` tool. A call to a known tool never runs this body --
    the middleware rewrites it to the real call as it leaves the model -- so
    only a mistaken name reaches it."""

    def use_tool(name: str, arguments: dict[str, Any]) -> str:
        """Run a tool that search_tools found, by its exact name, with the
        arguments its parameter schema describes.

        Args:
            name: the tool's name, as search_tools returned it.
            arguments: the tool's arguments as an object, matching its
                parameter schema.
        """
        return (
            f"There is no tool named {name!r}. Use search_tools to find the exact name "
            "of what you need."
        )

    return use_tool


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


def bound_tool_names(core_tool_names: Iterable[str]) -> set[str]:
    """The tools whose schemas every request sends."""
    return {*core_tool_names, SEARCH_TOOLS_NAME, USE_TOOL_NAME}


def unwrap_use_tool_call(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The (name, args) a `use_tool` call stands for; any other call, or a
    malformed `use_tool` one, comes back unchanged."""
    if name != USE_TOOL_NAME:
        return name, args
    inner = args.get("name")
    inner_args = args.get("arguments")
    if isinstance(inner_args, str):
        # Some models send the arguments object as a JSON string.
        try:
            inner_args = json.loads(inner_args)
        except json.JSONDecodeError:
            return name, args
    if isinstance(inner, str) and inner != USE_TOOL_NAME and isinstance(inner_args, dict):
        return inner, inner_args
    return name, args


def _retarget_calls(
    message: AIMessage, targets: dict[str, tuple[str, dict[str, Any]]]
) -> AIMessage:
    """A copy of `message` whose tool calls with these ids carry the given
    name and arguments, in every place a provider adapter might read them
    from: `tool_calls`, Anthropic's `tool_use` content blocks, and
    OpenAI's raw `additional_kwargs["tool_calls"]`."""
    calls = [
        {**call, "name": targets[call["id"]][0], "args": targets[call["id"]][1]}
        if call["id"] in targets
        else call
        for call in message.tool_calls
    ]
    update: dict[str, Any] = {"tool_calls": calls}
    if isinstance(message.content, list):
        update["content"] = [
            {**block, "name": targets[block["id"]][0], "input": targets[block["id"]][1]}
            if isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("id") in targets
            else block
            for block in message.content
        ]
    raw = message.additional_kwargs.get("tool_calls")
    if isinstance(raw, list):
        update["additional_kwargs"] = {
            **message.additional_kwargs,
            "tool_calls": [
                {
                    **item,
                    "function": {
                        **item.get("function", {}),
                        "name": targets[item["id"]][0],
                        "arguments": json.dumps(targets[item["id"]][1], ensure_ascii=False),
                    },
                }
                if isinstance(item, dict) and item.get("id") in targets
                else item
                for item in raw
            ],
        }
    if isinstance(message, AIMessageChunk):
        update["tool_call_chunks"] = []
    return message.model_copy(update=update)


def run_found_tools_directly(message: AIMessage, known_names: set[str]) -> AIMessage:
    """Turns the model's `use_tool(name, arguments)` calls into `name(arguments)`
    for tools that exist, so everything after the model sees the real call.
    An unknown name stays a `use_tool` call and gets its error from the tool."""
    targets: dict[str, tuple[str, dict[str, Any]]] = {}
    for call in message.tool_calls:
        name, args = unwrap_use_tool_call(call["name"], call["args"])
        if name != call["name"] and name in known_names and call["id"]:
            targets[call["id"]] = (name, args)
    return _retarget_calls(message, targets) if targets else message


def show_unbound_calls_as_use_tool(
    messages: Sequence[AnyMessage], bound_names: set[str]
) -> list[AnyMessage]:
    """The history as the model should read it: calls to tools that aren't in
    the request's tool list, and their results, as the `use_tool` calls it
    made them with."""
    shown: list[AnyMessage] = []
    proxied: set[str] = set()
    for message in messages:
        if isinstance(message, AIMessage) and message.tool_calls:
            targets = {
                call["id"]: (USE_TOOL_NAME, {"name": call["name"], "arguments": call["args"]})
                for call in message.tool_calls
                if call["name"] not in bound_names and call["id"]
            }
            if targets:
                proxied.update(targets)
                message = _retarget_calls(message, targets)
        elif isinstance(message, ToolMessage) and message.tool_call_id in proxied:
            message = message.model_copy(update={"name": USE_TOOL_NAME})
        shown.append(message)
    return shown


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
    """Narrows `request.tools` to `core_tool_names` plus `search_tools` and
    `use_tool`, and translates between the model's `use_tool` calls and the
    real tool calls everything else sees -- see this module's own docstring.
    Filters whatever `request.tools` already contains rather than holding its
    own copy of the full tool list, so it can never drift from what
    `create_agent` was actually given."""

    def __init__(self, core_tool_names: Iterable[str]) -> None:
        super().__init__()
        self._bound = bound_tool_names(core_tool_names)

    def _narrowed(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        return request.override(
            tools=[tool for tool in request.tools if _request_tool_allowed(tool, self._bound)],
            messages=show_unbound_calls_as_use_tool(request.messages, self._bound),
        )

    def _translated(
        self, request: ModelRequest[Any], response: ModelResponse[Any] | AIMessage
    ) -> ModelResponse[Any] | AIMessage:
        known = {name for tool in request.tools if (name := getattr(tool, "name", None))}
        if isinstance(response, AIMessage):
            return run_found_tools_directly(response, known)
        result = [
            run_found_tools_directly(m, known) if isinstance(m, AIMessage) else m
            for m in response.result
        ]
        return replace(response, result=result)

    def wrap_model_call(
        self, request: ModelRequest[Any], handler: Callable[[ModelRequest[Any]], ModelResponse[Any]]
    ) -> ModelResponse[Any] | AIMessage:
        return self._translated(request, handler(self._narrowed(request)))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        return self._translated(request, await handler(self._narrowed(request)))
