# coscribe -- Architecture

This file records *why* coscribe is built the way it is. How each piece was
verified, and the bugs found on the way, live elsewhere:
`docs/decisions/` (the load-bearing decisions, one short record each),
`docs/log/` and `office-agent/ROADMAP.md` (what shipped; the full old history is
in `docs/history/`),
`office-agent/PPTX_DESIGN.md` (presentation decisions) and
`office-agent/src/coscribe/runtime_lg/README.md` (the runtime's history).

## What it is

A local agent for office work, for people who don't write code: read and
write their files, produce Word/Excel/PowerPoint/PDF, use the web and their
tools, and repeat work on a schedule. It needs only a model API key.

- **Users first, not developers.** A feature has to be something an office
  user would use. Developer conveniences (a git/GitHub connector, once
  shipped) were removed for that reason.
- **Own the skeleton, borrow the capabilities.** The runtime, approvals,
  persistence and UI are coscribe's; specific services (mail, calendars,
  SaaS tools) come in through MCP, Skills and connectors rather than code
  written here. Office documents and the browser are the deliberate
  exceptions (see Scope).
- **Ideas from Claude Code, rebuilt in Python.** Task tracking, Plan mode,
  Skills, Hooks and isolated sub-agents follow its design; nothing is ported.

## Shape of the system

- **Shells.** The desktop app (`office-agent-desktop/`, Electron) starts
  `coscribe-web` as a local sidecar and shows the same web UI; Electron was
  chosen so the browser panel can be embedded natively. There is also a CLI
  (`coscribe`).
- **Server.** FastAPI; a WebSocket per conversation carries the turn
  (streamed text, tool calls, approvals, questions, usage).
- **Frontend.** React + TypeScript (`office-agent/frontend/`), built into
  `src/coscribe/web/static/`.
- **Runtime.** `coscribe.runtime_lg`, on LangChain/LangGraph: one
  coordinator agent per conversation with ~100 built-in tools, plus
  middleware for approvals, tool deferral, auto-compaction, a per-turn
  model-call cap, and notes the user adds mid-turn. It replaced a
  hand-written runtime whose own provider adapters kept producing bugs.
- **Models.** Anthropic, OpenAI and Gemini directly, and any
  OpenAI-compatible API (DeepSeek, Kimi, GLM, local runners) as a custom
  provider. `aisuite`, the original foundation, is kept only for a
  context-window lookup and an MCP compatibility fix.
- **Persistence.** Plain files under `state_dir` (JSON records, previews,
  tasks, memory) and a SQLite checkpointer for conversation state. No
  database server.
- **Two modes in the nav rail.** Chat, and Scheduled (tasks and workflows).
- **Package layers**, top to bottom: `cli`, `web`, `conversation`, `coordinator | runtime_lg`,
  `workflows | code_runtime`, `tools`, `runtime | config`, `providers`. A package
  imports only from below; the rule is checked in CI (`lint-imports`).

## Key decisions

### One coordinator, not a team of specialists

Specialist behavior comes from Skills and from delegation, not from a
standing roster of agents.

- **Planning** is the coordinator's own task list (`task_create`/`update`)
  plus Plan mode, where it can only read until the user approves a plan.
- **Reflection** needs someone who didn't do the work: `review_work` runs a
  fresh reviewer with read-only tools, the rendered pages, and a log of what
  the work's scripts and reads actually returned, so a figure that nothing
  produced stands out. The model decides when to use it, since forcing it
  would double the cost of a three-line memo.
- **Delegation**: `spawn_agent` runs a sub-agent in its own context with a
  chosen tool set and a step budget; background ones report back when done.
