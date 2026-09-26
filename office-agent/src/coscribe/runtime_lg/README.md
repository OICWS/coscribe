# runtime_lg -- migration findings

Evaluating a LangGraph-based replacement for `runtime/` + the vendored
`providers/gemini_provider.py`, after that adapter shipped three real bugs
in a row (schema `X | None` handling, missing `thought_signature` on
replay, `bytes`-vs-base64-text). **`cli.py`/`web/`/`coordinator.py`
themselves are still untouched** -- `../cli_lg.py` (Phase 2, below) is a
wholly new, separate, experimental command, not a change to the shipped
`coscribe` command.

## `coscribe-web-lg` slow startup -- root-caused with `python -X importtime`, one real waste found and fixed, the bigger cost is inherent

Live-reported by the user, previously only logged with three unconfirmed
candidates (see git history for the original text). Actually measured
this pass with `python3 -X importtime -c "import coscribe.web.app"`
(deterministic per-module import timing, not noisy wall-clock sampling)
plus a live `coscribe-web` start-to-`/api/tools`-responding timing
harness.

**Ruled out, with evidence, not just plausibility:**
- **MCP handshake** (`connect_mcp_tools_lg`) -- the live timing harness
  ran with zero MCP servers configured (confirmed via the startup log:
  `"MCP config file not found... starting with no MCP tools"`), and total
  startup was still ~1.6s. Whatever dominates, it isn't a per-server
  subprocess handshake that never ran.
- **`load_personas`/`load_skills`/`load_custom_providers` directory
  scans** -- didn't show up anywhere near the top of the importtime
  self-time breakdown; these are cheap directory listings, not the cost.

**Confirmed: it's overwhelmingly Python import-time cost**, exactly the
first (previously unconfirmed) candidate -- `coscribe.web.app`'s
cumulative import cost is itself the majority of total startup time, and
it's death by a thousand cuts across a genuinely large dependency graph
(`langchain`/`langgraph`/`langsmith`/`mcp`/`fastapi`/`reportlab`/
`openpyxl`/`pptx`/...), not one single dominant offender -- e.g. from one
importtime trace's self-time column: `mcp.types` 104ms, `fastapi.openapi.
models` 85ms, `langsmith.schemas` 82ms, `reportlab.lib.fonts` 71ms,
`langchain_core.tracers.event_stream` 47ms, `reportlab.platypus.
paragraph` 46ms, and dozens more in the single-digit-to-tens-of-ms range.

**One real, concrete waste found and fixed, though.** `tools/mcp.py` and
`web/app.py` both did `from aisuite.mcp.config import ...` purely to
reuse one validator function (confirmed, by reading it, to have zero
third-party dependencies of its own -- just `typing`) -- but importing
*any* submodule of a package always runs that package's own
`__init__.py` first, and aisuite's does `from .client import Client`
unconditionally, which itself does `from .mcp.client import MCPClient` in
a bare `try/except ImportError` -- meaning importing anything at all from
`aisuite` (even just the validator, even just `aisuite.provider.
ProviderFactory` for the real, still-needed `LLMClient.get_context_window`
path) always dragged in aisuite's *own* MCP client, which pulls in the
entire `mcp` SDK a second time (`langchain-mcp-adapters`, runtime_lg's
real connector, already needs `mcp` too -- this was pure duplicate
weight). Measured at ~485ms cumulative in the first importtime trace
(`aisuite`/`aisuite.client`/`aisuite.mcp.config`/`aisuite.mcp.client`
combined).

Fixed two ways:
1. `tools/mcp.py`: vendored a minimal `MCPConfig`/`validate_mcp_config`
   directly (pure Python, no new dependency) instead of importing
   aisuite's, narrowed to only the fields actually read downstream
   (`command`/`args`/`env`/`cwd`/`server_url`/`headers` -- confirmed via
   grep that `allowed_tools`/`use_tool_prefix`/`timeout_seconds`/
   `response_bytes_cap`/`lazy_connect` were never read by anything, so
   validating them was already dead weight before this change too).
   `web/app.py` now imports the same vendored version instead of
   aisuite's.
2. `runtime/llm_client.py`: its one remaining top-level `from aisuite.
   provider import ProviderFactory` (used only for the real,
   still-needed anthropic/openai built-in-provider path) moved to a
   lazy, function-local import inside `_resolve_provider`, matching this
   file's own existing convention for `providers/gemini_provider.py`
   (already lazily imported one function up). Deferred to first actual
   use of a built-in anthropic/openai provider instead of paid
   unconditionally on every process startup.

Confirmed via `sys.modules` after the fix (not just "should work"):
`import coscribe.web.app` alone no longer loads `aisuite` at all --
`'aisuite' in sys.modules` is `False`, where it was `True` before (with
`aisuite.mcp.client` and a second `mcp` SDK load along with it). A minor
mypy fallout from this, also fixed: aisuite has no `py.typed`/is under a
blanket `[[tool.mypy.overrides]] module = "aisuite.*"`, so its `MCPConfig`
was silently treated as untyped by mypy; the new local, fully-typed
`MCPConfig` TypedDict correctly isn't assignable to a plain `dict[str,
Any]` parameter (TypedDict-vs-dict variance), so `runtime_lg/mcp.py`'s
`config` parameters were retyped `Mapping[str, Any]` (read-only, which is
all they ever do with it) instead.

**Honest bottom line on impact, not overclaimed:** the `-X importtime`
trace shows this real, structural elimination (confirmed a strict subset
of what used to import) but the live start-to-serving timing harness
(3 runs before, 3 after, same machine, same warm disk cache) came back
statistically indistinguishable -- ~1.60s average before, ~1.62s average
after. This sandbox's timing noise is wide enough (single runs of
supposedly-identical code varied 1.4s-3.9s earlier in this same
investigation) that a ~100-150ms importtime-measured saving is real but
not the dominant term in ~1.6s of total startup -- worth keeping (it's a
genuine, verified waste removed, and reduces the tool's aisuite footprint
generally, not just at startup), but **not, on its own, a fix for
"startup feels slow."**

**The actual bigger lever, identified and then done in a same-day
follow-up (see below): `coscribe/tools/__init__.py` imports every
built-in tool module eagerly** (`documents`/`spreadsheets`/
`presentations`/...), and each pulls in its own heavy library at module
level regardless of whether that specific tool is ever called this
session -- `tools.documents` alone (markitdown/pypdf/reportlab) was
~371ms cumulative in the importtime trace, `tools.presentations`
(python-pptx) ~80ms, `tools.spreadsheets` (openpyxl) ~95ms.
`coordinator.py` needs every tool's *metadata* (name/docstring/schema) up
front to register it with the model, but not the underlying library
(`pptx`, `openpyxl`, `reportlab`) until that specific tool is actually
*called* -- deferring each tool function's own heavy import to inside its
own function body (the same lazy-import pattern this pass just applied to
`aisuite`, and the pattern `providers/gemini_provider.py`'s `google.genai`
import already uses).

**Verification.** `pytest -q`: 245 passed, 1 skipped (no change in count
-- this pass was a fix + a root-cause writeup, not new features).
`ruff check src tests scripts`/`mypy src` both clean.

## The bigger lever, done: lazy-importing each built-in tool's own heavy library

Same-day follow-up to the section above, at the user's explicit request
("继续把启动优化做完" -- finish the startup optimization) after weighing
it against starting Tauri desktop-packaging research instead (see the
Phase 6 planning note further down) -- checked this sandbox's Tauri Linux
build deps first (`pkg-config --exists webkit2gtk-4.1`/`gtk+-3.0`, both
missing, no `$DISPLAY` either) and confirmed that work can't be usefully
verified here anyway, so finishing this concrete, fully-testable win first
was the right call.

**Design.** `documents.py`/`spreadsheets.py`/`presentations.py`'s heavy
third-party imports (`mammoth`, `pdfplumber`, `docx`, `markdownify`,
`reportlab.*`, `openpyxl`, `pptx`) moved from module level into the
specific method body that actually uses each one -- `read_docx` imports
`mammoth`/`markdownify` itself, `write_docx` imports `docx.Document`
itself, `write_pdf` imports its four `reportlab` symbols itself, and so
on, one `import` statement per consuming function rather than one shared
module-level block. Two type-only imports (`openpyxl.worksheet.worksheet.
Worksheet`, `pptx.presentation.Presentation as PresentationType`) moved
under `if TYPE_CHECKING:` instead, since `from __future__ import
annotations` already means neither is ever evaluated at runtime -- mypy
sees them, nothing else needs to.

**Confirmed safe against the one real way this pattern can silently
backfire**, not just assumed: LangChain's tool-schema introspection reads
every registered tool function's parameter/return type annotations to
build its JSON schema, so a tool-facing function typed with one of these
libraries' own classes (e.g. a hypothetical `-> Worksheet`) would force
the "lazy" import open again the moment the tool gets registered --
which happens for every built-in tool at every agent-build, regardless of
whether it's ever called. Checked directly: every actual tool-facing
function in these three modules (`read_docx`/`write_docx`/`read_pdf`/
`write_pdf`/`search_pdf`/`read_xlsx`/`write_xlsx`/`read_pptx`/
`write_pptx`) is typed with only `str`/`int`/`dict`/`list`/`Optional` --
never a library type -- so registration alone never re-triggers the
import; only an actual call does.

**Verification.**
- `pytest -q`: 245 passed, 1 skipped, unchanged -- the existing document/
  spreadsheet/presentation test suites already exercise the real
  libraries (round-trip write-then-read, not mocked), so this alone
  already covers the relocation.
- Live, direct: built each toolkit's tools via `build_document_tools`/
  `build_spreadsheet_tools`/`build_presentation_tools` and called
  `write_docx`/`read_docx`/`write_pdf`/`read_pdf`/`write_xlsx`/
  `read_xlsx`/`write_pptx`/`read_pptx` end-to-end against a real temp
  directory -- all eight produced correct real files and correct
  round-tripped content (confirmed `write_pptx`'s `qa_skipped_reason`
  still correctly reports LibreOffice missing in this sandbox, same
  pre-existing, unrelated behavior as before).
- `python3 -X importtime -c "import coscribe.web.app"`: cumulative
  cost for the whole import dropped from ~1.67s (previous section's
  after-state) to **~1.13s**. Confirmed via `sys.modules` after a plain
  `import coscribe.web.app`: `pptx`/`openpyxl`/`reportlab`/`docx`/
  `mammoth`/`pdfplumber` are now all `False` -- genuinely not loaded
  until a tool that needs one is actually called.
- Live `coscribe-web` start-to-`/api/tools`-responding timing (3
  runs): **~1.1-1.4s, down from ~1.6s** in the previous section's
  measurement -- this time a real, measurable wall-clock improvement
  (unlike the aisuite fix alone), consistent with the importtime
  reduction being large enough to clear this sandbox's noise floor.

**A real, recurring sandbox issue struck again during this exact pass,
recovered the same documented way.** Mid-comparison (after stashing the
in-progress lazy-import edits to measure a clean "before" state, then
popping them back), a `pytest` run surfaced `tests/test_gemini_provider.py`
-- a file confirmed deleted in this same README's "audit + delete old
runtime" section, run months of commits ago. `git log --oneline` showed
HEAD sitting at `fffe0ec` (a `websearch:` commit from *before* the web
cutover, the audit, and this entire session's max_turns/auto_compact/
startup work), even though nothing in this session had run a destructive
git command since the last confirmed-good push. Recovered the same way
documented earlier in this file: `git fetch origin <branch>` (confirmed
the remote still correctly had `7bec619` as its tip), `git stash push`
the 3 in-progress files, `git reset --hard origin/<branch>`, `git stash
pop` (applied cleanly, no conflicts) -- then re-ran the full verification
suite from scratch to confirm nothing was silently lost, rather than
trusting the recovery on faith.

## `spawn_agent`'s nested-interrupt bridge -- closes Phase 1's open question

Phase 1 flagged `spawn_agent`/`review_work` feasibility as "genuinely
unverified": would an `interrupt()` raised inside a sub-agent's own tool
call correctly pause/resume through both graph layers? `subagents.py`
(`build_spawn_agent_tool`) answers this, live-verified against Gemini via
`../../../scripts/verify_nested_interrupt.py`, not just designed on paper.

**The real finding, and it's not what the feasibility verdict guessed:** a
sub-agent built via `build_langgraph_agent` is a separately-compiled graph
with its own checkpointer/thread_id. When one of its own tools triggers
`HumanInTheLoopMiddleware`'s `interrupt()`, the sub-agent's own Pregel
executor absorbs that internally and returns *normally* from
`sub_agent.invoke(...)`, with `sub_agent.get_state(...).next` populated --
nothing propagates to the parent automatically, and a naive
`sub_agent.invoke(...)`-then-return-last-message spawn_agent would silently
strand the sub-agent waiting for an approval nobody would ever see. Fixed
by having spawn_agent check the child's own pending state after invoking
it and, if paused, call `interrupt()` **itself** using the same payload
`HumanInTheLoopMiddleware` already produces -- so the existing approval
UI/wire protocol needs zero changes to render a nested approval identically
to a top-level one. The child's thread_id is derived from the parent tool
call's own injected `tool_call_id` (via `InjectedToolCallId`), not a fresh
uuid -- needs to survive the parent tool node re-running from the top on
its own resume, which a randomly generated id would not.

**A second worry, raised and then disproven, not just asserted away:**
before writing the code, the reasoning went that a Python `while` loop
bridging *multiple* rounds of child approval within one spawn_agent call
would be broken -- LangGraph re-runs a resumed node's function from the
top, so a loop iteration's `interrupt()` call resolving from cache (a
prior round already answered it) would still fall through to the
side-effecting `sub_agent.invoke(Command(resume=...))` line right after
it, seemingly reapplying an already-consumed decision to whatever the
child is *actually* paused on now. Live-tested specifically to check this,
not left as a plausible-sounding guess: `verify_nested_interrupt.py`'s
two-round scenario forces a sub-agent through two *sequential*
approval-gated writes (a real read-after-write dependency, so Gemini can't
batch both into one turn), decides round 1 APPROVE and round 2 REJECT
specifically to catch round 2 silently reusing round 1's decision -- and
the result was correct both times (round 1's file written, round 2's
correctly not). The worry was wrong: each `get_state()` call inside the
loop reads the child's real, currently-persisted state, which already
reflects whatever the *previous* iteration's real resume actually did, so
the loop advances one genuine round at a time rather than replaying stale
values. `build_spawn_agent_tool`'s `max_rounds` (default 4) is a safety
cap against a sub-agent that never stops asking, not evidence of a
correctness ceiling.

**Concurrent independent sub-agents spawned from the same parent turn --
now exercised, and it found two real bugs.** See "Concurrent spawn_agent
calls" below.

**Three levels of nesting (a sub-agent's own approval-gated call triggering
a sub-sub-agent) is not just unexercised -- it's not actually reachable.**
`build_spawn_agent_tool`'s `available_tools` param is always the *parent's*
own tool list, passed *before* spawn_agent itself is appended to it
(`cli_lg.py`/`session_lg.py` both do this) -- so a sub-agent's own
selectable `tool_names` never includes `"spawn_agent"` in the first place;
`tools_by_name`'s own self-exclusion is redundant defense-in-depth, not the
only thing preventing recursion. This is a deliberate scope boundary
(mirrors `tools/subagents.py`'s `_DISALLOWED_SUBAGENT_TOOLS`), not a gap to
close later.

**Wired into `coscribe-lg`/`coscribe-web-lg`, not left as a
standalone-script-only proof.** `cli_lg.py`'s `chat()` and
`session_lg.py`'s `ChatSessionLG.__init__` both now build
`spawn_agent_tool = build_spawn_agent_tool(model, agent.tools)` from the
tool list *before* appending it, then pass `[*agent.tools,
spawn_agent_tool]` into `build_langgraph_agent` -- excluding spawn_agent
from its own selectable subset (`tools_by_name` in `subagents.py` also
filters it out defensively, mirroring `tools/subagents.py`'s
`_DISALLOWED_SUBAGENT_TOOLS`). Live-verified through the real
`coscribe-lg` CLI (not just `verify_nested_interrupt.py`'s standalone
agent construction): "Delegate this to spawn_agent, giving it write_file:
write a file called hello.txt containing exactly: hi there" correctly
surfaced `[approval required] write_file(...)` through `cli_lg.py`'s
*existing*, unmodified `_resolve_pending_approvals` -- confirming the
bridged interrupt's payload shape really does match
`HumanInTheLoopMiddleware`'s own closely enough that no changes were
needed there. Approving wrote `hello.txt` with the exact requested
content.

**`review_work` -- spawn_agent's fixed-Reviewer sibling, also now wired
in.** `build_review_work_tool(model)` reuses `tools/subagents.py`'s
`REVIEWER_INSTRUCTIONS` string unmodified (imported directly, not
duplicated) and builds a tools-less `build_langgraph_agent` -- none of
spawn_agent's nested-interrupt bridging applies here, since a Reviewer has
no tools at all and its graph can never pause. `cli_lg.py`/`session_lg.py`
add it to the tool list alongside spawn_agent. Live-verified through
`coscribe-lg`: asking the model to call `review_work` on a sample
haiku returned the fixed Reviewer's own actual assessment ("This is
correct. It follows the 5-7-5 syllable structure perfectly...").

**Future idea, deliberately not started -- fixed "expert" presets on top
of spawn_agent.** Discussed and set aside for now: `spawn_agent` stays
general-purpose by design (the model picks `instructions`/`tool_names`
fresh each call, no hardcoded per-domain roster -- same principle
`tools/subagents.py`'s module docstring already states for the old
runtime). If a specific, recurring task type shows up often enough to be
worth locking down (a concrete candidate already seen live: SAP/browser-
automation workflows), the right shape is a small new tool layered *on
top* of spawn_agent's existing plumbing -- exactly how `review_work`
already relates to it (fixed instructions/tool subset, same underlying
mechanism) -- not a change to spawn_agent's own general signature. Not
worth building speculatively; needs a concrete recurring role in hand
first.

## Concurrent spawn_agent calls -- two real bugs found and fixed

Tested the scenario the "not exercised" note above used to flag: the
parent proposes *two* `spawn_agent` calls in one `AIMessage`. LangGraph's
Pregel executor runs same-superstep tool-node tasks concurrently, so both
calls run on separate OS threads at once, each independently bridging its
own child's approval-gated call via `interrupt()`. This is the first time
that path was actually exercised, and it surfaced two real, previously
invisible bugs -- both live-verified against real Gemini, both fixed.

**Bug 1: a single shared `Command(resume=...)` value cannot resolve more
than one simultaneously-pending interrupt.** Confirmed directly: with two
concurrent `spawn_agent` calls each paused on their own child's approval,
`agent.get_state(config).tasks` has two entries, each with its own
`Interrupt` carrying its own `.id` (derived from that task's checkpoint
namespace). `Command(resume={"decisions": [...]})` -- the shape every
existing single-task interrupt handler in this codebase used -- raised
`RuntimeError: When there are multiple pending interrupts, you must
specify the interrupt id when resuming` the moment a second task was
pending at the same time as the first (LangGraph's own docs:
`resume` accepts either "a single value ... to resume the *next*
interrupt" or "a mapping of interrupt ids to resume values" -- only the
latter handles more than one at once). **Fixed** in
`web/session_lg.py`'s `_resolve_pending_approvals`: decisions are now
collected per-task and resumed via `Command(resume={interrupt.id: {...},
...})`, one entry per pending task, instead of one shared value for all of
them. A consequence worth knowing, not just an implementation detail:
multiple pending tasks are decided *sequentially* -- the second task's own
`interrupt()` inside `spawn_agent` isn't even reached, and so its
`approval_required` isn't sent, until the first task's decision has been
fully resolved. A client (or test) expecting to receive both
`approval_required` messages before answering either one will deadlock
against this method, not against LangGraph.

**Bug 2, more serious: a resumed `spawn_agent` call was silently re-asking
its child from scratch and applying the human's decision to the wrong
question.** `interrupt()`'s own documented semantics -- "resumes from the
start of the node, re-executing all logic" -- apply to `spawn_agent`'s
*entire* function body on every resume, not just the code after its
`interrupt()` call. `sub_agent = build_langgraph_agent(model, selected,
instructions)` used to pass no `checkpointer`, defaulting to a *fresh*
`InMemorySaver()` -- rebuilt on every single call, including every replay.
Debug instrumentation confirmed it directly: `id(sub_agent)` differs
between the pre-interrupt and post-resume executions of the *same*
`spawn_agent` call. So on resume, `sub_agent.get_state(child_config)` read
back empty on the new, blank saver, `if not state.values:
sub_agent.invoke(...)` silently re-asked the child *from scratch* with the
original prompt, and the human's resume decision -- meant for the
*original* pending question -- got applied to whatever *new* question that
fresh ask happened to produce instead. This was invisible in every prior
test (both the single-round and two-round scenarios in
`scripts/verify_nested_interrupt.py`) purely by luck: the redundant re-ask
happened to propose the identical tool call the second time, so
re-approving/re-rejecting a duplicate that looked identical to the
original produced a correct-looking result even though the mechanism
underneath was wrong. The concurrent-call test broke that luck -- after
both concurrent calls had already completed successfully, a *third*,
unexpected `approval_required` for one of them appeared, which traced back
to exactly this. **Fixed** by giving each `build_spawn_agent_tool` closure
one `InMemorySaver` shared across every call it ever makes (not per-call,
not per-replay) -- `sub_agent`'s *compiled graph* object still gets
rebuilt on every call/replay (cheap, harmless), but `get_state`/`invoke`
against the *same* checkpointer now correctly finds the real, already-
persisted checkpoint for a given child thread_id, so a replay resumes the
actual pending question instead of silently asking a new one.

**A real, separate side-effect noticed while fixing this, not a bug:** a
sub-agent's own streamed narration bleeds into the parent's own
`agent_delta` stream. When a tool synchronously invokes another compiled
graph (`sub_agent.invoke(...)` inside `spawn_agent`) while the *parent*
graph is itself being driven via `astream(stream_mode=["messages"])`,
LangGraph propagates the sub-graph's own run's messages up into the
parent's stream too -- so the child's "done" reply shows up as an
`agent_delta` event before the parent's own final answer does. Harmless
(the final written files and approval outcomes were all correct
throughout), but worth knowing if a user-facing transcript ever looks like
it's narrating a sub-agent's internal monologue.

**Live-verified against real Gemini, both before and after the fix.**
Before: `Command(resume={"decisions": [...]})` raised the multi-interrupt
`RuntimeError` exactly as predicted. After: two concurrent `spawn_agent`
calls, one approved (a.txt written) and one rejected (b.txt correctly
never created), resolved in a single `Command(resume={id: ..., id: ...})`
call with `state.next` immediately empty afterward -- no lingering or
phantom interrupts. A permanent, runnable regression script capturing this
exact scenario lives at `scripts/verify_concurrent_spawn_agent.py`
(same "standalone, not pytest, needs a real key" pattern as
`verify_nested_interrupt.py`). A deterministic automated test also exists
(`tests/test_web_lg.py::test_concurrent_spawn_agent_approvals_resolve_
independently`), using a content-routing fake model instead of an
index-based one -- necessary because two concurrent sub-agent model calls
genuinely race on separate OS threads sharing one fake model instance, so
an index counter (`FakeToolCallingChatModel`'s own approach, fine for
every single-threaded test in this file) would nondeterministically assign
a canned response to the wrong thread.

## MCP tools under runtime_lg -- verified live, one real bug fixed, one real gap found

Never exercised through any of Phases 1-3 until now. Connected the
zero-config `@modelcontextprotocol/server-memory` catalog entry (no API
key/browser needed) through `tools/connect_mcp_tools` -- the same function
`cli_lg.py`/`app_lg.py` already call -- and fed the resulting tools into
`build_langgraph_agent`.

**Bug 1, fixed: `create_agent()` crashed immediately with
`AttributeError: 'MCPToolWrapper' object has no attribute '__qualname__'`.**
aisuite's `MCPToolWrapper` sets `__name__` on each tool instance but never
`__qualname__`; LangChain's `create_schema_from_function` reads
`func.__qualname__` unconditionally (no `getattr`/`hasattr` guard) to
detect a bound method. A no-op for this runtime's own `Runner`, which
never touches `__qualname__` at all -- but a hard crash under runtime_lg.
Fixed in `tools/mcp.py`'s `connect_one_mcp_server` (`tool.__qualname__ =
tool.__name__`, right next to the existing signature-ordering workaround
for the same wrapper class) rather than in runtime_lg itself, since it's
purely additive and belongs with that existing "real aisuite gap" note.

**Bug 2, found, not fixed: MCP tool schemas are lossy under runtime_lg,
in a way that broke a real tool call.** `MCPToolWrapper.__init__` keeps
the tool's *real*, detailed JSON Schema on `self.__mcp_input_schema__`
(field names, per-field types, nested object shapes) -- but also
collapses it into a generic Python signature (`self.__signature__`,
`self.__annotations__`) for `__call__`'s sake, e.g. `create_entities`'s
`entities` parameter becomes just `List[dict]` with no indication its
items need `entityType`/`name`/`observations` keys specifically. This
runtime's own `Runner` presumably reads `__mcp_input_schema__` directly
when building its own LLM tool-calling request (not confirmed by reading
its source, inferred from behavior) -- **LangChain's `create_agent` has
no knowledge `__mcp_input_schema__` exists**, only `inspect.signature()`,
so it builds an args_schema with `"additionalProperties": true` and zero
nested field info. Live-verified failure, not a hypothetical: asked the
model (Gemini) to create a memory entity, it called
`memory__create_entities` with `{"type": "test", ...}` (guessing a
plausible-sounding key) instead of the schema's real `entityType`,
got `MCP error -32602: ... expected string, received undefined at
entities[0].entityType` back, and retried the identical wrong call
rather than correcting -- looping rather than succeeding.

**Bug 2's real fix, found and live-verified: `langchain-mcp-adapters`
(LangChain's own official MCP client) instead of routing MCP through
aisuite's `MCPClient` at all.** Not a custom JSON-Schema-to-pydantic
adapter written here -- a maintained upstream library
(`langchain-mcp-adapters`, `pip install`-able, `MultiServerMCPClient` +
`get_tools()`) that does this correctly already.
`convert_mcp_tool_to_langchain_tool` (its `tools.py`) builds each
`StructuredTool` with `args_schema=tool.inputSchema` -- the MCP server's
*real* raw JSON Schema handed to `StructuredTool` directly, no lossy
Python-signature round-trip at all. Confirmed by inspecting the same
`create_entities` tool this bug was found through: its `args_schema` came
back with `entityType`/`name`/`observations` and their types/required-ness
all present verbatim, none of aisuite's `"additionalProperties": true`
flattening. Re-ran the exact live scenario Bug 2 was found through (ask
Gemini to create a memory entity via `build_langgraph_agent`) through this
library instead of `tools/connect_mcp_tools` -- correct on the first call,
`entityType` right every time, no wrong-field-name guess, no retry loop.
(Cosmetic-only noise seen along the way: Gemini's schema translation logs
"Key '$schema' is not supported in schema, ignoring" for every tool, since
MCP's `inputSchema` carries a `$schema` meta-key Gemini's own schema
dialect doesn't recognize -- stripped automatically, no effect on the
successful result above.)

**Now actually wired in, not just verified.** `runtime_lg/mcp.py`
(`connect_mcp_tools_lg`) is a new, `runtime_lg`-specific connector --
`MultiServerMCPClient` in place of aisuite's `MCPClient`, reading the
*same* `mcp.json` config file shape via `tools/mcp.py`'s own
`load_mcp_server_configs` for continuity (that half was never the broken
part, only aisuite's tool-wrapping was -- see `mcp.py`'s module
docstring). `cli_lg.py` and `app_lg.py` call it instead of
`tools/connect_mcp_tools` now. One real, previously-unexercised gap
surfaced and got fixed along the way: `agent.py`'s/`subagents.py`'s tool-
name lookups only checked `__name__` (true for aisuite's `MCPToolWrapper`,
false for a LangChain `BaseTool`/`StructuredTool`, which has `.name`
instead) -- `build_spawn_agent_tool`'s `tools_by_name` would have crashed
the moment an MCP tool was offered to a sub-agent. Fixed by extracting the
existing dual-lookup (`.name` then `__name__`) already used for approval-
gating into a shared `agent.tool_name()` helper, reused in both places.

**Different connection lifecycle than the aisuite path, confirmed to
matter for cleanup code, not just a lifecycle detail:** `MultiServerMCPClient`
opens a fresh session per tool call rather than a persistent
client/subprocess (see its `get_tools()` docstring) -- there's no
`mcp_client.close()` to call anymore. `cli_lg.py`'s `finally:` block that
used to do this is gone entirely (its whole surrounding `try:` was only
there for that); `app_lg.py`'s `lifespan` connects the tools inside its
own `async with` block instead of before `FastAPI(lifespan=...)`
construction, since `connect_mcp_tools_lg` is async and `create_app_lg`
itself isn't.

**Correction, found later via live user testing, not by this pass's own
review: "a fresh session per tool call" is not a benign lifecycle detail,
it's a real bug that silently breaks any stateful MCP tool.** See the
"Persistent MCP sessions" section further down -- `connect_mcp_tools_lg`/
`connect_one_mcp_server_lg` now return a connection object that *does*
need closing again, the opposite of what this paragraph originally said.
Left the paragraph above intact rather than rewritten, since the mistaken
reasoning in it (a fresh session seemed harmless because nothing at the
time exercised a *stateful* MCP tool) is itself worth keeping visible.

**Live-verified end-to-end through both real entry points, and this
surfaced a real, CLI-specific gap `coscribe-web-lg` doesn't share.**
Through `coscribe-lg` (sync `.stream()`/`.invoke()`): connecting
worked (`Loaded 9 MCP tool(s).`), the approval prompt showed the *correct*
`entityType` field on the very first call (the exact failure Bug 2 was
about, now gone) -- but executing the approved call raised `StructuredTool
does not support sync invocation`. langchain-mcp-adapters' MCP tools are
async-only (an MCP call is inherently a subprocess/session round-trip);
LangGraph's sync `.stream()` path can't call them. Re-ran the identical
scenario through `coscribe-web-lg` (`astream`/`ainvoke`, `session_lg.py`'s
own async path) and it completed cleanly end to end: approval card,
correct execution, correct final reply, `entityType` right throughout.
**Not fixed for `cli_lg.py`**: doing so means converting its REPL loop to
run on an event loop (`ainvoke`/`astream` throughout), a real but separate
piece of work from this MCP integration itself, and CLI feature parity was
already deprioritized in favor of the web entry point (see the earlier
"switch to LangChain" discussion) -- so MCP tool *calls* specifically
don't work yet through `coscribe-lg`, tracked here rather than fixed
speculatively. Connecting/listing tools and every non-MCP tool call
(built-ins, spawn_agent, review_work) are unaffected -- confirmed by the
same run, which reached the approval prompt with the right schema before
hitting the sync/async wall specifically at tool *execution*.
(Cosmetic-only noise seen on both paths: Gemini's schema translation logs
"Key '$schema' is not supported in schema, ignoring" for every tool, since
MCP's `inputSchema` carries a `$schema` meta-key Gemini's own schema
dialect doesn't recognize -- stripped automatically, no effect on either
successful result above.)

## Persistent MCP sessions -- a real bug, live-reported during user testing, not caught by this project's own review

Found the way most of this project's real bugs actually get found: a user
testing `coscribe-web-lg` for real, against a real MCP server
(`@playwright/mcp`, browser automation), not by inspecting the code.
Reported symptom: `playwright_browser_navigate` opened a page, and the
page closed again *immediately* -- and a single navigate call took on the
order of a minute on real hardware. First suspected a corporate proxy
(the user had just configured one, and it *had* broken an unrelated
Selenium script) -- ruled that out by walking through what a proxy could
and couldn't explain, then went back to this module's own "Different
connection lifecycle" paragraph above and re-read it with the actual
symptom in mind instead of assuming it as background fact.

**Root cause, confirmed from `langchain-mcp-adapters`' own source, not
guessed:** `MultiServerMCPClient.get_tools()`'s docstring says plainly "A
new session will be created for each tool call." For a stateless tool
(read a file, look something up) that's invisible. For a *stateful* one --
above all browser automation, where `navigate`/`click`/`fill`/`screenshot`
only make sense against the *same* open browser/page across several calls
-- it's fatal: every single call was spawning a brand-new `npx
@playwright/mcp` subprocess and a brand-new Chromium, running exactly one
action, then tearing the whole thing down the instant that call returned.
That's the immediate-close (the process backing the page was gone) *and*
the minute-long latency (a cold Chromium launch on *every single tool
call*, not just the first) in one root cause. Confirmed by reading
`langchain_mcp_adapters/tools.py`'s `_execute_tool_async`/`execute_tool`
directly: `if session is None: async with create_session(...) as
tool_session: ... call_tool(...)` -- a fresh `create_session` on every
call whenever a bare `connection=` (not a live `session=`) was handed in,
which is exactly what `client.get_tools(server_name=...)` (this module's
original implementation) does under the hood. `coscribe-web`'s
aisuite-based `tools/connect_mcp_tools` never had this problem -- its
`MCPClient` is one persistent client/subprocess per server, alive for the
app's lifetime; nothing about that path routes through `langchain-mcp-
adapters` at all.

**Fix: `McpServerConnection` (new, in this module), one persistent
`ClientSession` per server instead of a fresh one per call.**
`MultiServerMCPClient` does expose a persistent-session path --
`client.session(name)`, an async context manager yielding a live
`ClientSession` that `load_mcp_tools(session, ...)` can bind tools to
directly, bypassing the per-call `create_session` branch entirely once
built this way. The nontrivial part wasn't finding that API, it was
*holding it open safely*: a caller needs to open this session in response
to one request (app startup, or a `POST /api/mcp/servers` call) and close
it in response to a *different*, later one (app shutdown, or a `DELETE
/api/mcp/servers/{name}` call) -- and anyio's stdio transport ties its
internal task-group-scoped resources to whichever `asyncio` task opened
them, so entering the context manager in one request-handling task and
exiting it from a different one later raises `RuntimeError: Attempted to
exit cancel scope in a different task than it was entered in`.
`McpServerConnection` sidesteps this by never touching the session from
outside a single dedicated background task: `connect()` starts a task that
opens `async with client.session(name)`, builds and tags the tools,
signals readiness via an `asyncio.Event`, then just waits on a second
`asyncio.Event`; `close()` sets that second event and awaits the task,
so both the `async with` block's entry *and* its exit happen inside that
one task's lifetime throughout, regardless of which external task called
`connect()`/`close()` or when.

**Live-verified with a real subprocess, not mocked** --
`scripts/verify_mcp_persistent_session.py` spawns a tiny custom stateful
MCP stdio server (`_verify_mcp_persistent_session_server.py`, a `FastMCP`
server with one tool, `increment()`, that adds 1 to an in-process counter
and returns it -- no network/npm dependency, deterministic, fast) and
calls it twice via the old `get_tools()`-per-call path, then three times
via the new `McpServerConnection` path. The old path returned `[1, 1]`
(confirming the bug directly: two calls, two fresh processes, the counter
reset both times); the new path returned `[1, 2, 3]` (confirming the fix:
the same process handled all three calls). Both results matched exactly
what the root-cause analysis predicted before the script was ever run.

**`app_lg.py`'s connect/disconnect wiring updated to match:**
`connect_mcp_tools_lg`/`connect_one_mcp_server_lg` now return `(tools,
connection-or-connections)` instead of just a tool list -- a real,
intentional breaking change to both functions' public contracts, not an
addition. `create_app_lg` keeps a new `mcp_connections: dict[str,
McpServerConnection]` holder alongside the existing `extra_tools_holder`;
`_disconnect_mcp_server_lg` is now `async` and actually calls `.close()`
on a server's connection (previously a no-op beyond filtering the tool
list, back when there was nothing to close), and `lifespan` closes every
remaining connection after its own `yield`, so an MCP server's subprocess
-- a lingering Chromium, most visibly -- doesn't outlive this app's own
process. `cli_lg.py`'s bridging call was updated to unpack the new tuple
but deliberately does *not* wire in the same closing logic: MCP tool
*calls* already don't work in that CLI at all (see the sync/async gap
noted earlier in this section), so there is no live session there worth
keeping tidy in the one entry point where MCP is otherwise non-functional
regardless.

**Tests updated to match the new contract**, not just left passing by
accident: `test_mcp_lg.py`'s fake `MultiServerMCPClient` now implements
`session(name)` (an async context manager) instead of `get_tools()`, plus
two new tests exercising `McpServerConnection.close()` directly (that it
actually waits for the background task to finish, and that calling it
twice is safe); `test_web_lg.py`'s MCP-server fakes/stubs updated to
return `(tools, connection)` tuples. All 68 tests in `test_web_lg.py`/
`test_mcp_lg.py` pass.

**Accepted trade-off, not a new limitation introduced here:** a
persistent session (and therefore a persistent browser, for Playwright) is
now shared by *every* thread in this process, same as aisuite's
`MCPClient` already is for `coscribe-web` today -- two unrelated
conversations both calling `playwright_browser_navigate` would drive the
*same* browser/tabs. Matches existing behavior, not a regression; a
session-per-thread pool would fix it but is real added complexity this
pass wasn't asked to take on.

## A tool's own raised exception used to crash the whole turn -- found testing the persistent-session fix above, same live session

Found immediately after fixing the MCP session bug above, testing the
exact same real scenario (Playwright against an internal SAP GUI URL,
recorded as a `/startworkflow`): the agent called `read_file("sap_login.md")`
looking for a saved login procedure, the file didn't exist, and the whole
turn died on the spot -- a bare `error: File does not exist: sap_login.md`
in the chat, then nothing: no further `agent_message`, no `tasks_changed`,
indistinguishable from the turn hanging forever. Reported by the user as
"stuck," and it genuinely was -- `_handle_user_message_locked`'s `except
Exception` had already returned by the time anyone could tell.

**Root cause, confirmed from `langgraph.prebuilt.tool_node`'s own source,
not assumed:** `create_agent`'s internally-built `ToolNode` defaults
`handle_tool_errors` to `_default_handle_tool_errors`, which is:
```python
def _default_handle_tool_errors(e: Exception) -> str:
    if isinstance(e, ToolInvocationError):
        return e.message
    raise e
