# coscribe

[![coscribe CI](https://github.com/OICWS/project/actions/workflows/office-agent-ci.yml/badge.svg)](https://github.com/OICWS/project/actions/workflows/office-agent-ci.yml)

Local, user-friendly agentic system for office work. Built on top of
[`aisuite`](https://github.com/andrewyng/aisuite) for LLM access and agent
runtime primitives. See `../ARCHITECTURE.md` for the full design.

A single Coordinator agent with built-in file, document/spreadsheet/
presentation, task-tracking, Skill, and subagent-delegation tools, plus
optional MCP server tools and Hooks -- runnable from the CLI (`coscribe`)
or a full web UI (`coscribe-web`), with Plan Mode and Accept Edits Mode as
in-session toggles.

## Setup

```bash
cd office-agent
python3 -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev,web]"
# Required for real multi-turn tool-calling with Gemini's "thinking" models --
# see the google-genai comment in pyproject.toml's dependencies list for why
# this can't just be folded into the pin above (aisuite's own httpx pin
# conflicts with google-genai>=1.5.0's, so it has to be upgraded as its own
# separate step, after the main install, not resolved together with it).
# Skipping this produces pydantic "extra_forbidden"/"thought_signature"
# errors on the second turn of any tool-calling conversation with Gemini.
# Upper-bounded below 2.14.0, which removed Part.thought_signature again
# (an upstream regression) and reintroduces the exact same errors.
pip install -U "google-genai>=2.13.0,<2.14.0"
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY,
# and COSCRIBE_DEFAULT_MODEL as "provider:model" (e.g. gemini:gemini-2.5-flash)
cd frontend && npm install && npm run build && cd ..
# ^ one-time (and after any frontend change) -- compiles into
# src/coscribe/web/static/, which coscribe-web serves and which is
# gitignored (a build artifact, not source) so a fresh checkout has none
# yet. Needed for coscribe-web; not needed for the CLI-only `coscribe`.
```

Supported model prefixes: `anthropic:`, `openai:`, `gemini:` (Gemini Developer
API — a plain API key from https://aistudio.google.com/apikey, not Vertex AI).
Every real conversation turn goes through `langchain-google-genai`/
`langchain-anthropic`/`langchain-openai` (published packages, not vendored
code) via runtime_lg's `resolve_chat_model`. A small Gemini provider is
still vendored into this repo (`src/coscribe/providers/gemini_provider.py`)
for one narrow, unrelated purpose -- the context-usage bar's max-size
lookup, see [below](#web-ui) -- see that file's header comment for why.

Any other OpenAI-compatible API (DeepSeek, Kimi/Moonshot, Zhipu GLM, or
anything else) can be added as a custom provider prefix -- see
[Custom LLM providers](#custom-llm-providers) below.

## Run

```bash
coscribe
```

This starts an interactive REPL backed by a real LLM call (per
`COSCRIBE_DEFAULT_MODEL` in `.env`). The Coordinator can list/read/search
files under `COSCRIBE_WORKSPACE_ROOT` (defaults to `./workspace`).

The built-in file/document/spreadsheet/presentation tools can also reach
specific directories outside the workspace, if you configure them. In the
web UI, Settings > Workspace has a unified **Directories** list: Workspace
Root always sits at the top (always fully readable and writable), and you
can add any number of additional directories below it, each with its own
Read only / Read & Write toggle -- type a path directly or use **Browse...**,
backed by a small server-side folder picker (`/api/browse-dirs`), not the
browser's native file picker, which never exposes a selected folder's real
absolute path for security reasons; this sidesteps that by listing real
directories on your machine directly. This is useful for, say, letting the
Coordinator read a file a browser-automation MCP server just downloaded,
without first copying it into the workspace by hand.

Under the hood this is still two `.env` variables --
`COSCRIBE_EXTRA_READABLE_DIRS` (read-only) and
`COSCRIBE_EXTRA_WRITABLE_DIRS` (read+write; a writable directory doesn't
need to also be listed as readable) -- each a comma-separated list of
absolute paths, e.g.
`COSCRIBE_EXTRA_WRITABLE_DIRS=/home/you/Downloads,/home/you/Documents`,
for when you'd rather edit `.env` directly than use the directories list.

`COSCRIBE_MAX_TURNS` (`.env`, default `20`) caps how many tool-calling
turns a single agent loop runs before giving up, to bound token/time spend
from a loop that keeps calling tools without finishing -- applies to a
normal chat turn and to `run_workflow`'s "agent" mode; `spawn_agent`/
`review_work` sub-agents use their own smaller, separate default (6), not
this setting. Editable from the web UI's Settings > General as well as
`.env` directly.

Session state is persisted to `.coscribe/state/<thread-id>.json` so a
conversation can be resumed with `coscribe --thread <thread-id>`.

For multi-step requests, the Coordinator can plan and track progress with
`task_create`/`task_update`/`task_list`, persisted per-thread alongside the
session state at `.coscribe/state/<thread-id>.tasks.json`.

Several commands are typed as a message mid-session (not startup flags):

- `/plan` -- Plan Mode: only read-only and task-tracking tools run; anything
  that would change real state (`write_file`, any MCP tool) is blocked
  outright, no prompt. The model is expected to use `task_create` to note
  what it would do instead, then wait for you to run `/plan` again to let it
  proceed.
- `/accept-edits` -- Accept Edits Mode: tool calls that would normally need
  approval run without asking, for a faster loop when you trust the agent for
  a stretch of work. No effect while Plan Mode is also on, since nothing
  needs approval to begin with in that state.
- `/compact` -- summarize the current thread's message history down to a
  single note, freeing up context for the rest of a long-running session.
  Does nothing if there's not much yet to compact. This also happens
  automatically once a turn's usage crosses `COSCRIBE_AUTO_COMPACT_THRESHOLD`
  of the model's context window (default 0.8) -- see
  [Scheduled / unattended runs](#scheduled--unattended-runs) below for why.
- `/clear` -- wipe the current thread's conversation history outright and
  start fresh (unlike `/compact`, which summarizes rather than discards).
  Doesn't touch Plan Mode/Accept Edits Mode (those are session-level
  toggles, not conversation content) or memory/tasks/saved workflows --
  it does cancel an in-progress `/startworkflow` recording, if any, since
  its step-index bookkeeping would otherwise refer to state about to be
  wiped.
- `/startworkflow`, `/endworkflow <name> [summary]`, `/saveworkflow <name>`,
  `/runworkflow <name>` -- record, save, and run named [workflows](#workflows).
  Bare `/saveworkflow` (no prior `/startworkflow`) has the model figure out
  what's worth saving from the conversation itself, asking a clarifying
  question first if it's unclear and always previewing before it actually
  saves anything -- see [Workflows](#workflows) below.
- `/stop` -- stop the current in-progress run (a runaway tool-calling loop,
  a workflow stuck retrying, or just a response you no longer need). In the
  web UI, this is also what the composer's send button turns into while a
  run is in flight -- click it (or type `/stop`) to cancel. Unlike the other
  commands here, it doesn't wait its turn behind whatever's currently
  running: it reaches into that run directly, denying any tool call it's
  currently waiting on approval for. The run ends with whatever partial
  progress it had already made; nothing already-executed is undone. Web UI
  only -- the CLI's REPL has no way to accept a `/stop` while it's blocked
  waiting on the current turn.
- `/init` -- have the Coordinator explore `COSCRIBE_WORKSPACE_ROOT` and
  write a project overview to `OVERVIEW.md` at its root, analogous to Claude
  Code's own `/init`.
- `/<skill-name>` -- invoke a loaded skill directly by its name (e.g.
  `/pdf-extract summarize report.pdf`), splicing its full instructions
  straight into that turn instead of relying on the model to notice it's
  relevant and call `load_skill` itself. Anything typed after the name is
  passed along as the request. A `/word` that matches neither a built-in
  command nor a skill name is rejected locally with no LLM call, rather than
  sent through as plain text.

### Scheduled / unattended runs

The intended pattern for a repeating task: develop and validate it
interactively first (with the normal approval gate on risky tool calls),
then hand the *same thread* off to an external scheduler (cron, systemd
timer, ...) that re-invokes it on a schedule, reusing `--thread` so it
keeps its accumulated context across runs.

```bash
coscribe --thread nightly-report --accept-edits \
  --message "Run workflow 'nightly-report'."
```

This is also how a scheduler runs a saved [workflow](#workflows): the
`--message` text just asks the Coordinator to call `run_workflow`, same
effect as typing `/runworkflow nightly-report` interactively -- no separate
scheduler-facing entry point exists or is needed. (`--message` mode doesn't
interpret slash commands, so `/runworkflow` itself isn't available there --
plain text asking to run it is, since `run_workflow` is still a model tool,
just no longer a model-callable *creation* tool.)

- `--message`/`-m TEXT` -- send one message non-interactively and exit,
  instead of starting the REPL. Output is exactly the model's final reply
  (nothing else), and the exit code is nonzero if the call errored or hit
  the turn limit -- both meant for cron/systemd logs and alerting. Slash
  commands (`/plan`, `/init`, skill names, ...) are **not** interpreted in
  this mode; the text is sent to the agent as-is.
- `--accept-edits` -- start in accept-edits mode from the first turn.
  Without it, `-m` still works, but any tool call that needs approval will
  hang waiting on a prompt nothing unattended can ever answer -- combine
  the two for a real scheduled job.
- `COSCRIBE_AUTO_COMPACT_THRESHOLD` (`.env`, default `0.8`) -- since a
  thread reused by every scheduled run would otherwise grow forever with
  nobody present to type `/compact`, it's compacted automatically once a
  turn's token usage crosses this fraction of the model's context window.
  Set it above `1.0` to disable and rely on manual `/compact` only.

### Sleep and wake -- pausing a conversation instead of scheduling a new one

The pattern above works well for a task that runs on a fixed external
schedule. For a task where the *agent itself* decides when to check back
-- "wait until 9am tomorrow," "let me know once that workflow run
finishes," "resume once something else tells you to" -- the Coordinator
has its own tools instead of you writing a second cron entry per task:

- `sleep_until(wake_at, reason)` / `sleep_for(seconds, reason)` -- pause
  this conversation and automatically resume it at a wall-clock time or
  after a delay.
- `wake_on(job_id, reason)` -- pause until a specific in-progress
  [workflow](#workflows) run finishes (`job_id` is that run's id).
- `wake_on_task(task_id, reason)` -- pause until a specific in-progress
  background script (started via `run_background_script`, see below)
  finishes (`task_id` is that task's id).
- `wake_on_event(event_key, reason)` / `signal_event(event_key, payload)`
  -- pause until a named event fires; `signal_event` is how another turn
  (this thread, another thread, or a future connector/webhook) fires it.
- `list_wakes()` / `cancel_wake(wake_id)` -- see or cancel this
  conversation's own pending sleep/wake requests.

Resuming needs *something* to periodically check whether a pending
sleep/wake is due and, if so, restart that thread with a synthetic
"scheduled wake-up reached" message. Two ways to get that:

- **Run `coscribe-web`.** While it's running, it checks for due wakes
  automatically every `COSCRIBE_WAKE_POLL_SECONDS` (`.env`, default `30`)
  -- nothing else to configure.
- **Schedule `coscribe --check-wakes`** via external cron/systemd (e.g.
  every 5 minutes) if you'd rather not keep the web server running. It
  checks every thread once, resumes whichever have a due wake, prints a
  one-line summary per thread it resumed, and exits -- the generic,
  agent-driven counterpart to hand-writing a fixed-time `--message` cron
  entry per scheduled task.

A tool call that needs approval during a wake-triggered turn is left
pending, same as an unattended `-m` run made without `--accept-edits` --
there's nobody there to answer it, so it waits durably until you next open
that thread. If a browser tab already has the thread open live when a
background poll resumes it, the reply is saved correctly but won't stream
into that open tab in real time -- you'll see it on the next reload or
reconnect, not pushed live (see `ARCHITECTURE.md` for why).

### Scheduled Tasks -- a real, product-level scheduler

Both patterns above still need *something*: an external cron entry, or a
conversation already open to call `sleep_until`/`sleep_for` from. Scheduled
Tasks is the built-in alternative that needs neither -- a named,
independently-managed trigger that runs once at a future time, or daily/
weekly/monthly, entirely on its own.

Create one either from chat (the Coordinator has `create_scheduled_task`)
or from **Settings > Scheduled Tasks**, which has a full form -- name, a
friendly schedule picker (no cron syntax), and either a freeform
instruction or a saved [workflow](#workflows) to run each time:

- `create_scheduled_task(name, kind, at, prompt=None, workflow_name=None, weekday=None, day_of_month=None)`
  -- `kind` is `"once"` / `"daily"` / `"weekly"` / `"monthly"`; `at` is a
  full timestamp for `"once"`, otherwise `"HH:MM"`. Pass exactly one of
  `prompt` (a freeform instruction, run fresh each time) or `workflow_name`
  (an existing saved workflow). `weekday` (0=Monday..6=Sunday) is required
  for `"weekly"`, `day_of_month` for `"monthly"` (clamped to a short
  month's real last day, e.g. day 31 in February lands on the 28th/29th).
- `list_scheduled_tasks()` / `pause_scheduled_task(trigger_id)` /
  `resume_scheduled_task(trigger_id)` / `delete_scheduled_task(trigger_id)`
  -- manage every scheduled task, not just ones the current conversation
  created (unlike `list_wakes`, which only ever shows its own thread's
  pending wakes). Resuming recomputes the next run time from *now*, so a
  task paused for a week doesn't fire a backlog of missed runs the moment
  it's resumed.

Each trigger gets its own dedicated, persistent conversation
(`scheduled-<trigger_id>`) -- never the thread it was created from, so a
recurring fire never interrupts whatever you're doing in your own chat. A
prompt-backed trigger's history genuinely accumulates in that dedicated
thread across runs, same `COSCRIBE_AUTO_COMPACT_THRESHOLD` keeping it from
growing unbounded as the manual cron recipe above.

Firing works exactly like the sleep/wake poll above and shares the same
mechanism -- `coscribe-web`'s background poll (every
`COSCRIBE_WAKE_POLL_SECONDS`) and `coscribe --check-wakes` both check for
due scheduled tasks in the same pass they check for due wakes, no separate
flag or server to run. `at`/`"HH:MM"` are interpreted in the machine's own
local wall-clock time -- coscribe is single-user, local-first software
with no per-user timezone concept, so if it's ever run remotely on a
machine in a different timezone from you, schedule times will be off by
that offset.

If a fire's action calls a tool that needs approval (any `WRITE_LOCAL`/
`EXEC`/`EXTERNAL`-risk tool, `write_file`/`write_xlsx` included -- see
`ARCHITECTURE.md`'s risk taxonomy), there's nobody there to answer it, so
the gated action never actually runs -- the interrupt is left paused in
that trigger's thread for you to resolve by opening it in the web UI, and
firing returns promptly either way, never blocking later fires of other
scheduled tasks or wakes. A `workflow_name` trigger reports this
precisely (`last_run_status="failed"`, with an error explaining what
happened); a `prompt` trigger doesn't currently inspect the turn's
outcome at all (`last_run_status` is always `"completed"`, same
simplification `sleep_until`/`sleep_for`'s own wake-up already makes) --
in both cases, design the prompt/workflow to only need `READ`-risk tools
if you want it to genuinely complete unattended every time.

## Files

The Coordinator's most basic tools work on plain-text files under the
workspace root (plus any `extra_readable`/`extra_writable` directories --
see `Settings.extra_readable_dirs`/`extra_writable_dirs`, e.g. your
Downloads folder, so a file doesn't need to be copied into the workspace
first):

- `list_files(path=".", pattern="*", recursive=True)` / `search_files(query, path=".", pattern="*", regex=False)`
  -- glob-listing and line-by-line search, literal substring by default or
  (`regex=True`) a Python regular expression (an invalid pattern raises
  immediately rather than matching nothing). Each caps its result (200
  files, 100 matches) and returns a `truncated` flag when it does (plus a
  real `total_count` for `list_files` -- `search_files` stops scanning
  early once it hits the cap, so it can't report an exact total without
  giving up that performance benefit). Don't assume the returned list is
  complete without checking `truncated`; narrow `path`/`pattern` and call
  again for the rest.
- `read_file(path, head=None, tail=None)` / `write_file(path, content, overwrite=True)`
  -- whole-file UTF-8 text read/write (same write contract as
  `write_docx`/`write_pdf`). `read_file`'s `head`/`tail` (mutually
  exclusive) read only a large file's first/last N lines instead of the
  whole thing.
- `get_file_info(path)` -- a file or directory's size and last-modified
  time (`{"path", "is_directory", "size_bytes", "modified"}`, `size_bytes`
  is `null` for a directory), without reading its contents -- check before
  `read_file` on a file that might be large, or to tell which of a few
  candidates is newest.
- `edit_file(path, old_text, new_text, replace_all=False)` -- replaces one
  exact, unique occurrence of `old_text` with `new_text` (every occurrence
  with `replace_all=True`) without resending the rest of the file -- the
  tool to reach for over `read_file`+`write_file` when only part of a
  (especially large) text file needs to change. Raises rather than
  guessing if `old_text` isn't found, or matches more than once without
  `replace_all` -- include enough surrounding context in `old_text` to
  pin down the one place you mean, same convention Claude Code's own Edit
  tool uses.
- `edit_file_batch(path, edits)` -- several `edit_file`-style edits to
  one file in a single call. `edits` is one string: alternating
  old_text/new_text chunks separated by a line containing only `---`
  (the same convention `write_pptx` uses for its own slide content) --
  not a structured list/object parameter, since this codebase's tool
  schemas have to stay Gemini-compatible and the schema builder they go
  through has no support for list/dict-typed parameters at all. Applied
  in order (a later edit can match text an earlier one in the same batch
  just introduced), written to disk only once every edit has validated --
  one bad edit anywhere in the batch leaves the file untouched. No
  per-edit `replace_all`; a change that needs it still goes through a
  separate `edit_file` call.
- `delete_file(path)` -- permanently deletes one file. Rejects a
  directory outright (not a disguised `rm -rf`) -- use `run_python_script`
  (`shutil.rmtree`) for a whole directory.
- `move_file(source, destination, overwrite=False)` -- moves or renames a
  file *or* a whole directory (a same-directory move is just a rename).
  Always rejects an existing directory at `destination`, even with
  `overwrite=True` -- merging into vs. replacing it is ambiguous either
  way, so this doesn't guess; use `run_python_script` for that case.
- `copy_file(source, destination, overwrite=False)` -- unlike
  `read_file`/`write_file`'s UTF-8-text-only shape, this copies bytes
  directly, so it works on binary files too (images, PDFs, `.pptx`, any
  format) without corrupting them. Single files only, same directory
  restriction as `delete_file`.

`write_file`/`edit_file`/`edit_file_batch`/`delete_file`/`move_file`/
`copy_file` are all `WRITE_LOCAL` risk and require approval;
`list_files`/`read_file`/`search_files`/`get_file_info` are `READ` risk
and don't.

## Documents (Word/PDF/Excel/PowerPoint)

Alongside plain-text `read_file`/`write_file`, the Coordinator has
`read_docx`/`write_docx`, `read_pdf`/`write_pdf`, `read_xlsx`/`write_xlsx`,
and `read_pptx`/`write_pptx` for real Word, PDF, Excel, and PowerPoint files
under the workspace -- see `ARCHITECTURE.md` for why these are built in
rather than routed through Skill/MCP like other document formats. The
docx/pdf writers (`python-docx`/`reportlab`) take the same lightweight
markdown subset:

- `#` / `##` / `###` for headings (up to 3 levels)
- `-` or `*` for bullet items, `1.` for numbered items
- `| cell | cell |` rows for tables
- any other line is a plain paragraph

`read_docx`/`read_pdf` go the other way through `mammoth`+`markdownify`
and `pdfplumber` respectively -- the same libraries microsoft/markitdown
itself uses internally for these formats, without pulling in markitdown's
own mandatory file-type-sniffing dependency. Their output is real
markdown, a superset of what the writers accept (arbitrary heading depth,
links, bold/italic), so reading a generated file back is semantically
faithful but not necessarily byte-for-byte identical. There's no partial
in-place editing -- to change an existing Word/PDF file, read it,
produce the modified full text, and write it back with `overwrite=True`
(same whole-file-overwrite contract `write_file` already uses). Reads are
low risk; writes are medium risk and require approval, same tier as
`write_file`.

For a specific term inside one or more PDFs, `search_pdf(query, path=".",
pattern="*.pdf")` finds matching page numbers across every PDF under
`path` without spending context on `read_pdf`-ing whole documents first --
same idea as `search_files`, just PDF-aware (uses the same `pdfplumber`
extraction `read_pdf` does).

`read_xlsx`/`write_xlsx` (`openpyxl`) work differently from the docx/pdf
pair, since a workbook is a grid, not flowing text: `content` for
`write_xlsx` is pipe-table rows only (no headings/bullets/paragraphs), and
cells that look numeric are stored as real numbers so they're usable in
Excel formulas afterward. `read_xlsx(path)` with no `sheet` renders every
sheet as a `## <sheet name>` heading plus its table, in one call; pass
`sheet="Name"` to read just one sheet, same "don't spend context you don't
need" idea as `search_pdf`. `overwrite` is also scoped differently here --
to *the one sheet* you're writing, not the whole file: writing a new
`sheet_name` to an existing workbook always appends it (build a multi-sheet
report across several calls), while writing an existing `sheet_name`
replaces just that sheet's content if `overwrite=True`, or raises if not.

A cell starting with `=` in `write_xlsx`'s `content` is written as a real
Excel formula (`=SUM(B2:B9)`), not a hardcoded number -- prefer this
whenever a value depends on other cells, so the sheet actually
recalculates when its inputs change. `XLOOKUP`/`XMATCH`/`SORT`/`FILTER`/
`UNIQUE`/`SEQUENCE` are rejected outright (LibreOffice, used to verify
every formula, can't evaluate them, and a partial success would silently
populate only one cell); use `INDEX`/`MATCH` for lookups instead. Six
post-2007 functions (`TEXTJOIN`/`CONCAT`/`IFS`/`SWITCH`/`MAXIFS`/
`MINIFS`) are auto-prefixed with the hidden `_xlfn.` Excel itself uses, so
you don't need to remember that. If `soffice` is on `PATH`, `write_xlsx`
automatically re-opens any file it just wrote a formula into and forces a
real LibreOffice recalculation (openpyxl only ever writes the formula
text, never a cached value) -- see the response's `recalc_status`
(`"success"`/`"errors_found"`/`"skipped"`), `total_errors`, and
`formula_error_locations`. `recalc_xlsx(path, timeout=30.0)` does the same
recalculation on demand, for a file that already has formulas but wasn't
written by `write_xlsx`. `format_xlsx_cells(path, sheet_name, cell_range,
number_format="", font_color="", bold=False)` applies a number format
(e.g. `"$#,##0"`, `"0.0%"`) and/or font color/weight to a cell or range --
chain it after `write_xlsx` the same way `add_xlsx_chart` is chained.

`read_pptx`/`write_pptx` (`python-pptx`) use a slide-separated markdown
convention: a line containing exactly `---` starts a new slide (same
convention Pandoc/Marp/reveal.js already use); within a slide, the first
heading becomes the slide title and the rest becomes the body -- bullets/
paragraphs *or* a single pipe table, not both (put a table on its own
slide). `write_pptx` is whole-file overwrite, like `write_docx`/`write_pdf`.
`read_pptx(path)` with no `slide` renders every slide; pass a 1-based
`slide` number to read just one.

`add_xlsx_chart(path, sheet_name, chart_type, data_range="", title="", anchor="")`
and `add_pptx_chart(path, slide, chart_type, data, title="")` add a bar,
line, or pie chart to a file that already exists -- they don't create the
file themselves, chain them after `write_xlsx`/`write_pptx`. For xlsx,
`data_range` is optional and defaults to the sheet's entire used range
(the first column is the category axis, the rest are data series named
from their header cell) -- pass an explicit A1 range like `"A1:C6"` only
to chart a subset of a larger sheet; naming the range yourself is easy to
get one row or column off, silently dropping data or plotting an empty
series. For pptx, `data` is the same pipe-table convention `write_xlsx`
uses, since a slide doesn't have a spreadsheet's persistent grid to
reference. Both are deliberately scoped to single-purpose charts, no
stacked or secondary-axis/combo charts -- see `ARCHITECTURE.md` for why.
`add_pptx_chart` positions the chart in the slide's own content area,
below any text already on that slide's body placeholder (shrinking that
placeholder to fit its own text first) rather than a fixed box that
could land on top of it or ignore the deck's real slide size; if the
existing body text leaves too little room, it raises rather than
producing an overlapping or off-slide chart. It also matches the deck's
own theme text color (so it stays readable on a dark `theme=`) instead
of python-pptx's chart default black. A pie chart takes exactly one
data column (a second series has no meaningful rendering as pie
slices).

`write_docx`/`write_pptx` both take an optional `template_path` -- an
existing .docx/.pptx file whose styles/page setup (docx) or theme/fonts
(pptx) carry over to the new file, with its own content/slides cleared
first.

`write_pptx` also takes an optional `theme` parameter for a from-scratch
deck (no `template_path` needed) that would otherwise stay plain white:
comma-separated `key=value` color/font tokens -- `bg`, `text`, `surface`,
`accent` (6-hex-digit colors, no `#`), `heading_font`, `body_font`. Every
key is optional; e.g. `theme="bg=0F172A,text=F8FAFC,accent=38BDF8"` for a
dark navy deck with a cyan accent. This edits the deck's own OOXML
`<a:clrScheme>`/`<a:fontScheme>` directly (python-pptx has no public API
for it), so it reaches every shape that references a theme color, not
just the slide background -- `layout: icon-list`/`layout: stat-callout`
slides that don't specify their own accent hex follow the theme's
`accent` automatically. Chinese/Japanese/Korean text in a `write_pptx`
deck (with or without `theme`) always renders in a real CJK typeface
(Microsoft YaHei), not whatever fallback font the opening machine's
PowerPoint happens to pick -- every stock python-pptx theme ships with
that font slot empty. `add_pptx_image(path, slide, image_path, left=1.0, top=1.0,
width=None, height=None)` inserts a picture already on disk onto an
existing slide (position/size in inches); `set_pptx_notes(path, slide,
notes)` sets that slide's speaker notes, replacing them entirely.
`set_pptx_transition(path, slide, transition, duration=1.0)` sets the
slide-change transition for advancing into that slide -- any of
PowerPoint's own real native transitions (48 effects across its own
Subtle/Exciting/Dynamic Content gallery categories -- `list_pptx_
transition_types()` returns the full list, e.g. `"morph"`, `"vortex"`,
`"honeycomb"`, `"cube"`, `"page_curl"`, plus the originally-supported
`"fade"`/`"push"`/`"wipe"`), or `"none"`. Each effect uses PowerPoint's
own sensible default variant (e.g. `"push"` always enters from the
right) -- no direction/shape/style customization yet, a deliberately
scoped-down v1 of the much larger registry this is adapted from (see
`PPTX_DESIGN.md` §27). A handful of legacy names (`"strips"`, `"wheel"`,
...) resolve as aliases onto one of the 48 canonical ones.

`fill_pptx_template(path, template_id, content, overwrite)` fills your
title/bullets into one of coscribe's own bundled, hand-designed decks
instead of building slides from scratch -- unlike `write_pptx`'s
`template_path` above, it keeps every decorative shape/background in the
template file exactly as designed rather than clearing the slides first.
Each template declares one repeatable "content" slide role (see its
`.yaml` manifest's `slide_roles` list) that is duplicated or trimmed
automatically to match however many `---`-separated chunks `content`
actually has, so the chunk count no longer has to equal the template's
own slide count -- only its non-repeatable slides (title, closing) still
need exactly one matching chunk each, in their fixed position. Each
chunk is title + bullets/paragraphs only, no tables or `layout:`
directives. Templates ship as `.pptx`/`.yaml` pairs under
`src/coscribe/builtin_templates/pptx/<id>/`, generated by
`scripts/build_pptx_templates.py` (run it to add a new one or tweak an
existing design in code, or hand-author a `.pptx`/`.yaml` pair directly
the way the original `modern-block` template was). Bundling a genuine
third-party template is possible, but only under a clearly redistribution-
-permitting license (CC0/public domain, verified against the real
source, not just "free to use") -- most "free PowerPoint template" sites
turn out to be personal-use-only or attribution-required in ways that
don't clearly permit shipping the file inside a third-party open-source
tool, so this bar has only been cleared once so far. As of this writing
there are four, all 4 slides (title/content/content/closing):
`modern-block` (solid color blocks, navy accent), `minimal-light` (white
background, one thin accent rule per title, no color blocks),
`bold-statement` (full-bleed dark title/closing slides, a colored accent
spine on content slides), and `velis` (a real third-party design by
Laurens R. Krol, CC0 1.0 -- teal/sage/magenta palette, stage-curtain
motif, the design's own A4-landscape proportions rather than 16:9; see
`src/coscribe/builtin_templates/pptx/velis/LICENSE`). The model picks
among them by tone/fit when asked to build a deck -- `GET
/api/templates`-style discovery isn't needed since the full list is
already in its system instructions. Each listed template is tagged
`[16:9]`/`[NOT 16:9]` (`TemplateInfo.is_widescreen`, computed from the
real file's own `slide_width`/`slide_height`, not assumed), and the
model defaults to a `[16:9]` one unless the user is fine with different
proportions or names a non-16:9 template specifically -- real, live
user feedback: `velis`'s own A4-landscape proportions got picked for a
plain deck request with no stated preference, which surprised the
user, since 16:9 is the expected default for a PowerPoint deck.

`extract_pptx_template(source_path, template_id, name, description,
overwrite=False)` distills a *new*, immediately-usable
`fill_pptx_template` template from an arbitrary reference `.pptx` -- a
user's own company-branded deck, say -- instead of coscribe's own
bundled four. It classifies the reference deck's real slides into the
same title/content/closing roles the bundled templates use (first
slide = title, last = closing, everything between = content) and
requires every slide already have a real title and/or body placeholder
`fill_pptx_template` can write into; a slide that doesn't raises,
naming which one, rather than silently producing a broken template. The
result is saved under `COSCRIBE_CUSTOM_TEMPLATES_DIR` (default
`./templates`, auto-created, gitignored -- same convention as
`COSCRIBE_SKILLS_DIR` above) alongside coscribe's own bundled set, so it
shows up in the model's own template listing on future runs too, not
just the turn it was created in. Only the reference deck's *design* is
kept -- its own original wording is always overwritten by
`fill_pptx_template`'s next call, and no logo/master-layout-perfect
clone is attempted, just what python-pptx can read/write directly.

`edit_pptx_text(path, slide, title, content, placeholder_index)` edits one
existing slide's title and/or one content placeholder's text in place, in
any `.pptx` already in the workspace -- not just coscribe's own bundled
templates the way `fill_pptx_template` is restricted to, so this is the
tool for a real file the user placed in the workspace or uploaded
themselves (their own company template, a deck someone sent them) and
wants the wording changed. Everything else in the file -- theme, every
other slide, images, decorative shapes, the file's own placeholders it
wasn't told to touch -- is left exactly as authored; both `title` and
`content` are optional and independent, so a call can rename just the
title, replace just the body, or both. Tested live against a real
Microsoft-authored template downloaded from Microsoft's own template
catalog (13 slides, 12 distinct layouts): this surfaced and fixed two
real bugs that `fill_pptx_template`'s always-empty bundled templates never
exposed -- placeholder text was being appended onto instead of replacing
a placeholder's existing content (garbled output against any template
that ships with real sample text, which real-world templates always do),
and a content-placeholder slot already holding a native table crashed
with an opaque `AttributeError` instead of a clean, actionable error.
`placeholder_index` (0-based, default 0) reaches a slide's second content
placeholder on a "two column"-style layout -- call `edit_pptx_text` again
with `placeholder_index=1` for the other one.

`delete_pptx_slide(path, slide)` removes one slide;
`duplicate_pptx_slide(path, slide, insert_at=None)` copies one (every
shape, its exact position/size/formatting, and every image on it --
speaker notes are not carried over to the copy; defaults to inserting
right after the source, pass a 1-based `insert_at` to place it
elsewhere); `reorder_pptx_slide(path, slide, new_position)` moves one
slide to a new 1-based position. All three operate on an existing
`.pptx`'s slide *structure* -- every other slide's own content is
untouched.

`list_pptx_shapes(path, slide)` returns a structured inventory of every
shape on one slide of an existing `.pptx` -- index, type, position/size
(inches), rotation, a short text preview, whether it's a table
(`is_table`) or picture (`is_picture`), fill color (`"#RRGGBB"` for
a literal color, `"theme:ACCENT_1"` for a `theme_color` reference),
whole-shape hyperlink address (`None` if it has none), and whether it's
real SmartArt (`is_smartart`) -- if so, `smartart_text` lists every
node's own text, since neither this tool nor `python-pptx` can generate
or edit real SmartArt at all (the layout algorithm lives in PowerPoint
itself, not the file format). `edit_pptx_shape(path, slide, shape_index,
left_in, top_in, width_in, height_in, rotation, fill_color)` moves,
resizes, rotates, and/or recolors one shape in place -- every property is
optional, only the ones given are changed, and nothing else on the slide
(or the rest of the deck) is touched. `delete_pptx_shape(path, slide,
shape_index)` removes one shape entirely, any type. Call `list_pptx_shapes`
first to find the right `shape_index` rather than guessing it: this is
the base set of tools for adjusting or removing one element of an
already-designed deck (a real uploaded template, or one coscribe already
generated) instead of rebuilding the slide from scratch. For a SmartArt
shape specifically, the practical path is: read its `smartart_text`,
`delete_pptx_shape` it, then rebuild the same idea with this file's other
shape/icon/table tools (or `write_pptx`'s own `icon-list` layout) --
there is no way to edit real SmartArt in place.

`replace_pptx_image(path, slide, shape_index, image_path)` swaps an
existing picture's image in place -- position, size, and crop are
untouched, only the pixels change (it repoints the shape's own
`<a:blip>` at a new image part rather than touching the shape itself).
Raster formats only (PNG/JPEG/GIF/BMP/TIFF, same as `add_pptx_image`),
not SVG. Use this to swap out a photo or an already-placed icon for a
different one without disturbing its placement.

`list_pptx_icons()` returns coscribe's bundled icon names -- a curated
~50-icon subset of [Lucide](https://lucide.dev) (ISC license) covering
common business-deck needs (arrows/trend, status, org/business, charts,
actions, common objects), rasterized to black-on-transparent PNGs at
build time by `scripts/build_pptx_icons.py` (re-run it to add another
name to the bundle; it needs `cairosvg`, a dev-only tool, not a project
dependency). `add_pptx_icon(path, slide, icon_name, left_in=1.0,
top_in=1.0, size_in=1.0, color="1F2937")` inserts one as a new square
picture on an existing slide, recolored to `color` (6-hex, no `#`) via
plain PIL alpha-masking at call time -- no SVG-rendering dependency at
runtime. This is a real icon, unlike `write_pptx`'s own `layout:
icon-list` (a glyph inside a plain colored circle).

`recolor_pptx_icon(path, slide, shape_index, color)` changes an
already-placed icon's color without moving, resizing, or re-inserting
it -- same relationship-swap mechanism as `replace_pptx_image`, but the
new image is derived from the icon's own *current* pixels (its existing
alpha/transparency channel recolored to `color`) rather than a bundled
icon looked up by name, so it works on any icon `add_pptx_icon` placed
regardless of which one, and can be called again on an already-recolored
icon. It does not detect edges -- every non-transparent pixel becomes
the new solid color, so using it on an opaque photo flattens the whole
picture to one color block; only meant for icons/logos with real
transparency. Find `shape_index` via `list_pptx_shapes` the same way as
the shape-editing tools above.

`edit_pptx_table_cell(path, slide, shape_index, row, col, text)` replaces
one cell's text in an existing table in place (0-based `row`/`col`);
`merge_pptx_table_cells(path, slide, shape_index, start_row, start_col,
end_row, end_col)` merges a rectangular range of cells into one (e.g. a
spanning header) -- either diagonal corner order works, and it raises if
the range already contains a merged cell. Both find the table via
`list_pptx_shapes`'s `shape_index`/`table_dimensions` the same way
`edit_pptx_shape` does. Merging keeps every merged-away cell's text,
appended as an extra paragraph into the resulting cell, rather than
discarding it -- clear a cell with `edit_pptx_table_cell` first if you
don't want its old text carried into the merge. Row/column insertion and
row-height/column-width resizing aren't supported yet.

`add_pptx_hyperlink(path, slide, shape_index, url, text=None)` makes a
shape or a piece of its text clickable, linking to an external URL --
pure `python-pptx` public API (`Run.hyperlink`/
`Shape.click_action.hyperlink`), no hand-written XML, unlike
`set_pptx_transition`/`add_pptx_animation` above. Find `shape_index` via
`list_pptx_shapes` the same way as the shape-editing tools above. Omit
`text` to hyperlink the *whole shape* (an image, an icon, an autoshape,
even a table) via its click action; pass `text` to hyperlink just one
run inside a text frame -- it must match that run's exact text (raises,
listing the shape's actual run texts, if nothing matches; hyperlinking a
substring within a run isn't supported). `url` must start with
`"http://"`, `"https://"`, `"mailto:"`, or `"ftp://"` -- linking to
another slide in the same deck (an internal, `TargetMode="Internal"`
hyperlink) isn't supported, since `python-pptx`'s own hyperlink API has
no first-class support for it either.

`read_pptx_theme_colors(path)` returns an existing `.pptx`'s real master
theme color palette (its `<a:clrScheme>`) -- the same 12 colors
PowerPoint's own Design tab color picker edits: `dk1`/`lt1`, `dk2`/`lt2`,
`accent1`-`accent6`, and `hlink`/`folHlink`, each a 6-hex-digit string.
`edit_pptx_theme_colors(path, colors)` changes one or more of them in
place, `colors` the same comma-separated `key=value` string convention
as `write_pptx`'s own `theme` parameter but naming the real slots
directly (e.g. `"accent1=0F6B5C,accent2=C2410C"`) -- only the slots
named are changed, applied to every slide master in the file. Because
every theme-color-referencing shape/placeholder across the whole deck
draws from these same 12 shared slots, this is the single most
far-reaching edit in this file -- call `read_pptx_theme_colors` first
rather than guessing which slot maps to "the color in the title."
**Scope note**: coscribe's own generated decorative shapes (`write_pptx`'s
icon-list circles, stat-callout cards, scrims) use hardcoded RGB, not
theme-color references, so this doesn't recolor those -- it's most
useful against a real uploaded or downloaded template whose own design
already references theme colors, not a coscribe-generated deck's own
decorative elements.

`write_docx` also supports four things `python-docx` has no native API
for, aligned with what Anthropic's own docx skill covers: a `[TOC]` line
anywhere in `content` inserts a real Word table-of-contents field built
from the document's heading levels (raw OOXML field-code XML, since
python-docx has no TOC API at all) -- it shows placeholder text until
Word recalculates it, which happens automatically the first time the file
is opened, no manual "Update Field" needed. A trailing `{{comment: text}}`
on a heading/paragraph/bullet/numbered-item line attaches a real Word
comment (visible in Word's Comments pane, authored as `comment_author`)
anchored to that whole line -- not recognized inside table cells.
`page_size` (`"letter"`/`"legal"`/`"a4"`/`"a3"`) and `orientation`
(`"portrait"`/`"landscape"`) override the page setup of a blank document
or a `template_path`. `track_changes=True` wraps everything this call
writes in a tracked insertion (`w:ins`, authored as `change_author`)
instead of already-final content -- useful when the document should go to
a human as a reviewable redline rather than a silent rewrite; combined
with `template_path`, the template's own existing paragraphs are marked
as a tracked *deletion* instead of being discarded outright, so the
reviewer sees old and new content side by side in Word. Tables are always
written as final content under `track_changes`, and table-level tracked
deletion isn't supported when replacing a template's tables -- both
documented scope limits, not oversights.

`add_pptx_animation(path, slide, shape_index, animation, duration=0.5,
trigger="on-click", delay=0.0, by_paragraph=False)` adds an animation to
one shape, `shape_index` 0-based the same way as `list_pptx_shapes`/
`edit_pptx_shape` (title first, then body/table, then anything added
later by `add_pptx_image`/`add_pptx_chart`). Six effects across three categories:
entrance (`"fade"`, `"fly-in"` -- shape starts hidden, then reveals), exit
(`"exit-fade"`, `"exit-fly"` -- shape is visible, then hides), emphasis
(`"emphasis-grow"`, `"emphasis-spin"` -- shape stays visible throughout,
scales up 50%-then-back or does one full rotation). Every effect's
`presetID`/`presetClass`/`presetSubtype`/motion-element shape is taken
from real PowerPoint-authored XML (cross-checked against
[hugohe3/ppt-master](https://github.com/hugohe3/ppt-master)'s own preset
manifest), not guessed -- entrance and exit variants of the same visual
effect share identical `presetID`/`presetSubtype` in real PowerPoint
files (`presetClass` is what actually distinguishes them), which the
read-back verification checks for explicitly. `trigger` is PowerPoint's
own Animation Pane Start mode: `"on-click"` (default) starts a new,
independently click-triggered group; `"with-previous"`/`"after-previous"`
instead chain onto the last animation added to that slide -- playing at
the same time, or automatically once it finishes -- with `delay` adding
any extra gap in seconds. The very first animation on a slide still plays
even if given `"after-previous"`, same as real PowerPoint: it just starts
on slide entry instead of needing a click. `by_paragraph=True` animates
each of the shape's non-empty paragraphs as its own step instead of the
whole shape at once -- the standard way to reveal a bulleted list one
bullet at a time (pair with `trigger="on-click"` for one click per
bullet, or `"after-previous"` with a small `delay` for an automatic
cascade). Unlike everything else in this section, this is hand-written
OOXML timing XML -- `python-pptx` has no animation API at all.
It's checked two ways before it ever reaches you: the generated XML is
validated against ECMA-376's real element ordering (verified empirically
by round-tripping through `python-pptx` with warnings treated as errors),
and after saving, the file is re-opened and structurally checked that the
animation actually landed on the right shape -- if that check fails, the
original file is left untouched and the tool raises instead of silently
producing a broken deck. What it can't check is whether it *looks* right
in real PowerPoint: this environment only has LibreOffice to test against,
and LibreOffice's leniency isn't a reliable stand-in for PowerPoint's own
stricter validation. Treat `add_pptx_animation` as best-effort, and open
the result in real PowerPoint before relying on it for anything that
matters.

`search_images(query, max_results=5)` searches the live web for images
(via `ddgs`, the same library `web_search` uses) and returns candidate
URLs -- nothing is downloaded yet. `download_image(url, path,
overwrite=True)` fetches one of those URLs into the workspace, rejecting
anything that isn't actually a decodable image (an HTML error page, a
non-image content type, a response over 20MB) before writing it.
`set_pptx_background_image(path, slide, image_path)` then applies that
image as a full-bleed background on one slide, cropped (never stretched)
to cover the whole slide from the image's own aspect ratio, and placed
behind the slide's existing title/content/other shapes automatically --
nothing to configure. **Images found through `search_images` are not
filtered by license or usage rights** -- this was a deliberate choice
(a curated free-license stock-photo API was considered and not used),
so treat a deck built with a web-sourced image as a draft, and check the
image's actual rights yourself before sending or publishing it externally.
Unlike `web_search`'s text search (which tries several search engines and
falls back if one is blocked), `ddgs`'s image search has exactly one
backend, so a soft anti-bot block there fails the whole search outright
rather than just returning fewer results -- retrying the same query after
a short wait is normal, not a sign the tool is broken.

When usage rights actually matter (the deck is going external, or the
user asks for something "royalty-free"/"commercially usable"),
`search_licensed_images(query, orientation="", min_width=0, min_height=0,
required_terms="", max_results=5)` searches Openverse and Wikimedia
Commons instead -- two real, zero-API-key providers, no signup needed.
Every result is already classified into exactly one of two tiers:
`"no-attribution"` (CC0/Public Domain -- use freely, no credit needed) or
`"attribution-required"` (CC BY/CC BY-SA -- comes with a ready-made
`attribution_text` string for an on-slide credit line); anything else
(CC BY-NC, CC BY-ND, all-rights-reserved, unknown) is rejected outright
and never returned. Results are ranked by query relevance first, then
license tier/orientation/size as tie-breakers, and deduplicated across
both providers. `download_image` is still how you actually fetch the
chosen result -- `search_licensed_images` only returns candidate
metadata, same division of labor as `search_images`. Pass a
comma-separated `required_terms` (e.g. `"eiffel tower,paris"`, or
`"Jiefangbei|Liberation Monument"` for either-satisfies alternatives
within one entry) when a specific name/landmark/entity must be exactly
right rather than just visually plausible.

If text over a background image ends up hard to read,
`add_pptx_scrim(path, slide, opacity=0.35, color="000000")` adds a
full-slide semi-transparent color layer between the background and the
other shapes -- `python-pptx` has no transparency API at all, so this is
hand-appended OOXML the same way the background-image crop is. Not
applied automatically: pick `color` to contrast with the slide's actual
text color (a light scrim under dark text, a dark scrim under light text
-- a dark scrim under dark text barely helps, confirmed by actually
rendering both and looking), and only add it when text genuinely needs
it, not as a default on every background image -- `review_work` (see
[Subagent delegation and review](#subagent-delegation-and-review) below)
is a good way to find out whether it's actually needed.

**Optional: install LibreOffice for overflow warnings and preview thumbnails.**
If `soffice` is on `PATH`, `write_pptx` renders the deck to PDF in the
background and flags any text that overflows its slide bounds in the
response's `overflow_warnings` -- the single most common visual defect in
generated decks. `write_docx`/`write_pdf`/`write_xlsx`/`write_pptx` and
`add_xlsx_chart`/`add_pptx_chart` also each render a one-page PNG thumbnail
(the first slide for pptx, the affected sheet for xlsx) that shows up as an
inline image in the chat UI right under the tool call, so you can see what
got generated -- chart included -- without opening the file yourself. Both
are best-effort: without LibreOffice installed, every tool still works
exactly the same -- `write_pptx` just returns `overflow_warnings: []` with a
`qa_skipped_reason`, and every write/chart tool's `preview_path` comes back
`null` with a `preview_skipped_reason`
explaining why. LibreOffice is a real (non-`pip install`) system dependency
(`apt install libreoffice` / `brew install libreoffice` / etc.) -- worth
having for nicer decks and previews, not required for the tools to function.
Note also that `python-pptx` itself hasn't seen a release since August 2024;
it's still the standard library for this, just not as actively maintained as
the libraries `docx`/`pdf`/`xlsx` support relies on (see `ARCHITECTURE.md`).

`render_pptx_preview(path)` is the multi-slide counterpart: LibreOffice's own
`--convert-to png` only ever emits page 1, so `write_pptx`'s automatic
thumbnail (and this tool, without its second dependency) can only ever show
the first slide -- not enough to catch a deck where the title slide looks
fine but the rest don't. With `poppler-utils` also installed (`apt install
poppler-utils` / `brew install poppler`; provides `pdftoppm`),
`render_pptx_preview` renders every slide (capped at `max_slides`, default
8) so `review_work` can check the whole deck, not just the first slide --
this is what a deck built with `run_node_script` needs for any real visual
QA, since that tool returns no preview of its own. Same graceful-degradation
contract as everything else here: missing either dependency just means
`preview_paths` comes back empty with a `preview_skipped_reason`.

## Running scripts

`run_python_script(script, description, timeout=120.0)` lets the
Coordinator write and run a real Python script for anything the built-in
document/spreadsheet/presentation tools can't express in one call --
processing thousands of rows, a multi-step transformation, custom logic.
It's the highest-risk tool in coscribe (`risk_level="high"`, always
requires approval) and the *only* one with no sandbox around it: **there is
no `WorkspaceScope` path check here** -- a script can read or write any
file the coscribe process's own OS user can reach, and make any
network call. Approving a script call is full trust, not "runs in an
isolated box" -- the same real-world posture Claude Code itself has on a
native Windows host with no WSL2 configured (its own docs: "This option
does not support native Windows. On Windows hosts, use WSL2 or one of the
container or VM approaches"). The approval prompt shows the complete
script text plus the model's own one-line description of what it does --
read it before approving.

There's deliberately no import/library allowlist either -- like Claude
Code's own Bash tool, any such restriction would be trivially bypassable
(`__import__`, `subprocess.run(["pip", "install", ...])`) and would only
give a false sense of security. What *is* controlled is which Python
environment the script runs in: a dedicated virtual environment, kept
completely separate from coscribe's own runtime dependencies, so a
script installing a package can never destabilize the app itself. That
environment ships pre-seeded with `openpyxl`/`python-docx`/`python-pptx`/
`pandas`/`pdfplumber` -- the same libraries this package's own tools use --
so a script can read/write the same file formats without extra setup.
Anything else (numpy, requests, whatever a task needs) is added from
**Settings -> Environment**: a plain add/remove list of installed
packages, no scripts or command-line knowledge required, deliberately
simpler than Claude Code's own freeform "Setup script" field since
coscribe is aimed at non-technical office users.

There is currently no control over what network hosts a script can reach
(Claude Code's own cloud environments offer a None/Trusted/Full/Custom
dropdown for this, but that's enforced at the container/network-policy
level -- coscribe is a local desktop app with no equivalent
infrastructure to enforce it against). Full script visibility at approval
time is the only safeguard for now.

### `run_node_script` -- real pptxgenjs decks

`write_pptx`'s markdown-to-fixed-layout model (Title-and-Content/
Title-Only) can't express custom shapes, multi-column layouts, precise
positioning, or icons -- the things that make a deck look genuinely
designed rather than templated. `run_node_script(script, description,
timeout=120.0)` is the fix: the same `risk_level="high"`, always-approve,
no-sandbox posture as `run_python_script` (see above -- everything in that
section about approval being the only safeguard applies here identically),
just for Node.js instead of Python, with `pptxgenjs` (the same library
Anthropic's own pptx Skill is built on) pre-installed and ready to
`require("pptxgenjs")`.

**Not just a fallback for special cases** -- confirmed live (see
`scripts/verify_pptx_quality.py`): with the pptx Skill enabled but its
guidance framed as "use run_node_script only when write_pptx's layouts
genuinely aren't enough," the model defaulted straight to `write_pptx` for
a whole "professional pitch deck" request and produced exactly the
bare-template look users kept reporting. `coordinator.py`'s own
instructions now explicitly tell the model to `load_skill("pptx")` before
writing any deck when that skill is available, and the skill itself
frames `write_pptx` as the rough-draft option and `run_node_script` as the
default for anything the user will actually look at -- re-verified live
that this actually changes behavior (`load_skill` + `run_node_script` get
called, and the resulting file is built from real composed shapes, not
stock placeholder layouts).

**Optional: install Node.js** (https://nodejs.org) for this tool to work
at all -- unlike Python (coscribe's own runtime, always present),
Node.js/npm are a genuinely optional system dependency, the same way
LibreOffice is optional for preview thumbnails/overflow warnings above. A
call to `run_node_script` before Node.js is installed fails with a clear
message naming what's missing, rather than every other built-in tool
being affected -- `write_pptx` and everything else keep working without
it.

Isolation works differently here than the Python side's venv: Node has no
equivalent of "bake a fixed interpreter+site-packages path into one
binary," so packages installed via **Settings -> Environment**'s second
list (kept separate from this project's own `frontend/` build, not just
from coscribe's own runtime) are found via the `NODE_PATH` environment
variable rather than directory-tree `require()` resolution -- verified to
actually work (a script file and working directory both outside that
package directory can still `require("pptxgenjs")`) before this tool
shipped, not assumed from documentation.

### `run_background_script` -- scripts that run longer than the 600s cap

`run_python_script`/`run_node_script` both cap out at 600 seconds --
plenty for the document/spreadsheet work above, not enough for a big OCR
job, a long batch pipeline, or anything else genuinely long-running.
`run_background_script(language, script, description, timeout_seconds=1800)`
is the fire-and-forget counterpart: same no-sandbox posture and approval
prompt as the two synchronous tools (`language` is `"python"` or `"node"`,
running against those exact same dedicated environments), but it starts
the script and returns immediately with a `task_id` instead of waiting for
it to finish -- `timeout_seconds` can go up to 21600 (6 hours).

Modeled on [pi-background-tasks](https://github.com/earendil-works/pi)
rather than a live output stream: the script's combined stdout/stderr is
written to a durable log file as it runs, and `check_background_task
(task_id, tail_bytes=4000)` reads a bounded tail of it -- works whether
the task is still running (partial output so far) or already finished
(full output plus `exit_code`). `list_background_tasks()` lists this
conversation's own tasks; `kill_background_task(task_id)` stops one early.
To get told automatically once it's done instead of polling yourself,
combine it with `wake_on_task(task_id, reason)` (see "Sleep and wake"
above).

**Known limitation**: a background task's live process handle only exists
in the running `coscribe-web` process's own memory. If the server
restarts while a task is mid-run, its on-disk record is left stuck at
"running" -- nothing left to ever mark it finished, so a pending
`wake_on_task` for it never resolves either. Not fixed for now, same as
this app's other "the desktop app stays running for the length of the
task" assumptions elsewhere (e.g. selfwake's own open-tab-doesn't-live-
stream limitation).

## Web search

`web_search(query, max_results=5)` queries the live web through the
`ddgs` package (no API key required) -- for grounding (current events,
facts to verify, anything beyond training data), not document retrieval.
It's a separate thing from `search_files`/`search_pdf`, which only ever
look at files already in the workspace; use `web_search` when the answer
genuinely isn't something already on disk or already known. Returns a
list of `{"title", "url", "snippet"}` per result. Low risk, no approval
required -- it only reads from the public web, no side effects.

Uses `ddgs`'s own `"auto"` backend mode -- queries several free,
no-API-key engines (DuckDuckGo, Brave, Google, Mojeek, Startpage,
Wikipedia, Yahoo, Yandex) and returns whichever results come back, rather
than being pinned to one. This isn't just theoretical resilience: an
earlier version pinned to DuckDuckGo alone, and a real user hit
DuckDuckGo's own anti-bot response (an HTTP 202 with no real results)
returning an empty search for a live query -- switching to `"auto"`
means one engine's soft block no longer empties the whole search.

## Skills

Two sources, both parsed the same `SKILL.md` format (YAML frontmatter
`name`+`description`, required, followed by markdown instructions) — the
exact same convention [Claude Code's own
Skills](https://code.claude.com/docs/en/skills) use:

- **Built in**: three skills shipped with coscribe itself — *PPTX Slides*,
  *Excel Spreadsheets*, *Word Documents* — each a level deeper than the
  base instructions on design/formula/formatting conventions for that file
  type (color palettes, typography, the financial-model color convention,
  when to use `write_docx`'s TOC/comments/tracked-changes). No setup: they
  work out of the box for every install.
- **Local**: `COSCRIBE_SKILLS_DIR` (default `./skills`, auto-created) holds
  your own Skill subdirectories, optionally with `references/`/`assets/`/
  `scripts/` files alongside each `SKILL.md`. Drop a folder in and it's
  available next run; nothing to configure. A skill authored with Claude
  Code's `/skill-creator` works here too (only the `SKILL.md` itself
  transfers, not `/skill-creator`'s own Claude-Code-specific
  evaluation/benchmarking workflow), and coscribe has no code-execution
  tool yet, so a skill whose instructions depend on *running* a bundled
  script is only partially usable until that lands.

**Selecting which skills are active** is per-thread and freely
re-toggleable — the Settings → Skills tab lists every skill (built-in and
local) as a checkbox; more than one can be on at once, and toggling takes
effect immediately for that conversation, no restart. The three built-in
skills are **on by default** for a brand-new thread — no setup needed for a
new install to get better PPTX/Excel/Word output — while a local skill
(`COSCRIBE_SKILLS_DIR`) stays off until you turn it on, since there's no
way to know in advance whether an arbitrary local skill's guidance is
something you want applied by default. Being enabled only means a skill is
*offered*: the Coordinator still decides for itself whether a given turn
actually calls `load_skill(name)`, the same judgment call as before. A
skill toggle only changes which design guidance is on offer — it never
narrows the *tool* set — aimed at a non-technical user who'd find
authoring a whole config file too unfamiliar, but still wants to say
"I'm making a PowerPoint today" and get better slides.

Every enabled skill's name and description are always visible to the
Coordinator (so it knows what's on offer and when to reach for one); the
full body is loaded on demand via `load_skill(name)`, and
`read_skill_file(name, path)` reads any bundled reference/asset file the
instructions point to. Both are low risk, no approval needed — same tier as
`read_file`. Whether to actually call `load_skill` (and, later,
`review_work`) is left to the model's judgment, the same "instructed, not
enforced" philosophy the [Subagent delegation and
review](#subagent-delegation-and-review) section below explains for
`review_work` itself — a skill toggle widens what's on offer, it doesn't
force a review step.

## Memory

The Coordinator can call `remember(fact)` to append one short, durable fact
to `COSCRIBE_MEMORY_PATH` (default `./MEMORY.md`, auto-created,
gitignored by default -- track it yourself if you want shared/team memory).
Whatever's in that file is read back into the system instructions of every
future session, so a fact remembered in one thread is visible in all
others. It's meant to stay a short, curated list (a preference, a stable
detail) -- not a running log, and not addressed by `/compact` (which only
ever touches a single thread's conversation history, a much faster-growing
and more urgent problem than a short memory file).

## Subagent delegation and review

The Coordinator has two more tools, built on runtime_lg's own
`build_spawn_agent_tool`/`build_review_work_tool` (a fresh
`build_langgraph_agent(...)` invocation under the hood -- the same
mechanism the Coordinator itself runs on):

- `spawn_agent(instructions, prompt, tool_names="")` -- delegates a
  self-contained sub-task to an independent agent with its own context
  window and a tool whitelist (comma-separated names, e.g.
  `"read_file,search_files"`) drawn from the Coordinator's own tools. Only
  the sub-agent's final summary re-enters the conversation; its own
  intermediate steps and tool calls never touch the parent's context or get
  persisted to disk (no `--thread`-resumable state for a sub-agent run).
- `review_work(original_request, summary_of_work, file_path="", preview_name="")`
  -- runs a fixed Reviewer persona (a fresh agent that didn't do the work)
  to check a result against what was actually asked, before the Coordinator
  finalizes or acts on it. Unlike `spawn_agent`, the Reviewer isn't given a
  free tool whitelist -- it always gets the same fixed, read-only toolset
  (`read_docx`/`read_pdf`/`search_pdf`/`read_xlsx`/`read_pptx` today, every
  tool with `category="documents"` and no approval requirement), so passing
  `file_path` lets it independently read the actual file rather than just
  trusting the Coordinator's own summary. Passing `preview_name` (a write
  tool's own `preview_path`, when it returned one) goes further: the
  Reviewer is shown the actual rendered image, not a description of it --
  real vision, not just text. This is genuinely useful, not just plumbing:
  live-tested against a real full-bleed background-image slide, it
  independently named the exact legibility problem, suggested the right
  fix (a semi-transparent overlay), and separately caught a stray bullet
  point and an alignment inconsistency nobody had pointed out to it.
  **Cross-provider verification** (a follow-up pass to the initial
  Gemini-only test above): `review_work`'s image content block is the
  standard LangChain OpenAI-style shape (`{"type": "image_url",
  "image_url": {"url": "data:image/png;base64,..."}}`). Reading the
  installed `langchain-anthropic`/`langchain-google-genai`/
  `langchain-openai` source confirms all three convert it correctly into
  each provider's native multimodal format (`langchain_anthropic`'s
  `_format_image()` regex-matches this exact data-URI shape and converts
  it to Anthropic's native `base64` block). Live-verified for two of the
  three wired code paths: Gemini (above), and the `ChatOpenAI` class
  shared by both the native `openai:` provider and any custom
  OpenAI-compatible provider (tested against a real vision model on a
  configured custom provider -- the model correctly engaged with the
  image's content, not just echoed text). The native Anthropic path has
  no live Anthropic API key available in this environment, but got an
  indirect check: routing `ChatAnthropic` at DeepSeek's
  Claude-SDK-compatible endpoint (same `ChatAnthropic` class, same
  `_format_image` conversion code, different `base_url`) confirmed the
  image block reaches the model layer intact -- it correctly answered
  "yes" to "is an image attached" only when one actually was sent -- but
  the described content was fabricated, consistent with the model behind
  that endpoint not having real vision capability rather than a
  conversion bug. Net: the format-conversion code itself is verified
  correct; whether a genuinely vision-capable Claude model gives
  `review_work` useful judgment on a real screenshot is still unconfirmed,
  for lack of a real Anthropic API key to test against.

`ARCHITECTURE.md` describes "Specialist agents" as domain-divided experts
with tool sets decided by configured MCP/Skills -- in practice that's not a
fixed roster of named agent types, it's `spawn_agent` used ad hoc with
whatever instructions and tool subset fit the sub-task at hand. The Reviewer
is the one genuinely new, dedicated agent role.

A sub-agent's own tool calls still go through the same approval/Plan-Mode/
Hooks gating as the Coordinator's -- delegating to a sub-agent is not a way
around them, including for modes toggled *after* the sub-agent tools were
set up (mid-session `/plan`/`/accept-edits` changes still apply to calls a
sub-agent makes afterwards). A sub-agent can never be granted `spawn_agent`
or `review_work` itself, so delegation can't recurse.

## Workflows

Once you've worked out a repeating task through ordinary conversation, save
it as a named **workflow** so you can re-run it later (see [Scheduled /
unattended runs](#scheduled--unattended-runs) above for the actual
unattended-execution story -- there's no scheduler built in yet, workflows
only run when asked). *Creating* a workflow is always an explicit command
you type yourself, never something the model decides to do on its own --
early on, a model-callable `save_workflow` tool existed, but it let the
Coordinator persist a workflow (and, by default, capture a long-running
thread's *entire* tool-call history) on its own judgment, which could bake
an earlier, unrelated conversation's actions into a workflow a later,
different conversation would go on to replay. Recorded/saved workflows are
global by name and outlive `/clear` by design, same as memory/tasks -- so
that decision is now yours alone:

- `/startworkflow` then `/endworkflow <name> [summary]` -- **chain mode**.
  Everything you do between the two commands (only that -- not the rest of
  the thread's history) becomes a fixed, ordered list of tool calls, replayed
  exactly as recorded on every future run, with **zero model calls** during
  the replay itself -- predictable, and the reason to prefer it whenever the
  steps themselves won't need to change. A second `/startworkflow` before
  `/endworkflow` discards the in-progress recording and starts over;
  `/clear` also cancels one if it's still open.
- `/saveworkflow <name>` (without a prior `/startworkflow`) -- the model
  judges what's worth saving from the conversation so far and how, via a
  one-off, tool-less curator call (same division of labor `/compact` uses --
  the decision to save is still yours, only *what exactly* gets captured is
  model-judged): a clean, deterministic sequence of tool calls becomes a
  **chain**-mode workflow exactly as `/startworkflow`/`/endworkflow` would
  have recorded it, had you marked the range yourself; something more
  exploratory or judgment-dependent becomes an **agent**-mode workflow (a
  natural-language summary, replayed by handing it to a fresh agent loop --
  the same mechanism `spawn_agent` uses -- that decides what to do fresh
  every time). If the conversation covers multiple unrelated tasks or is too
  vague to tell, it asks a clarifying question instead of guessing (up to a
  few rounds, then gives up and points you at `/startworkflow` for precise
  control). Either way, it always shows a preview -- the chain's exact steps,
  or the agent-mode summary text -- and waits for you to confirm before
  anything is actually saved.
- `/runworkflow <name>` -- run a saved workflow now, directly (no model
  round-trip to decide to call it -- same reasoning as gating creation).
  Goes through the same approval gating as everything else: in accept-edits
  mode it just runs; in normal mode, each replayed chain step still prompts
  individually if it's risky, exactly as if you'd typed each action
  yourself. The web UI's top-left menu (see below) offers this as a click
  instead of typing it.
- `list_workflows()` / `get_workflow(name)` / `delete_workflow(name)` --
  still reached through conversation, since they're read-only/low-risk
  (`delete_workflow` requires approval). `list_recorded_steps()` lists the
  current thread's recorded tool calls with their indices, if it helps
  explain what's been done so far.
- If a chain-mode run fails partway through, its result names which step
  failed; rather than blindly redoing the whole chain (earlier steps like a
  login or navigation may not be safe to repeat), ask to resume from that
  step (`run_workflow`'s `resume_from_step` argument) once whatever caused
  the failure is fixed.
- Chain mode's "zero model calls during replay" also means replay can't
  *notice* a step that ran without error but didn't actually do what it
  looks like it did (a browser navigation against a stale tab that silently
  no-ops, say) -- so `/endworkflow` makes one extra, one-off model call of
  its own, invisible to you, that looks at each step's actual recorded
  result and decides whether it contains a short, distinctive marker of
  real success worth checking for next time. Nothing to configure: most
  steps get no check at all, and a replay only fails loud on a mismatch
  instead of silently continuing on a false premise.

The web UI's header has a session/workflow menu (click the "coscribe"
label, top left) alongside Settings > Workflows:

- **New Session** / **Recent Session** -- start a fresh thread, or switch to
  one you were in before.
- **New Workflow** -- pick one of your saved workflows and run it now (the
  `/runworkflow` command above, triggered by a click).
- **Current Workflow** -- if a run is in flight, its live per-step progress
  and a Stop button, right in the menu; a small dot on the menu's own icon
  shows a run is active even when the menu is closed or you're on a
  different thread.
- **Recent Workflow** -- recent run records; click one to open Settings >
  Workflows for full detail.

Settings > Workflows itself has the saved-workflow list and a **Recent
runs** section below it, each entry showing status and, for chain mode,
live per-step progress as it happens -- pending/running/done/failed while a
run is still in flight, not just the final result once the whole tool call
returns. Every card has a delete button (🗑) -- for a saved workflow or a
run record, backed by `DELETE /api/workflows/{name}` and
`DELETE /api/workflow-runs/{run_id}` respectively. The runs list itself is
backed by `GET /api/workflow-runs` (recent runs) and
`GET /api/workflow-runs/{run_id}` (one run's full detail); runs are
persisted one JSON file per run under `.coscribe/state/workflow_runs/`,
separate from the saved workflow definitions themselves, which are stored
one JSON file per name under `.coscribe/state/workflows/`.

## MCP servers

Set `COSCRIBE_MCP_CONFIG_PATH` in `.env` to a JSON file in the same
`mcpServers` shape as Claude Desktop's config to give the Coordinator tools
from external MCP servers (document processing, browser automation, etc. —
see `../ARCHITECTURE.md`):

```json
{
  "mcpServers": {
    "fetch": { "command": "uvx", "args": ["mcp-server-fetch"] },
    "custom-db": {
      "command": "npx",
      "args": ["my-mcp-server"],
      "env": { "API_KEY": "..." }
    }
  }
}
```

Same schema Claude Desktop, Claude Code, and Cursor all use for local
(stdio) MCP servers -- `command`/`args`/`env` per server, keyed by name --
since it's the shape Anthropic's own MCP spec examples popularized, not
something coscribe invented. The web UI's Connectors > Custom tab
exposes all three fields (`env` as one `KEY=value` pair per line, not
space-split like `args`, since env values routinely contain spaces);
`env` values are never echoed back to the browser after saving, only
masked, same as provider API keys elsewhere in this panel. Remote
(HTTP/SSE + OAuth) MCP servers aren't supported yet -- only local
stdio servers.

Tool names are always prefixed with the server name to avoid colliding with
the built-in file tools, and every MCP tool requires approval before running
(same as `write_file`) — MCP servers are less trusted than the sandboxed
built-ins. A server that fails to connect at startup (or the config file
itself being missing) is skipped with a warning rather than blocking the
others or crashing the process.

In the web UI, adding, removing, or updating a connector (Connectors panel)
is saved immediately and `coscribe-web` never needs restarting for
this — but it only takes effect for the *next* new conversation, not
already-open ones (each conversation's tools are fixed once it starts;
reload the page or open a new thread to pick up the change). The
`coscribe` CLI still picks up MCP config once per invocation, same as
any other setting, since it isn't a long-running server multiple sessions
share.

The built-in catalog's `playwright` entry pins an exact
`@playwright/mcp` version rather than `@latest` — an unpinned tag makes
`npx` hit the npm registry on every single startup just to check for a
newer version, which is most of the extra delay adding it used to add.
Any configured connector whose args contain a `package@version` token
(scoped or not) gets a "Check for updates" link in its Configured card,
which looks up the real latest version on npm and, if newer, updates that
one arg in place and reconnects live — no need to remove and re-add.

The built-in catalog no longer includes `git`/`github` entries (and the
GitHub OAuth Device Flow sign-in button that used to come with the
`github` one) -- coscribe is a general file/task automation assistant,
not a developer coding tool, so a git/GitHub connector isn't relevant to
its target audience by default. Either is still addable by hand via the
Custom tab (name + command + args) for anyone who specifically wants one.

`tools/mcp.py` also monkeypatches a real bug in aisuite 0.1.14 (the latest
published release) on import: `MCPToolWrapper._create_signature` builds a
Python function signature straight from an MCP tool's JSON Schema property
order, which breaks (`ValueError: non-default argument follows default
argument`) the moment an optional property is listed before a required one
-- valid JSON Schema, and exactly what most of the official playwright-mcp
server's interaction tools (`browser_click`, `browser_type`, `browser_hover`,
...) do. The patch only reorders parameters (required first) before handing
them to `inspect.Signature`; no semantic change, since tool calls are always
keyword-based. Verified live: without the patch, `connect_mcp_tools` against
a real playwright-mcp server returns zero tools; with it, all 24 connect and
the Coordinator can drive real tool calls through them.

The web UI's **+** → **Add connectors** panel edits this same file for
you (see the Web UI section) instead of you writing this JSON by hand —
functionally identical either way.

## Custom LLM providers

Beyond the built-in `anthropic:`/`openai:`/`gemini:` prefixes, any
OpenAI-compatible API can be registered as its own model prefix by setting
`COSCRIBE_PROVIDERS_CONFIG_PATH` in `.env` to a JSON file:

```json
{
  "providers": {
    "deepseek": { "base_url": "https://api.deepseek.com/v1", "api_key": "sk-..." }
  }
}
```

Once registered, use it like any other model, e.g.
`COSCRIBE_DEFAULT_MODEL=deepseek:deepseek-v4-flash`. No provider-specific
code is needed: `LLMClient._resolve_provider` hands the `base_url`/`api_key`
straight to aisuite's own `OpenaiProvider`, which forwards them to the real
`openai` SDK's client constructor -- that's all an OpenAI-compatible
endpoint needs. `default_model` in the file is just a UI convenience for
pre-filling the "add custom provider" form; it isn't read by the backend.

The web UI's **Settings → Providers** tab (see the Web UI section) edits
this same file for you, with DeepSeek, Kimi (Moonshot), and Zhipu GLM as
one-click catalog entries plus a form for any other OpenAI-compatible API
-- those three are convenient examples, not an exhaustive list. Adding or
removing a provider here is saved immediately, no restart needed -- but,
same as connecting an MCP server (see above), it only takes effect for the
next new conversation, not already-open ones. Anthropic,
OpenAI, and Gemini (the three built-in providers, configured by API key
rather than base URL) also appear in this tab once their key is set in
**Settings → API Keys**, alongside an optional "default model" field per
key -- that pairing is what lets the composer's model pill (below) offer a
one-click switch to that provider without you having to remember or type
its exact model id.

## Secrets storage

Every secret coscribe itself writes -- built-in provider API keys (`.env`),
custom providers' `api_key` (`providers.json`, above), and MCP server
`env`/header values including a connected GitHub token (`mcp.json`, see
[MCP servers](#mcp-servers)) -- goes through the same two-layer hardening:

1. The file itself is always written with owner-only permissions (`0o600`),
   regardless of the layer below.
2. If a working OS keychain is available (macOS Keychain, Windows
   Credential Locker, or a Linux Secret Service/kwallet daemon, via the
   `keyring` package), the actual secret *value* lives there instead --
   the file on disk holds only a small reference, not the real key.
   If no keychain backend is available (common on a headless Linux
   server/container -- no Secret Service running), storage falls back to
   the permission-hardened plaintext file above; nothing fails, saving a
   key/token always works either way.

This degrades the same way LibreOffice-optional features do elsewhere in
this project: best effort, never the reason an operation fails. One real
tradeoff worth knowing: a keychain-backed secret doesn't travel with a
copied `.env`/`state_dir` the way a plaintext one does -- if you move
coscribe's config to a different machine (or the keychain becomes
unavailable for any other reason) and it can't read a secret back, it
tells you plainly rather than silently breaking; just re-enter that key/
token via Settings.

## Hooks

Set `COSCRIBE_HOOKS_CONFIG_PATH` in `.env` to a JSON file listing shell
commands per lifecycle event:

```json
{
  "PreToolUse": ["./hooks/audit.sh"],
  "PostToolUse": ["./hooks/log.sh"],
  "SessionStart": ["./hooks/notify.sh"]
}
```

Each command receives a JSON payload on stdin describing the event
(`event`, `tool_name`, `arguments`, `agent_name`, plus `result` for
PostToolUse or `thread_id` for SessionStart) and is interpreted by its exit
code:

- **PreToolUse** runs before *every* tool call, regardless of risk level or
  Plan Mode/Accept Edits Mode — it's a governance/audit layer independent of
  the built-in risk classification. A nonzero exit denies the call outright
  (no approval prompt reached); stderr becomes the reason shown to the model,
  the same way a user-denied or Plan-Mode-denied call already surfaces.
- **PostToolUse** runs after a tool call succeeds, for logging or side
  effects. It can't undo the action; a failing hook is only logged as a
  warning.
- **SessionStart** runs once when the CLI starts. A failing hook is logged
  as a warning rather than blocking startup, same tolerance as a broken MCP
  server or malformed skill.

Hooks run arbitrary shell commands with your own permissions — only point
this at configuration you trust, same trust model as an MCP server's
`command`/`args`.

## Web UI

The frontend is a React + TypeScript app in `frontend/` (a sibling
Node/TS project, not part of this Python package) -- see
`frontend/README.md` for its dev loop, architecture, and live-verified
feature-parity notes. `npm run build` there compiles straight into
`src/coscribe/web/static/`, which is what `coscribe-web` below
actually serves; that directory is a build artifact (gitignored), not
hand-edited source.

If [Setup](#setup) above already included the `web` extra and the
`npm run build` step, just run:

```bash
coscribe-web          # binds 127.0.0.1:8000 by default
```

If `coscribe-web` isn't on `PATH` (e.g. right after installing, or in some
Windows shells), `python -m coscribe.web.app` runs the identical entry
point without depending on `PATH` at all.

A local, single-user web UI covering the same ground as the CLI: a chat
pane, an approval prompt for anything requiring approval, and a live task
panel -- the three pieces `../ARCHITECTURE.md` calls out as the core UI.
Hooks configuration stays file-based, same as the CLI; MCP and Skills now
have a friendlier path too (see below), but hand-editing their config
files still works exactly as before.

`coscribe-web [--host HOST] [--port PORT]` starts a FastAPI process
that serves the frontend and a `/ws/<thread-id>` WebSocket per
conversation; opening `/` generates a thread id and puts it in the URL
(`?thread=...`), so reloading or revisiting that URL resumes the same
thread, same as `coscribe --thread <id>`. The mode pill (bottom-left of
the message box) covers Normal/Plan/Accept-Edits -- it just sends the same
`/plan`/`/accept-edits` text the CLI understands, no separate protocol.
The web layer (`src/coscribe/web/app.py` + `web/session.py`) runs
directly on runtime_lg -- `ChatSessionLG` compiles and drives a LangGraph
agent per thread, it isn't a bridge in front of a separate execution
engine.

Below the message box, a small bar shows how much of the model's context
window the current thread is using (updates after each reply) next to the
active model's name, shown as a pill (bottom-right of the message box).
The token count comes from the real model response's own `usage_metadata`
-- specifically, the *last* model response within a turn (a tool-calling
turn involves two or more separate responses; only the final one's own
total reflects the whole conversation's current size, since each
response's `input_tokens` already includes everything sent before it --
summing multiple responses together would double-count). Omitted entirely
(no event, bar stays in its default state) if the active model never
reports usage at all, rather than showing a fabricated number. The
max-size half of that bar resolves via `LLMClient.get_context_window` --
a real, live number from the provider when it offers one (currently just
Gemini, via `GeminiProvider.get_context_window`'s `client.models.get()`
call -- not a hardcoded guess), falling back to a rough, explicitly
unverified per-vendor estimate otherwise. Clicking the model pill opens a picker
listing every provider with a key/default model configured (built-in and
custom, from **Settings → Providers**/**API Keys**) plus a free-text
field for any other "provider:model" string -- picking one switches
*this thread's* model immediately, no restart, no reconnect. The switch
is per-thread and in-memory, same as the mode pill's Plan/Accept-Edits
toggle: it survives a page reload (restored from the session's live
state) but not a process restart, which resets every thread back to
`COSCRIBE_DEFAULT_MODEL`.

The **+** button next to the message box opens three things:

- **Slash commands** -- the same autocomplete list that also pops up
  automatically while typing `/` in the message box (fixed commands plus
  every loaded skill); typing a skill's name as `/<skill-name>` works
  exactly as it does in the CLI.
- **Add files or photos** -- images are attached as real multimodal image
  parts the model actually sees (not just a filename reference) -- see
  `ChatSessionLG.handle_user_message`'s `images` parameter. Anything else
  (a `.docx`, a `.pdf`, a plain text file, ...) is uploaded straight into
  the workspace via `POST /api/upload` (capped at 25MB, name collisions
  auto-renamed rather than overwritten) and referenced by path in your
  message, so the Coordinator reaches for `read_docx`/`read_pdf`/
  `read_file` on it like any other workspace file -- both kinds show up
  as removable chips above the message box while queued.
- **Add connectors** -- a small curated catalog (currently `playwright` for
  browser automation and `fetch` for reading web pages -- both plain local
  commands, no OAuth) plus a form for any custom MCP server, backed by
  `GET`/`POST /api/mcp/servers` and `DELETE /api/mcp/servers/{name}` in
  `web/app.py`. These are the same stdio MCP servers the CLI's
  `COSCRIBE_MCP_CONFIG_PATH` config file already supports (see below) --
  the panel just reads and writes that file instead of you hand-editing
  JSON, defaulting to `./mcp.json` if you haven't set one. Same
  restart-required caveat as the settings panel: MCP connections are only
  ever made once at process startup.
- **Providers settings tab** -- register any OpenAI-compatible API
  (DeepSeek, Kimi, GLM catalog one-click entries, or a custom form for any
  other) as a new model prefix, backed by `GET`/`POST /api/providers` and
  `DELETE /api/providers/{name}` in `web/app.py`. Reads and writes the same
  `providers.json` file the `COSCRIBE_PROVIDERS_CONFIG_PATH` setting
  points at (see [Custom LLM providers](#custom-llm-providers) above). API
  keys are masked in the configured list, same as the API Keys tab.
- **Tools settings tab** -- a read-only, grouped list of the built-in tools
  the Coordinator always has access to (file/document/spreadsheet/task/
  memory/subagent, plus Skill-loading tools if any Skills are configured),
  each with its description and a "requires approval" badge for medium-risk
  tools. Backed by `GET /api/tools`, which builds a real Coordinator agent
  (the same entrypoint `cli.py`/the WebSocket session use) and reads off its
  tool list, so it can never drift out of sync with what the agent actually
  has. Distinct from Connectors (external, MCP) and Providers (which LLMs).

The gear icon opens a settings panel that reads and writes `.env` directly
(`GET`/`POST /api/config` in `web/app.py`, using `python-dotenv`'s
`dotenv_values`/`set_key` so unrelated lines and comments are left alone).
Provider API keys are only ever shown masked (last 4 characters) and a
blank field on save means "leave it unchanged," so the real value never
round-trips through the browser. Nothing hot-reloads -- `Settings` and the
provider clients are all built once at process startup -- so saving shows
a "restart required" notice rather than silently doing nothing; that
matches editing `.env` by hand today, it's just safer to do from the UI.
Proxy variables (`HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY`) aren't in the
Settings panel, but `.env` works fine for them (see `.env.example`) --
`_prepare_env`'s `load_dotenv(..., override=True)` makes a `.env` value
win over a stale OS-level proxy already set, the same real corporate-
machine bug that fix was written for. Left out of the panel because a
masked/never-round-tripped text field would be a worse editing experience
for a plain URL than just opening `.env` directly, not because `.env`
doesn't work for it.

Binds to `127.0.0.1` by default; this is a local single-user tool, not
meant to be exposed beyond your own machine.

Replies stream token-by-token (both here and in the CLI) rather than
appearing all at once, for providers whose SDK supports it -- currently the
vendored Gemini provider. Providers without a native streaming method
(Anthropic/OpenAI via the published aisuite package, as of writing) fall
back to their normal single response, delivered as one chunk; nothing
breaks, replies just appear all at once for those providers until
streaming support is vendored for them too.

## Desktop app

`../office-agent-desktop/` is an Electron shell around this same web UI
-- not a second interface to maintain. It starts `coscribe-web` as a
supervised local subprocess ("sidecar") on a free port and opens a
window pointed directly at it -- see `office-agent-desktop/src/main/
index.ts`'s own module docs for the design. Browser mode (`coscribe-web`
+ a normal browser tab) keeps working completely independently.

This replaced an earlier Tauri shell at the Browser panel native-window
migration's Phase 4 cutover (see `../office-agent/ROADMAP.md`) -- this
public repo never carried that Tauri shell itself (deliberately left
out of the fork; the private `OICWS/project` repo keeps it as a
rollback net).

Two things only matter for the desktop build, not the browser one:
- **`_exit_when_orphaned` (`web/app.py`)**: when the shell sets
  `COSCRIBE_EXIT_WITH_PARENT=1`/`COSCRIBE_PARENT_PID`, the sidecar
  watches that PID and self-exits if it dies abruptly -- defense-in-depth
  alongside the shell's own kill-on-quit. A no-op otherwise.
- **Per-user app-data paths**: `workspace_root`/`state_dir`/`skills_dir`/
  `memory_path` default to paths relative to the process's
  cwd (correct for a terminal-launched server, meaningless for a
  double-clicked app with no natural project directory) -- the desktop
  shell sets each as an explicit `COSCRIBE_*` env var pointed at a
  real per-user directory instead
  (`~/Library/Application Support/coscribe` on macOS,
  `%APPDATA%\coscribe` on Windows). `.env` loading
  (`cli.py`'s `_dotenv_path`) falls back to the same directory whenever no
  `./.env` already exists in cwd, so the Settings panel's "paste your API
  key" flow works identically in both modes.

Building/running the desktop app needs its own toolchain (Node only, no
Rust) and its own PyInstaller-frozen copy of `coscribe-web` (**onedir**,
never onefile -- see `coscribe/packaging/coscribe-server.spec`'s
own docstring for the measured startup-latency reason, directly
continuing this project's own import-time work above). See
`office-agent-desktop/`'s own docs for the exact build steps; the short
version is `office-agent-desktop/scripts/stage-sidecar.sh` (builds the
PyInstaller binary and stages it into `resources/sidecar/`) then
`npm start` / `npm run dist:portable` / `npm run dist:nsis` from
`office-agent-desktop/`.

## Test

```bash
pytest
ruff check src tests
mypy src
```

Running the full suite (including `tests/test_web.py`) needs the `web`
extra installed too: `pip install -e ".[dev,web]"`.

Tests do not require an API key or network access — they exercise
configuration loading and tool wiring directly (including `cli.py` and the
web backend, via `typer.testing.CliRunner`/`fastapi.testclient.TestClient`
with a fake LLM client). The actual LLM round-trip needs a real key and is
not covered by automated tests; run it manually to verify end-to-end
behavior.

CI (`.github/workflows/office-agent-ci.yml`) runs all three on every push/PR
touching `coscribe/`.