- **Every loop is bounded.** A turn ends after a set number of model calls
  (counted from the conversation, so approvals don't reset it), and each
  file gets one review and one re-check per turn.

### Approvals instead of a sandbox

Every tool, built-in or MCP, carries a risk tier, and the tier, not where
the tool came from, decides whether it is gated:

- `READ` -- no side effects (reading files, web search);
- `WRITE_LOCAL` -- changes on this machine;
- `EXEC` -- runs code;
- `EXTERNAL` -- reaches other services (connectors, downloads).

The user picks a permission mode per conversation: Manual, Accept Edits,
Plan, or Auto, where a reviewer model decides on gated calls and hands the
unclear ones to the user. Scheduled runs have their own approval tiers.
Every decision goes to an audit log, and Hooks (`PreToolUse`,
`PostToolUse`, `SessionStart`/`End`, `UserPromptSubmit`, compaction and
interrupt events) let a user add their own checks.

There is no OS sandbox. Scripts (`run_python_script`, `run_node_script`)
are approved like any other action; the only extra layer is a folder guard
that refuses writes outside the conversation's folders. It catches the
model's mistakes and is not a security boundary: scripts can still read
anywhere, use the network, or bypass it. The UI and docs say so. No
import allow/deny lists: they are easy to bypass and only look like safety.

### Office formats are built in, not MCP

The MCP servers for docx/xlsx/pptx/pdf were small personal projects; running
one means an unaudited process reading and writing the user's files. The
built-in tools use maintained libraries (`python-docx`, `openpyxl`,
`python-pptx`, `pdfplumber`, `reportlab`) and keep each feature narrow
enough to be reliable:

- Native, editable charts only through the libraries' own chart APIs
  (a docx chart reuses python-pptx's chart part).
- Workbooks are recalculated with LibreOffice so formula values are
  cached. `XLOOKUP`/`XMATCH`, which it can't evaluate, are computed by
  coscribe; dynamic-array functions (`FILTER`, `SORT`, ...) are refused.
- Where OOXML is written by hand (tracked changes, comments, transitions,
  animations, background images), the result is converted with LibreOffice
  to prove it opens.
- Every write renders a preview image, so the model and the reviewer see
  the page, not a description of it.

LibreOffice is an optional runtime dependency: without it, previews,
recalculation and checks are skipped, never the write itself.

### Repeated work becomes fixed steps; the AI stays outside the run

Day to day, the user just asks and the coordinator plans and acts. Work that
repeats becomes a scheduled task, of one of two kinds:

- **A prompt task**: written instructions the model works out again each run.
- **A fixed workflow**: the tool calls of a successful conversation turned
  into fixed steps, compiled into a LangGraph `StateGraph` (branches, loops,
  approvals as interrupts, resumable from checkpoints). Nothing is decided
  by a model at run time except explicit model steps, so it is fast,
  repeatable and cheap.

The AI works only around a fixed workflow: it drafts one from a
conversation and test-runs it (up to three repair rounds) before the user
saves it, and after a failed run, which stops at the failing step, it
reproduces, fixes and re-tests in a new conversation, again for the user to
save. The step editor is for these workflows; there is no general graph
canvas.

A conversation can also pause itself and resume later (`sleep_until`,
`wake_on_task`), which is separate from scheduled tasks: a wake belongs to
one conversation, a task is a global record that starts its own.

### Code is a second runtime, not a node in the first

Programming work goes to Codex (`openai/codex`, Apache-2.0) run as an
external process over its app-server JSON-RPC protocol (`code_runtime/`),
rather than to a coding graph rebuilt inside `runtime_lg`. It is the chat's
assistant, not a mode of its own: the chat hands it a task (`run_code_task`)
that runs as a sub-agent, so code ability comes to the conversation instead
of a second place to work in. It is optional and off by default: the pinned
binary is downloaded on first use, not bundled. Its commands and file
changes come back through coscribe's approvals, as synthetic gated tools.
Workflows never use it: a fixed run must not contain an autonomous agent.

### Cost is part of the design

- **Prompt caching works by prefix**, so nothing that differs between
  conversations goes into the system prompt: folders and the global instructions
  are a note on the user's message, sent again only when they change.
- The tool list is fixed for the whole conversation: most tools start
  hidden, `search_tools` returns the ones the model asks for with their
  parameters, and it runs them through `use_tool`, so finding a tool never
  changes the cached prefix.
- Provider quirks that break the cache are handled per provider (DeepSeek
  needs its reasoning sent back; there, all built-in tools are bound up
  front instead of discovered).
- Usage is reported per turn in the UI.

### Built in where an integration would be worse

- **Web search** (`ddgs`): falls back across free engines; used for
  grounding, not retrieval.
- **Images**: DuckDuckGo image search with no license filtering, and the
  user is told to check rights.
- **Browser** (desktop only): tabs in an Electron `WebContentsView` driven
  over the DevTools protocol, so the AI uses the user's own logins, works
  where the user can watch, and asks per site. Elements are targeted by
  description, not one-off refs; downloads land in the workspace.
- **Secrets**: provider keys, connector tokens and `mcp.json` values go to
  the OS keychain when there is one (a `chmod 0600` file otherwise). *Global
  secrets* a user gives a conversation are keychain-only and are used through
  `http_request` placeholders, bound to hosts; the model cannot see a value
  ([decision 0003](docs/decisions/0003-secrets-promise.md)).

### Extension points

- **MCP**: the `mcpServers` format Claude Desktop and Claude Code use; a
  "connector" is a pre-filled MCP configuration. The curated list holds only
  hosted servers the user signs in to in their own browser (MCP's OAuth,
  dynamic client registration): nothing to install, no app to register, no
  company IT step, so each works wherever the service itself can be opened.
  A service that refuses to register clients (HubSpot, Google Workspace) gets a
  guided one-time setup with an app the user registers
  ([decision 0004](docs/decisions/0004-connectors-sign-in-once-in-the-browser.md)).
- **Skills**: `SKILL.md` folders loaded on demand. Built-in: pptx, excel,
  word and skill-creator; users add their own in `skills_dir`.
- **Hooks**: scripts on lifecycle events (above).
- **Global instructions**: one `MEMORY.md` the user edits in Settings, read into
  every conversation. The model cannot write to it.

## Scope

Not built, on purpose:

- Hand-written mail/calendar integrations (MCP covers them), OAuth
  infrastructure, OpenAPI-to-tool generation, A2A.
- A general workflow canvas.
- Multi-level permission sources and team features: coscribe is local and
  single-user.
- Personas: memory and instructions cover the same need.

Known limits: schedules use the machine's time zone; the code module has
not yet been run on real Windows.