```
Only a framework-internal `ToolInvocationError` (bad tool name, bad
arguments) gets converted into a graceful error `ToolMessage` by default --
**every other exception, including a plain `ValueError` a tool function
raises itself, is explicitly re-raised.** This isn't a corner case: nearly
every built-in coscribe tool raises a plain `ValueError` for an
ordinary "the input was bad" case as a matter of course (`tools/files.py`'s
`read_file`: `raise ValueError(f"File does not exist: {path}")`;
`tools/documents.py`, `spreadsheets.py`, `presentations.py`, `skills.py`
all do the identical thing). Under the old runtime, aisuite's `Tools.
execute_tool` catches these and feeds them back to the model as a normal
tool error, so this pattern has always been safe to write throughout
`tools/*.py` -- runtime_lg silently didn't honor that same contract, for
*any* tool, since Phase 1, and nothing in this project's own review or test
suite happened to exercise a tool's raised-exception path against a real
turn until a live user hit it.

**Fix: `_CatchToolErrorsMiddleware` (new, `runtime_lg/agent.py`),
`wrap_tool_call`/`awrap_tool_call` middleware installed on every graph
`build_langgraph_agent` builds** -- `create_agent` has no public parameter
to reconfigure the internal `ToolNode`'s own `handle_tool_errors`, but
`wrap_tool_call` is LangChain's documented interception point for exactly
this. Catches whatever the wrapped handler raises and returns an error
`ToolMessage` instead, same contract `ToolInvocationError` already gets.

**Must not catch `GraphInterrupt` -- checked deliberately, not assumed
safe.** `HumanInTheLoopMiddleware`'s approval pause is itself implemented
as an exception (`GraphInterrupt`) propagating up through this exact same
handler call, and `GraphInterrupt`'s base class, `GraphBubbleUp`
(`langgraph.errors`), is a subclass of plain `Exception` -- a bare `except
Exception` here would have silently swallowed every approval pause in the
app and turned it into a bogus successful-looking tool result instead of
ever actually stopping for a human. Excluded explicitly (`except
GraphBubbleUp: raise` before the catch-all) and live-verified with a real
`HumanInTheLoopMiddleware` + an approval-gated tool call: the interrupt
still pauses (`state.next` populated) and resumes correctly with this
middleware installed alongside it.

**A second, genuinely subtle bug found by this project's own test suite
immediately after the first fix landed, not caught by manual testing --
worth documenting for what it says about this codebase's sync/async
seams.** A first version of this fix used the single-function `@wrap_tool_
call` decorator on an `async def`, which only ever generates the
*async* hook (`awrap_tool_call`) -- reasonable, since every top-level graph
in this app runs via `astream`/`ainvoke`. But `build_langgraph_agent` is
also how `runtime_lg/subagents.py`'s `spawn_agent` builds its *child*
graph, and `spawn_agent`'s own tool body drives that child graph
*synchronously* (`sub_agent.invoke(...)`/`sub_agent.get_state(...)`), even
though `spawn_agent` itself is always called from an async top-level
graph. The moment a gated tool call resolved *inside* a sub-agent's own
sync `.invoke()`, LangGraph needed the *sync* `wrap_tool_call` hook --
which didn't exist, so it raised `NotImplementedError: Synchronous
implementation of wrap_tool_call is not available...`. That
`NotImplementedError`, raised from inside `spawn_agent`'s own tool body,
then got caught by *this exact same middleware* running on the *parent*
graph (since `spawn_agent` is itself a tool call there) and silently
turned into a bogus successful-looking tool result -- no crash, no `error`
WS message, just both concurrent sub-agents' files quietly never written.
Caught immediately by `test_concurrent_spawn_agent_approvals_resolve_
independently` (an existing test, not a new one written for this bug)
failing with a plain missing-file assertion and no other clue. Fixed by
subclassing `AgentMiddleware` directly and implementing *both* `wrap_tool_
call` and `awrap_tool_call` with identical logic, rather than relying on
the decorator to generate only one.

**Verified two ways.** A new test, `test_tool_raising_a_plain_exception_
is_reported_to_the_model_not_a_crashed_turn`, scripts exactly the live-
reported scenario (a `read_file` call on a nonexistent path) and asserts
the turn completes normally (an `agent_message`, `tasks_changed`, no
`{"type": "error"}`) with the tool's real error text visible in its
`tool_result`. Live-verified directly against a real `build_langgraph_
agent()` graph too (not just the WS-level test): the exact reported
`ValueError("File does not exist: sap_login.md")` scenario, run standalone,
confirms the model sees `ToolMessage('File does not exist: sap_login.md')`
and gets to respond in its next turn instead of the graph raising. All 69
tests in `test_web_lg.py`/`test_mcp_lg.py` pass, including the concurrent-
spawn_agent regression this bug's own fix briefly broke.

## `GET/DELETE /api/threads` pointed at the wrong storage entirely -- found immediately after, same live session

Reported as "switching sessions, can't see history" right after the two
fixes above. `app_lg.py`'s `list_threads`/`delete_thread` were direct,
unmodified ports of `web/app.py`'s identical endpoints -- which glob
`settings.state_dir` for `*.json` files, because that's where the old
runtime's `FileStateStore` persists one JSON file per thread. runtime_lg
never writes those files at all: every thread's conversation history lives
in the shared `AsyncSqliteSaver` checkpointer
(`runtime_lg_checkpoints.sqlite`). The glob-based `list_threads` therefore
always returned an empty list, silently, no error -- the session-switcher
menu (which calls this on open) just never had anything to show, even
though every thread's real state was sitting right there in the checkpoint
database the whole time. `delete_thread` had the identical problem in
reverse (deleting a `.json` file that was never written does nothing) --
this half was undiscovered until fixing the first half surfaced it, since
nothing ever exercised it live.

**Fix: query the checkpointer's own `checkpoints` table directly.**
`AsyncSqliteSaver` has no built-in "list every thread" method (only
per-thread `aget_tuple`/`alist`), but its underlying `aiosqlite` connection
is a public attribute (`checkpointer.conn`) and the schema is simple --
confirmed directly, not assumed, by writing two checkpoints under two
thread ids and inspecting the live database: a `checkpoints` table with a
`thread_id` column, `SELECT DISTINCT thread_id FROM checkpoints` returns
exactly what `list_threads` needs. `delete_thread` uses the checkpointer's
own public `adelete_thread(thread_id)` (confirmed from source: deletes
every row for that thread from both `checkpoints` and `writes`) plus the
same `.tasks.json`/persona-sidecar/`sessions.pop(...)` cleanup
`web/app.py`'s version already does for those file-based, runtime-agnostic
parts of a thread's identity.

**One more real edge case, caught by a test that runs *without* ever
sending a message first, not by manual testing:** `AsyncSqliteSaver.
from_conn_string` does *not* create the `checkpoints`/`writes` tables
eagerly -- `setup()` (idempotent, a no-op once already run) is normally
triggered lazily by the checkpointer's own `aput`/etc. on first real use.
Querying `checkpointer.conn` directly, as both endpoints now do, bypasses
that lazy trigger entirely: on a freshly started app where nobody has sent
a single message yet, the raw query would hit `sqlite3.OperationalError:
no such table: checkpoints`. Fixed by calling `await checkpointer.setup()`
before the query in both endpoints -- cheap and safe to call unconditionally
every time, not just once, since it no-ops immediately once already set up.

**Verified against real conversation state, not fixtures.** New tests
drive an actual WS turn (real `astream()` call, real checkpoint write) via
`_client_lg`'s fake model, then assert the thread shows up in `GET
/api/threads`; a companion delete test also creates a real
`.tasks.json`/persona sidecar first and confirms all three (checkpoints,
tasks file, sidecar) are gone afterward and the thread drops out of the
list. A fourth test deletes a thread that was never created at all,
confirming the `setup()`-before-query fix rather than a 500.

**A second, real bug surfaced while re-running the suite to verify this
one: `list_recorded_steps` could see its own not-yet-finished call.**
`test_list_recorded_steps_reflects_the_real_checkpointed_history_lg` first
looked like one isolated flake (seen once, dismissed as noise); repeating
the full `test_web_lg.py`/`test_mcp_lg.py` run several more times as a
final check before committing surfaced it again, which meant it wasn't
noise and needed a real root cause, not a shrug.

Root cause: `recorded_tool_call_steps_lg` (this module, above) built its
result list from every `tool_calls` entry found on any `AIMessage` in
checkpointed state, defaulting a call's result to `None` via
`results_by_id.get(call["id"])` when no matching `ToolMessage` existed
yet. That default silently conflated two different situations: a call
that genuinely returned `None`, and a call that simply hasn't returned
yet. `list_recorded_steps` itself is one of the tools this function scans
for -- so when its own proposing `AIMessage` became visible in checkpointed
state before its own tool node finished (a timing-dependent ordering, not
guaranteed either way), it would see itself mid-flight and report a
phantom extra step for its own not-yet-completed call.

Fixed by changing the check from `results_by_id.get(call["id"])`
(defaults to `None`, indistinguishable from "no result yet") to `if
call["id"] not in results_by_id: continue` -- a call with no matching
`ToolMessage` is skipped outright rather than reported with a `None`
result. Safe for every other call site (`_handle_start_workflow`,
`record_chain_workflow_lg`, `propose_workflow_save_lg`) since they all run
between turns, once every call in scope has already settled.

Verified honestly, not just re-run until green once: after the fix, ran
the full `test_web_lg.py`/`test_mcp_lg.py` suite eight consecutive times
(72 passed each time, zero failures) plus `ruff check`/`mypy`, in place of
the single passing run that would normally be enough -- specifically
because the first "it's just a flake" conclusion above turned out to be
wrong once, so a single green run here wasn't going to be trusted either.

## Switching sessions showed a blank chat log -- a real, live-reported bug, a genuinely new capability neither runtime had

Live-reported: after the threads-listing fix above made the session
switcher actually show past threads, switching to one showed an empty
chat pane -- no messages at all -- even though asking a follow-up question
proved the model still remembered the whole prior conversation perfectly.

Root cause, once traced through both `app.js` and both backends: the
visible chat log was *only ever* built up from live WS events
(`agent_delta`/`agent_message`/`tool_result`/...) arriving during the
*current* connection's lifetime. Nothing re-populates it from a thread's
real history on connect, and nothing caches it client-side either (no
`localStorage` anywhere in `app.js`). So reconnecting -- or switching to
any existing thread -- always started from a blank DOM, regardless of how
much the backend actually remembered. Confirmed this is **not** a
runtime_lg regression: `web/app.py`'s `ChatSession.send_state` has no
history field either, and there's no other endpoint or WS message
carrying past messages in the old runtime -- the exact same gap exists
there verbatim, just never surfaced before because reconnect/switch was
never this easy to reach until this session's own threads-listing fix.
Per this project's own established scope split, only runtime_lg gets
fixed here; the old runtime's identical gap is left alone and just
documented, same treatment the reconnect-orphaned-turn-lock gap and the
tool-error-crashes-the-turn gap both got earlier in this file.

**Design.** `runtime_lg/messages.py` gains
`serialize_history_for_ws_lg(messages) -> list[dict]`: walks a thread's
checkpointed message list once (built from `self.lg_agent.aget_state
(self.config)`, the same read `list_recorded_steps`/workflow save already
use) and emits an ordered, JSON-shaped replay -- `{"kind": "user"|"agent",
"text": ...}` for a human/model turn's text, `{"kind": "tool", "tool_name",
"arguments"}` for each completed tool call, built the same way
`recorded_tool_call_steps_lg` is (a `results_by_id` map from every
`ToolMessage`, then walking messages in order). Reuses that same fix's
`if call["id"] not in results_by_id: continue` check for the identical
reason: a tool call still awaiting approval when the page reloads has no
result yet, and showing it as a "recorded" history entry would be a
phantom step -- `resume_after_reconnect` already redelivers the real,
live `approval_required` event for it separately, so this just skips it.
Live-verified this exact skip against real Gemini and a
`write_file`-style approval flow too, not just the fake-model unit test
below.

`ChatSessionLG.send_history(websocket)` (next to `send_state`) reads that
state and sends one `{"type": "history", "entries": [...]}` WS message.
`app_lg.py`'s `ws_endpoint` calls it exactly once, right after the
existing `send_state(websocket)` call at the top of every fresh
connection -- not folded into `send_state` itself, since that method is
also called after `switch_model`/`select_persona`/`/plan`/`/accept-edits`
mid-conversation, where re-sending the whole history would duplicate
everything already rendered live. A brand-new thread has no checkpointed
messages, so this is a harmless empty-list no-op on first connect -- no
special-casing needed for "is this thread new or not."

`app.js` gained one new `case "history":` branch (`appendHistoryEntries`,
next to `appendBubble`/`appendMuted`) that replays each entry through the
exact same rendering primitives live turns already use, so a replayed
bubble is visually identical to one that streamed in live.

**A real formatting bug found while verifying, not shipped separately.**
The checkpointed `HumanMessage` for every turn actually carries a
`mode_note` prefix (`"[normal mode: risky tool calls ask for approval as
usual] "` and two siblings for plan/accept-edits mode) that
`_handle_user_message_locked` prepends before the text ever reaches the
model -- purely for the model's benefit, so it always knows which mode is
active. The *live* user bubble never shows this (the frontend renders it
client-side from what the user actually typed, before the WS round-trip
even happens), so replaying the raw checkpointed text verbatim would have
made a history-replayed bubble visibly different from the same message's
own live rendering, moments earlier in the same session. Fixed by hoisting
the three `mode_note` literals out of `session_lg.py` into shared
`PLAN_MODE_NOTE`/`ACCEPT_EDITS_MODE_NOTE`/`NORMAL_MODE_NOTE` constants in
`runtime_lg/messages.py` (session_lg.py now references them instead of
inlining the strings), and `serialize_history_for_ws_lg` strips a leading
match of one of them back off before emitting a "user" entry.

**A large, mechanical test-suite update, not a design smell.** Adding a
new message unconditionally sent right after `send_state` on every
connection shifted the position of *every subsequent* WS message in
*every* existing test that connects fresh (this file's own docstring:
"asserts the same WS message type/ordering contract as tests/test_web.py"
-- accurate, and exactly why this showed up so broadly). Rather than
changing the design to avoid touching tests, ~40 call sites across
`test_web_lg.py` each gained one `ws.receive_json()  # history` drain
line immediately after their existing state-drain -- the correct fix
(the wire protocol genuinely changed, on purpose), not a workaround.

**Live-verified against real Gemini, not just the fake-model unit
tests.** Started a real thread against `gemini-3.1-flash-lite`, asked it
to read a real file (triggering `list_files` + `read_file`), let the turn
finish, then opened a second, independent WebSocket connection to the
same thread_id (exactly what reloading the page or switching sessions
does) and printed the `"history"` event's `entries`: a `"user"` entry
with the mode-note-stripped original question, both real tool calls with
their real arguments, and the model's real final answer -- in the correct
order, matching what the live turn had actually produced. Three new
tests in `test_web_lg.py` cover the same three cases against the fake
model (empty history for a brand-new thread; a full reconnect replay with
a tool call in the middle; a pending-approval call correctly omitted),
plus the mechanical `# history` drain across the rest of the suite. Full
`test_web_lg.py`/`test_mcp_lg.py` suite (79 tests now) run clean five
times in a row, `ruff check`/`mypy` both clean.

## `GET /api/browse-dirs` was missing entirely -- another real, live-reported bug

Live-reported: clicking "select folder" in the Settings panel's extra
readable/writable directories picker did nothing visible -- no OS folder
dialog, no error, the modal just sat there with no contents. Cause turned
out to be the same shape as the threads bug above: `app.js` is shared
verbatim between `web/app.py` and `app_lg.py` (both mount the same
`STATIC_DIR`), and `app.js`'s `openDirBrowser`/`loadDirBrowser` calls
`/api/browse-dirs` unconditionally to drive a server-side directory
listing (the browser's own File API deliberately never exposes a selected
folder's real absolute path, so this was never going to be a native OS
picker in either backend -- see that function's own comment). `web/app.py`
defines that route; `app_lg.py` never did. The fetch 404'd, the endpoint's
JSON shape (`{"path", "parent", "directories"}`) was absent, and
`renderDirBrowser` throw on `data.directories` being `undefined` --
silently, since nothing in `openDirBrowser` catches it -- leaving the
overlay visible but empty with no error message, which is exactly what
"点击选择文件夹，没有打开文件夹让选择" (clicking select folder didn't open
anything to select) describes.

Fixed by porting `web/app.py`'s `browse_dirs` handler into `app_lg.py`
unchanged (same deliberately-unrestricted-anywhere-on-disk trust model,
same `Path.home()` default, same directories-only + alphabetical-sort
behavior). Verified with the same four tests `test_web.py` already has for
this endpoint, ported to `_client_lg`: subdirectory listing (with one
runtime_lg-specific addition -- the lifespan's checkpointer creates a
`state/` directory at startup, so the expected listing has one more entry
than `web/app.py`'s equivalent test), default-to-home, nonexistent-path
error, and file-path error. All pass, plus the full `test_web_lg.py`/
`test_mcp_lg.py` suite (76 tests now) run clean four times in a row,
`ruff check`/`mypy` both clean.

## `/api/config`, `/api/mcp/*`, `/api/providers/*` -- the Connectors/Providers
## settings panels, now in `app_lg.py`

Closes another gap from Phase 3's original "deliberately out of scope"
list. Ported directly from `web/app.py`'s identical endpoints -- all the
module-level catalog data, pydantic request models, and file-read/mask
helpers are imported from `web/app.py` unchanged (`MCP_CATALOG`,
`PROVIDER_CATALOG`, `BUILTIN_PROVIDERS`, `ConfigUpdate`,
`MCPServerUpdate`, `ProviderUpdate`, `_mask`, `_read_mcp_servers_raw`,
`_read_providers_raw`, ...) -- none of that was runtime-specific, only the
*mutation* endpoints' bodies needed a different tail end.

**The one real behavioral difference, decided deliberately, not an
oversight:** a config change here takes effect for the *next new*
thread/session, not every already-open one. `web/app.py`'s identical
endpoints hot-reload into every live `ChatSession` because
`runtime/runner.py`'s `Runner` re-reads `agent.tools` and re-resolves the
model fresh on every single turn -- mutating a shared list in place is all
a live update needs there. `runtime_lg`'s `create_agent()` compiles a
fixed graph once, model and tools baked in; an already-open
`ChatSessionLG` keeps whatever it was built with regardless of what
`extra_tools_holder`/`providers.json`/`os.environ` say afterward.
Rebuilding every open session's compiled graph in place on every config
change would close that gap, but is meaningfully more work than this pass
-- discussed and scoped down explicitly before writing any code, not
discovered as a limitation after the fact.

MCP connector add/remove/bump-version reconnect just that one server via
`runtime_lg/mcp.py`'s `connect_one_mcp_server_lg` and splice its tools
into (or out of, keyed by `tool_metadata`'s `category="mcp:{name}"` tag
rather than `web/app.py`'s name-prefix string match, since the tag was
already available) `extra_tools_holder["tools"]` -- the same in-memory
list `_get_session` builds every *new* `ChatSessionLG` from. Provider
add/remove writes `.env`/`providers.json` the same way `web/app.py` does;
`resolve_chat_model` (`runtime_lg/providers.py`) builds a fresh chat model
instance on every call with no cache to invalidate, so a builtin
provider's key just needs mirroring into `os.environ` (no
`client.invalidate_provider(...)` equivalent needed) -- but a *custom*
provider needs `_get_session` to re-read `providers.json` fresh for every
new session (not the fixed `custom_providers` dict `context_window_client`
was built from at startup), since nothing else would ever pick up a
provider added after the app started otherwise.

**Live-verified end-to-end, not just unit-tested against fakes.** Started
a real `coscribe-web-lg`, called `POST /api/mcp/servers` to add the
zero-config `memory` catalog entry (`connected: true`, connecting for
real), then opened a brand-new WebSocket thread and asked it to create a
memory entity: approval card, correct `entityType` field, execution,
correct final reply -- confirming a connector added through this panel is
actually usable by the next new session, not just persisted to disk.
`POST`/`GET`/`DELETE /api/providers` round-tripped a custom
OpenAI-compatible provider (add, appears in `GET`, masked key, remove)
the same way.

**A real, pre-existing test-suite bug found and fixed along the way, not
introduced by this pass.** `create_app`/`create_app_lg`'s own
`load_dotenv(".env")` call writes straight into the real process
`os.environ` -- unlike a `monkeypatch.setenv` call, nothing automatically
reverts that between tests. `tests/test_web.py`'s
`test_post_mcp_server_creates_config_and_sets_env_var_when_unset` (already
failing before this session's work, previously assumed pre-existing and
unrelated -- see this file's earlier entries) turned out to have this
exact root cause: an earlier test run in the same pytest process leaves
`COSCRIBE_MCP_CONFIG_PATH` set process-wide, which `Settings()`
picks up even with `_env_file=None`, so the endpoint's `if
settings.mcp_config_path is None:` branch silently gets skipped on a
later run. Fixed with `monkeypatch.delenv(..., raising=False)` at both
that test and its new `tests/test_web_lg.py` counterpart -- confirmed
order-independent afterward (ran forwards, backwards, and in isolation).

## Plan mode, accept-edits, compact, and Hooks under `runtime_lg`

Closes the last item on Phase 3's original "deliberately out of scope"
list -- `web/session_lg.py` (and `web/app_lg.py` for Hooks' `SessionStart`
event and config loading) now supports all four, live-verified against
real Gemini. `cli_lg.py` was deliberately left alone: CLI feature parity
was explicitly deprioritized by the user (see this file's Phase 2 section
and the plan history) with the CLI itself slated for eventual removal, so
there was no reason to duplicate this work there.

**Plan mode and accept-edits don't touch `HumanInTheLoopMiddleware`'s
`interrupt_on` map at all -- they're decided entirely in
`_resolve_pending_approvals`, per pending request, instead of rebuilding
the compiled graph.** This works *in this codebase* because of a fact
verified by grepping every `tool_metadata(...)` call under `tools/`:
every tool with `risk_level != "low"` also sets `requires_approval=True`,
with no exceptions. That means `interrupt_on`'s membership (built from
`requires_approval`) already exactly equals the set `PlanModePolicy` would
block outright (`risk_level != "low"`) -- so plan mode can just reject
every genuinely-gated pending request without asking, instead of needing
a live `risk_level` lookup or a second interrupt_on rebuild. `ChatSessionLG`
still keeps its own `_approval_required_names` set (independent of
whatever `HumanInTheLoopMiddleware`'s `interrupt_on` ends up widened to
for Hooks below), since plan mode must *not* block a request that's only
present because of hook-gating -- see below.

**Accept-edits** is the mirror case: a request for a tool in
`_approval_required_names` gets `{"type": "approve"}` fed straight back
via `Command(resume=...)` with no `approval_required` WS message sent at
all, instead of `AllowAllToolPolicy` skipping the check entirely the way
the old runtime does it. Same outcome (no prompt, tool runs), different
mechanism forced by `create_agent`'s fixed-graph shape.

**Hooks: PreToolUse widens `interrupt_on` to every tool, not just the
already-gated ones.** The old runtime's `HookToolPolicy` wraps *every*
tool call regardless of risk level (`runtime/policies.py`), so a
low-risk tool like `task_create` can still be vetoed by a hook. Under
`create_agent`'s fixed-graph model there's no equivalent wrap-every-call
choke point outside the interrupt system itself -- so when
`hooks_config["PreToolUse"]` is non-empty, `ChatSessionLG.__init__` passes
every tool's name to `build_langgraph_agent`'s new
`extra_interrupt_tool_names` param (`agent.py`), which folds them into
`interrupt_on` alongside the genuinely-approval-required ones. A request
for one of these extra, not-actually-risky tools still reaches
`_resolve_pending_approvals`, but skips the plan-mode/accept-edits/ask-the-
user branches entirely (`name not in self._approval_required_names` short-
circuits straight to auto-approve) -- the *only* reason it was gated at
all was to give the hook a chance to run first. The real cost, accepted
as the price of correctness here: every tool call now pays one interrupt/
resume round-trip through the checkpointer when any PreToolUse hook is
configured at all, not just the ones that already paid it. Opt-in (only
happens if the user configures a PreToolUse hook), so this is not a
default-path regression.

PostToolUse hooks need each tool call's *arguments*, which a `ToolMessage`
never carries (only the result) -- `_stream_turn` accumulates the
streamed `AIMessageChunk`s (`stream_mode=["messages"]` yields deltas,
merged via `AIMessageChunk.__add__`) and flushes any newly-seen
`.tool_calls` into `self._pending_tool_args` (keyed by tool name, FIFO per
name) in two places: right before a `ToolMessage` that arrives in the
*same* `_stream_turn` call (the common, non-gated case), and again
unconditionally once that call's `astream` loop ends. **The second flush
is load-bearing, not defensive padding -- a real bug was caught by the
new `test_post_tool_use_hook_receives_the_tool_result` test before this
was added:** an approval-gated call's proposing `AIMessage` is fully
streamed in *one* `_stream_turn` call, but the interrupt then ends that
call with no `ToolMessage` ever arriving in it (the `ToolMessage` only
shows up in `_resolve_pending_approvals`'s *next* `_stream_turn` call,
post-resume) -- without the end-of-loop flush, the accumulated args were
silently discarded when the first call returned, and the hook received
`arguments: {}` for every gated call. The live verification run below
had this exact bug at the time (every `PostToolUse` log line showed
`"arguments": {}`) and it went unnoticed there; the automated test caught
what manual live-checking missed.

**`/compact`** can't reuse `runtime/compaction.py`'s `run_compaction`/
`compact_state` (those work on `RunState`'s plain dict messages, collapsed
via `FileStateStore.save_state`) -- `runtime_lg` needs the checkpointer's
own message list rewritten instead. Verified live (a standalone script,
before wiring into `_handle_compact`) that `agent.aupdate_state(config,
{"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), <new message>]})`
correctly collapses a real thread's checkpointed history down to one
note, and that a later turn on the same thread still works and correctly
reflects only the compacted note, not the original messages -- LangGraph's
`add_messages` reducer treats `RemoveMessage(id=REMOVE_ALL_MESSAGES)` as
"clear everything accumulated so far in this update" before applying
whatever follows it in the same call. One more thing verified empirically
while building this: `create_agent`'s checkpointed state never contains a
system message at all (`system_prompt` is injected fresh at call time
only) -- unlike the old runtime's `RunState.messages`, which always keeps
one at index 0 -- so `_handle_compact` has nothing to preserve/re-add
after clearing, simplifying it relative to `compact_state`. The summarizer
itself is a direct `self.model.ainvoke([...])` call (no tools, one round),
reusing `runtime/compaction.py`'s own `COMPACT_INSTRUCTIONS` text rather
than duplicating the prompt.

**Two narrower-than-`ChatSession` gaps, documented rather than closed in
this pass:** there's no threshold-triggered *automatic* compaction (only
the manual `/compact` command -- `maybe_auto_compact`'s equivalent would
need per-turn usage/context-window tracking wired through the streaming
path, which `runtime_lg` doesn't currently do); and Hooks configured on a
top-level session don't propagate into `spawn_agent`'s/`review_work`'s own
nested sub-agent graphs (`runtime_lg/subagents.py` builds those via a
plain `build_langgraph_agent(model, selected, instructions)` call with no
hooks-awareness) -- a sub-agent's own tool calls run without PreToolUse/
PostToolUse checks.

**Live-verified end-to-end against real Gemini**, not just unit-tested
against fakes: started a real `coscribe-web-lg` with a `hooks.json`
configuring `PreToolUse`/`PostToolUse`/`SessionStart` hooks that log to
files (and one `PreToolUse` hook that denies any path containing
`"forbidden"`). Confirmed, in one running session: `/plan` toggled state
and then caused a `write_file` call to be silently blocked with no
approval prompt, with the model correctly narrating the block and falling
back to `task_create` instead (matching `PlanModePolicy`'s own fallback
guidance verbatim); `/accept-edits` toggled state and then let a
`write_file` call through with no prompt; **critically, a `PreToolUse`
hook denial overrode accept-edits** -- a write to a `forbidden/`-prefixed
path was blocked even with accept-edits on, and the workspace afterward
only contained the two allowed files, never the forbidden one; the
`PreToolUse` hook fired for every tool call including low-risk ones
(`task_create`/`task_list`/`task_update`), not just `write_file`; the
`PostToolUse` log captured the hook's own denial reason
(`"result": "blocked by policy: forbidden path"`) as the tool result; and
`SessionStart` fired once per new thread. The run hit Gemini's free-tier
daily quota (20 requests/day for the model in use) partway through, which
is why `/compact` wasn't re-verified through this same live run --
`/compact`'s own live verification (the `aupdate_state`/
`RemoveMessage(REMOVE_ALL_MESSAGES)` mechanism) happened separately,
before quota ran out, as described above.

## `/api/upload` -- closes the last remaining gap vs. `coscribe-web`

A direct, unmodified port of `web/app.py`'s identical endpoint into
`app_lg.py` -- `MAX_UPLOAD_BYTES` and `_unique_upload_path` are imported,
not copied. Pure file I/O against `settings.workspace_root` via
`WorkspaceScope`, nothing runtime-specific about it -- unlike
`/api/config`/`/api/mcp/*`/`/api/providers/*`, there's no "next new
session only" wrinkle here: an uploaded file just needs to exist on disk
before the model's next tool call reads it, which every already-open
`ChatSessionLG` can do immediately (reading a file from disk doesn't go
through the compiled graph at all). Verified against the automated test
suite (ported the same four `web/app.py` upload tests: write-and-return-
path, auto-rename on name collision, path-traversal filename sanitizing,
oversized-file rejection) and live: `curl -F file=@... /api/upload`
against a running `coscribe-web-lg` wrote the file correctly, and a
real Chromium session (via Playwright, driving the existing, unmodified
frontend) confirmed the attachment chip UI renders after uploading through
the actual page -- not just that the wire response looked right.

With this, `app_lg.py` has no remaining "deliberately out of scope"
endpoints from Phase 3's original list. Multi-provider verification
(Anthropic/DeepSeek/GLM, beyond the already-verified Gemini) remains open,
but is a sandbox limitation, not a code gap -- see "Live verification
results (Phase 1)" below for why (missing key for Anthropic; this
session's own egress-proxy policy blocking `api.deepseek.com`/
`open.bigmodel.cn` for DeepSeek/GLM). `scripts/verify_runtime_lg.py`
already has all four scenarios wired up and ready to run from a machine
without those restrictions.

## `/clear`, `/stop`, and model switching

Three more `ChatSession` features ported into `ChatSessionLG`. Each needed
a materially different mechanism than the old runtime, not just a
line-for-line port -- same "port the contract, adapt to a compiled graph"
discipline as Plan mode/accept-edits/compact/Hooks above.

**`/clear`** reuses `_handle_compact`'s own `aupdate_state(config,
{"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)]})` mechanism, minus
the summarize step -- wipe outright instead of collapsing to a note. Same
pending-approval guard as `/compact` (refuses with an error rather than
racing an `aupdate_state` call against a paused interrupt task), and same
"no system message to re-add" simplification (verified empirically
elsewhere in this file: `create_agent`'s checkpointed state never contains
one in the first place).

**`/stop`** is necessarily cooperative, not a hard kill -- mirrors
`runtime/runner.py`'s `threading.Event`-based `stop_event` exactly in
spirit, just as an `asyncio`-checked flag instead of a thread-safe `Event`,
since this runtime's turn loop is one async coroutine rather than a worker
thread `asyncio.to_thread` drives. `request_stop()` does two things:
denies every currently-pending approval future immediately (so a stop
can't get stuck waiting behind a prompt nobody's going to answer -- the
flag checks elsewhere run between chunks/decisions, which a pending
approval wait would otherwise never reach), and sets a flag
`_stream_turn`/`_decide_action_request` check at their own natural yield
points. `_stream_turn` explicitly closes its `astream()` generator in a
`finally` (whether the loop ended naturally or via a stop-triggered
`break`) rather than letting it fall out of scope uncleaned-up -- an
abandoned, ungarbage-collected async generator keeps running server-side
instead of propagating `GeneratorExit` into LangGraph's own execution.
Same real limitation the old runtime already has for any non-token-
streaming provider: this can't interrupt a single model call already in
flight, only stop consuming *further* chunks/turns promptly -- not a
regression, the same boundary Runner's own stop_event has.

**Model switching** is the one where "adapt the mechanism" mattered most.
`ChatSession.switch_model` just mutates `self.agent.model` in place,
because `Runner.run_sync` re-reads `agent.model` fresh on every turn.
`create_agent()` bakes the model into a *compiled graph* once at
construction time -- there's no string to mutate. `ChatSessionLG.
switch_model` instead rebuilds `self.lg_agent` (and the model-bound
`spawn_agent`/`review_work` tool closures feeding it) against a *new*
model, reusing everything else about the session unchanged: same
checkpointer, same thread_id/config, same base tools, same instructions.
This is safe even mid-approval (a compiled graph rebound to the same
checkpointer/thread_id resumes from checkpointed *state*, not from the
old compiled graph object's identity; `HumanInTheLoopMiddleware`'s
`interrupt_on` set is derived purely from tool names/hooks configuration,
both unchanged by a model switch) -- reasoned through carefully since
Gemini's daily free-tier quota was exhausted for the rest of this session
by the time this was built, so that specific claim (switching mid-
approval) is *not* live-verified, only the normal-case switch and the
invalid-model-string rejection path are (see below). Flagged here
honestly rather than glossed over.

Also added `"model"` to the `send_state`/`"state"` WS payload -- previously
missing entirely, which the shared, unmodified frontend's own `app.js`
(`currentModel = data.model; ... currentModel.includes(":")`) would have
thrown on the moment anyone actually looked at the model pill through
`coscribe-web-lg`. Never caught before now because nothing in this
migration had exercised that specific code path yet.

**Live-verified where quota allowed, honestly flagged where it didn't.**
Started a real `coscribe-web-lg` against real Gemini: `switch_model`
with an invalid (`":" not in model`) string correctly returned the
validation error over a live WebSocket, and the initial `"state"` payload
correctly included `"model": "gemini:gemini-flash-latest"`. Every other
live scenario (`/clear` actually forgetting prior context, `/stop`
correctly aborting an in-flight approval, a real model-to-model switch)
hit `ResourceExhausted: 429 ... GenerateRequestsPerDayPerProjectPerModel-
FreeTier` -- this session had already spent Gemini's entire daily free
quota on the concurrent-spawn_agent and multi-provider verification work
earlier. Deterministic automated coverage for all three features exists
regardless (`tests/test_web_lg.py`, using the same `FakeToolCallingChatModel`
harness as everything else in that file) and passes -- including
`/stop` actually aborting a real pending-approval turn and the final
reply carrying `"[stopped]"`, and `switch_model` actually routing a
*different* model object's response back to the client after a switch.
The live scenarios blocked by quota should be re-run once it resets
(tomorrow, or from a machine without this sandbox's exhausted key) rather
than assumed correct from the fake-model tests alone.

## `_turn_lock` -- a real concurrency gap found by a deliberate review, plus a deadlock it introduced and fixed

`ChatSession` (the old runtime) has always had an `asyncio.Lock` (`_turn_
lock`) serializing `handle_user_message` calls, with an explicit comment
explaining why: `ws_endpoint` dispatches every `"user_message"` via
`asyncio.create_task` without awaiting it (so a turn blocked on approval
doesn't freeze the socket), which means two messages sent close together
would otherwise start two fully concurrent turns racing on the same
session state. `ChatSessionLG` never had this lock ported over -- not
because it was decided unnecessary, just never revisited once the
async/checkpointer rewrite made the original reasoning less visible. A
deliberate concurrency review (prompted by the same instinct that caught
the concurrent-spawn_agent bugs above) confirmed the identical race
exists here: two concurrent `handle_user_message` calls would both mutate
`self._pending_tool_args`/`self._stop_requested` and issue concurrent
`astream()`/`aget_state()` calls against the *same* checkpointed thread.
**Fixed** by porting the same lock, wrapping both `handle_user_message`
(via a new `_handle_user_message_locked` split, mirroring `ChatSession`'s
own `_handle_user_message_locked` naming) and `resume_after_reconnect`
(which didn't exist in the old runtime, but needed the identical
treatment for the identical reason -- it also mutates turn-level session
state). `resolve_approval`/`request_stop` are deliberately *not*
lock-gated, matching `ChatSession`'s identical methods exactly: they have
to reach whichever turn is currently in flight, not queue behind it.

**Adding the lock immediately surfaced a second, genuinely new problem
`ChatSession` never has to deal with, because runtime_lg's checkpointer-
backed reconnect/redelivery feature (see the Phase 3 verdict below) has
no analog in the old runtime.** If a turn is blocked awaiting an approval
Future when its websocket disconnects, nothing can ever resolve that
Future -- the client that would have sent `approval_response` is gone.
Before the lock, that orphaned server-side task just sat there forever, a
harmless (if wasteful) leak. *With* the lock, it sits there forever
**holding the lock**, silently blocking every future turn on that
`thread_id` -- including a genuine reconnect's own
`resume_after_reconnect` trying to redeliver that exact same pending
approval to a fresh connection. Caught immediately, not shipped
silently: `tests/test_web_lg.py::test_clear_refuses_while_an_approval_is_
pending` (written for the lock-less design, sending a second message
while the first sat on a never-answered approval) hung the whole test
suite the moment the lock was added.

The fix is *not* to reuse `request_stop()` on disconnect, tempting as
that looks -- `request_stop()` denies the pending approval as a real
decision and resumes the graph via `Command(resume=...)`, which would
permanently consume the very interrupt a reconnect is supposed to
redeliver *untouched* (the whole reason a persistent checkpointer was
chosen over `InMemorySaver` in the first place -- see "does this actually
fix approval-survives-reconnect" below). Instead, `app_lg.py`'s
`WebSocketDisconnect` handler now calls a new `abandon_orphaned_turn()`,
which hard-cancels whichever `asyncio.Task` currently holds `_turn_lock`
(tracked via a new `self._current_turn_task`, set at the top of both
`handle_user_message` and `resume_after_reconnect` once each has acquired
the lock). Cancellation unwinds that task without the code ever reaching
a `Command(resume=...)` call, so the checkpointer's real pending state is
left exactly as it was; the lock still releases correctly regardless,
since `async with` runs its cleanup on cancellation the same as on any
other exception. Verified this doesn't regress the reconnect guarantee
the straightforward way, not by inspection alone:
`test_pending_approval_is_redelivered_on_reconnect` (disconnects without
ever answering the pending approval, then reconnects and expects the
*identical* approval to be redelivered) now exercises
`abandon_orphaned_turn` on every run via the same `WebSocketDisconnect`
path a real client dropping its connection hits, and still passes.

One existing test needed rewriting once the lock changed what was
actually reachable: `_handle_clear`/`_handle_compact`'s own
"is anything pending?" guard predates `_turn_lock` and is now unreachable
through ordinary concurrent messaging (a second message queues behind the
lock instead of racing the first turn's pending approval, so by the time
it runs, that approval has always already been resolved one way or
another) -- kept anyway as a defensive backstop for the one window the
lock doesn't cover (a fresh process restart racing `resume_after_reconnect`
for a not-yet-held lock). The old test asserting an immediate error is now
`test_clear_queues_behind_a_pending_turn_instead_of_racing_it`, asserting
the actually-correct behavior instead: `/clear` queues, and runs only
after the pending turn is answered and finishes.

Not caught by this pass, deliberately out of scope (a bigger, separate
change): `web/app.py`'s `ChatSession` has this exact same theoretical gap
-- its own `WebSocketDisconnect` handler is a bare `pass`, so a turn
orphaned by a mid-approval disconnect would also hold `_turn_lock` forever
there. It has simply never been *observed*, because the old runtime has no
reconnect-redelivery feature to race against it and expose it as a
deadlock the way this pass's own test did. Left alone here since fixing it
isn't something this runtime_lg-focused work should reach into the shipped
runtime to do without it being asked for specifically.

## Personas -- closes the workflows/personas gap's smaller half

**Removed entirely, later.** Everything below describes a real, shipped
feature at the time of this migration -- kept as history, not deleted,
per this codebase's own "leave history visible" convention -- but none
of it is in the current codebase. Personas turned out to be a coscribe-
specific idea borrowed from a single reference project (`andrewyng/
openworker`), not something any mainstream product (Claude Cowork,
ChatGPT, WorkBuddy) actually does -- they all use one global "Custom
Instructions"/"Global Instructions" text field instead, which coscribe
already had a direct equivalent of (`MEMORY.md`, editable in Settings).
See ROADMAP.md's own entry for the removal for the full reasoning and
what replaced it (nothing -- Global Instructions already covered the
real value).

`ChatSession` (the old runtime) has supported personas since before this
migration started: `runtime/personas.py`'s `PersonaConfig` (a markdown file
with YAML frontmatter -- id/name/description/instructions, plus optional
`tools` whitelist/`default_model`/`default_permission_mode`), loaded once at
startup into `web/app.py`'s `personas_by_id`, chosen either via a `?persona=`
WS query param at connect time or an in-app `select_persona` WS message,
and persisted per-thread via a `{thread_id}.persona` sidecar file so a
reconnect with no query param still picks the same persona back up.
`ChatSessionLG` had none of this -- the module docstring listed it flatly
as "no ... persona support yet" alongside workflow recording. This pass
closes the persona half of that gap; workflow recording
(`/startworkflow`/`/endworkflow`/`/saveworkflow`/`/runworkflow`) is real,
independent work deferred to a later pass, not touched here.

**Why this couldn't just reuse `apply_persona`.** `runtime/personas.py`'s
`apply_persona(agent, persona)` mutates a `runtime.types.Agent`'s
`instructions`/`model`/`tools` attributes in place -- `Runner` re-reads
those fresh on every turn, so mutating them is enough. `ChatSessionLG` has
no equivalent live-mutable `Agent` object: `_instructions`/`_model_string`/
`_base_tools` are plain fields consumed once by `_build_lg_tools`/
`_build_lg_agent` to produce a *compiled* graph (`self.lg_agent`), the same
reason `switch_model` already has to rebuild that graph rather than mutate
a model string in place (see the `/clear`, `/stop`, and model switching
section above). So this adds a parallel, `ChatSessionLG`-shaped equivalent
instead of reusing `apply_persona` itself:

- `_apply_persona_to_base(persona)`: same three effects as `apply_persona`
  (append `persona.instructions`; override `_model_string` if
  `persona.default_model` is set; narrow `_base_tools` to `persona.tools`
  when it's a list, matched via `tool_name()` rather than `tool.__name__`
  so it also matches `BaseTool` instances like `spawn_agent`/`review_work`,
  which have `.name` but no `__name__` -- `apply_persona`'s own
  `tool.__name__` check would silently fail to match those in this
  runtime).
- Applied at construction time when a persona resolves before the session
  is built (the `?persona=`/sidecar path, mirroring `ChatSession.__init__`
  taking `persona` as a constructor argument): `_instructions`/
  `_model_string`/`_base_tools` are mutated *before* `self.model`/
  `self.lg_agent` are ever built, so the first compile already reflects it.
- `select_persona(persona, websocket)` (the in-app picker path, mid-session
  after the socket is already open): mirrors `ChatSession.select_persona`'s
  contract exactly (only meaningful once -- `_apply_persona_to_base` isn't
  idempotent, so a second call is rejected with the same "This thread
  already has a persona set" error rather than double-appending
  instructions), but the mechanism differs the same way `switch_model`'s
  does: after mutating the base fields, it rebuilds `self.model`/
  `self.lg_agent` against the new instructions/model/tools, with the same
  revert-on-failure shape `switch_model` already has (a bad
  `persona.default_model` -- e.g. an unresolvable provider string -- must
  not corrupt an otherwise-working session; reverted and reported as an
  `{"type": "error", ...}` message instead). Unlike `switch_model`, this
  also recomputes `_approval_required_names`: a model switch alone never
  changes the tool set, but `persona.tools` can genuinely shrink it, and a
  gated tool that no longer exists must stop being treated as
  approval-required.
- `send_state` gained a `persona_name` field (`None` when no persona is
  active), matching `ChatSession.send_state`'s existing field -- the
  shared, unmodified frontend already renders this for the old runtime, so
  no frontend changes were needed here either.
- `app_lg.py` gained the same wiring `web/app.py` already has:
  `personas_by_id` loaded once at startup via `load_personas(settings.
  personas_dir)`, a `GET /api/personas` catalog endpoint, `_persona_sidecar_
  path`/`_write_persona_sidecar`/`_resolve_persona` (byte-for-byte the same
  logic as `web/app.py`'s versions), `ws_endpoint` gaining a `persona: str |
  None = None` query param passed into `_get_session`, and a
  `"select_persona"` WS message branch. The sidecar files live under the
  same `settings.state_dir` the old runtime uses -- harmless to share,
  since the two runtimes are separate console scripts/ports not expected to
  serve the same `thread_id` concurrently, and "which persona this thread
  uses" means the same thing to either one anyway.

**Verified two ways.** `tests/test_web_lg.py` gained the same nine
persona tests `tests/test_web.py` already has (catalog listing, connect-time
query param, unknown-id fallback, sidecar persistence across a simulated
process restart -- two independent `_client_lg` app instances over the same
`tmp_path`, same as the old runtime's equivalent test -- `select_persona`
live-apply, unknown-id error, double-select rejection), plus two
runtime_lg-specific ones: `test_persona_tools_whitelist_narrows_approval_
gating` (a persona's `tools: [list_files]` must make `write_file`
genuinely unreachable, not just unlisted) and `test_persona_default_
permission_mode_is_applied` (a `default_permission_mode: plan` persona
must land in `plan_mode` at connect time, same as the old runtime). All 11
pass deterministically against `FakeToolCallingChatModel`.

`scripts/verify_persona_web.py` then exercises `ChatSessionLG` directly
against real Gemini (bypassing a real FastAPI/uvicorn server -- a minimal
`_CapturingWebSocket` stub stands in, since `handle_user_message`/
`select_persona` only ever call `.send_json` on it): a persona whose
instructions force a fixed one-word reply, applied both at construction
time and via `select_persona`, plus the same `tools: [list_files]`
whitelist test against a real model actually trying to write a file. All
four checks passed live at some point across a few runs, but never all
four in the *same* run -- this sandbox's Gemini free-tier daily quota
(`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, limit 20, same
recurring limitation documented elsewhere in this file) was exhausted
partway through more than one attempt, with `langchain-google-genai`'s own
retry loop burning through most of a run's remaining budget on one 429
before the process gave up. Documented honestly rather than glossed over:
every individual check has real-model confirmation, but a single, complete,
uninterrupted live run of the script was never achieved in this sandbox.

## Workflows -- closes the rest of the workflows/personas gap

> **Superseded (ROADMAP Phase 8av):** recorded workflows were removed; a
> workflow is now just a scheduled task with a standalone prompt, each run
> in its own conversation. Kept below as the record of what was built.

`ChatSession` supports named, reusable workflows (`tools/workflows.py`):
"chain" mode (a fixed, deterministic sequence of tool calls, replayed
verbatim) and "agent" mode (a natural-language summary handed to a fresh
sub-agent each run). Creation is gated behind explicit commands, never a
model-callable tool (`/startworkflow` + `/endworkflow <name>` marks and
saves an exact tool-call range; bare `/saveworkflow <name>` runs a curator
model call that either proposes a chain/agent-mode save or asks a
clarifying question, previewed and confirmed before anything persists);
`/runworkflow <name>` runs a saved one, bypassing the model entirely.
`ChatSessionLG` had none of this -- the module docstring listed it flatly
alongside personas as "no ... workflow recording ... yet". This pass closes
it; personas were already closed in an earlier pass (see above).

**Storage is reused directly, not re-implemented.** `Workflow`/
`WorkflowStep`/`WorkflowStore`/`WorkflowRun`/`WorkflowStepStatus`/
`WorkflowRunStore`/`reconcile_interrupted_runs` (`tools/workflows.py`)
depend only on `state_dir` and plain dicts -- nothing about `runtime.types.
Agent`/`Runner`/`RunState` -- so `app_lg.py`'s `GET/DELETE /api/workflows`,
`GET/DELETE /api/workflow-runs`, and the startup reconciliation call are
direct ports of `web/app.py`'s identical code, imported unchanged. The
sidecar-style `state_dir/workflows`/`state_dir/workflow_runs` storage is
shared with the old runtime, same "harmless, two separate console
scripts/ports" reasoning as the persona sidecar file above.

**What needed a genuine LangGraph-native replacement** (new module
`runtime_lg/workflows.py`, plus new `ChatSessionLG` methods in
`web/session_lg.py`):

- `recorded_tool_call_steps_lg(messages)` replaces `recorded_tool_call_
  steps`, which reads `RunState.steps` off a `FileStateStore` -- a shape
  runtime_lg's checkpointer-backed threads don't have. It instead pairs
  each `AIMessage.tool_calls` entry with its matching `ToolMessage` (by
  `tool_call_id`) out of `agent.aget_state(config).values["messages"]`.
- `propose_workflow_save_lg`/`infer_step_assertions_lg` replace
  `propose_workflow_save`/`_infer_step_assertions`'s one-off `Agent` +
  `Runner.run_sync` calls with a direct `model.ainvoke([...])` -- the same
  pattern `_handle_compact` already uses for its own one-off summary call.
  `WORKFLOW_CURATOR_INSTRUCTIONS`/`WORKFLOW_ASSERTION_INSTRUCTIONS` (plain
  prompt strings) and the `WorkflowSaveProposal`/`WorkflowStep` dataclasses
  are reused unmodified from `tools/workflows.py`; only the model-call
  mechanism and the transcript/step-listing source (checkpointed messages,
  not `RunState`) differ.
- `record_chain_workflow_lg`/`record_agent_workflow_lg` are thin wrappers
  around the above plus a small private `_save_workflow_record_lg` (a
  duplicate of `tools/workflows.py`'s own private `_save_workflow_record`
  -- ~15 lines, not worth a cross-module private import for).

**Chain-mode replay (`run_chain_lg`) turned out to need *no* interrupt()
bridging at all, unlike spawn_agent.** `tools/workflows.py`'s `_run_chain`
drives `Runner.execute_tool_call` under a live `ToolPolicy` -- runtime_lg
has no equivalent "call a tool under an approval policy" primitive outside
of a compiled graph's own `HumanInTheLoopMiddleware`. The natural instinct
(mirroring `runtime_lg/subagents.py`'s spawn_agent, which bridges a
sub-graph's own `interrupt()` up through the parent's) would be to make
`run_workflow` a model-callable tool and bridge each step's approval the
same way. **Deliberately not done that way.** `/runworkflow` here is *not*
a model-callable tool -- only reachable via the slash command or the
shared frontend's picker, exactly like `/startworkflow`/`/saveworkflow`
already bypass the model for workflow *creation*. Since replay therefore
never runs inside a graph's own tool node, each step's approval decision
is made by `ChatSessionLG` directly, reusing `_decide_action_request` --
the *exact* function a normal top-level turn already uses to decide a
pending interrupt's `action_requests` -- and each step is invoked directly
against the live tool object afterward (`_invoke_tool_lg`, handling all
three tool shapes this runtime hands around: `BaseTool.ainvoke`, a plain
async function awaited directly, a plain sync function offloaded via
`asyncio.to_thread`). `run_chain_lg` itself takes `decide`/`invoke_tool` as
plain async callbacks, so it has no LangGraph-specific code in it at all --
just the same halt-on-first-failure/`expect_contains`-substring-check
replay loop `_run_chain` has. Live-verified as a genuinely real approval
round-trip (not an auto-approve) via `test_runworkflow_chain_mode_asks_
approval_for_gated_steps_lg`: a `write_file` step pauses for a real
`approval_required` message, and only writes the file once approved.

**Agent-mode replay reuses `_stream_turn`/`_resolve_pending_approvals`
directly, refactored to take an explicit `agent`/`config` pair.** Both
methods previously always operated on `self.lg_agent`/`self.config`;
they now default to those but accept overrides. `_run_workflow_agent_mode`
builds a fresh compiled graph (`workflow.summary` as its instructions, same
`build_langgraph_agent` call shape as spawn_agent's own `sub_agent`) against
a dedicated, session-lifetime-shared `InMemorySaver` (mirroring spawn_
agent's `child_checkpointer` sharing pattern -- a fresh `thread_id` per run,
`f"workflow-run-{run.run_id}"`, means runs never collide on it), then drives
it with the *same* streaming/approval-resolution code a normal turn uses --
so a gated tool call inside an agent-mode run surfaces as an ordinary
`approval_required` message, no bridging needed there either. One
deliberate, documented simplification versus `ChatSession`'s `_run_agent_
mode`: agent-mode runs don't track granular per-tool-call `WorkflowStepStatus`
progress (`run.steps` stays empty throughout, only `run.status`/`error`
change) -- chain mode (the deterministic, pre-declared-step-list case where
progress is genuinely meaningful) gets full tracking; wiring the same
granularity into agent mode would mean threading a second, workflow-specific
callback through `_stream_turn` for a "Recent Runs" panel detail, more
machinery than this pass's scope justifies. Not a functional gap -- run
status/completion still update correctly, just without a live per-step feed.

**A real, previously-silent gap found along the way and fixed:**
`build_coordinator_agent` (shared by both runtimes) already wires in
`tools/workflows.py`'s `build_workflow_tools(thread_id, state_dir)`, which
includes `list_workflows`/`get_workflow`/`delete_workflow` (storage-only,
already worked correctly here) *and* `list_recorded_steps` (reads
`FileStateStore` -- always empty for a runtime_lg thread, forever, since
this runtime never writes one). The model could always call this tool in
`coscribe-web-lg`; it always silently returned `[]`, even immediately
after a real tool call in the same thread. Fixed by stripping the stale
one out of `self._base_tools` in `__init__` and adding a replacement (an
*async* closure over `self`, unlike the old sync one -- needed because
`self.lg_agent`'s real checkpointer in production is an `AsyncSqliteSaver`,
which only supports `aget_state`, not the sync `get_state` a plain sync
tool function would have to use) built alongside spawn_agent/review_work in
`_build_lg_tools`, backed by `recorded_tool_call_steps_lg` against the real
checkpointed history. Covered by `test_list_recorded_steps_reflects_the_
real_checkpointed_history_lg`, which fails against the old tool and passes
against the new one.

**One narrower-than-`ChatSession` gap, documented rather than silently
missing:** no model-callable `run_workflow` tool -- "run FBL5N" in plain
English won't trigger a run the way it does in `coscribe-web`; the
user has to type `/runworkflow FBL5N` or use the picker. `coordinator.py`'s
shared `INSTRUCTIONS` (used verbatim by both runtimes) tells the model to
call `run_workflow(name)`, which would be actively misleading here since no
such tool is ever bound to this graph -- fixed by appending a session-level
note (`_RUN_WORKFLOW_NOTE`, `ChatSessionLG.__init__`) redirecting the model
to tell the user about the slash command/picker instead, rather than
letting it try and fail to call a nonexistent tool.

**Verified two ways.** 16 new tests in `test_web_lg.py` mirror `test_web.
py`'s workflow coverage (REST endpoints, reconcile-at-startup,
`/startworkflow`+`/endworkflow` capturing only post-marker steps,
`/endworkflow` without a marker erroring, `/clear` cancelling an in-progress
recording, bare `/saveworkflow`'s curator both proposing a save and asking a
clarifying question, `/runworkflow` with zero LLM calls, `/runworkflow`
against an unknown name), plus three runtime_lg-specific ones (the
approval-round-trip test and the `list_recorded_steps` regression test
above, and a chain-save test that deliberately exhausts the fake model to
exercise `infer_step_assertions_lg`'s fail-open path). All 66 tests in
`test_web_lg.py`/`test_mcp_lg.py` pass, no hangs.

Live verification against real Gemini was attempted (a `ChatSessionLG`-level
script, not written to a file since deterministic coverage above already
proved every code path) but blocked immediately by this sandbox's Gemini
free-tier *daily* quota (`GenerateRequestsPerDayPerProjectPerModel-
FreeTier`, limit 20) being fully exhausted for the day by the time this
pass reached it -- confirmed via a bare one-token `model.invoke(...)` call
failing the same way, not assumed. Same recurring sandbox limitation
documented elsewhere in this file; not a code gap.

## Phase 3: `coscribe-web-lg`, an async web backend

**Update: the file paths and console-script name below are historical --
see "Web cutover" further down for what actually shipped.** `session_lg.py`
was renamed to `web/session.py`, `app_lg.py` to `web/app.py`, and the
`coscribe-web-lg` script entry removed once `coscribe-web` itself
was promoted onto this module. Kept as-is below since it accurately
describes the code and reasoning at the time this phase was built.

`../web/session_lg.py` (`ChatSessionLG`) + `../web/app_lg.py`
(`create_app_lg`) + a new `coscribe-web-lg` console script
(`pyproject.toml`) -- the payoff phase, since every Gemini bug this
migration was started over (`thought_signature` missing/wrong-type, the
`X | None` schema bug) was actually hit through `coscribe-web`, not the
CLI Phase 2 shipped. Reuses the **existing frontend unmodified**
(`web/static/*`, mounted the same way `create_app` does) -- the WS wire
protocol (`state`, `agent_delta`, `agent_message`, `approval_required`,
`tool_result`, `tasks_changed`, `error`, ...) is fully decoupled from
backend internals, confirmed empirically below, not just by inspection.

Native async throughout: `agent.astream(..., stream_mode=["messages"])`
and `agent.aget_state`/`Command(resume=...)` via FastAPI's own event loop,
replacing `ChatSession`'s `queue.Queue`/`asyncio.to_thread`/
`run_coroutine_threadsafe` bridging entirely (that plumbing exists only
because `Runner.run_sync` is synchronous -- LangGraph's compiled graphs
don't have that problem). Uses
`langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver` (confirmed
available/importable), one shared instance across all threads, opened once
in `create_app_lg`'s lifespan.

**Design gap solved, not just ported**: `HumanInTheLoopMiddleware`
batches every currently-pending tool call into one `action_requests` list
per interrupt, but the existing WS contract expects one
`approval_required` message per gated call. `ChatSessionLG` sends one per
`action_request` (a freshly generated id, tracked in a local
`dict[str, asyncio.Future[bool]]`), awaited **sequentially** before
resuming with a `decisions` list in the same order -- matches the old
system's own sequential, one-at-a-time approval behavior, so the frontend
never sees more than one open approval card at a time either way.

**Deliberately narrower than `coscribe-web`**, same deferral list as
Phase 2 (`cli_lg.py`): `/plan`, `/accept-edits`, `/compact` each send an
`error`-type message pointing back at the real command; hooks and
`spawn_agent`/`review_work` aren't wired in. `/api/config`, `/api/mcp/*`,
`/api/providers/*`, and `/api/upload` (config-mutation/upload endpoints,
orthogonal to which runtime drives the chat loop) are left out of
`app_lg.py` for this phase too -- only `/`, `/api/threads`,
`/api/threads/{id}/tasks`, `/api/commands`, `/api/tools`, and the `/ws/*`
endpoint exist.

**`usage` event: checked, not assumed away.** `AIMessageChunk.usage_metadata`
exists as a field, but a live check against streaming Gemini
(`model.astream(...)`) returned `{"input_tokens": 0, "output_tokens": 0,
"total_tokens": 0}` on every chunk -- not usable. `ChatSessionLG` omits the
`usage` event entirely rather than showing a fabricated number, which the
existing WS contract already treats as optional/conditional (`ChatSession`
does the same thing when the old runtime's `RunResult.usage` is `None`).

**Update: this finding didn't hold up -- see "The usage bar" section
further down.** Re-checked against the currently-pinned
`langchain-google-genai` and found real, correctly-computed per-chunk
usage now flows through this exact path (an upstream fix in that package,
not a mistake in this finding at the time it was made) -- the `usage`
event is sent for real now.

**Live-verified, real bugs found and fixed along the way:**
- Confirmed via `inspect.signature`/`model_fields` that `ToolMessage`
  exposes `.name`/`.tool_call_id`/`.content` directly -- `_stream_turn`
  uses `.name` for the `tool_result` event's `tool_name` and
  `json.loads`s `.content` back into the real Python object (it's usually
  a JSON-serialized string of the tool's return value, and the old
  system's `tool_result` event sends the raw object, not a JSON string).
- **A real gap found by actually testing reconnects, not just assuming the
  checkpointer handles it**: the first version of `ws_endpoint` sent
  `state` on connect and then just waited for client messages -- a
  reconnect to a thread with an already-pending approval (from a dropped
  connection or a process restart) got nothing, silently stuck forever.
  Fixed by adding `ChatSessionLG.resume_after_reconnect`, called
  (backgrounded, not awaited inline -- it can block on a future only the
  receive loop can resolve) right after `send_state` on every connect:
  checks `agent.aget_state(config).next`, and if truthy, redelivers the
  pending `approval_required` the same way a fresh turn would.
- Live Playwright walkthrough against real Gemini
  (`gemini:gemini-3.1-flash-lite`), same UI as `coscribe-web` today: a
  `write_file` request renders an identical approval card
  (`Approval required: write_file({"path":"note.txt","content":"hello"})`,
  Approve/Deny buttons), approving actually runs the tool and shows `Ran
  write_file` + the model's final reply -- confirms the frontend really
  does need zero changes.
- **The actual process-restart test, not a same-process simulation**:
  started `coscribe-web-lg` for real, opened a raw WebSocket to
  `/ws/restart_test_1`, sent a `write_file` request, received
  `approval_required`, then killed the server process (`kill -9`) without
  ever responding -- confirmed the file was *not* written. Restarted the
  server fresh (same `--state-dir`, same on-disk `AsyncSqliteSaver` file),
  opened a brand-new WebSocket connection to the same thread id, and the
  redelivery fix above sent the identical pending `approval_required`
  automatically; approving it ran the tool and the file was written with
  the correct content. This is the actual mechanism working across a real
  process boundary, not a same-process stand-in for it (a same-process
  regression test, `test_pending_approval_is_redelivered_on_reconnect`, is
  also in `tests/test_web_lg.py` so this doesn't regress silently later).
- `tests/test_web_lg.py` (9 tests, using a hand-rolled
  `FakeToolCallingChatModel` since `langchain_core`'s own
  `FakeMessagesListChatModel` doesn't implement `_stream` at all -- every
  message would arrive as a plain `AIMessage` instead of the
  `AIMessageChunk`s real provider integrations actually stream, silently
  defeating the exact chunk-type filter this phase depends on) asserts the
  same message type/ordering contract as `tests/test_web.py`: `state`
  first, `approval_required` before `tool_result`, deltas before the final
  `agent_message`, `tasks_changed` last. Skips cleanly (via
  `pytest.importorskip`) rather than breaking collection when run from
  this project's own venv, which doesn't have `langchain_core`/`langgraph`
  installed (same reason `cli_lg.py` has no `tests/test_cli_lg.py`
  equivalent -- but this phase's plan explicitly called for a real pytest
  file here, so the `importorskip` guard is what makes that possible
  without breaking `pytest -q` in the venv that actually ships).

**Not yet tested**: MCP tools (no reachable MCP server in this sandbox,
same caveat as Phase 2); multiple concurrent threads sharing the one
`AsyncSqliteSaver` instance under real concurrent load (exercised
sequentially here, not concurrently); images/multimodal input (wired
through using the same content-block shape `ChatSession` already builds,
but not live-verified this phase).

---

## `coscribe-lg` reaches real feature parity, reusing `ChatSessionLG`

The user's plan, once `runtime_lg` had been proven this thoroughly through
a whole session of live bug-hunting (persistent MCP sessions, tool-error
handling, thread listing, the folder picker, history replay -- all above):
commit to it as the real engine, starting with retiring the old CLI. Before
that could happen safely, `cli_lg.py` needed a real audit against the old
`cli.py` -- and turned out to be missing far more than its own docstring
said: no `/clear`, `/compact`, `/plan`, `/accept-edits`, no `--message`/
`--persona` flags, no workflow commands (`/startworkflow` etc.) at all, and
MCP tool *calls* didn't work (connecting/listing did) because the loop was
synchronous while `langchain-mcp-adapters`' tools are async-only.

**Design: reuse `ChatSessionLG` (`web/session_lg.py`) directly instead of
re-implementing each gap a second time.** Every one of those features is
already built and tested against the web transport, and
`ChatSessionLG.__init__` needs nothing web-specific -- `settings`, a
`thread_id`, a `context_window_client` (`LLMClient`, itself unrelated to
the buggy adapter -- just a context-window lookup), `custom_providers`,
`extra_tools`, a `checkpointer`, `hooks_config`, and an optional `persona`,
all things a CLI entry point already has to build anyway. `cli_lg.py` now
constructs one real `ChatSessionLG` and drives it through `_CliSocket`, a
small duck-typed stand-in for the `WebSocket` parameter `ChatSessionLG`'s
methods expect -- every method that talks back to a client just calls
`.send_json(...)` on it, which `_CliSocket` renders to the terminal
instead of over a real socket (mirrors `app.js`'s own `ws.addEventListener
("message", ...)` switch, just printing instead of updating the DOM).

Since `_handle_user_message_locked` already recognizes and fully handles
every slash command (`/plan`, `/accept-edits`, `/compact`, `/clear`,
`/startworkflow`/`/endworkflow`/`/saveworkflow`/`/runworkflow`, `/init`,
`/<skill-name>`) internally, `chat()`'s own interactive loop shrank to
almost nothing: read a line, hand it to `session.handle_user_message(...)`
unconditionally, print a blank line after. No CLI-side command parsing
left to keep in sync with the web version's own.

**The one real design wrinkle: approval flow doesn't need the web's
concurrency.** `ChatSessionLG._decide_action_request` registers a pending
`Future` in `self._pending_approvals` *before* awaiting `websocket.
send_json({"type": "approval_required", ...})`, then does `await future`
once that call returns. The real web deployment answers that future from
a *separate* concurrent task (`app_lg.py`'s `ws_endpoint` receive loop,
handling a same-tab `approval_response` message that can arrive while the
turn-handling task is still suspended) because a browser can't respond
synchronously inside the very call that's asking it something. A CLI has
no such second concurrent client -- so `_CliSocket.send_json`'s
`approval_required` branch just calls `typer.confirm(...)` and
`session.resolve_approval(id, approved)` synchronously, right there,
before returning. By the time control reaches `await future` back in
`_decide_action_request`, the future is already resolved -- no deadlock,
no `asyncio.create_task` fan-out needed, confirmed correct both by reading
the exact await ordering and by live-testing the full approve/deny flow
against real Gemini.

**Fixed via the same async conversion already proven in `app_lg.py`.**
`chat()` is now a thin sync Typer command wrapping `asyncio.run(
_chat_async(...))`; the checkpointer switched from `SqliteSaver` to
`AsyncSqliteSaver`. This alone is what fixes the MCP tool-call gap --
`cli_lg.py`'s own (accurate) diagnosis of the root cause (async-only tools
under a sync loop) just needed the same fix `coscribe-web-lg` already
has, not a new one.

**Two deliberate, documented behavior differences from the old `cli.py`,
not oversights:**
- `--message` (the cron/systemd one-shot flag) used to never interpret
  slash-commands ("sent as-is to the agent"). It now goes through the same
  `handle_user_message` every other turn does, so a one-shot message
  starting with `/plan` etc. would now be interpreted as that command.
  Traded a narrow, easy-to-avoid old edge case for one shared code path
  instead of a second, slightly different one.
- Auto-compact-over-threshold (`cli.py`'s `maybe_auto_compact`) is **not**
  ported, and not silently missing either: `session_lg.py`'s own existing
  comments already record that Gemini's streaming path returns all-zero
  `usage_metadata`, so there's no reliable token-usage signal to trigger
  an automatic compaction on in this runtime today. Manual `/compact`
  works fully. Wiring up a real usage signal (and porting auto-compact to
  both the CLI and the web session, since neither has it) is separate,
  future work -- not something to fake with an unreliable number.

**Live-verified against real Gemini, every gap this pass closed, not just
unit-tested against fakes:** a plain round trip; `/plan` blocking a
`write_file` call outright (turned into a `task_create` instead, exactly
like the real `coscribe`); toggling `/plan` back off and the same
request going through the normal approval prompt, approved, file written
correctly; `/startworkflow` -> a real tool call -> `/endworkflow <name>
<summary>` -> `/runworkflow <name>` replaying the exact same call with no
LLM involved; `/compact` collapsing a real multi-turn thread; `/clear`
wiping it; `--persona` applying a test persona's instructions (verified
via a distinctive reply prefix the persona's instructions require) and
its narrowed tool subset; reconnecting with the same `--thread` and
seeing the *prior* turn's user/agent/tool-call history replayed before
the next prompt (reusing `send_history`, a capability the old `cli.py`
never had at all); and -- the concrete regression test for the whole
reason this rewrite exists -- a real MCP tool (a tiny stateful test
server's `increment()` tool, the same one `scripts/verify_mcp_persistent_
session.py` uses) actually **called** three times in a row and returning
the correct increasing sequence `1, 2, 3`, not just connected/listed.

**Test-suite-level verification, not just the live smoke tests above.**
New `tests/test_cli_lg.py` (13 tests): the same coverage shape as
`tests/test_cli.py`'s real-CLI tests (not copy-pasted, since the
mechanism differs), run against a `FakeToolCallingChatModel` the same way
`test_web_lg.py` already does. Full `pytest -q` (476 passed, 1 skipped,
one single pre-existing unrelated failure in `test_gemini_provider.py`
confirmed to already fail on `origin/main` before this pass touched
anything) and `tests/test_cli_lg.py`/`test_web_lg.py`/`test_mcp_lg.py`
together (92 tests) run clean three times in a row. `ruff check`/`mypy`
both clean.

**Not yet done, deliberately scoped out of this pass:** `cli.py` itself
is untouched -- this pass only brings `cli_lg.py` to parity so that a
later pass can safely delete `cli.py` and promote `cli_lg.py` in its
place, per the plan's own two-phase sequencing (parity first, verified
clean, *then* the cutover -- never a window where `coscribe` has
fewer features than before).

## The cutover: `cli.py` deleted, `cli_lg.py` promoted in its place

The second half of the two-phase sequencing above, done once the parity
pass above was fully verified clean. `src/coscribe/cli.py` (the
original hand-rolled-runtime implementation) deleted outright;
`cli_lg.py` renamed to `cli.py` -- `coscribe = "coscribe.cli:
main"` in `pyproject.toml` needed no change at all, since it already
pointed at the now-promoted module. The `coscribe-lg` console script
entry point is gone; there is only `coscribe` now, and it runs on
`runtime_lg`.

**A real bug found doing this, not anticipated by the plan:** `cli.py`
(promoted) now imports `ChatSessionLG` from `web/session_lg.py` at module
level -- but `session_lg.py` itself does `from ..cli import INIT_PROMPT`.
When `cli.py` and `cli_lg.py` were two separate files, this was a clean,
one-directional chain (`cli_lg.py` -> `web.session_lg` -> `cli`, and
nothing ever imported `cli_lg.py` back). Promoting `cli_lg.py` to *be*
`cli.py` turned that chain into a genuine circular import: `cli.py` ->
`web.session_lg` -> `cli` (now the same, partially-initialized module).
Confirmed the hard way (`ImportError: cannot import name 'INIT_PROMPT'
from partially initialized module 'coscribe.cli'`), not predicted in
advance. Fixed by reordering `cli.py` itself: `INIT_PROMPT`/
`_load_settings` are now defined *before* the `from .web.session_lg
import ChatSessionLG` line further down (with a `# ruff: noqa: E402` at
the top of the file to allow the deliberate split) -- Python resolves a
circular import fine as long as the name being re-imported is already
bound as a module attribute by the time the cycle closes, which this
ordering guarantees. `INIT_PROMPT`/`_load_settings` themselves needed no
relocation to a third module, despite that being the more obvious-looking
fix -- just reordering within the one file already promoted was enough.

**A second, positive discovery made while updating `pyproject.toml`'s
entry points: the `langgraph_spike` extra's own reason for existing no
longer applied, and hadn't for a while.** That extra was kept separate
from the base `dependencies` specifically because it was documented as
unable to coexist with them: `aisuite[anthropic]==0.1.14` hard-pins
`anthropic<0.31.0`, disjoint from every `langchain-anthropic` release's
`anthropic>=0.64.0` floor. Re-reading the base `dependencies` list while
making this change surfaced that this project *already* stopped
installing aisuite's own `anthropic` extra -- for a completely unrelated
reason (prompt-caching support needing a newer SDK than aisuite's pin
allowed) -- and pins a compatible `anthropic>=0.68.0,<1.0.0` directly
instead. That change predates this session's own memory of the project,
but is sitting right there in the file; the `langgraph_spike` extra's own
comment block just never got updated to reflect that its blocking
condition was already gone.

Verified empirically rather than trusted on re-reading alone: installed
`dependencies` + `langgraph_spike`'s former package list together in a
completely fresh venv -- resolved cleanly, `anthropic-0.120.2` (well
within both ranges) satisfying aisuite and `langchain-anthropic` at once,
no `ResolutionImpossible`, no backtracking. Merged `langgraph_spike`'s
packages into the base `dependencies` list and deleted the extra entirely
-- `coscribe` (the promoted CLI, the primary entry point) needs them
unconditionally now, so gating them behind an opt-in extra would have
meant a bare `pip install coscribe` producing a command that's
registered but crashes on first run. This also meant `[tool.mypy]`'s
`exclude` list (`runtime_lg`, the old `cli_lg.py`, `session_lg.py`,
`app_lg.py` -- all previously excluded specifically because their
imports couldn't resolve in this project's own venv) no longer needed any
of those entries either.

**This also means the separate `.venv-lg` this whole session's work was
developed and verified against is no longer necessary.** Rebuilt this
project's own (single) `.venv` from the merged `pyproject.toml`, and
confirmed *from that one venv*: `mypy src` clean (`Success: no issues
found in 41 source files`, no excludes needed for anything runtime_lg-
related anymore), full `pytest -q` clean (one single pre-existing
unrelated `test_gemini_provider.py` failure, same as always) across
three repeated runs, and a real live smoke test of the actual installed
`coscribe` console script (not a `python -m` invocation, not
`CliRunner` -- the literal command a real install produces) against real
Gemini, reading a real file end to end. `docs/README.md`'s existing
install instructions (`pip install -e ".[dev]"`, `".[web]"`) needed no
wording changes at all -- they already describe exactly the commands
that now transparently include the LangGraph engine too.

## `web_search` -- a new built-in tool, not a runtime_lg-specific change

Adds `tools/websearch.py`'s `web_search(query, max_results=5)` for
grounding (current events, facts beyond training data) -- explicitly not
RAG, and not the same thing as `search_files`/`search_pdf` (which only
ever look at files already in the workspace). Wired into
`build_coordinator_agent` (`coordinator.py`) alongside the other built-in
tool modules, which is why this needed no `runtime_lg`-specific code at
all: `coordinator.py` is shared, and `ChatSessionLG._build_lg_tools`
builds off `agent.tools` from that same shared call, so this one addition
reached the CLI, old `coscribe-web`, and `coscribe-web-lg`
simultaneously.

Uses the standalone `ddgs` package directly (not `langchain_community`'s
`DuckDuckGoSearchRun`, now considered legacy in favor of it). Originally
pinned to its `"duckduckgo"` backend explicitly; now uses `ddgs`'s own
`"auto"` mode instead (see the live-reported bug below for why) -- either
way, free, no API key, matching this project's existing local-first
stance for every other built-in tool.

**A real dependency-resolution wrinkle, same shape as the google-genai/
httpx one already documented above (not a new kind of problem):** `ddgs`
requires `httpx>=0.28.1`, while `aisuite==0.1.14` pins `httpx<0.28.0`.
Verified compatible in practice the same way that earlier conflict was --
installed both together and ran the full test suite clean, confirming
aisuite's own httpx usage (the provider backends and MCP path this
project actually exercises) isn't affected by the 0.27->0.28 change.

**A real gotcha found writing the unit tests, worth recording:** `from
ddgs import DDGS` doesn't import the real class -- `ddgs`'s own
`__init__.py` exports `_DDGSProxy`, a lazy-loading metaclass-based
stand-in that only imports and instantiates the real `ddgs.ddgs.DDGS` on
first call. The "obvious" monkeypatch target
(`coscribe.tools.websearch.DDGS.text`) silently patches the proxy,
not the class that actually ends up handling the call -- the first test
attempt made a real network call instead of using the fake, with no error
at all pointing at why. `tests/test_websearch_tool.py` patches
`ddgs.ddgs.DDGS.text` (the real class) instead; documented directly in
`websearch.py`'s own comments too, not just here, since this is exactly
the kind of thing a future maintainer would rediscover the hard way
otherwise.

**Not live-verified from this sandbox, and can't be -- a genuine
environment restriction, not a gap in the work.** This project's own
development sandbox blocks every general web-search-engine host at its
egress-proxy policy level: `curl $HTTPS_PROXY/__agentproxy/status` showed
`html.duckduckgo.com`, `search.brave.com`, `en.wikipedia.org`,
`www.startpage.com`, and others all rejected with a 403 policy denial,
confirmed non-negotiable per that proxy's own README ("org policy denials
are not to be routed around") -- the same category of restriction that
already blocked live DeepSeek/GLM verification earlier in this project's
history. `scripts/verify_websearch.py` (a real, no-mock call to
`web_search`, no LLM involved at all since the tool itself is a pure
function) is ready to run from an unrestricted machine -- the same one
already used to independently verify DeepSeek/GLM. Unit tests
(`tests/test_websearch_tool.py`, 4 tests, real `ddgs` result shape
mocked, no network) plus `GET /api/tools` metadata assertions in both
`test_web.py` and `test_web_lg.py` cover what's verifiable from here;
`pytest -q` (452 passed, 1 skipped, the one pre-existing unrelated
failure), `ruff check`, and `mypy` are all clean.

## Pinning `web_search` to one backend was a real, live-reported bug -- fixed by using ddgs's own "auto" mode

The user tested `web_search` for real on their own machine (the one thing
this sandbox's egress-proxy policy blocks, see above) and hit exactly the
risk this file's own earlier draft had flagged as a documented but
unexercised concern: the server log showed
`INFO:primp:response: https://html.duckduckgo.com/html/ 202` (`primp` is
`ddgs`'s underlying HTTP client) -- an HTTP 202 from DuckDuckGo's
html-only search endpoint, not the real results page. `ddgs`'s HTML
parser found nothing to extract from that response, so `web_search`
legitimately returned an empty list (no exception, no error surfaced --
just zero real results), and the model, with nothing to ground on,
answered from stale training knowledge instead and then hedged vaguely
when asked to search again. This is DuckDuckGo's own anti-bot/soft-block
behavior, not a bug in how the request was made -- confirmed by checking
`ddgs.ddgs.DDGS._search_sync`'s own default: `backend: str = "auto"`.
Pinning to `backend="duckduckgo"` (this tool's original choice, reasoned
about but never live-tested until now) meant that single engine's own
soft block emptied the *entire* search, with nothing else to fall back
to.

Fixed by dropping the explicit `backend="duckduckgo"` argument entirely,
letting `ddgs`'s own default `"auto"` mode take over: confirmed via
`ddgs.engines.ENGINES["text"]` that this queries up to eight free,
no-API-key engines in parallel (`duckduckgo`, `brave`, `google`,
`mojeek`, `startpage`, `wikipedia`, `yahoo`, `yandex`) and aggregates
whichever ones actually return results -- one engine's own anti-bot
response no longer empties the whole call, it just quietly doesn't
contribute to the aggregate. All still free and keyless, so this doesn't
compromise the original "no forced paid dependency" reasoning for picking
`ddgs` in the first place -- it was never really a "DuckDuckGo tool," the
package itself was always multi-backend, `backend="duckduckgo"` was just
an unnecessary self-imposed restriction that turned out to be exactly the
failure mode this file's own module docstring had already warned about
("if DuckDuckGo's own anti-bot measures ever make this backend
meaningfully unreliable, that's the documented escape hatch") -- just
reached sooner than expected, and via live user testing rather than this
sandbox (which can't reach any of these hosts to have found it first).

Updated `tests/test_websearch_tool.py` (the `backend="duckduckgo"`
assertion now checks the key is *absent* from the call, i.e. `ddgs`'s own
default applies) and `ARCHITECTURE.md`/`README.md`'s wording (no longer
described as "a DuckDuckGo tool"). Not live-re-verified from this sandbox
for the same reason as before (egress policy) -- `pytest -q`, `ruff
check`, `mypy` all clean again after the change; the user's own machine
is what will actually confirm this fixes the live symptom.

## The model had no ground truth for "today" -- a second real bug, found immediately after the fix above, same live session

Once the "auto" backend fix above actually returned real results, a new,
different symptom showed up: asked for current AI news, the model
answered with stale, plausible-sounding but wrong information, and when
the user pointed out the real date, it apologized, claimed a "system time
display / cognitive bias" it never actually experienced, and repeated
that same apology three times almost verbatim before finally producing a
real, current answer. Not a hallucinated tool failure this time -- the
tool was already working (confirmed by the surrounding log lines showing
real 200 responses) -- but a hallucinated *self-diagnosis*: the model had
no authoritative source for what day it actually is, only its own
training-cutoff-anchored guess, and when live search results didn't match
that guess, it had nothing to reconcile against except inventing an
explanation.

Root cause, confirmed by reading the actual code: `coordinator.py`'s
`INSTRUCTIONS` (the Coordinator's system prompt) never stated the current
date anywhere, and nothing else in the request path did either --
`session_lg.py` has its own `_now_iso()`, but that's only ever used for
timestamps written to disk (workflow saves etc.), never surfaced to the
model. The model's only "now" was whatever its training data implied,
with no live signal to correct it -- the exact gap that made a genuinely
working `web_search` call look, from the model's own perspective, like
proof that something was broken.

Fixed with a new `_current_date_note()` in `coordinator.py`, prepended to
`instructions` in `build_coordinator_agent` (computed fresh on every call,
i.e. once per new thread -- not baked into the static `INSTRUCTIONS`
string, which has no per-session context of its own): `"Today's real date
is YYYY-MM-DD."`. Also strengthened `web_search`'s own instructions
paragraph with an explicit "an empty/unhelpful result means try a
different query, not that the tool is broken" line, directly countering
the specific wrong conclusion the model jumped to live. Both fixes are
plain system-prompt text, not runtime_lg-specific -- `coordinator.py` is
shared, so this reaches the CLI and both web backends at once, same as
`web_search` itself.

**New regression test, not just a live-observed fix.**
`test_instructions_state_the_real_current_date` (`tests/
test_coordinator.py`) asserts the real, actual current date (computed the
same way the fix does, `datetime.now().strftime("%Y-%m-%d")`) appears
verbatim in a freshly built agent's instructions. Existing instructions
tests all use substring assertions (`"..." in agent.instructions`), not
exact-string equality, so prepending this note didn't require updating
any of them -- confirmed by running the full suite, not assumed. `pytest
-q` (453 passed, 1 skipped, the one pre-existing unrelated failure),
`ruff check`, `mypy` all clean; `tests/test_coordinator.py`/
`test_web.py`/`test_web_lg.py`/`test_cli.py` together (189 tests) run
clean twice in a row.

## A persona's `tools` whitelist was silently stripping MCP connector tools too -- a third real bug, found live via the Ops Coworker persona

Live-reported directly by the user, and self-diagnosed correctly before I
even had a clean repro: Playwright MCP tools worked with no persona
active, but vanished the moment the "Ops Coworker" persona (a `tools:
[...]` whitelist naming only its own domain tools) was selected in the
same browser tab, same connectors panel still showing Playwright as
connected. ("我发现问题了，default有那个工具，ops coworker模式没有" -- "I
found the problem: default has that tool, Ops Coworker mode doesn't.")
This surfaced mid-session while chasing a red herring first (whether a
DeepSeek-model turn was actually still bound to Gemini) -- the persona
angle turned out to be the real, unrelated variable.

Root cause, confirmed by reading `runtime/personas.py`'s own docstring for
`apply_persona`: a persona's `tools` whitelist is documented to scope only
the "domain" tools `build_coordinator_agent` built -- it must never strip
MCP connectors or the delegation/workflow tools, which are meant to stay
available regardless of which persona is active. The old `cli.py` upheld
this purely through *ordering*: `build_coordinator_agent` -> `apply_persona`
(narrows `agent.tools`) -> MCP tools spliced in afterward via
`agent.tools.extend(mcp_tools)` -- persona narrowing runs before MCP tools
ever exist in that list, so it can't touch them.

`ChatSessionLG.__init__` (`web/session_lg.py`) didn't preserve that
ordering: it did `agent.tools.extend(extra_tools)` (merging MCP tools
straight into `agent.tools`) *before* building `self._base_tools` from
that same list. `_apply_persona_to_base`'s whitelist filter then ran over
`self._base_tools`, which by that point already contained the MCP tools --
so selecting any persona with a `tools:` whitelist silently dropped every
MCP-sourced tool that whitelist didn't happen to name, exactly as the user
observed.

Fixed by keeping MCP tools out of `self._base_tools` entirely: `__init__`
now builds `self._base_tools` from `agent.tools` *before* merging anything
else in, and stores the MCP tools separately as `self._extra_tools`
instead of extending `agent.tools` with them. Persona selection
(`_apply_persona_to_base`) only ever narrows `self._base_tools`, so
`self._extra_tools` -- and therefore every MCP connector tool -- is now
structurally immune to a persona's whitelist, matching the old runtime's
documented guarantee instead of relying on call-order coincidence.
`_build_lg_tools` was updated to recombine
`[*self._base_tools, *self._extra_tools]` into `combined_tools`, used both
for `spawn_agent`'s `available_tools` (sub-agents can still delegate to
MCP tools, matching the old `cli.py`'s behavior) and as the base of the
tool list actually handed to the compiled graph.

**New regression test, not just a live-observed fix.**
`test_persona_tools_whitelist_does_not_strip_mcp_connector_tools` (`tests/
test_web_lg.py`) connects a fake MCP tool via `POST /api/mcp/servers`,
opens a *new* session (so it picks up the freshly-spliced tool per
`app_lg.py`'s own "next new thread" contract), selects a persona whose
`tools:` whitelist does not name that MCP tool, and asserts the model can
still call it and get a real `tool_result` back. Confirmed this test
actually catches the regression: temporarily reverted just the
`session_lg.py` fix (keeping the new test) and re-ran it -- it fails with
`Error: fetch__fetch_url is not a valid tool, try one of [list_files,
spawn_agent, review_work, list_recorded_steps]`, i.e. exactly the
user-observed symptom. With the fix restored: `pytest -q` (454 passed, 1
skipped, the one pre-existing order-dependent `test_gemini_provider.py`
failure -- confirmed unrelated by reproducing it identically with this
session's changes fully stashed out), `ruff check src tests scripts`, and
`mypy src` all clean.

## Web cutover: `web/app.py`/`web/session.py` deleted, `app_lg.py`/`session_lg.py` promoted in their place

The web equivalent of the CLI cutover above, done once `coscribe-web-lg`
had real feature parity plus several rounds of live use turning up no gaps
the old runtime still covered (the last of which was the persona/MCP fix
directly above, found and fixed in the same live session that prompted
this cutover). `src/coscribe/web/app.py` and `web/session.py` (the
original hand-rolled-runtime implementations) deleted outright; `app_lg.py`
renamed to `app.py`, `session_lg.py` renamed to `session.py` --
`coscribe-web = "coscribe.web.app:main"` in `pyproject.toml` needed
no change at all, since it already pointed at the now-promoted module. The
`coscribe-web-lg` console script entry point is gone; there is only
`coscribe-web` now, and it runs on `runtime_lg`. `tests/test_web.py`
(the old suite) deleted, `tests/test_web_lg.py` promoted to `tests/
test_web.py` in its place.

**A real, code-breaking problem found doing this, not anticipated by the
plan (same shape as `cli.py`'s circular-import bug, but different
mechanics):** the promoted `app.py` (formerly `app_lg.py`) did `from .app
import (...)` at module level -- importing a batch of module-level catalog
data, pydantic request models, and file-read/mask helpers (`MCP_CATALOG`,
`PROVIDER_CATALOG`, `BUILTIN_PROVIDERS`, `ConfigUpdate`, `_mask`,
`_read_mcp_servers_raw`, etc.) from the *old* `web/app.py`, deliberately
reused rather than duplicated (see this file's own now-superseded module
docstring, quoted in an earlier commit). Deleting the old `app.py` and
renaming `app_lg.py` into that same path turned that import into a
self-import of the not-yet-fully-defined module -- caught immediately by
just trying to import the promoted module (`ImportError`), not subtle.
Fixed by pulling those definitions' actual source (recovered via `git show
HEAD:.../web/app.py` before the deletion landed) directly into the
promoted `app.py`, in place of the `from .app import (...)` block --
`FIXED_COMMANDS` was skipped in that copy since `app_lg.py` already
defined its own identical copy, confirmed by diffing the two before
merging. Also adopted the old `app.py`'s `_NoCacheStaticFiles` class
(`app_lg.py` had been mounting plain `StaticFiles`, no no-store
`Cache-Control` header) -- a real, if minor, regression risk this cutover
was the right moment to close, since the whole reason that class exists
(a stale-cached `app.js` after `git pull` + restart silently killing every
listener registered past the point a moved/removed DOM id throws) applies
to whichever module is the one users actually run.

**A second real, if smaller, correctness gap found while checking every
symbol this cutover touched: `runtime/personas.py`'s `apply_persona`
docstring pointed at `web/session.py`'s `ChatSession.__init__` for its
ordering guarantee -- a reference that predated this whole migration and
was never updated when the persona/MCP bug above was fixed.** Now that the
old `ChatSession` is gone and the guarantee is enforced structurally in
the promoted `session.py`'s `ChatSessionLG` (separate `self._base_tools`/
`self._extra_tools`, not just call order), that docstring was rewritten to
say so explicitly, including a pointer to this file's own persona/MCP
section for why the distinction mattered in practice, not just in theory.

**A real, user-facing documentation gap, not a code bug:** `README.md`'s
MCP-connector and custom-provider sections both claimed adding/removing
one "applies immediately, in every open conversation" -- true of the old
runtime's `Runner`, which re-reads `agent.tools` and re-resolves the model
fresh on every turn, but never true of `runtime_lg`'s compiled-graph
sessions, which fix their tools/model at construction (see this module's
own docstring, and the "web cutover" note added to it above). This claim
was only ever safe to leave uncorrected while the old runtime was still
the one most users actually ran; promoting `runtime_lg` to be the *only*
`coscribe-web` makes it actively misleading if left as-is. Fixed by
rewording both `README.md` passages to state the real, current behavior
(saved immediately, takes effect for the next new conversation, not
already-open ones) instead of quietly promoting a claim that was never
true for this backend.

**Verification, following the same discipline as the CLI cutover:** a
sandbox-side git-state corruption (this session's `git` HEAD silently
reverting to a stale commit -- a recurring, previously-documented issue,
unrelated to this cutover's own content) was hit and recovered from
*during* this work, via `git fetch` + `git reset --hard` back onto the
correct remote history, followed by manually re-applying the file
promotions on the correct base rather than trusting a `git stash pop`
across the gap for the two files (`session_lg.py`, `test_web_lg.py`) the
missed commits had actually touched -- called out here because it's
exactly the kind of silent-data-loss risk this session's "verify fully,
don't assume" discipline exists to catch, not because it reflects on the
cutover's own design. Once back on the correct base: `pytest -q` clean
(360 passed, 1 skipped, the one pre-existing order-dependent
`test_gemini_provider.py` failure, unrelated -- confirmed by reproducing
it identically with every change from this whole session stashed out),
`ruff check src tests scripts` and `mypy src` clean (40 source files, down
from 42 -- the two deleted old-runtime web files). Live smoke test of the
actual installed `coscribe-web` console script (not a bare module
import): real server boot with no API key configured beyond a dummy
`COSCRIBE_DEFAULT_MODEL`, `GET /` and `GET /static/app.js` both 200,
`GET /api/tools`/`GET /api/config`/`GET /api/mcp/servers` all real
responses (not mocked), `_NoCacheStaticFiles`'s `Cache-Control: no-store`
header confirmed present on the static response. `coscribe` (the CLI,
which also imports `web/session.py`) re-verified working end to end too,
since it shares the promoted module.

## Audit + delete old runtime -- closing the loop this whole migration was started for

Once both cutovers above landed (CLI, then web), the old hand-rolled
runtime (`runtime/runner.py`, `runtime/policies.py`, `runtime/
state_store.py`, `tools/subagents.py`'s tool factory, `providers/
anthropic_provider.py`, most of `providers/gemini_provider.py`) had no
real entry point left calling it -- but nothing had actually confirmed
that, and parts of `runtime/` (`ToolMetadata`/`Agent`, persona parsing,
hooks, provider-config loading) were known to still be genuinely shared.
Deleting the wrong thing here would silently break the running app, so
this started with a dedicated research pass (an Explore agent, not
assumed) tracing real reachability from the four actual entry points
(`cli.py`, `web/app.py`, `web/session.py`, `coordinator.py`) plus
everything `runtime_lg/` itself imports, file-by-file and symbol-by-symbol,
every claim grep+read verified. Full findings kept in that pass's own
report; summarized below is what actually got deleted/trimmed and why,
plus what the audit found that turned out to be more than pure
housekeeping.

**Deleted outright** (confirmed zero reachable callers): `runtime/
runner.py` (`Runner`/`RunResult`), `runtime/policies.py` (`ToolPolicy` and
every concrete policy), `runtime/state_store.py` (`FileStateStore`, once
its one remaining real call site -- `tools/workflows.py`'s
`build_workflow_tools` closure -- was itself confirmed to build a
`list_recorded_steps` tool that `web/session.py` was *already* filtering
out of every real session, backed by `_build_list_recorded_steps_tool`'s
own checkpointer-based replacement instead), `tools/subagents.py`'s
`build_subagent_tools` tool factory (its one still-needed piece,
`REVIEWER_INSTRUCTIONS`, moved into `runtime_lg/subagents.py`, its only
real consumer), `providers/_vendor/chat_completion_chunk.py` (the
OpenAI-shaped streaming-chunk dataclasses, only ever constructed by the
now-dead completion/streaming code below), and `providers/
anthropic_provider.py` entirely -- confirmed its subclass added nothing
once its one method (`chat_completions_create`, prompt-caching
`cache_control` breakpoints) was dead: aisuite's own
`ProviderFactory.create_provider("anthropic", config)` produces an
equivalent object directly (verified live: `hasattr(..., "get_context_window")
== False` on aisuite's base class either way, so `get_context_window`
already fell through to the fallback table for Anthropic regardless of
which class built the provider).

**Trimmed, not deleted** (real code stayed alive, dead code removed from
the same file): `runtime/compaction.py` (kept only `COMPACT_INSTRUCTIONS`,
the constant `web/session.py`'s own `_handle_compact` still uses; deleted
`run_compaction`/`compact_state`/`maybe_auto_compact`/`render_transcript`,
confirmed zero callers -- `_handle_compact`'s own docstring already said
outright it couldn't reuse them), `runtime/llm_client.py` (kept `LLMClient`
itself, provider resolution/caching, and `get_context_window`; deleted
`.complete()`/`.complete_stream()` and their helpers, confirmed unreachable
now that `resolve_chat_model`'s LangChain models are the only real
generation path), `runtime/personas.py` (kept `PersonaConfig`/
`load_personas`/parsing; deleted `apply_persona` and
`format_persona_listing`, both confirmed to have zero call sites --
`web/session.py`'s own `_apply_persona_to_base` is a separate
reimplementation, not a caller of this one, built because it narrows a
plain list of `Callable`/`BaseTool` rather than an `Agent.tools` field;
`web/app.py`'s `GET /api/personas` builds its own listing directly rather
than calling `format_persona_listing`), `runtime/types.py` (kept `Agent`/
`ToolMetadata`/`tool_metadata`/`get_tool_metadata`; deleted `RunResult`/
`RunState`/`RunStep`/`ensure_json_serializable`, confirmed used only by
the dead code above), `tools/workflows.py` (kept the data model/storage
--`Workflow`/`WorkflowRun`/`WorkflowStore`/`WorkflowRunStore`/
`WorkflowSaveProposal`, the two curator-prompt constants runtime_lg's own
`record_chain_workflow_lg`/`propose_workflow_save_lg` still import
directly, `reconcile_interrupted_runs`, and `build_workflow_tools` itself
minus its dead `list_recorded_steps` closure; deleted
`record_chain_workflow`/`record_agent_workflow`/`propose_workflow_save`/
`build_workflow_run_tool`/`_run_chain`/`_run_agent_mode` and their private
helpers, confirmed superseded end to end by runtime_lg/workflows.py's
`_lg`-suffixed equivalents), and `tools/mcp.py` (kept
`load_mcp_server_configs`; deleted `connect_mcp_tools`/
`connect_one_mcp_server` and the module-level aisuite `MCPToolWrapper`
signature-ordering monkeypatch, confirmed both `cli.py` and `web/app.py`
exclusively use `runtime_lg/mcp.py`'s `connect_mcp_tools_lg`/
`connect_one_mcp_server_lg` instead, which never touches aisuite's
`MCPToolWrapper` at all -- so the monkeypatch was already inert, not just
unused).

**The most consequential deletion, symbolically as much as practically:**
`providers/gemini_provider.py` -- the exact hand-vendored adapter whose
three real, live-shipped bugs (a `str | None`/`int | None` schema bug, a
missing-`thought_signature`-on-replay bug, a `bytes`-vs-base64-text type
bug) were this whole migration's original motivation (see this file's
Context section at the very top) -- had its entire message/schema
conversion layer (`GeminiMessageConverter`, `chat_completions_create`,
`achat_completions_create`, `chat_completions_create_stream`,
`achat_completions_create_stream`, `_StreamState`, the 429-retry helpers)
confirmed unreachable and deleted. What's left is ~50 lines: `__init__`
and `get_context_window`, the one method genuinely still exercised (every
`send_state()` call for a `gemini:*` model). Every real Gemini
conversation now goes through `langchain-google-genai` instead --
published, not vendored, and not this project's problem to maintain
message-shape conversion for anymore.

**Test files followed the same split.** Deleted outright (tested only
now-gone code): `tests/test_runner.py`, `tests/test_policies.py`,
`tests/test_subagents.py`, `tests/test_compaction.py`,
`tests/test_anthropic_provider.py`, `tests/test_gemini_provider.py` (every
test in it exercised the deleted converter/streaming code; grepped first
to confirm none of them exercised `get_context_window`, the one method
that survived), and `tests/fakes.py` itself (`FakeLLMClient`'s
`.complete()`/`.complete_stream()` mirrors -- once nothing imported `.fakes`
at all anymore, confirmed by grep, the whole module was dead, not just
parts of it). Trimmed in place (kept the tests for what's still alive in
each file, deleted the rest): `tests/test_hooks.py` (kept `run_hook`/
`load_hooks_config` tests, deleted `HookToolPolicy` integration tests),
`tests/test_llm_client.py` (kept provider-resolution/registration/
`get_context_window` tests, deleted `.complete()`/`.complete_stream()`
tests), `tests/test_personas.py` (kept parsing tests, deleted
`apply_persona` tests), `tests/test_mcp_tools.py` (kept
`load_mcp_server_configs` tests, deleted `connect_mcp_tools`/monkeypatch
tests), `tests/test_workflows_tool.py` (kept `WorkflowStore`/
`WorkflowRunStore`/`reconcile_interrupted_runs`/list-get-delete tests,
deleted every `record_*`/`propose_workflow_save`/`run_workflow` test --
the large majority of the file).

**A genuinely useful side effect, not engineered for:** this session's
one remaining pre-existing flaky test
(`test_gemini_provider.py::test_replayed_function_call_part_validates_against_real_sdk_content_model`,
order-dependent, confirmed unrelated to every other fix landed earlier
this session) is simply gone now, along with the rest of that file --
`pytest -q` is clean with no caveats for the first time this whole
session, not because the flake was fixed, but because the code path it
tested no longer exists to test.

**One real, honest gap surfaced by this audit, not fixed here -- flagged
rather than silently left for someone to rediscover:**
`Settings.max_turns`/`Settings.auto_compact_threshold` (`config.py`) are
still real, documented, UI-exposed settings (`COSCRIBE_MAX_TURNS`/
`COSCRIBE_AUTO_COMPACT_THRESHOLD`), but grepping for real readers of
`settings.max_turns`/`settings.auto_compact_threshold` anywhere in `src/`
turns up nothing -- both were wired to the old runtime's own `Runner`
loop (a turn cap; `maybe_auto_compact`, called after every turn) and
never got an equivalent wired into runtime_lg. `max_turns` has no
exposed LangGraph knob wired up yet; auto-compact's old call site
(`maybe_auto_compact`) was independently confirmed dead by this same
audit, before this gap was even being looked for. Manual `/compact`
still works (`web/session.py`'s own `_handle_compact`) -- only the
automatic, threshold-triggered trigger doesn't fire.

(A second gap flagged in this section originally -- the web UI's
context-usage bar never populating -- turned out to be a stale finding,
not a real one; fixed in a follow-up pass, see "The usage bar" section
further down.)

**Update: this gap is now closed too.** See "`max_turns` and
`auto_compact_threshold` -- closing the 'One real gap' left by the audit
above" further down for the design (including why `SummarizationMiddleware`'s
own native `trigger=("fraction", X)` mode couldn't be used directly) and
live verification against real Gemini.

**Verification.** `pytest -q`: 236 passed, 1 skipped, run twice back to
back for stability -- zero failures, zero the-one-usual-caveat for the
first time this session. `ruff check src tests scripts` and `mypy src`
both clean (35 source files, down from 40 after the web cutover, 42
before it -- three whole files deleted this pass: `runner.py`,
`policies.py`, `state_store.py`, plus `tools/subagents.py`, `providers/
anthropic_provider.py`, and the `_vendor/` package). Live verification,
not just import-level: started the actual installed `coscribe-web`
console script fresh, confirmed `GET /`/`/api/tools`/`/api/config`/
`/api/personas`/`/api/workflows` all real 200s (21 tools listed, one
fewer than before -- `list_recorded_steps` no longer appears at all,
correctly, since it was already inert); then a real end-to-end
`coscribe --message "List the files in your workspace, then tell me
the current date you were given." --accept-edits` run against live
`gemini-3.1-flash-lite` (a real API key was available in this pass,
unlike most of this session) -- it genuinely called `list_files`,
reported the real empty workspace, and reported the real current date
correctly (confirming the earlier date-grounding fix, `coordinator.py`'s
`_current_date_note()`, still works end to end through every layer
touched by this pass).

**A sandbox-side git-state corruption was hit and recovered from during
this same pass**, unrelated to the audit's own content -- this session's
recurring "git HEAD silently reverts to a stale commit" issue struck
again partway through the web cutover (see that section above), and the
recovery (`git fetch` + `git reset --hard` onto the correct remote
history, then manually re-doing just the two files the missed commits had
touched rather than trusting a blind `git stash pop` across the gap) is
recorded there, not repeated here -- noted again only because it's the
kind of silent-data-loss risk this session's "verify fully, don't assume"
discipline exists to catch.

## The usage bar -- a "known gap" that turned out to be a stale finding, not a real one

Flagged, not fixed, in the audit section above: the WS `usage` event
(drives the frontend's context-usage bar) was never sent under
`coscribe-web`, because Phase 3's own live check against streaming
Gemini found `AIMessageChunk.usage_metadata` came back
`{"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}` on every
chunk. Re-investigated on request, rather than assumed still true: read
the currently-installed `langchain-google-genai` (3.2.0, allowed by this
project's own `>=2.0.0` pin) source directly, and found real,
correctly-computed per-chunk delta usage in `_response_to_result` --
Gemini's API returns *cumulative* token counts with every chunk, and this
version explicitly subtracts the previous chunk's cumulative value
(`subtract_usage`) to get each chunk's own delta, with a documented
workaround for a Gemini-2.0-specific case where the final chunk's
cumulative prompt count is *lower* than the running total. **This code
did not exist, or worked differently, whenever the original "all zeros"
finding was made** -- it's a real upstream fix in a package this project
only loosely version-pins, not a mistake in the original finding at the
time it was made.

**Live-verified before writing any fix, not assumed from reading source
alone:** a plain `model.astream(...)` call showed real, non-zero
`usage_metadata` per chunk. The actual path `_stream_turn` uses --
`agent.astream(turn_input, config=config, stream_mode=["messages"])`
through a real `create_agent`/`HumanInTheLoopMiddleware` graph, including
a genuine tool call in the middle of the turn -- also showed real usage on
every `AIMessageChunk`, correctly reflecting each individual model
response's own cumulative total (not the whole graph turn summed
together, which would double-count: two separate model responses within
one turn each report their *own* `input_tokens` as everything sent in
that call's prompt, so response 2's input already includes response 1's
output + the tool result -- summing both would count that overlap twice).

**The fix**, in `web/session.py`'s `_stream_turn`: a second accumulator
(`segment`, alongside the existing `accumulated` used for tool-call
argument recovery) that resets at every `ToolMessage` boundary -- so it
only ever sums the chunks of the single most recent model response, never
across a whole multi-response turn. Its final `usage_metadata.total_tokens`
is stored on `self._last_usage_metadata`, read once after both
`_stream_turn` and any `_resolve_pending_approvals` resume loop finish
(not sent from inside `_stream_turn` itself, since a resume can trigger
additional `_stream_turn` calls after an approval -- the WS `usage` event
should reflect the truly final state, not an intermediate one from before
the approval). Sent from both `_handle_user_message_locked` and
`resume_after_reconnect`, mirroring where `tasks_changed` was already
sent from both. Omitted entirely (no event) when a response never
populates `usage_metadata` -- still a real possibility for some
providers/configurations, not assumed universal just because Gemini
works.

**One provider-specific gap closed proactively, not just for Gemini:**
`langchain_openai.ChatOpenAI` (used both for the plain `openai:` prefix
and any custom OpenAI-compatible provider -- DeepSeek/Kimi/GLM/etc.) only
auto-enables `stream_usage` when `base_url` is unset, confirmed by reading
its own `__init__`. Every custom provider always sets `base_url`
(`runtime_lg/providers.py`'s `resolve_chat_model`), so without an explicit
`stream_usage=True` there, the usage bar would have silently never
populated for exactly the providers most likely to be cost-sensitive
about it. Added explicitly, not left to the implicit default (which
`openai:` itself happens to satisfy today, but that's relying on
unstated library behavior rather than a guarantee).

**A real regression, caught only by live-testing the fix end to end, not
by `pytest`:** live-verifying the usage-bar fix against a real running
`coscribe-web` (WebSocket client script, real Gemini) crashed
`send_state` outright --
`TypeError: Can't instantiate abstract class GeminiProvider with abstract
method chat_completions_create`. The dead-code audit's trim of
`providers/gemini_provider.py` had deleted `chat_completions_create`
entirely as confirmed-unreachable -- true for reachability, but it missed
that aisuite's own `Provider(ABC)` marks that method `@abstractmethod`,
so a class that doesn't implement it can no longer be *instantiated at
all* -- breaking `get_context_window` too, the one method that pass was
supposed to keep working, since both are only reachable through a
constructed instance. Grepped every existing test for this path afterward
and confirmed why `pytest` never caught it: every `test_llm_client.py`
test monkeypatches `_VENDORED_PROVIDERS` with a fake, so none of them ever
construct a real `GeminiProvider`. Fixed by adding back a minimal
`chat_completions_create` stub that raises `NotImplementedError` with a
message pointing at this section -- satisfies the ABC requirement without
resurrecting any of the deleted conversion logic, and documents plainly
that nothing should ever actually call it. Added
`test_gemini_provider_is_actually_instantiable_through_the_real_client`
(`tests/test_llm_client.py`) specifically to close this real gap in test
coverage -- confirmed it fails with the exact same `TypeError` when
`chat_completions_create` is reverted, so it genuinely catches this class
of bug now.

**Full verification.** Unit tests (`tests/test_web.py`): a
`FakeToolCallingChatModel` extended to forward `usage_metadata` through
its `_stream`, three new tests -- a `usage` event with the right total
when the model reports one, the "use only the final segment, don't sum
across a tool call" behavior specifically (two scripted responses with
different usage, asserting the second wins), and no event at all when the
model reports none. Live, not just unit-tested: started the real
installed `coscribe-web` against real Gemini, drove it through a raw
WebSocket client script (not `TestClient`) -- a tool-calling turn produced
a real `usage` event (`total_tokens: 4888`) in the correct wire position
(after `agent_message`, before `tasks_changed`); a second turn on the same
thread showed the total genuinely growing (4926 -> 4968), confirming
cumulative context tracking works across turns, not just within one.
`pytest -q` (240 passed, 1 skipped, run twice for stability), `ruff check
src tests scripts`, `mypy src` all clean throughout.

**Update: the custom-OpenAI-compatible-provider half of this gap is now
closed too, live-verified against two real services, not just read from
source.** This sandbox's own egress-proxy policy was the actual blocker
(the same kind of per-host allowlist that blocked `web_search`'s live
verification earlier), not a lack of a key -- confirmed directly:
`integrate.api.nvidia.com` and `open.bigmodel.cn` both returned `403` on
the proxy's own `CONNECT` tunnel (`curl -sv`), independent of whether a
valid key was ever sent. The user added both domains to this session's
environment's custom network allowlist, live, no restart needed (`curl -sv`
against `integrate.api.nvidia.com` went from `403` to `200 Connection
Established` immediately after saving) -- from that point on this was a
normal live-verification pass, not a workaround.

- **GLM (`open.bigmodel.cn`, `glm-4.5-air`)**: clean, complete success --
  ran through `resolve_chat_model`'s exact custom-provider path (real
  `ChatOpenAI(base_url=..., stream_usage=True)`), a real tool call, and
  the full `coscribe-web` WS protocol with two full turns on the same
  thread. Real per-segment `usage_metadata` on both direct-`astream` and
  WS runs (291 -> 317 tokens within one multi-response turn tested
  directly; `5700 -> 5759` across two separate WS turns on the same
  thread) -- correctly growing, never summed/double-counted, exactly the
  behavior the fix was designed for. GLM's own reasoning-model output
  (`reasoning_content`, similar in spirit to Gemini's "thinking" models)
  passed through with no special handling needed.
- **NVIDIA NIM (`integrate.api.nvidia.com`,
  `meta/llama-3.1-70b-instruct`)**: the `stream_usage=True` mechanism
  itself is equally confirmed working here (a direct `astream` test with
  a real tool call showed correct per-segment totals, 227 then 250) -- but
  a *separate*, real finding, unrelated to this fix: driving the same
  model through the full WS-based web app with the real coordinator's
  tool set showed unreliable behavior (looping on `list_files` more than
  once, then a stream that never completed) that a direct, non-WS test
  with the identical real tool set did *not* reproduce. Not root-caused
  further -- most likely this specific free-tier model's own weaker
  multi-step tool-use reliability, or an NVIDIA-side streaming quirk under
  a longer/more complex trace, not a bug in `_stream_turn` or the
  `stream_usage` fix (which is independently confirmed correct via the
  successful direct test). Noted honestly rather than swept aside, since
  it's a real thing a user picking `nvidia:meta/llama-3.1-70b-instruct`
  as their model could hit.

- **DeepSeek (`api.deepseek.com`, `deepseek-v4-flash`)**: clean success,
  a single deliberately-minimal test (a real, metered/paid key -- one
  direct `astream` run, not the fuller multi-turn WS pass GLM/NVIDIA got,
  to keep real cost down). Real tool call, real per-segment usage on both
  model responses (`461` then `482`, correctly growing, not summed) --
  also the first provider tested this pass whose usage payload includes
  `output_token_details: {"reasoning": ...}` and `input_token_details:
  {"cache_read": ...}`, both passed through untouched by `_stream_turn`
  (it only ever reads `.total_tokens`, so extra per-provider detail
  fields present or absent doesn't matter to it either way).
- **DeepSeek again, but through `langchain_anthropic.ChatAnthropic`
  itself** (not the OpenAI-compatible path above): DeepSeek documents a
  *second*, separate compatibility endpoint,
  `https://api.deepseek.com/anthropic`, speaking Anthropic's own Messages
  wire format rather than OpenAI's. Pointing the real `ChatAnthropic`
  class at it (`anthropic_api_url`/`base_url="https://api.deepseek.com/
  anthropic"`, a DeepSeek API key) -- the *exact* class `runtime_lg/
  providers.py`'s `anthropic:` prefix constructs, just with a different
  `base_url` than that prefix ever passes -- exercises genuinely different
  code than every other provider tested this pass: real Anthropic-shaped
  streaming events (`thinking` content blocks with a `signature`,
  `input_json_delta` tool-argument streaming, content-block `index`
  values), not OpenAI-shaped ones. One minimal test (same paid key, kept
  to one run): real tool call, real thinking-block output, real
  per-segment usage on both model responses (`461` then `494`, correctly
  growing). This is the closest live confirmation available in this
  session that the *mechanism* `anthropic:` depends on for the usage bar
  -- `langchain_anthropic`'s own streaming usage handling -- genuinely
  works, without an actual Anthropic API key. Not a full substitute for
  testing real `api.anthropic.com` (DeepSeek's compatibility layer isn't
  guaranteed byte-identical to Anthropic's own wire format in every edge
  case), but meaningfully stronger evidence than source-reading alone.
  **Precisely how far that substitution goes is now documented, not
  guessed:** DeepSeek's own published compatibility matrix
  (api-docs.deepseek.com's Anthropic API guide, shared by the user) lists
  `stream`/`system`/`temperature`/`top_p`/`stop_sequences`/`thinking`/
  `tools`/`tool_choice`/text and `tool_use`/`tool_result` content blocks
  as fully or mostly supported -- exactly the surface this test exercised
  (a plain text turn + one tool call), so nothing tested above was
  accidentally exercising an unsupported code path that happened not to
  error. Two real, documented gaps in the compatibility layer itself,
  worth knowing before leaning on it for anything beyond this one usage-
  bar question: **image/document/search_result content blocks are not
  supported at all** (so real multimodal image-attachment behavior, which
  `web/session.py`'s own `images` parameter sends as Anthropic/OpenAI-
  shaped `image_url`/image content parts, can't be verified this way, and
  a real `anthropic:` user attaching an image against a real Anthropic
  key would exercise code this test never touched), and **`cache_control`
  is ignored everywhere it appears** (prompt-caching behavior -- already
  moot for this project specifically, since `providers/
  anthropic_provider.py`'s own cache_control logic was deleted as dead
  code in the runtime audit above, but relevant if `langchain_anthropic`
  ever adds its own caching behavior this project starts depending on).
  `anthropic-version`/`anthropic-beta` headers are also silently ignored
  by DeepSeek's layer, so any future Anthropic API version/beta-flag-gated
  behavior wouldn't be caught by testing against this endpoint either.

**Still not verified live**: real OpenAI (`openai:`, the default-base-url
path specifically, not the custom-provider one already covered by GLM/
NVIDIA/DeepSeek above) -- no key was available for it even after this
pass, and unlike Anthropic, DeepSeek doesn't offer a compatibility
endpoint that would exercise `langchain_openai`'s *default-base-url*
construction branch specifically (the custom-provider tests above all
necessarily set `base_url`, which is exactly the branch that needed the
explicit `stream_usage=True` workaround -- confirmed live correct for
that branch, but still not for the implicit-auto-enable branch real
`openai:` actually takes). Worth closing if a real OpenAI key ever
becomes available, but a low-priority gap: unlike the Anthropic path
(now cross-verified via a real, differently-shaped wire protocol), this
is the *same* `ChatOpenAI` class and the *same* general streaming
mechanism already proven correct three times over, just missing
confirmation of one conditional inside its `__init__`.

## `max_turns` and `auto_compact_threshold` -- closing the "One real gap" left by the audit above

The audit + delete section above flagged one honest, unfixed gap:
`Settings.max_turns`/`Settings.auto_compact_threshold` were still real,
documented, UI-exposed settings, but nothing in `runtime_lg` read either
one -- both used to drive the old runtime's own `Runner` loop directly (a
plain turn-count cap; `maybe_auto_compact`, called after every turn) and
never got a `runtime_lg` equivalent. This closes both.

**Design, researched before writing any code.** LangGraph's own
`recursion_limit` config knob was the first candidate for `max_turns` --
confirmed via reading `langgraph.pregel._loop`/`main.py` directly that it's
a real graph-step cap (default `DEFAULT_RECURSION_LIMIT = 25`, from
`langchain_core.runnables.config`), and empirically derived the exact
relationship between graph steps and actual model calls with a scripted
`CountingModel` test sweeping limit values `[3, 5, 10, 25, 40, 41, 42,
50]`: `model_calls_before_GraphRecursionError = (recursion_limit + 1) //
2`, i.e. `recursion_limit = 2*max_turns + 1` guarantees N complete
tool-calling rounds plus one final non-tool response. Workable, but two
real downsides: it raises `GraphRecursionError` instead of ending
gracefully, and the "+1" arithmetic is a fragile thing to keep correct by
hand. Browsing `langchain.agents.middleware`'s actual exports turned up a
better-fit, purpose-built alternative instead:
**`ModelCallLimitMiddleware`** -- counts real model calls (via graph state
`run_model_call_count`/`thread_model_call_count`, incremented in
`after_model`, checked in `before_model`), not abstract graph steps, and
`exit_behavior="end"` ends the run by injecting a limit-exceeded
`AIMessage` and jumping straight to `END` instead of raising. Chosen over
`recursion_limit` for both reasons -- no formula to keep in sync with
`create_agent`'s own internals, and a clean ending instead of an
exception.

For `auto_compact_threshold`, `langchain.agents.middleware
.SummarizationMiddleware` is the obvious fit -- it supports
`trigger=("fraction", X)`, which sounds like exactly what
`auto_compact_threshold` (a *fraction* of the context window) wants
directly. **It isn't usable that way here, and this was checked
empirically, not assumed.** `("fraction", X)` (and `keep=("fraction", X)`,
the retention-side equivalent) requires
`model.profile["max_input_tokens"]` -- LangChain's own model-profile
registry -- and `SummarizationMiddleware.__init__` raises immediately if a
fraction-based trigger/keep is requested without it. Checked directly
against the actual chat model classes this project's `resolve_chat_model`
constructs:

```python
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite", api_key="fake").profile  # {}
ChatOpenAI(model="x", base_url="https://api.deepseek.com/v1", api_key="fake").profile  # {}
ChatAnthropic(model_name="claude-sonnet-4-5", api_key="fake", timeout=None, stop=None).profile
# {'name': 'Claude Sonnet 4.5 (latest)', ..., 'max_input_tokens': 1000000, ...}
```

`ChatGoogleGenerativeAI` and any custom-`base_url` `ChatOpenAI` (i.e.
Gemini and DeepSeek/GLM/NVIDIA-style custom providers -- this project's
actual primary users, per the live verification earlier in this file) both
have an **empty** `.profile`; only real `ChatAnthropic` has real profile
data populated. Using `("fraction", X)` directly would have made
`build_langgraph_agent` raise at agent-*construction* time for the most
common models this project runs against -- a considerably worse failure
mode than the gap it would have closed. **Fix**: use
`trigger=("tokens", N)` instead, with `N` computed from this project's own
already-working `LLMClient.get_context_window(model_string)` (a live,
per-provider best-effort lookup that already existed purely for the usage
bar's UI indicator) multiplied by `auto_compact_threshold`, rather than
depending on LangChain's incomplete model-profile registry.
`keep` (the retention side) is left at its own default, `("messages",
20)` -- also not `"fraction"`-based, so no profile dependency there
either.

**What actually got wired, and where (deliberately narrow).**
`runtime_lg/agent.py`'s `build_langgraph_agent` gained two new optional
keyword parameters, `max_turns: int | None` and
`auto_compact_tokens: int | None`, both `None` by default (no behavior
change for any caller that doesn't pass them) -- appending
`ModelCallLimitMiddleware(run_limit=max_turns, exit_behavior="end")` and/or
`SummarizationMiddleware(model=model, trigger=("tokens",
auto_compact_tokens))` to the middleware list when set. Only
`web/session.py`'s `ChatSessionLG._build_lg_agent` (the *one* place that
builds the top-level, user-facing compiled graph -- called from
`__init__`, `switch_model`, and `select_persona`) actually passes them,
computing `auto_compact_tokens = max(1, int(context_window_client
.get_context_window(model_string) * settings.auto_compact_threshold))`.
`runtime_lg/subagents.py`'s `build_spawn_agent_tool`/
`build_review_work_tool` -- which also call `build_langgraph_agent`, for
`spawn_agent`'s child graph and `review_work`'s reviewer graph -- were
**deliberately left untouched**, leaving both parameters at their `None`
default: checking the old, since-deleted `tools/subagents.py` (via `git
show` on the commit before it was deleted) confirmed `spawn_agent` always
used its *own*, independent turn cap (a `max_rounds`/`max_turns` function
parameter, default 6, unrelated to `Settings.max_turns`) and `review_work`
always used a hardcoded `max_turns=1` -- neither ever ran auto-compact at
all. Wiring `Settings.max_turns`/`auto_compact_threshold` into sub-agents
too would have been scope creep beyond what the audit actually flagged as
missing, not a restoration of prior behavior.

**A real bug this surfaced, not just documented -- found before it ever
shipped, by testing against a real live model, not just fakes.** Printing
`agent.astream(..., stream_mode=["messages"])`'s raw output for a
`max_turns`-capped run showed `ModelCallLimitMiddleware`'s injected
"limit exceeded" message arriving as one complete `AIMessage`, **not** an
`AIMessageChunk` -- unsurprising in hindsight (it's added directly by a
`before_model` hook's return dict, never actually streamed from a model
call), but `web/session.py`'s `_stream_turn` only ever handled
`isinstance(message, AIMessageChunk)`/`isinstance(message, ToolMessage)`.
A plain `AIMessage` matched neither branch (`AIMessageChunk` is a subclass
of `AIMessage`, not the reverse), so the limit-exceeded text was silently
dropped and the turn would have ended with an empty "[no reply -- the
model ended its turn without responding; try rephrasing]" instead of
telling the user *why* it stopped -- correct behavior (the run does stop)
wrapped around a genuinely confusing user-facing message. **Fixed** by
adding an `elif isinstance(message, AIMessage):` branch to `_stream_turn`
that forwards its text the same way a real reply's final chunk would be.

**Verification.**
- `pytest -q`: 245 passed, 1 skipped (up from 236) -- nine new tests: four
  in a new `tests/test_runtime_lg_agent.py` exercising
  `build_langgraph_agent` directly (both parameters' `None`-means-
  unchanged default behavior, `max_turns` actually stopping a runaway
  tool-calling loop after exactly the configured number of model calls,
  and `auto_compact_tokens` genuinely collapsing a 22-message thread once
  past `SummarizationMiddleware`'s own 20-message `keep` floor -- checked
  via the real `RemoveMessage`/summary-message/preserved-tail shape, not
  just "didn't crash"), plus a `max_turns` regression test in
  `tests/test_web.py` covering the `_stream_turn` `AIMessage` fix above
  end-to-end over a real WebSocket connection. `ruff check src tests
  scripts` and `mypy src` both clean.
- **Live, against real Gemini** (`gemini-3.1-flash-lite`, a real key
  available this pass) -- not just the fakes above:
  - `max_turns=3` against an instructed-to-loop-forever tool: the model
    was called exactly 3 times (confirmed by inspecting the final
    checkpointed state's `AIMessage` count), and the run ended with
    `"Model call limits exceeded: run limit (3/3)"` as its final message,
    not an exception.
  - `auto_compact_tokens=1` (force-triggering on every check) driven
    through 11 real user/assistant round trips (22 messages, past the
    20-message `keep` floor): the checkpointed history came back with a
    real, Gemini-generated summary message (`additional_kwargs["lc_source"]
    == "summarization"`, structured under the middleware's own `## SESSION
    INTENT` / `## SUMMARY` / `## ARTIFACTS` / `## NEXT STEPS` prompt
    headings) replacing the earliest turn, with the rest of the
    conversation preserved intact -- real proof the summarization call
    itself (a second, nested real model call, not scripted) works
    end-to-end, not just that the middleware object gets constructed.
  - A full `coscribe-web` server run against real Gemini, with
    **default, unforced settings** (`max_turns=20`,
    `auto_compact_threshold=0.8`, i.e. the values every existing user
    already has configured) confirmed a completely ordinary short
    conversation (list files, report the current date) still works
    end-to-end with both middlewares installed but not triggered --
    guards against a regression where merely *having*
    `SummarizationMiddleware`/`ModelCallLimitMiddleware` in the graph
    changes normal, non-edge-case behavior.

`config.py`'s `max_turns`/`auto_compact_threshold` docstrings have been
updated to point here instead of describing the gap.

## Phase 6: `office-agent-desktop` -- a Tauri shell around the existing web server, not a second UI

The user's stated goal: a "click and open" desktop experience like Claude
Code's desktop app, with an explicit constraint agreed on beforehand --
web and desktop stay the *same* frontend, never two UIs to maintain.
Planned properly first (plan-mode, user-approved) rather than guessed,
because this is a genuinely new toolchain (Rust/Tauri/PyInstaller) this
project had never touched.

**Research, not guessing: a real, complete reference implementation was
already vendored in this checkout.** `andrewyng/openworker` (Tauri shell +
Python agent-server sidecar, MIT, shipped July 2026) is the exact same
shape as this plan -- and a full, unredacted copy of it (under the name
`coworker`, apparently predating the public `openworker` rename) already
existed at `/home/user/project/aisuite-main/aisuite-main/platform/` in
this sandbox. Read directly, not summarized: `tauri.conf.json`,
`src-tauri/src/lib.rs` (762 lines), `packaging/coworker-server.spec`
(PyInstaller), `packaging/server_entry.py`, `coworker/server/run.py`.
Also checked Jan.ai (Tauri, not Electron -- confirms the framework
choice over the heavier alternative) and a Tauri v2 GitHub issue on
`WebviewUrl::External`'s IPC limitation (see below) via web search.

**One deliberate, real divergence from the reference, not a shortcut.**
openworker's frontend is a separate Vite/React SPA bundled *into* Tauri
(`frontendDist`), so its shell has to inject `window.__COWORKER_HTTP__`/
`__WS__` globals before that SPA loads, and the SPA talks cross-origin to
the sidecar. coscribe's frontend has no build step at all -- `web/
app.py`'s `GET /` already serves `web/static/index.html` directly, and
`app.js` already builds every URL (the WebSocket included) from
`location.host`/root-relative paths, confirmed by reading it, never a
hardcoded origin. So `office-agent-desktop`'s one window just navigates
directly at the sidecar's real origin (`WebviewUrl::External(format!(
"http://127.0.0.1:{port}"))`) -- no bundled frontend, no injected
globals, no second copy of the UI. `dist/index.html` is an unused
placeholder Tauri's config schema requires to exist but this app's
window never loads.

**The real tradeoff of that choice, checked, not glossed over**: a
`WebviewUrl::External` page doesn't get Tauri's IPC bridge by default (a
Tauri v2 GitHub issue: IPC requests from an external-origin webview fail
an Origin-header check unless the origin is explicitly granted through a
remote `Capability`). This version needs zero JS-invoked native commands
(sidecar spawn/window creation/kill all happen in `lib.rs`'s own
`setup()`/`run()`, nothing the page calls into), so this is real but
currently inconsequential -- a future native command (file picker, tray)
would need a `remote` capability grant, not a redesign.

**A real, previously-undiscovered requirement this research surfaced**:
`Settings.workspace_root`/`state_dir`/`skills_dir`/`personas_dir`/
`memory_path` all default to paths relative to the process's cwd --
correct for a terminal-launched server, meaningless for a double-clicked
app with no natural project-directory cwd. Fixed two ways: `lib.rs`'s
`app_data_dir()` (mirrors `coscribe.cli._app_data_dir()` on the
Python side exactly -- `~/Library/Application Support/coscribe` on
macOS, `%APPDATA%\coscribe` on Windows) sets each of those as an
explicit `COSCRIBE_*` env var when spawning the sidecar; and
`cli.py`'s new `_dotenv_path()` (used by both `_load_settings()` and
`web/app.py`'s `create_app_lg`) falls back to that same app-data
directory for `.env` whenever no `./.env` already exists in cwd, so the
Settings panel's "paste your API key" flow (writes to `.env` via
`set_key`) works identically in both modes -- verified directly: a
Python script confirmed both branches (existing cwd `.env` kept; no
`.env` falls back to `~/.config/coscribe/.env` on this Linux
sandbox) resolve correctly.

**A new parent-death watchdog, adapted (not copied) from the
reference's `_exit_when_orphaned`**: `web/app.py`'s `main()` now watches
`COSCRIBE_PARENT_PID` (POSIX: `os.kill(pid, 0)` polling; Windows: a
blocking `WaitForSingleObject` on a process handle, since there's no
POSIX-style re-parenting signal there at all) when
`COSCRIBE_EXIT_WITH_PARENT=1` is set, self-exiting if the shell dies
abruptly -- a no-op for the ordinary CLI/browser case. **Live-verified,
not just read**: spawned a real sidecar with a fake "parent" process,
killed the fake parent, confirmed the sidecar self-exited within ~2s
(one watchdog poll cycle). The reference's own onefile-specific
"grandchild PID" wrinkle (PyInstaller's bootloader sitting between the
GUI and the real Python process, breaking plain `getppid()`) doesn't
apply here since this project uses onedir (see below), one part of the
reference genuinely simpler to port than to copy verbatim.

**PyInstaller `--onedir`, never `--onefile` -- directly justified by
this same file's own prior startup-latency work.** The reference
documents a real, measured regression: onefile self-extracts its whole
archive to a temp dir on *every* launch (6-7s, measured, vs. ~0.5s for
the actual Python import) -- which would erase the effort earlier in
this same section-set brought `coscribe-web`'s own cold-import time
down to ~1.1-1.4s. New `coscribe/packaging/coscribe-server.spec`
(modeled on the reference's, adapted for this project's own dependency
tree -- notably `langchain`/`langgraph`/`langsmith` and friends, which
the reference never needed since `coworker` doesn't use LangChain at
all) plus a thin `packaging/server_entry.py` PyInstaller needs a real
file to analyze (the `coscribe-web` console_script is generated
metadata, not a file it can point at).

**Built and iteratively fixed for real in this sandbox, not assumed
correct from reading the reference alone.** `pip install pyinstaller`
and a real `pyinstaller coscribe-server.spec` build surfaced one real
gap the reference's own hiddenimports list couldn't have caught (it's
specific to this project): `web/static/*` (the frontend HTML/JS/CSS)
isn't Python code, so `collect_submodules("coscribe")` never touched
it -- the first frozen binary crashed immediately with a Starlette
`RuntimeError: Directory ... does not exist` from the `StaticFiles`
mount. Fixed with an explicit `collect_data_files("coscribe")`.
After that, the build succeeded and **every one of the four lazy-
imported document tools was driven through the real frozen binary** over
a real WebSocket connection, approvals answered for real (`write_docx`/
`write_pdf`/`write_xlsx`/`write_pptx` are all `requires_approval=True`) --
all four produced correct, real files (confirmed via `file`: genuine
Word/PDF/Excel/PowerPoint documents, not corrupted output), proving the
spec's `collect_all` entries for `mammoth`/`pdfplumber`/`docx`/
`markdownify`/`reportlab`/`openpyxl`/`pptx` (this project's own recent
lazy-import work, see the startup-latency sections above) actually cover
what PyInstaller's static analysis misses on function-local imports --
exactly the failure mode the reference's own spec comment warns about
for its equivalent (`websockets`/`pypdf`/`pypdfium2`).

**A genuinely unexpected, much deeper live verification than this phase's
own plan thought possible.** The plan (written before attempting any of
this) assumed the sandbox couldn't build or run Tauri at all -- `pkg-
config --exists webkit2gtk-4.1`/`gtk+-3.0` both failed, no `$DISPLAY`.
That assumption held for `cargo check` exactly as written -- but this
sandbox has root, and `apt-get install libwebkit2gtk-4.1-dev libgtk-3-dev
libayatana-appindicator3-dev librsvg2-dev` succeeded, after which `cargo
check`, `cargo clippy --no-deps`, and a full `cargo build` all passed
clean. Going one step further: `Xvfb :99` (a virtual framebuffer,
already installed) plus `imagemagick`/`xdotool` (installed) let the
actual compiled debug binary run for real -- it picked a free port,
resolved the sidecar via the *production* code path (the
`resources`-bundled `binaries/sidecar/` folder Tauri's own build script
stages next to the debug binary, not the dev-fallback venv path, since a
real PyInstaller build had already been staged there via a new
`office-agent-desktop/scripts/stage-sidecar.sh`), waited for it to start
listening, and opened a real native window pointed at it. A screenshot
(`import -window root`) showed the actual coscribe chat UI rendering
correctly inside the native window (model picker showing
`gemini-3.1-flash-lite`, thread id, composer). Went one step further
still: `xdotool` typed a real message ("Say hello in exactly three
words.") into the composer and pressed Enter -- a second screenshot
confirmed a real round trip: the message sent over a real WebSocket,
a real Gemini API call, and the real reply ("Hello, my friend.")
rendered back inside the native window, usage bar updating (4.8k/1.0M
tokens) exactly like the browser UI does. This is real, live, visual
proof of the entire mechanism working end-to-end -- not a Linux-specific
result either: the sidecar-spawn/port-pick/health-wait/window/kill-on-
quit logic is the same Rust code that runs on macOS/Windows, only the
GTK/WebKit rendering backend differs per-platform.

**What this does *not* prove, called out explicitly rather than
overclaimed**: a real macOS/Windows build. `cargo tauri build`'s
DMG/NSIS bundling, code-signing/notarization, `webviewInstallMode`, and
the Windows-only `CREATE_NO_WINDOW` sidecar-spawn flag are all platform-
specific code paths this Linux+Xvfb run never exercised -- genuinely
still needs a first real pass on the user's own Mac/Windows machine, and
`office-agent-desktop/README.md` says so plainly rather than claiming a
false "done."

**Explicitly out of scope for this phase, by design, not oversight**:
system tray/keep-running-after-close (no scheduled-automation feature to
justify it, unlike the reference's own scheduler), native folder picker,
autostart-at-login, auto-updater, code signing, Linux packaging. Closing
the window quits the app and kills the sidecar on every platform,
including macOS's own "stay in the Dock" convention -- explicitly
overridden (`app_handle.exit(0)` on `WindowEvent::CloseRequested`), a
deliberate default the plan flagged as worth pushback on rather than a
silent assumption.

**One addition beyond the reference, required by this project's simpler
architecture rather than optional.** Because this window navigates
directly at the sidecar's own origin (point above) rather than an
always-available bundled frontend, it can't safely open before the
sidecar is actually listening -- `lib.rs`'s `wait_for_port()` blocks
`setup()` with a plain TCP-connect poll (no HTTP client dependency
needed) before building the window, so the user never sees a bare
"connection refused" page during the ~1-1.5s the Python process takes to
start. The reference didn't need this: its window always loads its own
bundled frontend instantly, and only in-page API calls would need to
retry against a not-yet-ready sidecar.

**Files.** New: `coscribe/packaging/coscribe-server.spec`,
`coscribe/packaging/server_entry.py`; `office-agent-desktop/`
(`package.json`, `src-tauri/{Cargo.toml,tauri.conf.json,build.rs,src/
{main,lib}.rs,capabilities/default.json,icons/}`, `scripts/
stage-sidecar.sh`, its own `README.md`). Changed: `coscribe/web/
app.py` (`_exit_when_orphaned`/`_watch_parent_windows`), `coscribe/
cli.py` (`_app_data_dir`/`_dotenv_path`, wired into `_load_settings`
and `create_app_lg`). `../ARCHITECTURE.md` and this project's own
`README.md` (new "Desktop app" section) updated to describe the shell as
a thin wrapper, not a second UI.

**Verification.** `pytest -q`: 245 passed, 1 skipped, unchanged (no new
Python behavior for browser-mode/CLI users, only new opt-in desktop-
mode code paths). `ruff check`/`mypy` both clean. `cargo check`/`cargo
clippy --no-deps`/`cargo build` all clean. Every item above was actually
run, not assumed: the PyInstaller binary smoke-tested directly (all four
lazy-imported document tools through a real WS session), the parent-
death watchdog live-tested with a real kill, and the full Tauri app
live-tested end-to-end under Xvfb with a real screenshot of a real
Gemini reply rendered in the native window.

### Same-day follow-up: a portable, no-installer distribution mode, at the user's explicit request

The user's stated goal, verbatim: "click one exe, get in fast, no
installation" -- and to look beyond just openworker at other open-source
desktop AI agents' frontends first. That survey: Cherry Studio
(Electron + React), AnythingLLM (Electron + Vite/React), Jan.ai (Tauri +
React/Vite, its `web-app/` is a real bundled SPA) -- every one of them
uses a proper frontend framework bundled into the shell, unlike this
project's plain-HTML/JS-with-no-build-step frontend, which is exactly
what enabled the simpler `WebviewUrl::External` design above (see the
Phase 6 section's own point 1). Nothing in that survey suggested a
different frontend framework was needed here.

**On "no installer": checked directly rather than trusting an uncertain
web search.** Tauri has no first-party "portable" bundle target
(confirmed via `tauri-apps/tauri` discussion #3048 -- a long-standing,
still-open community request, not something this project can configure
its way into). Community discussion around it was inconclusive on one
specific point that matters here: does a raw `target/release/<exe>`,
run directly without going through the NSIS/WiX installer step, still
have access to `bundle.resources` (this project's sidecar)? One
maintainer comment suggested resources/sidecar "won't work" that way --
**checked empirically instead of taking that at face value**: ran a
plain `cargo build --release` (not `cargo tauri build`) and inspected
`target/release/` directly. `tauri-build`'s own build script *does*
stage `bundle.resources` next to the compiled binary for any cargo
profile, not only through the full installer pipeline --
`target/release/office-agent-desktop` sat right next to a real
`target/release/sidecar/` folder (the same PyInstaller onedir output
`stage-sidecar.sh` staged into `src-tauri/binaries/sidecar/`).
**Live-verified, not just inspected**: ran that exact release binary
under Xvfb the same way as the debug build above -- it found the
sidecar via the sibling-folder path, spawned it, and a second screenshot
confirmed the same real UI rendering correctly. So: zip
`target/release/<exe>` together with its sibling `sidecar/` folder as
one distributable -- a user extracts and double-clicks the exe, no
installer, no admin prompt, no registry entries.

On macOS, the native equivalent of "portable exe" is a `.app` bundle by
itself (no DMG wrapper needed to double-click and run it) --
`tauri.conf.json`'s `bundle.targets` gained `"app"` alongside the
existing `"dmg"`/`"nsis"` so `npm run tauri build -- --bundles app`
produces that directly. `office-agent-desktop/README.md` was restructured
to lead with this portable path as the primary distribution mode, with
the traditional DMG/NSIS installer kept documented as an optional
alternative rather than the default.

Nothing on the Python/`runtime_lg` side changed for this follow-up --
`tauri.conf.json`'s `bundle.targets` and `office-agent-desktop/README.md`
were the only files touched. `cargo check` re-verified clean after the
config change.

## Phase 2: `coscribe-lg`, a real (experimental) CLI

**Update: no longer accurate -- see "`coscribe-lg` reaches real
feature parity, reusing `ChatSessionLG`" and "The cutover: `cli.py`
deleted, `cli_lg.py` promoted in its place" below.** The user reversed the
"CLI deprioritized, slated for removal" decision below once `runtime_lg`
had proven itself thoroughly enough through this session's own live
bug-hunting to commit to it as the real engine: instead of removing the
CLI, `cli_lg.py` was rewritten to reach genuine feature parity with (and a
few capabilities beyond) the original `coscribe` `chat` command, then
promoted to *replace* it outright -- `coscribe` now runs on
`runtime_lg`; the original hand-rolled-runtime `cli.py` is gone. Kept
below for history, not silently rewritten.

`../cli_lg.py` + a new `coscribe-lg` console script
(`pyproject.toml`) -- an actually-usable REPL on `runtime_lg`, not just a
verification script. Reuses `build_coordinator_agent`'s tool list and
instructions unmodified, adds MCP tools via the existing
`connect_mcp_tools`, and persists conversation state (and pending
approvals) via `langgraph.checkpoint.sqlite.SqliteSaver` instead of
Phase 1's `InMemorySaver` -- one `.sqlite` file per workspace under
`settings.state_dir`, the direct analog of `FileStateStore`.

Deliberately narrower than `coscribe`'s real `chat` command: `/plan`,
`/accept-edits`, and `/compact` are not supported (each prints a message
pointing back at the real `coscribe` command) -- and, unlike
`coscribe-web-lg` (see the section above), stay that way: CLI feature
parity was explicitly deprioritized by the user once the web app became
the priority, with the CLI itself slated for eventual removal, so this
gap was never revisited here. `spawn_agent`/`review_work` delegation *is*
supported (see this file's dedicated section on the nested-interrupt
bridge) -- it was wired in before the deprioritization decision.

**Live-verified, real bugs found and fixed along the way:**
- A real bug in the first draft: `stream_mode=["messages"]` yields *every*
  message in the graph, including `ToolMessage` chunks carrying a tool's
  raw JSON return value flowing back into context -- the first version
  printed that raw JSON inline with the model's own reply. Fixed by
  filtering to `isinstance(message, AIMessageChunk)` before extracting
  text.
- Full multi-turn session against live Gemini
  (`gemini:gemini-3.1-flash-lite`): a read-only tool call (no interrupt),
  then a `write_file` call (interrupt fires, approval prompt printed in
  the same wording as the real CLI's `_confirm_tool_call`, approved,
  file actually written with correct content), verified via
  `typer.testing.CliRunner` end to end.
- **Closes Phase 1's open "does approval survive a real process restart"
  caveat, for real this time**: ran two separate `CliRunner.invoke()`
  calls (simulating two separate process launches) against the same
  `--thread` and the same on-disk sqlite checkpoint file. Process 1 sent a
  `write_file` request and was killed (EOF) while the approval prompt was
  still waiting -- confirmed the file was *not* written. Process 2 started
  fresh, detected the still-pending approval at startup (a new check added
  specifically for this: if `agent.get_state(config).next` is already
  truthy when `coscribe-lg` starts, resolve it before prompting for
  new input), re-displayed the identical approval prompt, was approved,
  and the file was written correctly. This is the actual mechanism (not
  just the theory) that Phase 1 could only partially verify.

**Not yet tested**: the reject path specifically *through the CLI* (the
underlying mechanism was already proven correct in Phase 1's
`verify_runtime_lg.py`; only the CLI's thin translation layer around it is
new and untested end-to-end). MCP tools weren't exercised in this sandbox
(no reachable MCP server here), though the wiring is mechanical and
identical to how the real `coscribe` command already does it.

---

# Phase 1 findings

## What's here

- `agent.py` -- `build_langgraph_agent(model, tools, instructions)`: wraps
  `langchain.agents.create_agent`, deriving its `HumanInTheLoopMiddleware`
  approval list from the **existing, unmodified**
  `ToolMetadata`/`get_tool_metadata` in `runtime/types.py` -- no new
  tagging system invented.
- `providers.py` -- `resolve_chat_model("provider:model", custom_providers)`:
  same string convention as `runtime/llm_client.py`'s `LLMClient`, resolves
  to a real LangChain chat model (`langchain-anthropic`/
  `langchain-google-genai`/`langchain-openai`) instead of hand-rolled
  message conversion.
- `../../../scripts/verify_runtime_lg.py` -- standalone script (not
  pytest) that builds a real agent from **unmodified**
  `tools.build_file_tools`/`build_memory_tools` and, per provider with a
  live key, checks: streaming produces chunks, a read-only tool call never
  interrupts, an approval-gated tool (`write_file`) does interrupt, the
  approve path actually executes the write, and -- the exact failure mode
  that broke the old Gemini adapter three times -- a **second turn on the
  same thread**, replaying the first turn's function-call history, still
  interrupts correctly and a **rejected** write does *not* execute.

## Environment note (a real finding, not a workaround)

`aisuite[anthropic]==0.1.14` pins `anthropic<0.31.0,>=0.30.1`; every
`langchain-anthropic` release requires `anthropic>=0.64.0`. These ranges
are disjoint -- confirmed via pip's resolver, not assumed. **The old and
new runtimes cannot be installed in the same venv today.** `runtime_lg/`
is developed and verified against a separate venv (this project's own
`.venv` does not have `langgraph`/`langchain-*` installed, and `mypy src`
excludes this directory for exactly that reason -- see `pyproject.toml`).
Any real cutover has to fully replace aisuite's Anthropic usage, not run
the two side by side within one environment.

## Live verification results (Phase 1)

**Gemini (`gemini-3.1-flash-lite`), live API key, run via
`scripts/verify_runtime_lg.py`:** all 6 checks passed --
streaming, no-interrupt-on-read, interrupt-on-write, approve-executes,
multi-turn-history-replay-still-interrupts, reject-does-not-execute. Zero
custom signature-handling code; the multi-turn replay case is the exact
scenario that broke the hand-rolled adapter three times.

**Anthropic:** *not verified.* No working `ANTHROPIC_API_KEY` was
available in the sandbox this phase was built in (checked: absent from
both the ambient environment and `coscribe/.env`, which only has a
blank template). This is an explicit, unclosed Phase 1 exit criterion --
`scripts/verify_runtime_lg.py` already has an Anthropic scenario wired up
and will run it automatically the moment a working key is present in the
environment (`ANTHROPIC_API_KEY=... python scripts/verify_runtime_lg.py`).

**DeepSeek / GLM:** *not verified, and not verifiable from this sandbox
specifically* -- both `api.deepseek.com` and `open.bigmodel.cn` are
blocked at this session's own egress-proxy policy level (`curl
$HTTPS_PROXY/__agentproxy/status` shows `connect_rejected` / 403 for both
hosts; per `/root/.ccr/README.md`, org policy denials are not to be routed
around). This is unrelated to the earlier Groq finding (that was Groq's
own geo-blocking; this is Anthropic's own sandbox egress allowlist not
including these two hosts) and unrelated to key validity. Real keys for
both are already wired into `scripts/verify_runtime_lg.py`
(`DEEPSEEK_API_KEY`/`GLM_API_KEY`, models `deepseek-v4-pro`/`glm-4.6v`) --
running the script from a machine without this sandbox's egress
restriction (i.e. the user's own machine) is the only way to close this
one, same as Anthropic above.

## Verdict: `spawn_agent`/`review_work` feasibility

Feasible without new mechanism, using the same `build_langgraph_agent`
building block: a `spawn_agent` tool function would resolve a
comma-separated subset of the parent's already-built tool list, call
`build_langgraph_agent(model, selected_tools, instructions)` to construct a
fresh sub-agent, and `.invoke(...)` it synchronously with a fresh
`thread_id` -- directly mirroring `tools/subagents.py`'s current
`Agent`+`Runner(...).run_sync(...)` pattern, just swapping which runtime
builds/drives it.

**Update: no longer unverified -- see "spawn_agent's nested-interrupt
bridge" at the top of this file for the live-verified answer** (built and
tested after Phase 3, not in Phase 1 itself). Originally flagged here as
genuinely unverified: whether an `interrupt()` raised *inside* a nested
sub-agent's tool call (i.e., while the parent graph's own tool node is
synchronously blocked inside `sub_agent.invoke(...)`) correctly
pauses/resumes through both graph layers, or whether nested interrupts hit
an unsupported case in LangGraph's checkpointer model. This wasn't built or
tested in Phase 1 -- it's a concrete, scoped task for Phase 2 (and today's
behavior already has the same "nested approval blocks inside a blocked
call" shape, so it's not a new problem being introduced, just one that
needs the same behavior
re-verified under the new mechanism).

## Verdict: does this actually fix "approval doesn't survive reconnect/restart"?

**Structurally yes, with one real caveat.** Today's `_pending_approvals`
(`web/session.py`) is a `dict[str, queue.Queue[bool]]` living in one
`ChatSession` Python object tied to one open WebSocket and one blocked OS
thread -- if the process restarts or the browser reconnects to a fresh
`ChatSession`, that state is simply gone. LangGraph's interrupt state is
retrieved via `agent.get_state(config)`, keyed only by `thread_id` through
the checkpointer -- proven in this phase's verification script, which
calls `get_state` across *separate* `agent.invoke()` calls (not one
continuously-blocked call) and correctly sees the pending interrupt each
time. That's the core mechanism that would let approval survive a
reconnect.

The caveat: Phase 1 used `InMemorySaver()` (checkpointer state lives in
the Python process's memory, same durability as today's `FileStateStore`
is better than -- but a real "survives an actual process restart" claim
needs a **persistent** checkpointer (e.g. `SqliteSaver`, mirroring
`FileStateStore`'s on-disk model) swapped in and *actually tested* by
killing the process mid-interrupt and resuming in a new one. Not done in
Phase 1 -- the mechanism that would make it possible is proven, the full
end-to-end claim across a real process restart is not yet.

## Not done in Phase 1 (by design, per the plan's stated scope)

- `cli.py`, `web/`, `coordinator.py` untouched.
- Nothing in `runtime/`/`providers/gemini_provider.py` deleted.
- MCP tool integration (`tools/mcp.py`) not exercised here.
- Persistent checkpointer (only `InMemorySaver` used).

## `switch_model` used a stale custom-providers snapshot -- a real bug, live-reported ("目前不能使用deepseek吗？")

A user tried switching a thread's model to a newly-added custom provider
(DeepSeek) and got `resolve_chat_model`'s own "Unsupported provider
'deepseek'" -- despite having just added it via the Providers tab with a
real, working API key.

Root cause, found by reading the actual call path rather than assuming
`resolve_chat_model` itself was broken: `web/app.py`'s `_get_session`
only calls `load_custom_providers` once, the first time a given
`thread_id`'s `ChatSessionLG` is created (`if thread_id not in sessions:`),
and caches the result on `self._custom_providers` for that session
object's entire lifetime -- documented there as a deliberate
simplification ("the cheapest way to get 'a provider added after startup
is usable in the *next new thread*'"). Adding a provider in an
*already-open* thread and then immediately trying to switch to it in
that same thread hits exactly the gap that comment calls out: the
session object genuinely never re-reads `providers.json`, so
`resolve_chat_model(model, self._custom_providers)` never sees it and
falls through to the "Unsupported provider" branch -- which reads as
"this provider type isn't supported at all," not "this session just
hasn't refreshed its own config yet," a confusing failure mode from the
user's side.

Fixed directly rather than just documented: `switch_model`
(`web/session.py`) now reloads `providers.json` fresh
(`load_custom_providers(self.settings.providers_config_path)`) right
before calling `resolve_chat_model`, inside the same try/except that
already reverts all other session state on failure. `add_provider`
(`web/app.py`) mutates the *same* `settings` object every already-open
session holds a reference to (`settings.providers_config_path = path`),
so this pickup is immediate, no new thread/reconnect/restart needed.
Cheap enough (a small JSON read) to redo on every switch rather than add
a second cache to keep in sync with `_get_session`'s per-thread one.
Regression test:
`test_switch_model_picks_up_a_provider_added_after_the_session_was_created`
(`tests/test_web.py`) -- adds a provider mid-session, over the same
already-open WebSocket, and asserts the very next `switch_model` call
actually receives it.

A second, smaller issue surfaced by the same report: `web/app.py`'s
`PROVIDER_CATALOG` pre-fills a guessed `default_model` for each
third-party entry (DeepSeek/Kimi/GLM/Ollama) into the Providers tab's
Add-provider form -- and DeepSeek's own guess (`deepseek-v4-flash`) was
already wrong (the user's own current account showed `deepseek-flash`/
`deepseek-v4-pro`). A vendor's model lineup is outside this project's
control and turns over on its own schedule; a wrong guess silently
pre-filled into a form reads as authoritative when it isn't, which is a
worse failure mode than an empty field the user has to fill in
themselves. Removed the four third-party `default_model` guesses
entirely (now `""`, matching the existing `openai` builtin entry's own
already-empty default) -- `base_url` guesses are kept, since a vendor's
API host is genuinely stable, unlike its model catalog. The one
*builtin* provider whose `default_model` was also caught stale in the
same pass, `anthropic`'s `"claude-opus-4-6"` (no such model -- current
family is Opus 5/Sonnet 5/Haiku 4.5), was corrected rather than emptied:
Anthropic doesn't publish a rolling `-latest` alias the way `gemini`'s
own catalog entry does (`"gemini-flash-latest"`, immune to this same
drift by construction), and emptying it would degrade the single most
common Add-provider path in the whole catalog.

## Tool-loading context cost -- measured real, a real fix identified, deliberately not built yet

User observation, live: a plain "你好" (hello) burns 40k+ tokens of
context before any real conversation content. Investigated rather than
guessed at.

**Measured, not estimated**: `build_coordinator_agent`'s full tool set
(`build_file_tools`/`build_document_tools`/`build_spreadsheet_tools`/
`build_presentation_tools`/`build_image_tools`/`build_task_tools`/
`build_interaction_tools`/`build_memory_tools`/`build_workflow_tools`/
`build_selfwake_tools`/`build_scheduled_task_tools`/
`build_websearch_tools`/`build_script_tools`/`build_node_script_tools`/
`build_background_task_tools`) is **90 tools**. Converting every one
through `langchain_core.utils.function_calling.convert_to_openai_tool`
and counting with `tiktoken`'s `cl100k_base` (a real proxy, not
Claude's own tokenizer, but the right order of magnitude): **~27,000
tokens** of tool JSON schema alone, plus `coordinator.py`'s own
`INSTRUCTIONS` string at **~7,400 tokens** -- **~34,600 tokens** as the
floor for every single turn, before skills/templates/memory/history.
`presentations.py` alone contributes 37 of the 90 tools; its docstrings
alone are ~13k of the ~27k tool-schema total (verbose-but-precise
docstrings, this project's own deliberate style throughout this
session, are a real, measurable cost here, not a free choice).

**A real, working fix exists, identified and verified (not just
theorized)**: Anthropic's Tool Search Tool
(`tool_search_tool_regex_20251119`/`tool_search_tool_bm25_20251119`,
[docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool))
is a real, documented Claude API feature -- mark a tool `defer_loading:
true`, the API excludes it from the model's context/cached prefix
entirely until Claude searches for it, then expands the matched
`tool_reference` into the full definition inline. This is confirmed to
be the actual mechanism behind this very session's own "deferred
tools"/`ToolSearch` behavior (Claude Code's own docs describe the
identical on-by-default behavior). Real numbers from Anthropic's own
docs: an 90-tool multiserver MCP setup at ~46-55k tokens drops over 85%
with this on -- the same scale as coscribe's own 27k tool-schema
number above. LangChain already ships this as a ready middleware,
`langchain.agents.middleware.ProviderToolSearchMiddleware` -- confirmed
importable in this project's own currently-installed `langchain`
(1.3.16), no dependency bump needed, and it plugs into `create_agent`
the exact same way `AnthropicPromptCachingMiddleware` already does in
`agent.py`. It also correctly preserves prompt caching (Anthropic's own
docs: "the prefix is untouched, so prompt caching is preserved") --
the caching-interaction risk raised when this was first discussed
turned out to be a non-issue, already solved server-side.

**The real gap, found by reading the middleware's own source, not
assumed**: `ProviderToolSearchMiddleware._get_model_provider` infers
the provider from the bound model's Python **class name** only
(`ChatAnthropic` -> `"anthropic"`, `ChatOpenAI` -> `"openai"`) --
`_provider_from_class_name` never inspects `base_url`. Every one of
this project's own custom OpenAI-compatible providers (DeepSeek/Kimi/
GLM/Ollama, see `web/session.py`'s `resolve_chat_model`) is a
`ChatOpenAI` instance with `base_url` overridden -- the middleware
would misdetect these as genuine OpenAI and inject OpenAI's own
`{"type": "tool_search"}` server tool into a request actually bound
for a different vendor's API, which has no idea what that tool type
means. This is not a detectable-and-fixable bug in coscribe's own
code: server-side deferred-tool expansion is a capability the
*provider's own infrastructure* has to implement, and there's no
public indication any of DeepSeek/Kimi/GLM/Ollama have built an
equivalent. Gemini is unsupported by this middleware entirely, for the
same reason (not in Anthropic/OpenAI's two-provider list at all).

**Decision, recorded per explicit user request, not yet implemented**:
build coscribe's own provider-agnostic tool-search middleware instead
of (or as a universal fallback under) the official one -- same
`wrap_model_call` hook shape `ProviderToolSearchMiddleware` itself
uses, but doing the "index now, expand on demand" dance in plain
Python: most tools unbound by default, a plain `search_tools(query)`
tool (an ordinary function tool, no provider-specific wire format)
lets the model discover by keyword, and the middleware dynamically
adds the discovered tools' real definitions to `request.tools` for the
next model call. Works identically on anthropic/gemini/openai/any
custom provider, since it never depends on a vendor implementing
server-side expansion -- the real tradeoff against the official
middleware is losing the "excluded from the *cached* prefix" server-
side saving on genuine Anthropic/OpenAI specifically (an extra round
trip on first discovery either way, unavoidable regardless of which
mechanism is used). Two designs were on the table --
(A) build only the universal client-side version, one code path,
identical behavior on every provider, no provider-detection risk at
all; (B) hybrid, using the official middleware when the real
`provider_key` is genuinely `anthropic` or non-custom `openai` (known
already, from the same string `switch_model` already parses, not
trusted to `ProviderToolSearchMiddleware`'s own fragile class-name
guess) and the custom one otherwise, trading one extra code path for
the caching win on the two most-used providers. User explicitly
leaning towards **(A)** -- deliberately not started yet, this section
exists purely to preserve the research (real measured numbers, the
verified official mechanism, and the exact gap found in its source) so
the next round doesn't have to re-derive any of it.

**Built as (A), now on by default.** `runtime_lg/tool_deferral.py`'s
`DeferredToolMiddleware` binds `coordinator.py`'s `CORE_TOOL_NAMES` plus
whatever `search_tools` has returned so far in the thread, on every
provider alike. It shipped off by default and was switched on after a
packaged-build test: with four connectors attached, a first "你好" cost
393.8k tokens (39.4% of the window). `search_tools`' own description now
carries a generated index of what can be found (category, count, a few
names), since a bare "search for tools" gave the model no reason to
expect, say, a browser. Measured on one thread with Playwright
connected: 12,781 tokens bound with deferral, 46,478 without. See
ROADMAP Phase 8bc.

## Sub-agents: approvals through the session, not through the parent's graph (2026-09-26)

The nested-interrupt bridge described above (a synchronous `spawn_agent`
calling `interrupt()` from inside the parent's tool node to pass a
child's approval up, and the shared child checkpointer that fixed its
replay bug) is gone, along with `scripts/verify_nested_interrupt.py` and
`scripts/verify_concurrent_spawn_agent.py`. Both delegation tools now start
the child as its own asyncio task (`runtime_lg/subagents.py`'s `_drive`),
and a child's pending action requests go to `SubAgentHost.decide` -- the
session's own `_decide_action_request`, given a stand-in socket
(`_SubAgentApprovalChannel`) that records the approval on the task for the
Sub Agents panel. Why:

- The user answers a sub-agent's approvals in the panel, and a background
  run can ask long after the parent's turn has ended. The bridge only
  worked while the parent was blocked in the tool call.
- Plan mode, Accept Edits, exec policy and hooks now apply to children
  through the same code path as the parent, not by construction.
- Two concurrent children can each have an approval pending at the same
  time (the old path decided them one after another).

Found while building it:

- `asyncio.create_task` copies contextvars, and LangChain keeps the running
  call's config there, so a child started from inside the parent's tool
  call streamed its own messages into the parent's `stream_mode="messages"`
  -- the same leak the old test comment called "a real LangGraph
  behavior". Runners start in a fresh `contextvars.Context()`.
- The approval middleware re-emits the model's message in its own
  `updates` chunk, so tool uses were counted twice until messages were
  de-duplicated.
- Stopping a waited-on child from the panel raised `CancelledError` inside
  the parent's `await asyncio.shield(runner)` and would have cancelled the
  parent's whole turn. The parent now checks
  `asyncio.current_task().cancelling()` to tell "only the child was
  stopped" from "my own turn is ending".
- Live (DeepSeek): a background child asked for approval after the tab had
  closed; forwarding it to the finished turn's socket threw and failed the
  run. The forward is best-effort now; the approval waits on the task.
- A record with no live runner reads as cut off by a restart and is
  settled to "stopped" on read. A stress run caught that racing the
  runner's own final save (read "running", runner saves "succeeded" and
  leaves the registry, reader overwrites with "stopped", 2 in 15 runs);
  settling now re-reads under the store's write lock, and a new run is
  saved only after its runner is registered. 40/40 afterwards.
