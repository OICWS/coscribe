# 0001: The agent's tool list is fixed; tools are deferred

Status: accepted (decided 2026-10, recorded 2026-10-08). See `ARCHITECTURE.md`, "Cost is part of the design".

## Context
Providers cache a prompt by prefix: system prompt, then tool definitions, then
messages. coscribe has about 100 built-in tools plus connectors, ~35k tokens of
schemas. Anything that differs between conversations before the messages
(a folder path, a connector, a secret's name) makes the provider re-read the
rest uncached.

## Decision
The tool list bound to the model is the same for every conversation. Most tools
start hidden: `search_tools` returns the ones the model asks for with their
parameters, and `use_tool(name, arguments)` runs them. Middleware rewrites the
calls so approvals, hooks, audit and the UI still see the real tool name.
Per-conversation facts (folders, global instructions, a conversation's secret names)
arrive as a note on the user's message or through a tool result, never in a tool
schema or the system prompt.

## Consequences
- A new tool is added to the fixed list once, in a category that is deferred
  unless it is in `CORE_TOOL_NAMES`.
- Tests pin it: the bound schemas and the system prompt must be identical across
  conversations that differ in folders and secrets (`tests/test_prompt_cache.py`,
  `tests/test_http_request.py`).
- Providers whose cache breaks on this (DeepSeek needs reasoning sent back) are
  handled per provider; there all built-in tools are bound up front.
