# coscribe roadmap

A short index, not a log. What is open now is in [`docs/plan.md`](../docs/plan.md).
What shipped since 2026-10-08 is one file each in [`docs/log/`](../docs/log/).
Why things are the way they are: [`docs/decisions/`](../docs/decisions/) and
[`ARCHITECTURE.md`](../ARCHITECTURE.md). The full history up to phase 8di (8.9k
lines, frozen) is [`docs/history/roadmap-through-2026-10-08.md`](../docs/history/roadmap-through-2026-10-08.md):
search it (`grep -n "^## Phase 8ax"`), don't read it whole. Do not append new
entries to this file.

**Standing constraint: Windows only.** The desktop app targets Windows; that is the
platform that is verified. The macOS code paths in `office-agent-desktop` are left
in place but are not an active target: don't extend or verify them.

## What was built, by phase

Phase ids are the ones code comments and docs use. Each line names the phase ids to
look up in the archive.

**Foundations (0 to 7, 2026-08).** 0 quick wins · 1 persona layer (removed in 8r) ·
2 session history · 3 nav menu, live status, delete · 4 unattended-safe risk tiers
and `sleep_until` · 4b scheduled tasks · 5 connector OAuth and Slack (paused, then
dropped) · 6 remote MCP OAuth (realized in 8cx) · 7 hardening ideas from
`openai/codex`.

**Interaction and polish (8 to 8o).** 8 `ask_user_question` · 8b, 8m, 8n, 8o,
8z visual and UX polish · 8c Skill Creator · 8d, 8f surveys of other agents ·
8e `/slug` and `/saveskill` · 8g, 8h desktop tray and background notifications ·
8i CI repaired · 8j, 8k approval cards preview the change · 8l Settings redesign.

**Connectors, scripts, desktop basics (8p to 8w).** 8p Slack and 8q Office 365 as
curated connectors (later replaced by 8cx) · 8r personas removed · 8s live
elapsed time and tokens · 8t background scripts and `wake_on_task` · 8u Settings
no longer blank after one failed fetch · 8v native folder picker · 8w workspace
picker is opt-in.

**Browser panel (8x to 8ak, 8bm to 8bw).** 8x live embedded browser · 8aa
keyboard and IME · 8ab to 8ah migration from Tauri to Electron with
`WebContentsView` · 8ac, 8ad, 8ak mouse, viewport and sharpness · 8y first-run key
setup · 8ai startup latency · 8aj corporate proxy · 8bm the AI drives the browser
in tabs you watch, site permissions · 8bs to 8bw dialogs, uploads, downloads and
waits that survive a replay.

**Documents (8al to 8au).** 8al click a shape to target it · 8ar to 8as DOCX
validation and tracked changes · 8at to 8au XLSX gaps and native XLOOKUP/XMATCH ·
the PPTX details are in `PPTX_DESIGN.md`.

**UI redesigns (8am, 8an, 8bi to 8bo, 8bq, 8br, 8cf, 8ch).** Nav rail, attachments,
Settings after Claude's, Claude blue, one top bar, session groups and status.

**Sub-agents and context (8ao to 8ap, 8bc, 8be, 8bp, 8bq, 8cm, 8cz).** Sub Agents
panel, context breakdown, folders per conversation, sub-agents that report back
and run on a budget, review rounds capped in code, the running count.

**Tool cost (8aq, 8cj to 8cl, 8cu).** Deferred tool loading, the prompt cache kept
across conversations, the tool list fixed for the whole conversation
([decision 0001](../docs/decisions/0001-fixed-cached-tool-list.md)).

**Scheduled work and workflows (8av to 8bb, 8bd, 8bf, 8bx to 8cc).** A workflow is a
scheduled task, one conversation per run, orchestrated workflows with branches,
loops and chat-built drafts, the assistant tests and repairs them, run pages and
unattended notifications.

**Permissions (8aw, 8bg, 8bh, 8cd, 8ce).** Approval tiers, the four permission modes,
Auto by default, every tool call answered before the model sees it, one folder rule
for scripts and workflows.

**Repository (8cb).** One public branch, identical repos, old material removed.

**Real-usage hunts (8cg, 8ci).** A production-scenario bug hunt and a cross-format
quarterly review run live.

**Code module (8cn to 8cw, 8cy, 8da).** Codex run as a second runtime, downloaded
on demand, driven over its app-server protocol, folded into the chat, with its own
approvals ([decision 0005](../docs/decisions/0005-code-is-a-second-runtime.md)).

**Connectors and skills (8cx, 8db, 8dc, 8dd, 8df).** Hosted-only catalog that signs
in through the browser, Yours and Discover pages
([decision 0004](../docs/decisions/0004-connectors-sign-in-once-in-the-browser.md)),
71 role skills and plugin pages you can read before adding, guided setup for
services that need a registered app, a connector call can no longer hang a turn.
(Two entries are numbered 8dc: a sub-agent task id check and the skills plugin pages.)

**Chat details (8cp, 8cr, 8cs, 8de).** Tool-run headers counted by kind of work,
`ask_user_question` with several questions, notes added while a reply is being
worked on, attachments as file cards, softer question cards.

**Secrets (8dg, 8dh).** Global secrets in the keychain, the Secrets page, per-session
environments ([decision 0003](../docs/decisions/0003-secrets-promise.md)).

**Repository health (8di).** Package dependencies are checked
([decision 0006](../docs/decisions/0006-changes-reach-main-through-pull-requests.md)).

## Open items carried over from the history

The current open work is in `docs/plan.md`. These older ones were still open when
the history was archived:

- A real multi-hour unattended run (`sleep_until` waking and finishing) was only
  verified with a fake model and simulated clocks (phase 4).
- Screenshots the model can request itself, for vision-capable models (8bm).
- `image_search.py`: zero-key image sourcing from Openverse and Wikimedia (flagged in
  the PPTX notes, not acted on).
- A terminal UI: build it only once the WebSocket protocol has settled.

Superseded and no longer open: the persona picker test (personas were removed in
8r), Slack as an approval channel (dropped), the Asana placeholder (replaced by the
hosted catalog), remote-server OAuth (done in 8cx), the Playwright-MCP stop problem
(the connector was removed in 8bm), the prompt-cache and startup-latency items (done
in 8cj to 8cl, 8ai, 8cu), the two-runtimes direction (realized by the code module).

## Not doing, and why

- **A hosted component (`cloud.py`)**: conflicts with a local-first product.
- **Voice input**: a different modality, out of scope.
- **A public "anyone publishes, anyone installs" plugin marketplace**: hooks and MCP
  servers run with the user's full privileges and there is no sandbox
  ([decision 0002](../docs/decisions/0002-approvals-not-a-sandbox.md)); a
  marketplace would turn "your own config" into "trust a stranger's code". The skill
  catalog is the narrow, safe form of it: a curated list pinned to commits and
  checked by hash, with no code that runs without an approval.
- **A real sandbox for scripts**: not scheduled. If it is ever built, give
  `run_python_script` and `run_node_script` one swappable execution interface first
  instead of calling `subprocess` inline.
- **Hooks that users write or browse**: hooks stay internal plumbing; they run
  unapproved on every matching event.
