# coscribe Architecture Roadmap

Working plan for shifting Coscribe from "single local agent, reactive
chat only" toward "multi-persona, unattended-capable, channel-integrated,"
informed by a comparative read of [andrewyng/openworker](https://github.com/andrewyng/openworker)'s
architecture (persona system, `selfwake.py` suspend/resume, `risk.py`'s
risk tiering, Slack-as-approval-channel, MCP OAuth, TUI). ("Multi-persona"
is this plan's original framing, kept here as-is for history; the persona
system itself was removed in full -- see Phase 1 below.)

Core identity stays the same: single Coordinator + dynamic `spawn_agent`
delegation, local-first, no code-execution tool. Everything below is a
layer added on top, not a rewrite.

**Standing constraint -- Windows only.** The desktop app
(`office-agent-desktop`) targets Windows; that's the platform actually
being verified/prioritized, not macOS or Linux. Not previously written
down anywhere in this repo -- a real gap, not a deliberate omission --
so this note exists specifically so it doesn't get silently dropped
again. `office-agent-desktop`'s own macOS code paths (`.dmg`/`.icns`
bundling, `Library/Application Support` app-data path, `#[cfg(target_os
= "macos")]` branches in `lib.rs`) are left in place as-is for now
(a documentation-only fix, not a code cleanup pass) -- don't read their
presence as macOS being an active target, and don't spend new effort
verifying or extending them; a future pass can strip them out for real
if that's ever worth doing.

**Sequencing note (revised)**: backend/foundation stays the priority. TUI
is a real intention but deliberately *not* a numbered phase right now --
parked at the bottom, revisited once the items below have landed and
there's a reason to prioritize a new surface over more foundation.
Production packaging was in that same bucket originally but has since
shipped ahead of schedule (see the "Later" section below) -- backend
work continues regardless.

**Sequencing note (2026-08-26)**: Phases 5 and 6 (connector OAuth UX +
Slack/Asana, remote MCP OAuth) are explicitly paused, not dropped --
both are MCP/connector-shaped work, and the call was made to focus on
coscribe's actual core (the document/spreadsheet/presentation tools
real usage runs through) instead for now, prompted partly by this same
session's own live PPTX-editing failure (a weak model mishandling
existing tools, not a missing connector) being a much more direct
signal of where the product's real gaps are. Revisit Phase 5/6 when
there's a concrete reason to prioritize a new integration surface again.

**How this doc is used**: one phase (or sub-item) at a time. After each
one lands: full lint/type/test verification + whatever live check I can
run myself (Playwright, direct API calls, live server). Anything that
genuinely needs a human -- a real Slack workspace, an OAuth consent
screen, how a terminal UI actually feels to use, a packaged installer on
your OS -- gets called out explicitly and handed to you, not silently
skipped. Checkboxes get updated and committed as we go; treat this file,
not chat history, as the source of truth for status.

---

## Phase 0 -- No-dependency quick wins (interleave anytime)

- [x] ~~**Prompt caching on the Anthropic provider**~~ -- **stale, superseded,
      corrected below.** Originally shipped as `providers/anthropic_provider.py`
      subclassing aisuite's own `AnthropicProvider` to add `cache_control`
      breakpoints on the last tool definition, system prompt, and last
      message. That whole hand-rolled `runtime`/`providers` stack was
      later replaced by the LangGraph migration (`runtime_lg/`,
      `langchain_anthropic`'s `ChatAnthropic`) -- confirmed at the time
      (`runtime_lg/README.md`'s runtime-audit section) that this file's
      one real method was already dead code by then (aisuite's own
      provider construction produced an equivalent object directly,
      `cache_control` never actually reached a live code path), so it was
      deleted outright, not ported. Net result: **no explicit
      `cache_control` exists anywhere in the current, active codebase**
      -- this checkbox describes something that shipped, then was
      removed as part of an unrelated migration, not something currently
      true. Left unstruck-but-annotated rather than deleted from this
      doc, so the history is visible rather than silently rewritten.
- [x] **Fixed a real caching anti-pattern found live, provider-agnostic --
      not the Anthropic-specific work above**: `coordinator.py`'s
      `_current_date_note()` (real-world date grounding for the model,
      since its training-cutoff sense of "now" is otherwise stale) used
      to be glued onto the very front of the system prompt at
      `build_coordinator_agent` time -- the one genuinely volatile string
      in the whole prompt, positioned *ahead of* the large, otherwise-
      frozen `INSTRUCTIONS` block. Found while auditing whether coscribe
      has any caching optimizations at all (prompted by a live-reported
      case elsewhere of exactly this mistake -- a timestamp baked into a
      system prompt permanently defeating caching). This isn't only an
      Anthropic concern the way the bullet above was: coscribe is
      multi-provider, and Gemini's and OpenAI-compatible providers'
      *automatic*, no-code-needed prefix-based caching is defeated by the
      identical prefix instability -- no `cache_control` involved on
      their end at all for the damage to apply. Moved `current_date_note()`
      into `runtime_lg/messages.py` and inject it per-turn in
      `web/session.py`'s `_handle_user_message_locked`, alongside the
      existing `mode_note` (same "volatile content belongs after
      whatever's frozen" placement) -- keeps the system prompt genuinely
      byte-identical across turns/threads for every provider, with
      nothing provider-specific to configure. `strip_mode_note` got a
      matching regex so history replay still strips the note the live
      bubble never showed. Bonus, not just a caching fix: also closes a
      real staleness bug -- the date used to be computed once per
      thread/model-switch and never updated again, so it went stale for
      a conversation spanning midnight. Tests:
      `tests/test_coordinator.py` (system prompt stays date-free),
      `tests/test_web.py` (the fake model's actually-received messages:
      no date in the system message, the date *is* in the human
      message). Full suite (722 passed) clean.
- [x] **`cache_control` for Anthropic, for real this time** -- turned out
      not to need hand-rolled breakpoint logic at all (unlike the deleted
      `providers/anthropic_provider.py`, which did): `langchain_anthropic`
      ships its own ready-made `AnthropicPromptCachingMiddleware`
      (`langchain_anthropic.middleware`), an `AgentMiddleware` built for
      exactly `create_agent`'s middleware system. Appended unconditionally
      in `runtime_lg/agent.py`'s `build_langgraph_agent` (every call
      site -- the top-level coordinator *and* `spawn_agent`/`review_work`
      sub-agents, all of which build their own agent through this same
      function) with `unsupported_model_behavior="ignore"`, not the
      middleware's own "warn" default -- this app switches models per
      thread at runtime, so a non-Anthropic model (Gemini's the default)
      is the *normal* case here, not a misconfiguration worth a Python
      warning on every turn. On every model call it tags the last
      system-prompt content block and the last tool definition with
      `cache_control`; gracefully no-ops for every other provider (own
      `isinstance(request.model, ChatAnthropic)` check). Bumped the
      `langchain-anthropic` floor `0.3.0` -> `1.0.0` (the middleware
      architecture didn't exist before langchain-anthropic's 1.x line).
      **Known, not-yet-covered gap**: this only caches the system
      prompt + tool definitions, not a breakpoint on the growing message
      history itself (Anthropic's own "multi-turn conversations" pattern
      -- a breakpoint on the last block of the most-recently-appended
      turn, so each later request reuses the *entire* prior conversation
      prefix) -- real additional savings on a long conversation, left for
      later rather than reimplementing what the message-history half of
      the old deleted file used to do by hand.
      Tests: `tests/test_runtime_lg_agent.py` -- a real `ChatAnthropic`
      instance with its async SDK client mocked (same "assert the exact
      request shape" level the now-deleted `test_anthropic_provider.py`
      used, no live key needed) confirms `cache_control` actually lands
      on both the system prompt and the tool definition in the real
      outgoing request payload; a second test confirms a non-Anthropic
      model is silently unaffected -- explicitly asserts *no* Python
      warning fires, guarding the `unsupported_model_behavior="ignore"`
      choice. Full suite (724 passed, 14 skipped) clean. Live-verified:
      a real turn against real Gemini through the actual running
      `coscribe-web` server completed normally with no warnings/errors in
      the server log, confirming the unconditional middleware is a true
      no-op for this app's actual default provider. **Still needs your
      testing**: no real Anthropic key was available in this sandbox, so
      the real-cache-hit half (`usage.cache_read_input_tokens` on a
      second+ turn against a live `anthropic:<model>`) is unverified --
      the request-shape-level test above is as far as this sandbox can
      confirm on its own.

## Phase 1 -- Persona layer

**Removed entirely (2026-09-03).** Everything below shipped and worked
as described -- left unstruck-but-annotated rather than deleted, same
"leave history visible" convention as the Phase 0 caching bullet above.
You called the picker itself "不太顺手" (not smooth) because it forces a
choice at session start, and asked what an implicit alternative would
look like. Researching how actual mainstream products handle this
(Claude Cowork, ChatGPT, WorkBuddy) turned up that none of them have a
session-start picker at all -- they all use one always-on global
"Custom Instructions"/"Global Instructions" text field instead. That
turned out to already have a direct equivalent in this codebase: the
`MEMORY.md` / Global Instructions feature from Phase 8l, built earlier
and unrelated to this decision at the time. You then asked to reshape
persona around that Cowork wording specifically ("Instructions here
apply to all Cowork sessions...", not called "persona" anymore), and
when offered the choice between reshaping it or removing it outright,
picked full removal. In hindsight persona's "instructions" half was
pure duplication of Global Instructions; only the tool-whitelist-
narrowing half was genuinely different, and it wasn't judged worth
keeping on its own once the instructions half was gone.

Removed: `runtime/personas.py`, `tests/test_personas.py`,
`scripts/verify_persona_web.py`, `personas/ops.md` (and the `personas/`
dir), `frontend/src/components/PersonaPicker.tsx` and its e2e spec, the
`GET /api/personas` endpoint, the `select_persona` WS message, the
`?persona=` query param, the `{thread_id}.persona` sidecar, the
`persona_name` state field, `Settings.personas_dir`, and every test/doc
reference across both the backend and frontend -- including a real,
previously-harmless-but-dead `COSCRIBE_PERSONAS_DIR` env var the Tauri
desktop shell (`office-agent-desktop/src-tauri/src/lib.rs`) was still
setting for the Python sidecar process. Nothing replaced the
tool-whitelist half; Global Instructions already covered the rest.
Full backend test suite and frontend build/lint/typecheck re-verified
green after the removal (see below for this run's numbers).

Original phase description, kept for context: config *preset* for the
whole session (tools subset + instructions + permission mode +
recommended model), not a replacement for `spawn_agent`'s task-level
delegation. Same shape as a Skill (frontmatter + body), reusing the
existing `tools/skills.py` loading pattern rather than a new mechanism.

- [x] `PersonaConfig` + loader (`runtime/personas.py`, mirrors `load_skills`
      -- single `*.md` file per persona, not a directory, since personas
      don't bundle scripts/references/assets the way Skills do)
- [x] Wire persona selection into thread creation -- two paths, both write
      the same per-thread sidecar file (`<state_dir>/<thread_id>.persona`)
      so either way a reconnect picks the choice back up: a `?persona=`
      WebSocket query param (for deep links/automation), and a live
      `select_persona` WS message (what the picker below actually uses,
      since the picker necessarily shows *after* the socket is already
      open -- personas have to be fetched first). Web: a modal shown once,
      only for a genuinely new thread (`GET /api/personas` for the list);
      CLI: `--persona` flag. Instructions *append* after the Coordinator's
      base INSTRUCTIONS (confirmed with you); a persona's `tools` field,
      when set, is a *replacement* whitelist scoped only to the "domain"
      tools `build_coordinator_agent` builds -- MCP connectors/spawn_agent/
      run_workflow stay available regardless of persona.
- [x] One shipped reference persona: `personas/ops.md` (ops-style,
      confirmed with you) -- incident/operations focus, a curated tool
      subset, investigate-before-acting instructions.
- [x] Tests: `tests/test_personas.py` (loading/parsing/`apply_persona`
      unit tests), `tests/test_web.py` (catalog endpoint, both selection
      paths, sidecar persistence across a simulated process restart,
      double-selection rejected), `tests/test_cli.py` (`--persona` flag).
- [x] Live-verified end to end via Playwright: fresh page load shows the
      picker with the real `ops.md` persona listed, picking it hides the
      picker and shows an "Ops Coworker" pill in the header, reloading the
      same thread URL does *not* re-show the picker and the pill persists
      (sidecar file confirmed on disk).
- [ ] **Needs your testing**: does the persona picker's UX actually feel
      right at the point of starting a new conversation? (Verified it
      works correctly here; a genuine UX judgment call is still yours.)

## Fix -- gate workflow creation behind explicit commands (shipped, not a numbered phase)

Live-reported regression, found while testing Phase 1: after `/clear`, a
*new*, unrelated conversation's reply referenced "workflow step 2" and
carried information from an earlier conversation -- you never asked to
save anything as a workflow. Root cause: `save_workflow` was a freely
model-callable tool, gated only by prose in `INSTRUCTIONS`, defaulting to
capturing the *entire* thread's tool-call history when not given explicit
indices; `WorkflowStore` is global by name with no thread scoping and
outlives `/clear` by design (same as memory/tasks) -- so an autonomously
saved chain workflow from one thread could be autonomously replayed by a
later, unrelated thread, with chain-mode replay bypassing the LLM
entirely.

- [x] Removed `save_workflow` from the model's tool list entirely (both
      chain and agent modes, per your explicit "两种都收" choice) --
      workflow *creation* is no longer ever the model's own decision.
- [x] `/startworkflow` + `/endworkflow <name> [summary]` (chain mode):
      records only the tool-call steps between the two commands, never
      the thread's earlier history -- the direct fix for the "grabs
      unrelated history" half of the bug. `/saveworkflow <name>` (agent
      mode): summarizes the conversation via a one-off, tool-less
      summarizer call (same division of labor as `/compact`) -- the
      *decision* to save is still explicit, only the summary text is
      model-generated. Both added to `web/session.py` and `cli.py`,
      mirroring the existing `/compact`/`/clear` slash-command pattern.
      `/clear` now also cancels an in-progress recording.
- [x] `tools/workflows.py`: `record_chain_workflow`/`record_agent_workflow`
      (plain functions, not model tools) replace `save_workflow`.
- [x] `coordinator.py`'s `INSTRUCTIONS` rewritten: the model now suggests
      the slash commands instead of trying to save a workflow itself.
- [x] Tests: direct regression coverage in `tests/test_workflows_tool.py`
      (start-index slicing), `tests/test_web.py` (WebSocket-level:
      unrelated steps excluded, `/endworkflow` without `/startworkflow`
      errors, `/clear` cancels a recording, `/saveworkflow` produces an
      agent-mode workflow), `tests/test_cli.py` (same, through the real
      CLI), `tests/test_coordinator.py`/schema tests updated for
      `save_workflow`'s removal.
- [x] Live-verified via Playwright: `/startworkflow` → `/endworkflow` with
      no steps recorded errors correctly; a second `/startworkflow` while
      one is active reports discarding the previous recording;
      `/saveworkflow` on an empty thread reports "Nothing to save yet";
      `/clear` cancels an in-progress recording and the log confirms it.
      A full LLM-in-the-loop run (real tool call recorded between
      `/startworkflow`/`/endworkflow`) was blocked by the Gemini free-tier
      daily quota being exhausted from earlier live testing in this
      session -- the WebSocket-level tests exercise the identical
      production code path with scripted tool calls instead, so this is
      **the one live check I couldn't personally complete end-to-end
      with a real model call today** -- worth a quick real run when quota
      resets, if you want extra confidence.

## Phase 2 (new) -- Session/thread history browsing

Confirmed real gap: `GET /api/threads` already exists and returns the raw
list of thread ids from disk (`settings.state_dir.glob("*.json")`), but
there is *no browsing UI at all* today -- a thread is only reachable by
already knowing its id and typing `?thread=<id>` into the URL by hand.
No way to see what conversations exist, skim what happened in one, or
clean up old/test threads. Blocks using the tool day-to-day, let alone
debugging it -- prioritized ahead of Phase 4/5 below for that reason.

- [x] `DELETE /api/threads/{thread_id}` endpoint -- wraps the already-
      existing `FileStateStore.delete_state`, also removing that thread's
      `.tasks.json`/`.persona` sidecar files and its in-memory `ChatSession`
      if one exists. Pulled forward from this phase and shipped as a bug
      fix once the Phase 3 nav menu's Recent Session list made "I can list
      old threads but never get rid of them" an immediate, visible problem
      -- wired into the menu's own delete button, not (yet) into a
      dedicated Conversations panel.
- [x] Extend `GET /api/threads`'s response with real metadata per thread
      -- the description above was already stale by the time this shipped
      (runtime_lg persists in the shared `AsyncSqliteSaver` checkpointer,
      not one JSON file per thread -- see `list_threads`'s own comment).
      Per thread: `aget_tuple(thread_id)` fetches its latest checkpoint
      directly (no compiled Agent graph needed, unlike `send_history`'s
      `aget_state`) for `updated_at` (the checkpoint's own `ts`),
      `message_count`, and a `preview` (first human message, mode-note
      prefix stripped via a newly-public `strip_mode_note` -- previously
      private to `messages.py`, now a second real caller). `persona` reads
      the Phase 1 sidecar file and resolves it to a display name via
      `personas_by_id`. Sorted by `updated_at` descending.
- [x] Frontend: enriched the *existing* `SessionMenu`'s "Recent Session"
      list (preview/last-updated+message-count/persona badge, still
      click-to-open + delete per row) rather than building a separate
      "Conversations panel" -- the ROADMAP text above predates the React
      rewrite; there is no "Tasks `<aside>` panel" in the current
      frontend to reuse the pattern from, and a second surface showing
      the same data would be redundant with the one that already works.
      Also fixed a real, unrelated bug found while looking at this panel
      live: it was positioned `absolute left-0` off a trigger button
      sitting near the header's right edge, so its fixed `w-80` width
      overflowed off-screen (confirmed at both 1280px and 1600px
      viewports) -- `right-0` instead.
- [x] Tests: `tests/test_web.py` -- metadata shape (preview/message_count/
      persona/updated_at), persona-id-to-name resolution, mode-note
      stripped from the preview, most-recently-updated-first sort.
- [x] Live-verified via Playwright against a real running `coscribe-web`
      server (3 threads seeded directly into the checkpointer, one with a
      persona sidecar, to sidestep the exhausted-quota issue prior phases
      hit): the panel renders all 3 with correct previews/timestamps/
      message counts, the persona badge shows on exactly the one seeded
      with it, sort order is newest-first, and deleting a thread removes
      it from both the panel and `GET /api/threads` immediately.

## Phase 3 (expanded) -- top-left Session/Workflow nav menu + live status + delete (shipped)

Started as the original, narrower ask (the Workflows settings pane was
explicitly read-only, and `WorkflowRunStore` had no `delete` method at
all) but grew, confirmed with you, into a real navigation surface: a
click-to-toggle popup at a new top-left icon (replacing the inert
"Coscribe" title text) with New Session, New Workflow (pick a saved
one and run it), Current Workflow (live status/progress + Stop, if
anything's running -- plus a small indicator dot on the icon itself so
that's visible even with the menu closed), Recent Session, and Recent
Workflow (recent run records). Delete capability stayed in the existing,
more spacious Settings > Workflows pane rather than being crammed into
the compact popup.

- [x] `/runworkflow <name>` -- direct, non-model way to run a saved
      workflow (`web/session.py`/`cli.py`), same bypass-the-model
      reasoning as `/startworkflow`/`/endworkflow`/`/saveworkflow` for
      *creation*: a click in the menu (or typing the command) runs the
      workflow deterministically, no LLM round-trip to decide to call
      `run_workflow`. Not an instant command -- a run can take as long as
      a normal turn, so it gets the same Stop-button/turnInFlight
      treatment, driven by a new `workflow_run_started` WS message plus
      the existing `workflow_run_progress` stream (already carries the
      terminal completed/failed/stopped status, no new message needed
      for that).
- [x] `WorkflowRunStore.delete(run_id)` (genuinely new -- didn't exist in
      any form before) + `DELETE /api/workflows/{name}` and
      `DELETE /api/workflow-runs/{run_id}` endpoints, with a delete
      button (🗑) on every card in Settings > Workflows' saved-workflows
      list and Recent Runs list.
- [x] Frontend: the new top-left menu (`index.html`/`app.js`/`style.css`),
      populated fresh from `GET /api/threads` (existed, was never called
      by the frontend before this), `GET /api/workflows`, and
      `GET /api/workflow-runs` every time it opens -- same pattern the
      model-pill menu already used. `/stop` (existing, already shared
      across normal turns and workflow runs via one `_stop_event`) is
      reused as-is for the menu's Stop button, no new stop protocol
      needed.
- [x] Tests: `WorkflowRunStore.delete` round-trip, both DELETE endpoints
      (found + 404), `/runworkflow` over the WebSocket and through the
      real CLI with an empty `FakeLLMClient` response list (direct proof
      of zero model calls for a chain-mode run).
- [x] Live-verified via Playwright: all five menu sections render;
      clicking a pre-seeded chain workflow under New Workflow runs it
      with zero LLM calls (worked around the same exhausted Gemini free
      quota as the prior fix by pre-seeding a workflow directly and
      relying on chain mode's own zero-model-call replay), the indicator
      dot lights up while running and clears when done, the composer's
      Stop button engages/reverts correctly, Recent Session shows the
      current thread as non-clickable, Recent Workflow reflects the
      finished run, and delete buttons in Settings > Workflows actually
      remove both a saved workflow and a run record from disk.

**Fixes found in live use, shipped as follow-ups (not new phases):**

- [x] `startNewSession()` had a real bug -- it pre-set `?thread=<id>` in the
      URL before navigating, so the destination page's `isNewThread` check
      (which only looks at whether `?thread=` is present at all) always
      came back false, silently skipping the persona picker on every
      session started from the menu. Fixed by navigating to a bare URL
      with no thread param, same as a genuinely first-ever visit.
- [x] `DELETE /api/threads/{thread_id}` (see Phase 2 above, pulled forward)
      plus a delete button directly on each Recent Session row in the menu
      -- previously threads could be listed but never removed.
- [x] A delete button directly on each Recent Workflow row in the menu too
      (previously only reachable via Settings > Workflows, an extra
      navigation away).
- [x] The Current Workflow section's step display was the full-size
      Settings-panel step cards (one large rounded card per step, stacked
      vertically) crammed into a ~300px popup -- looked cramped/ugly in
      practice. Replaced with a compact, popup-specific renderer: small
      circular status dots in a single wrapped row, tool name as a hover
      tooltip instead of inline text.
- [x] Root-caused a real, higher-impact bug behind "clicking Stop does
      nothing": a `WorkflowRun` left at `status="running"` when its process
      died mid-run (crash, kill, or -- as reported live -- the user closing
      the browser and restarting the server, which doesn't actually stop a
      server-side run but *can* coincide with/mask a real crash) stays
      stuck at "running" forever -- nothing ever revisits it, and Stop
      can't help either, since there's no live task anywhere actually
      backing it by then. Two-part fix in `tools/workflows.py`: (1)
      `run_workflow` now catches any exception from `_run_chain`/
      `_run_agent_mode` and reports "failed" instead of letting it escape
      unfinalized (defense in depth for the process-survives case); (2)
      new `reconcile_interrupted_runs(state_dir)`, called once at startup
      in both `web/app.py`'s `create_app` and `cli.py`'s `chat()`, marks
      any run still "running" as failed with an explanatory error -- a
      fresh process can never have a genuinely in-flight run from before
      it started, so this is the reliable fix for the case no in-process
      exception handler can ever catch (a hard process kill).

## Phase 4 (was Phase 2) -- Unattended-safe risk tiering + selfwake

The foundation the rest of the roadmap leans on -- without this, "approve
via Slack" and "run a workflow unattended" have nothing real to gate on.

- [x] Risk taxonomy on `ToolMetadata` (`READ` / `WRITE_LOCAL` / `EXEC` /
      `EXTERNAL`, replacing the old `risk_level: "low"|"medium"|"high"` +
      independently-settable `requires_approval` bool -- two axes that
      could silently drift out of sync with each other by hand at any of
      this package's ~25 `tool_metadata(...)` call sites). Category now
      *is* the classification; `requires_approval` is a derived
      `@property` (`risk_category != "READ"`), not a separately-set
      field, so it can no longer disagree with its own tool's category.
      READ = no side effects, local or external (`web_search`,
      `search_images` included -- reaching the internet isn't itself a
      side effect if nothing gets written anywhere). WRITE_LOCAL = side
      effects confined to this machine. EXTERNAL = side effects that
      reach outside it -- every MCP tool (opaque external servers,
      classified this way in `runtime_lg/mcp.py` since coscribe can't
      introspect what one actually does) and `download_image` (fetches
      attacker-influenceable content from an arbitrary URL). One
      deliberate, flagged behavior change found doing this, not a
      mechanical rename: `remember` used to be ungated ("low risk") despite
      persisting a durable, cross-session, system-prompt-injected fact --
      reclassified `WRITE_LOCAL` (now gated), matching every other tool
      with that kind of consequence. `task_create`/`task_update` stayed
      `READ` on purpose despite writing a state file -- thread-scoped,
      disposable bookkeeping used constantly as part of ordinary
      multi-step work; gating it the same way as `remember` would make
      approval-fatigue the normal cost of using the tool at all, for
      something with no real externally-visible consequence.
- [x] Correcting a stale premise in this bullet, found while doing the
      above: EXEC was not a hypothetical, undecided risk class needing a
      "do we add one" answer -- `run_python_script`/`run_node_script`
      already exist (see `tools/scripts.py`), added specifically for the
      pptx skill work this same line predicted they'd be needed for. The
      "previously decided against" stance this bullet described was
      reversed at that point without this doc being updated to match.
      Both tools now carry `risk_category="EXEC"` for real, not a
      placeholder never assigned to anything.
- [x] Suspend/resume primitives: `sleep_for`/`sleep_until` (timer),
      `wake_on(job_id)` (background job completion), `wake_on_event(key)`
      (webhook/connector signal) -- converts an always-on unattended run
      into event-driven wake-ups. Researched the runtime's actual execution
      model first (see ARCHITECTURE.md's new bullet on this) rather than
      guessing: `HumanInTheLoopMiddleware`'s approval interrupt is durably
      checkpointer-backed but only ever resumed by a live WS message or a
      reconnect -- nothing resumes it on its own, so it's the wrong shape
      for "resume at a wall-clock time with nobody connected." Built
      instead on the *already-documented* external-scheduler pattern
      (README's "Scheduled / unattended runs"), formalized and automated:
      `tools/selfwake.py` (`WakeStore`/`SignalStore` + the model-callable
      `sleep_until`/`sleep_for`/`wake_on`/`wake_on_event`/`signal_event`/
      `list_wakes`/`cancel_wake` tools, no live client/checkpointer needed)
      + `runtime_lg/selfwake.py`'s `poll_due_wakes` (needs one, so it's
      runtime_lg-side, same split `tools/workflows.py`/`runtime_lg/
      workflows.py` already use) -- driven either by `web/app.py`'s
      lifespan (a background poll loop, zero config while the web server
      is running, interval `COSCRIBE_WAKE_POLL_SECONDS`) or `coscribe
      --check-wakes` (a one-shot poll for external cron/systemd, for a
      pure-CLI setup). `wake_on`/`wake_on_event` scoped to real backing,
      not hypothetical plumbing: `job_id` validates against
      `WorkflowRunStore` (the one real pollable "job" concept that already
      existed); `wake_on_event` pairs with `signal_event` as the concrete
      hook Phase 5c's own "depends on Phase 4" note needs, rather than a
      webhook receiver built prematurely now (none exists yet). **One
      documented v1 limitation, not fixed**: a poller-triggered resume on a
      thread that's currently open live in a browser tab writes correctly
      to the checkpointer but doesn't stream into that open tab in real
      time (no multi-writer broadcast mechanism exists) -- visible on next
      reload/reconnect instead. See ARCHITECTURE.md/README.md for the full
      writeup.
- [x] **Audit logging** -- dedicated append-only log of what an
      unattended/EXTERNAL-risk action did, when, and under what approval.
      `runtime_lg/audit.py`: `AuditEntry` (timestamp/thread_id/tool_name/
      arguments/decision/reason/detail) + `AuditLog` (one JSONL file,
      global across every thread under `state_dir/audit.jsonl`, mirroring
      the other global `*Store` classes' "one file/directory under
      state_dir" shape but append-only, since an audit trail is a
      sequence, never edited after the fact). Wired into
      `web/session.py`'s `_decide_action_request` -- the single choke
      point every gated tool call's decision actually passes through,
      for attended and unattended (selfwake/scheduled-task) turns alike.
      Logs every branch that decides something genuinely risky: a
      PreToolUse hook veto (even for a tool outside
      `_approval_required_names`, since a hook denying something is
      inherently security-relevant on its own), plan mode blocking a
      gated call, accept-edits auto-approving one with nobody actually
      watching, and a real human's live approve/deny. Deliberately does
      *not* log the stop-requested short-circuit (always a live human's
      own explicit action, not the was-anyone-watching gap this closes)
      or anything that was never gated in the first place (a READ-risk
      tool call would just duplicate the full transcript, not add an
      audit trail). No model-callable tool and no Settings UI panel
      added for this yet -- out of the scope this item actually asked
      for; the file is plain JSONL, readable by hand or via `AuditLog.
      read_all()`.
      Tests: `tests/test_audit_log.py` (append/read_all round trip,
      multiple `AuditLog` instances pointed at the same directory stay
      additive, an unparseable/blank line is skipped on read instead of
      raising), `tests/test_web.py` -- extended the four existing
      approval-path tests (human approve, human deny, plan-mode block,
      accept-edits auto-approve) plus the hook-denial test with a real
      assertion that the exact right `AuditEntry` landed on disk after a
      real WS turn, rather than adding duplicate new tests for the same
      paths.
- [x] **Secrets hardening** -- mapped every current secret storage path
      first (a dedicated Explore pass over `web/app.py`, `runtime/
      provider_config.py`, `tools/mcp.py`): three plaintext stores
      (`.env`'s built-in provider keys, `providers.json`'s custom
      `api_key`, `mcp.json`'s `env`/`headers` incl. the GitHub device-flow
      OAuth token), zero `os.chmod` calls anywhere, so every write got
      default umask permissions. Display-side masking (`_mask()`) was
      already fine; only at-rest storage needed hardening. Shipped two
      independent, additive layers, both in new `runtime/secrets.py`:
      (1) unconditional file permission hardening (`harden_file_
      permissions`, `chmod 0o600` after every write, no new dependency,
      no failure mode); (2) optional OS-keychain-backed storage
      (`store_secret`/`resolve_secret`/`delete_secret`, new `keyring`
      dependency) -- when a real backend is available, the actual secret
      lives in the OS keychain and the file holds only a `{"keyring_ref":
      ...}` reference; when it's not (confirmed live: this sandbox's own
      `keyring.get_keyring()` is `keyring.backends.fail.Keyring`, no
      Secret Service) or any call fails, storage falls back to today's
      plaintext, still hardened by layer 1, write never fails -- same
      "optional, gracefully degrading system dependency" shape the
      LibreOffice/poppler-utils checks already use elsewhere. `.env`
      needed its own sentinel-string encoding (`env_value_for_storage`/
      `env_resolve_secret_for_display`/`resolve_env_keyring_refs`, since
      it's flat text, not JSON) resolved into `os.environ` once at
      startup in both `cli.py` and `web/app.py`, right where `.env`
      already gets loaded -- so every SDK's own direct `os.getenv(...)`
      call keeps seeing the real key, unmodified. Legacy plaintext
      already on disk, and the keyring-unavailable fallback, share the
      same "just a string" shape -- no forced migration. Slack/OAuth
      tokens from Phase 5 don't exist yet (that phase hasn't started) --
      `store_secret`/`resolve_secret` are ready for them to reuse
      directly rather than inventing a third ad-hoc plaintext-JSON
      pattern when that phase lands.
- [x] Tests for suspend/resume: `tests/test_selfwake_tool.py` (WakeStore/
      SignalStore + all 7 tools, including risk-classification checks),
      `tests/test_selfwake_resume.py` (`poll_due_wakes` against a real
      checkpointer + fake model -- past-due/future timer wakes, job wakes
      against running/completed `WorkflowRun`s), `tests/test_web.py` (the
      background poll loop starts and cancels cleanly on shutdown),
      `tests/test_cli.py` (`--check-wakes` end to end). Audit-log tests
      not yet applicable -- that sub-item hasn't started.
- [x] Tests for secrets hardening: `tests/test_secrets.py` (store/resolve/
      delete round trips, both the keyring-available path -- mocked, since
      this sandbox has no real backend -- and a forced-failure path,
      legacy-plaintext passthrough, `.env` sentinel encode/decode,
      permission hardening), `tests/test_web.py` (provider/MCP/GitHub-
      token writes actually go through the keyring when mocked available,
      `GET` masking still correct either way, files end up `0o600`). Every
      pre-existing provider/MCP/github-auth test kept passing unmodified,
      naturally exercising the real fallback path in this environment.
- [ ] **Needs your testing**: an actual unattended run parked overnight
      via `sleep_until`, waking up and completing correctly -- verified
      here at the unit/integration level (fake model, real checkpointer,
      simulated past-due timestamps) but not against a real multi-hour
      wall-clock wait with a live model.

## Phase 4b -- Scheduled Tasks (product-level scheduler)

Followed a product-direction discussion (removing the git/github
connectors clarified coscribe's target audience is general office
automation, not developers) that identified "make scheduling a real
feature" as the highest-value, lowest-cost next step -- it builds
directly on Phase 4's suspend/resume infrastructure rather than needing
new plumbing. Before this phase, "make something happen later" was split
across three disconnected mechanisms, none a real product feature:
`tools/selfwake.py`'s `sleep_for`/`sleep_until` (agent-initiated, one-off,
only callable from inside an existing conversation), external cron +
`coscribe --thread X --accept-edits -m "..."` (entirely outside coscribe,
manual crontab editing), and `tools/workflows.py`'s saved `Workflow` (no
built-in recurrence of its own). This phase adds a fourth, unifying
concept, `ScheduledTrigger`, that's independently creatable and
manageable without needing an existing conversation -- see
ARCHITECTURE.md's new 范围边界 bullet for why it's a new concept rather
than an extension of `WakeRequest`.

- [x] `tools/scheduled_tasks.py` -- `ScheduleRule` (friendly `kind`:
      `"once"`/`"daily"`/`"weekly"`/`"monthly"` + `at`/`weekday`/
      `day_of_month`, no cron syntax exposed) and `compute_next_run_at`
      (pure stdlib `datetime`/`calendar` arithmetic, including month-end
      clamping for `day_of_month`); `ScheduledTrigger` + `ScheduledTriggerStore`
      (same JSON-per-record, atomic-write pattern every other `*Store`
      here uses); `create_trigger` extracted as a shared validation+
      persistence function used by both the model-callable
      `create_scheduled_task` tool and the REST `POST /api/scheduled-tasks`
      endpoint, so the two creation paths can't validate differently;
      `build_scheduled_task_tools(state_dir)` -- no `thread_id` parameter,
      deliberately global like `build_workflow_tools`, unlike
      `build_selfwake_tools(thread_id, state_dir)`.
- [x] `runtime_lg/scheduled_tasks.py`'s `poll_due_scheduled_tasks` --
      direct sibling of `runtime_lg/selfwake.py`'s `poll_due_wakes`, same
      `get_session` callback shape, imports `_SilentSocket` from
      `selfwake` rather than duplicating it. Each trigger fires into its
      own dedicated, persistent thread (`scheduled-<trigger_id>`, minted
      at creation, never the thread the trigger was created from) --
      either `session.run_saved_workflow(name, socket)` (new public
      wrapper added to `web/session.py`, exposing the previously-private
      `_run_workflow_lg` since `runtime_lg/scheduled_tasks.py` can't
      import `ChatSessionLG` directly without a circular import) or
      `session.handle_user_message(prompt, socket)`, matching whether the
      trigger is workflow-backed or prompt-backed. A `"once"` trigger
      disables itself after firing; a recurring one recomputes
      `next_run_at`. Same one-bad-fire-doesn't-abort-the-poll defensive
      posture as `poll_due_wakes`.
- [x] Wiring: `coordinator.py` (tool list + `INSTRUCTIONS` paragraph on
      when to use `create_scheduled_task` vs. `sleep_until`/`sleep_for`);
      `web/app.py`'s existing `_wake_poll_loop` (built for Phase 4) now
      also calls `poll_due_scheduled_tasks` each tick -- one background
      task checking two stores, not a second loop -- plus 5 new REST
      endpoints (`GET`/`POST /api/scheduled-tasks`, `POST .../{id}/pause`,
      `POST .../{id}/resume`, `DELETE .../{id}`); `cli.py`'s
      `--check-wakes` (built for Phase 4) now also fires due scheduled
      tasks in the same one-shot pass and includes them in its printed
      summary, rather than adding a second flag.
- [x] `ScheduledTasksTab.tsx` -- new Settings category (registered in
      `SettingsModal.tsx` next to Workflows), *not* read-only like
      `WorkflowsTab` -- a real create form (name, a friendly schedule
      picker: once/daily/weekly/monthly + time, weekday/day-of-month
      shown conditionally, a freeform-instruction textarea or a
      saved-workflow picker) plus a list with pause/resume/delete, same
      card styling `WorkflowsTab.tsx`/`ConnectorsTab.tsx` already use.
      New types in `types/settings.ts`, new REST calls in `lib/rest.ts`.
- [x] Tests: `tests/test_scheduled_tasks_tool.py` (23 tests --
      `compute_next_run_at` for every `kind` including month-end
      clamping and week rollover, `create_trigger` validation, the 5
      model-callable tools, risk classification), `tests/
      test_scheduled_tasks_resume.py` (`poll_due_scheduled_tasks` against
      a real checkpointer + fake model, mirroring `test_selfwake_resume.py`'s
      shape -- recurring/one-time/workflow-backed/not-yet-due/disabled
      cases), `tests/test_web.py` (all 5 REST endpoints end to end,
      including the 400 validation-error and 404 paths), `tests/
      test_cli.py` (`--check-wakes` also fires a due scheduled task).
- [x] Docs: this entry, README.md's new "Scheduled Tasks" section (next
      to "Sleep and wake"), ARCHITECTURE.md's 范围边界 bullet on why
      `ScheduledTrigger` is a new concept and the local-timezone-only
      limitation.
- [x] **Live check against a real running `coscribe-web` + real Gemini**:
      a prompt-backed trigger and a workflow-backed trigger both fired
      correctly through the live background poller, `next_run_at`
      advanced for the recurring one, `last_run_status` populated
      correctly for both. This is also how the bug below was actually
      found -- not from reading the code, but from a workflow-backed
      trigger's real fire simply never coming back.
- [x] **Found and fixed, during the live check above, not a pre-existing
      known issue being closed out**: an unattended turn (wake- or
      Scheduled-Task-triggered, anything driven through
      `runtime_lg/selfwake.py`'s `_SilentSocket`) that hit an
      approval-gated tool call (`write_file` and friends) hung the
      calling coroutine *forever*, instead of leaving the interrupt
      durably paused the way README/ARCHITECTURE.md already (optimistically)
      described. Since `poll_due_wakes`/`poll_due_scheduled_tasks` are
      awaited sequentially inside `web/app.py`'s one shared background
      poll loop, this wedged *every* scheduled task and wake, not just
      the one that hit it -- confirmed live (`asyncio.wait_for(...,
      timeout=90)` around a real fire never returned) before being
      diagnosed and fixed. Root cause: `web/session.py`'s
      `_resolve_pending_approvals` unconditionally creates a real
      `asyncio.Future` and awaits it, resolved only by a genuine
      `approval_response` WS message that a silent caller never sends.
      Fixed with a small duck-typed check, `_can_resolve_approvals
      (websocket)` (`getattr(websocket, "can_resolve_approvals", True)`
      -- can't use isinstance against `_SilentSocket`, that would be a
      circular import), guarding both places that call
      `_resolve_pending_approvals` unconditionally
      (`_handle_user_message_locked`'s turn logic,
      `_run_workflow_agent_mode`); `_SilentSocket` gets
      `can_resolve_approvals = False`, every real socket (fastapi's
      `WebSocket`, cli.py's `_CliSocket`) is unaffected via the
      `getattr` default. A skipped resolution on the workflow-agent-mode
      path now reports `status="failed"` with an explanatory error
      (previously would have falsely claimed `"completed"` once
      unstuck) -- `handle_user_message`'s own prompt-backed path has no
      return value to correct this way, so it keeps `poll_due_wakes`'s
      pre-existing "always claim success" simplification, now called out
      explicitly in README.md rather than left implicit. Regression
      tests: `tests/test_selfwake_resume.py`/`tests/
      test_scheduled_tasks_resume.py` each gained one test wrapping the
      call in `asyncio.wait_for(..., timeout=5)` so a reintroduced hang
      fails the test suite loudly instead of hanging it too.
- [ ] **Still needs your testing**: a manual walkthrough of the new
      Settings panel's create/pause/resume/delete flow in a real browser
      (the live check above exercised the REST API and background poller
      directly, not the frontend form).

## Phase 5 (was Phase 3, broadened) -- Connector OAuth UX + Slack

**Update**: the git/github MCP catalog entries and the GitHub-specific
OAuth Device Flow implementation this phase's original framing below was
built around have since been removed entirely -- a product-direction
correction, not a bug fix: coscribe's target user is a general file/task
automation assistant for a broad, not-necessarily-technical audience, not
a developer coding tool, and neither connector fit that audience (see
ARCHITECTURE.md's "目标" section for the explicit positioning note this
prompted). Whether this phase's *next* concrete step should be Slack/Asana
directly (skipping a GitHub-shaped proving ground entirely) instead of the
git/github-first sequencing originally described below is still an open
discussion, not yet decided -- the mechanism research below (device flow
vs. loopback redirect) stays valid regardless of which provider proves it
out first.

Original framing, kept for context on *why* a generic OAuth mechanism
(not a GitHub-specific one) was the right shape to begin with: Triggered
by live feedback that the git/github MCP catalog entries (when they still
existed, paste a PAT into a form) worked, but were worse UX than Claude's
own connectors ("click, get redirected to a login page"). The difference
is structural, not laziness -- claude.ai has a fixed public domain GitHub
can redirect back to; a locally-run Coscribe doesn't. But GitHub (and
most providers) support patterns built for exactly this local/CLI
situation, the same ones `gh auth login`/AWS SSO CLI use:
- **Device flow**: Coscribe shows a short code, the user visits a
  fixed URL (e.g. `github.com/login/device`) and enters it -- no listener
  needed on our side, just polling until the provider says "approved."
- **Loopback redirect**: a temporary `localhost:<port>` HTTP listener
  started only during the connect flow, standard OAuth redirect, torn
  down once the token arrives.
Scope this as a *generic* mechanism from the start (not GitHub-specific),
since Slack's own token acquisition has the identical problem and
shouldn't repeat the manual-paste pattern either.

- [ ] **5a -- Generic local-connector OAuth flow**: a reusable connect
      mechanism (device flow preferred where a provider supports it,
      loopback redirect otherwise) that any catalog entry needing a token
      can opt into, replacing today's "prefill the Custom form, paste a
      token" path for entries that support it. No GitHub entry left to
      retrofit onto this (removed, see this phase's update note above) --
      prove it out directly against whichever of Slack/Asana lands first.
- [x] **5b -- Slack as a connector** (post messages, read channels) --
      shipped in Phase 8p below, but *not* via 5a's mechanism: checked
      Slack's actual docs first (per this bullet's own note) and found
      the official MCP server authenticates with a plain Bot User OAuth
      Token the user copies from their own Slack app, no token exchange
      for coscribe to broker -- a loopback listener has nothing to do
      here, so this shipped as a `needs_config` catalog entry (the same
      Custom-tab-prefill mechanism the old GitHub PAT entry used)
      instead. 5a itself is still undone -- see that bullet.
- [ ] **5b2 -- Asana (or similar task-tracking tool) as a connector** --
      not yet designed, added here as a placeholder after the user flagged
      it as a concrete example of the "real office tools" this phase
      should prioritize (discussion in progress on scope/sequencing, not
      yet started).
- [ ] **5c -- Slack as an approval channel** -- depends on Phase 4's
      suspend/resume (a session needs to survive without an open browser
      tab to receive a Slack response). Design the trigger/response
      interface channel-agnostically (an "inbox" concept, not
      Slack-specific plumbing) even though Slack is the only channel
      actually wired up -- openworker's `inbox_routing.py`/`mentions.py`
      suggest their Slack approval is one instance of a general pattern,
      worth not painting ourselves into a Slack-only corner.
- [ ] **Needs your testing**: a real device-flow or loopback-redirect
      connect against an actual GitHub/Slack account, and a real approval
      round-trip against your actual Slack workspace -- none of this can
      be verified without live credentials.

## Phase 6 (was Phase 4) -- MCP OAuth support for *remote* servers

Distinct from Phase 5a: this is for connecting to a remote, third-party-
hosted MCP server that itself requires OAuth (the server is the OAuth
resource), not us obtaining a token for a locally-run connector. Different
mechanism, don't conflate the two when implementing.

- [ ] Extend `tools/mcp.py` to support OAuth-authenticated remote MCP
      servers alongside the existing local command+args+env kind
- [ ] **Needs your testing**: an actual OAuth consent-screen round-trip
      against a real remote MCP server

---

## Phase 7 -- Hardening ideas from reading openai/codex

OpenAI open-sourced Codex CLI (github.com/openai/codex) while this session
was already in progress; read through its `docs/` and the most relevant of
its 108 Rust crates (`sandboxing`, `execpolicy`, `network-proxy`, `hooks`,
`rollout`, `skills`, `memories`, `secrets`, `apply-patch`) looking for ideas
that fit coscribe's own scale, not a wholesale port of a much larger, more
heavily-resourced project. Full write-up of what was read and why each item
below was/wasn't picked up lives in chat history; this section is the
resulting scoped plan, agreed on before implementation.

Deliberately **not** adopted, and why:
- **A real OS-level sandbox** (`sandboxing`/`linux-sandbox`/
  `windows-sandbox-rs`, bwrap+landlock on Linux, Seatbelt on macOS,
  restricted-token+ACL+WFP on Windows) for `run_python_script`/
  `run_node_script` -- confirmed with you this stays out. Real reasons
  beyond "big lift": Windows restricted-token/ACL-manipulation code is
  exactly the kind of low-level process behavior corporate EDR/security
  software tends to flag, which cuts against the actual goal (coscribe
  working smoothly on a corporate machine) rather than helping it; and
  it needs real Windows hardware to develop/verify, same recurring
  friction as office-agent-desktop's own real-machine testing.
- **Lightweight env-var isolation for script execution** (don't inherit
  full `os.environ`, only pass through what's actually needed) -- a
  smaller, sandbox-adjacent idea floated as a partial mitigation for the
  same "a script gets the real API keys in its environment" gap;
  confirmed with you to leave alone for now, same reasoning as not
  touching sandboxing at all right now.
- **`agent-roles`/`agent-identity`/`agent-graph-store`/`code-mode`** --
  a heavier, role/identity/graph-based multi-agent framework. Confirmed
  with you this isn't a "we don't do multi-agent" stance -- coscribe
  already has one (`spawn_agent`/`review_work`, dynamic not fixed-role)
  -- just that Codex's heavier framework doesn't fit coscribe's existing
  "single Coordinator + dynamic delegation, not complex orchestration"
  choice, the same reasoning behind not adopting deepseek-harness's
  "everything is a plugin" architecture (see "Explicitly not adopting"
  below).
- **`memories`'s background auto-extraction pipeline** (mines past
  conversations for durable facts automatically, two-phase, runs async
  per-thread) -- coscribe's `remember` tool stays explicit/agent-
  initiated only; an automatic background miner is a much bigger,
  riskier surface (what gets "remembered" without anyone deciding to)
  than coscribe's current scale calls for.
- **`apply-patch`'s diff/hunk-based text editing** (a custom multi-hunk
  patch format, replacing exact-substring `old_text`/`new_text`) --
  discussed and rejected: `edit_file` is a secondary tool here (coscribe's
  real editing surface is docx/xlsx/pptx via their own dedicated tools,
  not text-file diffing at code-editing scale), and Codex's own
  multi-hunk format trades away exactly the property -- an unambiguous,
  cleanly-recoverable "not found/not unique" failure mode -- that this
  session's live GLM failure (see the PPTX section of chat history)
  showed matters for coscribe's weaker-model-tolerance bar. A worse
  partial-apply failure mode isn't worth the "fewer round trips on a
  large file" win coscribe rarely needs.

Actually picked up, in priority order (agreed with you):

- [x] **1. Redact secrets in the audit log.** A real gap found while
      reading Codex's `secrets` crate (`redact_secrets`, a sanitizer for
      anything about to be persisted/displayed): `runtime_lg/audit.py`'s
      `record_decision` was writing a gated tool call's `arguments`
      verbatim into `state_dir/audit.jsonl` -- if a call's arguments ever
      contained a real secret (an MCP server config's API key, a script
      argument carrying a token), it landed in that file in plaintext.
      Correction to how this bullet was originally scoped:
      `runtime/secrets.py` turned out to have no secret-*detection*
      logic to reuse -- it only hardens storage of secrets coscribe
      itself already knows it's writing (`.env`/providers.json/
      mcp.json), not arbitrary tool-call arguments -- so a new
      `redact_secrets(value, *, key=None)` was written in
      `runtime_lg/audit.py` instead: two layers, a dict key whose *name*
      looks secret-shaped (api_key/token/password/secret/...) redacts
      its whole value regardless of content, and a handful of
      well-known provider token formats (Anthropic/OpenAI/GitHub/Slack/
      Google/AWS/GitLab) catch a secret that leaked into an innocuously-
      named field. Deliberately not a generic high-entropy/long-string
      heuristic -- gated arguments routinely carry long legitimate
      content (file bodies, whole scripts, base64 image data) that would
      false-positive constantly. `record_decision` now runs both
      `arguments` and `detail` through it before persisting. Tests:
      `tests/test_audit_log.py` (key-name redaction, known-format
      redaction, recursion into nested dicts/lists, ordinary long
      strings/non-strings left alone, wired through `record_decision`).
- [x] **2. A declarative rule engine for EXEC-tier tools**, inspired by
      Codex's `execpolicy` crate, scoped down: `runtime_lg/exec_policy.py`
      loads an ordered rule list from a JSON file (`COSCRIBE_EXEC_POLICY_PATH`
      / `Settings.exec_policy_path`, unset = today's unchanged behavior --
      every call still asks), each rule `{"pattern": "<regex>", "decision":
      "allow"|"forbidden"|"prompt", "justification": "..."}`, matched via
      `re.search` against `run_python_script`/`run_node_script`'s `script`
      argument, first match wins. **Rules are human-authored config, never
      inferred by coscribe/the model from content** -- the module's own
      docstring spells out why this doesn't contradict `tools/scripts.py`'s
      documented "no import/library allowlist, trivially bypassable"
      stance: that critique is about coscribe deciding for itself what's
      safe; here the user pre-authorizes a specific pattern for their own
      convenience, the same trust model as accept-edits mode, just
      narrower. Wired into `web/session.py`'s `_decide_action_request`:
      `forbidden` rejects at the same precedence tier as a hook veto
      (checked before plan mode -- a categorical human-authored "never do
      this" always wins); `allow` skips straight to approval, but only
      *after* plan mode's own check, so plan mode's read-only guarantee
      stays absolute regardless of any exec-policy rule; `prompt`/no match/
      unconfigured falls through to today's normal flow unchanged. Both
      decisions get an audit-log entry (`reason="exec_policy"`, `detail`
      carries the rule's justification if it had one). Tests:
      `tests/test_exec_policy.py` (rule loading/matching/malformed-file
      fallback), `tests/test_web.py` (allow skips approval and the script
      actually runs, forbidden rejects without prompting, no-match falls
      through to normal human approval, an allow rule does not override
      plan mode), `tests/test_config.py` (the new Settings field).
- [x] **3. More hook event types.** coscribe's hooks (`hooks_config_path`)
      went from 3 events to 8: `PreToolUse`/`PostToolUse`/`SessionStart`
      (already existed) plus `SessionEnd`, `UserPromptSubmit`,
      `PreCompact`, `PostCompact`, `Interrupt` -- `runtime/hooks.py`'s new
      `HOOK_EVENTS` tuple and `empty_hooks_config()` helper are the single
      source of truth for the full set now, replacing 4 places that used
      to hand-spell the same 3-key dict literal. Every new event is
      observational only (a failing hook is logged, never blocks the
      action it's reporting on) -- only `PreToolUse` can veto, unchanged.
      Wired at already-existing async call sites: `UserPromptSubmit` at
      the top of `_handle_user_message_locked`; `PreCompact`/`PostCompact`
      around the summarization call in `_handle_compact`; `Interrupt`/
      `SessionEnd` from `web/app.py`'s `ws_endpoint`, alongside the
      existing `request_stop()`/`abandon_orphaned_turn()` calls (added two
      public, non-underscore methods on `ChatSessionLG` --
      `run_interrupt_hooks`/`run_session_end_hooks` -- for app.py to call
      into, matching those two methods' own naming convention).

      **Scoped down from Codex's 12 events, on purpose**: `SubagentStart`/
      `SubagentStop` were left out -- `runtime_lg/subagents.py` (where
      `spawn_agent`/`review_work` actually run) is a standalone module
      with no access to a session's `hooks_config` today, and wiring that
      through would be a new mechanism to build, not "one more call site"
      the way every event actually added here reuses machinery that
      already existed. Revisit if a real need for it shows up -- not
      scheduled speculatively. Auto-compact (the token-threshold-triggered
      `SummarizationMiddleware`, distinct from manual `/compact`) is also
      NOT covered by `PreCompact`/`PostCompact` -- it runs transparently
      inside LangGraph's own middleware chain with no call site coscribe
      controls directly; only manual `/compact` fires these two.

      Tests: `tests/test_hooks.py` (the full `HOOK_EVENTS` set parses/
      defaults correctly, unknown keys ignored, `empty_hooks_config`'s own
      shape), `tests/test_web.py` (each new event's payload actually
      arrives at a real hook script -- `UserPromptSubmit`'s message text,
      `PreCompact`/`PostCompact` firing around a real `/compact`,
      `Interrupt` on a `stop` message, `SessionEnd` on disconnect).
- [x] **4. Batch `edit_file`.** Smaller, unrelated to the rest of this
      phase but agreed on in the same discussion. Shipped as a new
      `edit_file_batch(path, edits)` tool rather than overloading
      `edit_file` itself -- keeps `edit_file`'s existing, already-tested
      signature untouched, and avoids a single tool with an awkward
      "sometimes required" parameter shape.

      **The parameter shape took two false starts to get right, both
      found live by this codebase's own Gemini-compat regression test
      (`test_coordinator.py::test_all_tool_schemas_are_gemini_compatible`)**,
      which is still worth exactly what it cost: this codebase's tool
      schemas have to work through aisuite's `Tools.__infer_from_signature`
      (still live today -- backs `/api/tools`, the Settings tools tab --
      even though real conversations run through the separate
      langgraph/LangChain path), and that schema builder turns out to
      have no handling *at all* for list/dict-typed parameters, nested or
      not, falling back to a broken literal type string for any of them:
      first attempt was a single `list[dict[str, str]]` (rejected);
      second attempt assumed two flat `list[str]` parameters would be
      safe by analogy to `add_mcp_server`'s `args: list[str]` (also
      rejected -- that comparison turned out to be wrong on inspection,
      since `add_mcp_server` is a REST endpoint parameter validated by
      FastAPI/Pydantic, never an LLM-facing tool schema at all, so it was
      never actually exercised by this schema builder in the first
      place). Landed on `edits: str` -- one string, alternating
      old_text/new_text chunks separated by a line containing only
      `---`, reusing `write_pptx`'s own already-proven `_slide_chunks`
      delimiter convention verbatim rather than inventing new syntax. A
      single `str` parameter is the only shape guaranteed to produce a
      valid JSON Schema "string" type through this builder.

      Edits apply in order -- each sees the result of the ones before it
      (so a later old_text chunk can be text an earlier new_text chunk
      just introduced), but nothing is written to disk until every edit
      has been validated against the running content; one bad edit
      anywhere in the batch leaves the file completely untouched, same
      "raise, don't silently do nothing" discipline as `edit_file`.
      Deliberately simpler than `edit_file` in one way: no per-edit
      `replace_all` -- every old_text chunk must already be unique at the
      point it applies; a change that genuinely needs `replace_all` still
      goes through a separate `edit_file` call. Not apply-patch's
      approach (see above) -- still exact-substring per edit, no diff
      syntax, no fuzzy/context-based matching. Tests:
      `tests/test_files_tool.py` (ordering, atomicity, no per-edit
      replace_all, odd-chunk-count/empty-input rejection, all of
      `edit_file`'s own validation rules mirrored), `tests/test_web.py`
      (a real end-to-end call through the actual LangGraph/LangChain
      tool-schema machinery, not just direct Python calls), and
      `test_coordinator.py::test_all_tool_schemas_are_gemini_compatible`
      itself, which the final shape passes.

---

## Phase 8 -- Interactive clarifying questions (`ask_user_question`) (shipped)

Prompted by a screenshot of another AI product's own choice-card UI:
"a lot of AI products now proactively ask the user a question with
clickable single/multi-select options -- can coscribe do that too, to
help the user decide and raise answer quality?" Confirmed scope with you
up front on two axes before writing any code: (1) ship both single- and
multi-select in v1, not single-select-first; (2) match the reference
screenshot's exact visual style (numbered rows, a hand-typed "Something
else" row, a top-right close/skip button), not a minimal first pass.

- [x] **New tool: `ask_user_question(question, options, header="",
      multi_select=False)`** (`tools/interaction.py`). `options` is a
      single newline-separated string, not a `list[str]` parameter --
      same reason `edit_file_batch` (Phase 7 item 4, above) landed on a
      delimiter-separated `str`: aisuite's tool-schema builder has no
      handling at all for list/dict-typed parameters. Unlike every other
      tool in this package, its Python body never actually runs -- it's a
      defensive `RuntimeError` that only fires if something forgets to
      wire the tool into `question_tool_names` for whichever graph calls
      it. The real answer comes from LangGraph's `HumanInTheLoopMiddleware`
      itself: alongside `approve`/`edit`/`reject`, its (undocumented in
      coscribe until this investigation, but present in the installed
      library's own source) `"respond"` decision skips the tool body
      entirely and substitutes the human's own message as the call's
      `ToolMessage` result. Registering `ask_user_question` in
      `interrupt_on` with `{"allowed_decisions": ["respond"]}`
      (`runtime_lg/agent.py`'s new `question_tool_names` parameter) reuses
      100% of the existing durable, checkpointer-backed, reconnect-safe
      interrupt/resume machinery already built for tool-call approvals --
      zero new interrupt infrastructure needed.
- [x] **`web/session.py` wiring.** `QUESTION_TOOL_NAMES` is tracked
      independently from `_approval_required_names` (the tool is
      `risk_category="READ"` -- no side effect -- so it was never in the
      approval set to begin with). `_decide_action_request` checks
      `is_question` before the "not gated, auto-approve" fast path and
      routes to a new `_decide_question_request`: sends a
      `question_required` WS event, awaits a `Future[str]` in a new
      `self._pending_questions` dict (parallel to `_pending_approvals`,
      kept separate since the value type differs and `request_stop`/
      reconnect handling treat the two independently), and returns
      `{"type": "respond", "message": answer}`. A `PreToolUse` hook can
      still veto the question (delivered as the tool's own "answer"
      instead of a `reject`, since `reject` isn't an allowed decision for
      this interrupt); `request_stop()` resolves any pending question
      with a placeholder ("Stopped by user before answering.") the same
      way it force-resolves pending approvals, so a disconnect or /stop
      can never leave the turn hanging on an unanswerable prompt.
- [x] **Suppressing the redundant tool-result row.** Found live during
      Playwright verification, not anticipated up front: every
      `ToolMessage` completing inside `_stream_turn` normally also sends
      a `tool_result` WS event (how every ordinary tool call's collapsed
      row gets created), and `ask_user_question` is no exception -- so
      answering a question produced *two* visible items, the real
      question/answer card plus a second, useless "Asked: a question"
      fallback row underneath it (the frontend's `TOOL_SUMMARIES` has no
      real args to summarize since this event carries no question text).
      Approval-gated tools don't show this same duplication only because
      `groupToolRuns` visually merges their "approval" and "tool" log
      items into one collapsible run; `"question"` items were never part
      of that grouping. Fixed by simply not sending `tool_result` for
      any name in `QUESTION_TOOL_NAMES` (`_stream_turn`'s `ToolMessage`
      branch) -- the question card already fully represents the call,
      the `PostToolUse` hook still fires either way. Confirmed the fix
      with a real live server + Playwright screenshot pass (see below).
- [x] **Sub-agent exclusion.** `runtime_lg/subagents.py`'s
      `build_spawn_agent_tool` already excluded `"spawn_agent"` itself
      from a sub-agent's own tool list defensively; extended the same
      exclusion to `QUESTION_TOOL_NAMES` -- a sub-agent's own inner graph
      never registers `question_tool_names` (`build_spawn_agent_tool`
      never passes one), so a sub-agent calling this tool would either
      hang forever or hit the defensive `RuntimeError` instead of
      pausing for a real person, same reasoning `review_work`'s
      curated read-only reviewer tool set already applies independently.
- [x] **Frontend.** New `QuestionCard` component matches the reference
      screenshot: numbered rows (single-select, click = instant answer)
      or checkboxes with an explicit Confirm button (multi-select,
      since there's no single click that means "done choosing"), a
      "Something else" row that reveals a free-text input with no
      server-side validation against the listed options (same escape
      hatch as Claude Code's own `AskUserQuestion`), and a top-right
      close button that answers with an empty string rather than forcing
      a choice. `reducer.ts` gained a `"question"` `LogItem` kind (kept
      out of `transcriptGrouping.ts`'s tool/approval run-grouping on
      purpose -- it's a standalone card, not a collapsible row) and
      `question_required`/`local_question_answered` handling;
      `transcriptGrouping.ts`'s `TOOL_SUMMARIES` still got an
      `ask_user_question` entry for the one place a resolved question
      *does* fall back to a plain collapsed row: page-reload history
      replay, which (like `approval` items) never reconstructs the
      interactive card, a pre-existing limitation shared with approvals,
      not a new gap introduced here.
- [x] **Not done here, deliberately out of scope at the time:** wiring
      `ask_user_question` into chain-mode workflow replay
      (`_run_workflow_chain_mode`'s `decide`/`invoke` in `web/session.py`,
      used when a saved workflow step is replayed outside a live graph
      turn). That path's `decide()` only understood a bare `approved:
      bool`, and its `invoke()` called a tool's real Python body
      directly -- neither matched this tool's `"respond"`-only, body-
      never-runs contract, so a saved workflow that happened to include a
      question step would have either silently skipped it (treated as
      "not approved") or hit the tool's own defensive `RuntimeError`.
      Flagged rather than fixed in this same pass since it touched
      workflow-replay architecture, not this feature's own scope --
      **since fixed in Phase 8c, below.**
- **Tests**: `tests/test_web.py`, 7 new (single-select answer feeding
      back into the model correctly, free-text "Something else" answers,
      not blocked by plan mode, a hook veto becoming a `respond` instead
      of a `reject`, `/stop` resolving a pending question with the
      placeholder instead of hanging, newline-separated option parsing
      including blank-line filtering, multi-select flag threading).
      Full suite verified green after each change (798 passed, 14
      skipped, no regressions). Visually verified live: a real `uvicorn`
      server with a scripted fake model driven end-to-end via Playwright
      through all three UI states (pending card, "Something else"
      custom-input revealed, answered) -- confirmed the rendered card
      matches the reference screenshot's layout, and confirmed the
      tool-result-suppression fix actually removes the duplicate row
      that an earlier verification pass caught live.

---

## Phase 8b -- Action-list and question-card visual polish (shipped)

Follow-up feedback right after Phase 8 shipped: "the interface still
isn't good-looking enough." Two concrete reference points, both
screenshots -- one of this very Claude Code session's own transcript UI
("Ran N commands" collapsible cards, light gray, expand to see each
step, emphasized keywords), one describing the original
`ask_user_question` reference more precisely than the first pass had
matched.

- [x] **Tool-call/approval rows restyled as real cards.** `ToolCallRow`
      (`ChatLog.tsx`) went from a bare chevron + tiny monospace text
      line to a rounded card (`bg-[var(--card-bg)]`, hover
      `bg-[var(--panel-bg)]`, `text-sm`) -- a run of several tool calls
      used to read as an undifferentiated wall of gray text; a distinct
      card per step is what makes "expand to see what happened" a real
      disclosure. `ToolRunGroupView`'s collapsed header stays plain text
      (no box), matching the reference.
- [x] **Structured verb/object summaries for emphasized keywords.**
      `transcriptGrouping.ts`'s `TOOL_SUMMARIES` changed from returning
      one flat string per tool ("Wrote deck.pptx") to `{verb, object,
      glue}` (`SummaryParts`) -- `object` (a filename, query, task
      content, ...) renders as its own light monospace chip
      (`ChatLog.tsx`'s new `SummaryLabel`), separate from the plain-text
      verb, the same "emphasized keyword" treatment the reference
      screenshot uses for a path or identifier inside an action
      description. Applied to both a single row's own label and a
      ToolRunGroup's joined header line (`summarizeGroupParts`).
      `summarizeItem` (flat string) kept around for anywhere that still
      just needs plain text.
- [x] **Real bug found while wiring the chip up: live-turn tool rows
      never actually had real arguments to show.** The chip mostly
      rendered generic fallback text ("Read `a file`" instead of "Read
      `notes.txt`") at first, which defeats the entire point of
      emphasizing the object. Root cause: `web/session.py`'s
      `_stream_turn` already recovers each tool call's real arguments
      server-side (`self._pending_tool_args`, used for the PostToolUse
      hook payload) but never forwarded them to the frontend -- the
      `tool_result` WS event only ever carried `tool_name`/`result`. A
      *gated* tool's row got its real arguments from a separate
      `approval_required` event, and a *history-replayed* row gets them
      from its own checkpointed `entry.arguments` -- but a live turn's
      *ungated* tool (the common case: `read_file`, `list_files`,
      `task_create`, ...) had no event carrying arguments at all, so the
      reducer's `tool_result` handler always fell back to `arguments:
      {}`. Fixed by adding `arguments: args` to the `tool_result`
      payload in both places it's sent (`_stream_turn`'s ToolMessage
      branch and `_run_workflow_chain_mode`'s `invoke` closure) and
      threading it through `wire.ts`'s `ToolResultEvent` and
      `reducer.ts`'s `tool_result` case (previously hardcoded to `{}` on
      the fallback-push path). Confirmed live: a `read_file(path=
      "notes.txt")` call now shows a `notes.txt` chip, not `a file`.
- [x] **`ask_user_question` card's background/badge tokens were
      backwards.** The user's own follow-up: "I see it's currently a
      gray background with a white badge" -- inverted from the intended
      white card + gray badge. Root cause, found by reading the actual
      theme tokens (`index.css`): the card body used `--panel-bg`
      (`#eae9e9`, the darker gray) while the option-number badge used
      `--card-bg` (`#f8f4f4`, near-white) -- exactly backwards, so the
      whole card read as flat gray-on-gray with the "badge" barely
      distinguishable. Swapped: card body/rows now rest on `--card-bg`,
      with `--panel-bg` reserved for hover/selected states and the
      number badge, so the badge reads as a visibly gray chip on a
      white card, matching the original reference screenshot.
- **Verified live**: real `uvicorn` server + scripted fake model driven
      through Playwright -- a 3-tool-call turn (`task_create`,
      `list_files`, `read_file`) grouped into a `ToolRunGroup`,
      screenshotted collapsed and expanded, confirming real filenames/
      content now appear as chips in both states; the `ask_user_question`
      card re-screenshotted confirming the white-card/gray-badge fix.
      No new automated tests -- this is a pure styling/data-plumbing
      change with no new branching logic; full backend suite re-run
      clean (798 passed, 14 skipped) since the `tool_result` payload
      shape changed.

---

## Phase 8c -- Skill Creator meta-skill + a real ask_user_question replay bug (shipped)

Two independent, previously-identified small items picked up together:
the "Extensibility/plugin system" scope's item 2 (skill-creator, above),
and a real bug this session flagged and queued as a separate follow-up
task right after Phase 8 shipped -- `ask_user_question` didn't work in
chain-mode workflow replay. Both fixed directly here instead.

- [x] **"Skill Creator" built-in skill**
      (`builtin_skills/skill-creator/SKILL.md`), same shape as the
      existing Word/Excel/PPTX built-ins (YAML frontmatter `name`+
      `description`, then a Markdown body) -- ships with the package, no
      user setup, always loaded unless a thread's own `skill_names`
      filter excludes it. Interviews the user about what's worth saving
      (suggesting `ask_user_question` for a genuinely ambiguous fork),
      checks the always-visible "Available skills" listing first to
      avoid a near-duplicate, designs frontmatter (the `description`
      field is what actually matters -- it's the only thing visible
      before `load_skill`, so it has to state the trigger condition
      plainly, not just summarize the content), writes the body, then
      `write_file`s the whole thing to `<skills_dir>/<slug>/SKILL.md`.

      **Real gap found while wiring this up, not anticipated from the
      original scope note:** `write_file` couldn't actually reach
      `settings.skills_dir` at all. It's scoped to `workspace_root` (plus
      whatever the user explicitly configured as `extra_readable_dirs`/
      `extra_writable_dirs`) via `WorkspaceScope` -- `skills_dir` is a
      completely different, coscribe-owned path that was never part of
      either list. `build_coordinator_agent` (`coordinator.py`) now
      passes `[*extra_readable, settings.skills_dir]` /
      `[*extra_writable, settings.skills_dir]` to `build_file_tools`
      specifically (not the document/spreadsheet/etc. tool builders --
      a skill is just a Markdown file, nothing else needs the extra
      reach), and a new always-present instruction paragraph, shown only
      when the Skill Creator skill is actually loaded, names the real
      resolved path so the model doesn't have to guess it. Deliberately
      kept out of the user-facing `extra_readable_dirs`/
      `extra_writable_dirs` config and `_describe_extra_dirs`'s own
      "directories outside the workspace" paragraph -- `skills_dir` isn't
      something the user opted into the way those are, it's coscribe's
      own fixed concept, so it gets its own dedicated line instead of
      being folded into that listing. No new review mechanism needed:
      `write_file` is already `WRITE_LOCAL` (approval-gated), so the
      user sees the exact SKILL.md content before it's created, same as
      any other file write.
- [x] **Chain-mode workflow replay now understands `ask_user_question`.**
      Real bug, found live during Phase 8's own Playwright verification
      pass and queued as a follow-up rather than fixed immediately:
      `_run_workflow_chain_mode`'s `decide`/`invoke` closures
      (`web/session.py`) only understood a bare `approved: bool` and
      called a tool's real Python body directly -- neither matches
      `ask_user_question`'s `"respond"`-only,
      body-never-runs `HumanInTheLoopMiddleware` contract (see
      `tools/interaction.py`'s own docstring). A recorded question step
      would have been silently treated as "not approved," or (had that
      been fixed alone) hit the tool's defensive `RuntimeError`.

      Fixed with no changes to `run_chain_lg` itself -- both closures
      already share an enclosing scope, so a new `pending_question_answer`
      local bridges them: `decide()` recognizes a `"respond"` outcome,
      captures its message, and reports `approved=True` (a "respond"
      decision has no "not approved" meaning of its own the way `reject`
      does); `invoke()` checks `QUESTION_TOOL_NAMES` and uses the
      captured answer directly instead of calling the tool's real body,
      then suppresses the `tool_result` WS event for it -- exactly the
      same "the question_required/answered card already represents this
      call" reasoning Phase 8's own live-turn fix used. A replayed
      question step genuinely re-asks the recorded question (its
      arguments replay fixed, same as every other step) and durably
      waits for a *fresh* live answer -- not some stale answer from when
      the workflow was first recorded, since `WorkflowStep` never stored
      one and this behavior is consistent with how every other step
      already works (arguments are fixed on replay, results are always
      produced fresh -- e.g. `read_file` re-reads whatever's on disk
      *now*, not a cached copy from record time).
- **Tests**: `tests/test_coordinator.py` (3 new -- Skill Creator listed
      with the right description and skills-dir path note, that note
      absent when filtered out, `write_file`/`read_file` actually
      reaching `skills_dir` end to end through the real tool functions,
      not just checking instructions text), `tests/test_web.py` (1 new --
      `/runworkflow` replaying a recorded `ask_user_question` step over a
      real WebSocket: `question_required` fires, a live answer resolves
      it, the run completes, no redundant `tool_result`). Five existing
      tests across `test_cli.py`/`test_skills_tool.py`/`test_web.py` that
      hardcoded "the three built-in skills" updated to four now that
      Skill Creator ships alongside Word/Excel/PPTX. Full suite green
      (802 passed, 14 skipped).

---

## Phase 8d -- Desktop-agent market survey; a local Ollama provider preset (shipped)

Asked to survey what similar desktop/office AI agent products are doing,
looking for ideas worth borrowing. Covered Microsoft 365 Copilot (agent
mode in Word/Excel/PPT, an Agent Store with org-level review), Claude for
Excel/Word/PPT (a fundamentally different architecture -- edits the live
workbook in place via an Office add-in, not generating new files the way
coscribe does), WPS灵犀 (validates coscribe's own local-file-integration
and long-running-task direction, nothing new), Feishu/Doubao's AI
partner (an enterprise collab platform, not a close comparable), Manus
(cloud-sandboxed multi-agent orchestration; its "Agent Skills" open
standard turned out to already match coscribe's own SKILL.md design
almost exactly -- the two differences worth noting are a `/SKILL_NAME`
slash command to force-load a skill, and "package this workflow into a
Skill" as an alternative to coscribe's existing chain-workflow
recording), OpenAI Atlas (deprecated; its agent mode never had local
filesystem access at all -- an actual point of validation for coscribe's
own local-first, direct-file-access bet, not something to copy), and
Genspark (a heavier 9-model/80-tool router architecture, the same kind
of complexity already declined in Phase 7's "not adopting Codex's
agent-roles" reasoning). Full comparison lives in chat history. The
`/SKILL_NAME` slash command and "workflow → Skill" ideas noted here have
since shipped too -- see Phase 8e, below.

- [x] **A local Ollama entry in `PROVIDER_CATALOG`** (`web/app.py`).
      Prompted by comparing against Open Interpreter -- architecturally
      the closest peer to coscribe (local execution, approval-before-
      running, and first-class local-model support via Ollama/LM Studio)
      -- which surfaced a real gap: coscribe's provider catalog only had
      cloud presets (deepseek/kimi/glm), nothing for a fully offline
      model runtime, despite "local-first" being the whole pitch.
      Genuinely just a catalog entry, no new engineering: `base_url:
      "http://localhost:11434/v1"` (Ollama's own OpenAI-compatible
      endpoint) plus an example `default_model: "qwen2.5"`. One real
      wrinkle, called out directly in the entry's own `description`
      field rather than papered over: `add_provider` still requires a
      non-blank `api_key` for every custom provider, but Ollama's local
      server never actually checks the Authorization header -- so unlike
      every other catalog entry, this one's API key field is a
      placeholder, not a real secret, and the description says so
      explicitly (any text works, e.g. `"ollama"`). `default_model` is
      also flagged as an example only -- coscribe has no way to see
      which models a user has actually pulled locally, so it can't be a
      real default the way deepseek/kimi/glm's pinned model names are.
      Tests: `tests/test_web.py`, 1 new (`/api/providers/catalog`
      includes the entry with the right `base_url`, and the full
      add-provider round trip works end to end with a placeholder key).
      Verified live via Playwright: the catalog card renders and its
      "+" button correctly prefills name/base_url/default_model, API
      key left blank as intended. Full suite green (803 passed, 14
      skipped).

---

## Phase 8e -- /<slug> force-load fix; /saveskill (shipped)

The two ideas Phase 8d's Manus comparison surfaced, picked up together.

- [x] **`/<slug>` force-load a skill -- fixed a real, previously-shipped
      but never actually working bug**, not built from scratch. The
      mechanism already existed (`web/session.py`'s own `skills_by_name`
      lookup, `_handle_user_message_locked`'s `/<word>` dispatch) but was
      keyed by `skill.name.lower()` -- a *display* label that can contain
      spaces ("skill creator", "word documents"), while the dispatch
      logic only ever looks up the single word before the first space in
      the typed text. Every current built-in skill has a multi-word
      name, so none of them could ever actually be force-loaded this
      way; the frontend's own autocomplete (`Composer.tsx`'s
      `selectAutocomplete`) made it worse by inserting the literal
      display name -- including its space -- into the composer, so even
      clicking the suggestion produced text the backend could never
      match. Found by reading both sides of the flow together, not from
      a live report -- confirmed with a regression test before touching
      anything.

      Fixed by adding `SkillInfo.slug` (`tools/skills.py`, a property:
      the skill's own directory name, lowercased -- already unique,
      already the convention Skill Creator's own step 5 documents for
      naming a new skill's directory) and keying `web/session.py`'s
      lookup (renamed `skills_by_name` -> `skills_by_slug`) and
      `web/app.py`'s `GET /api/commands` skill entries by it instead of
      the display name. Zero frontend changes needed -- `CommandInfo`'s
      `name` field already meant "the exact token to type" for every
      `FIXED_COMMANDS` entry (`"accept-edits"`, not a display label);
      skill entries just needed to actually follow that same convention.
      Tests: `tests/test_web.py`, 3 new (`/api/commands` lists slugs not
      display names, `/skill-creator <request>` genuinely splices the
      right skill's body into the model's next call, an unrecognized
      `/<word>` still errors cleanly). Verified live via Playwright: the
      autocomplete dropdown now shows `/skill-creator`, not
      `/Skill Creator`.
- [x] **`/saveskill <name>`** -- the reusable-knowledge counterpart to
      `/saveworkflow`, turning a live conversation into a new Skill
      instead of a replayable workflow. Deliberately a **separate,
      parallel mechanism** from `/saveworkflow`'s own curator/confirm
      state machine (`runtime_lg/skill_authoring.py`'s module docstring
      has the full reasoning) rather than a third mode bolted onto it --
      a workflow is a fixed, literally-replayable tool-call sequence; a
      Skill is generalized, reusable guidance a future agent follows
      with judgment, different enough in what the curator call has to
      produce (and how much judgment it needs) that sharing one state
      machine would have meant threading a "which shape is this" branch
      through every step of an already-nontrivial flow. The UX shape is
      deliberately familiar though: one curator call (clarify-or-propose,
      `propose_skill_save_lg`, mirroring `propose_workflow_save_lg`
      almost exactly) via a new `SKILL_SAVE_CURATOR_INSTRUCTIONS` prompt
      (`tools/skills.py`) that explicitly tells the model NOT to write
      a literal transcript or a fixed-argument replay script -- strip
      instance-specific details (a particular file name, a particular
      number) unless they're a genuinely fixed part of the procedure,
      and call out a real mistake-and-correction from the conversation
      explicitly, since that's exactly the kind of thing worth not
      repeating. `name` is always whatever the user typed, never
      model-proposed, same convention `/saveworkflow` already
      established (the model decides *how* to capture it, the user
      decides *what it's called*); the slug is derived mechanically
      (`slugify_skill_name`, kebab-case) rather than left to the
      curator, since a slug has to be deterministic and filesystem-safe,
      not a judgment call. A slug colliding with a *built-in* skill is
      refused outright (it would silently shadow the built-in for every
      future `/<slug>` lookup, not just overwrite a file this command
      owns); colliding with an existing *user* skill is allowed and
      treated as an update, same as Skill Creator's own "edit an
      existing skill" guidance.

      Writes `<skills_dir>/<slug>/SKILL.md` via `yaml.safe_dump` for the
      frontmatter (not a hand-formatted f-string like the built-in
      skills' own hand-authored files use) -- curator-model and user
      text can't be trusted to avoid characters (a colon, a leading
      quote/dash) that would break `_parse_skill`'s `yaml.safe_load` on
      a naively interpolated block, unlike text a person hand-wrote
      knowing the format. Plain filesystem I/O, not the `write_file`
      *tool* -- same precedent `/endworkflow`'s own save already set:
      the user's explicit slash-command confirmation already *is* the
      approval.

      **Immediately usable in the same session, no reconnect needed** --
      the confirm handler calls the existing `set_enabled_skills` (the
      Settings-panel skill-toggle method) with the new skill added to
      the enabled set, which was already a full, tested, revert-on-
      failure rebuild of `lg_agent`/instructions/tools from disk; it
      just needed one addition, made generally rather than as a one-off
      hack: it now also refreshes `skills_by_slug` from disk, so the
      brand-new skill's own `/<slug>` works right away too. Verified
      live, not just asserted in a test: after `/saveskill`, `/<the-new-
      slug> go` in the very next message correctly loaded the just-
      written skill.

      Tests: `tests/test_web.py`, 4 new (writes valid SKILL.md and the
      skill is immediately force-loadable via `/<slug>` in the same
      session -- including a check that `set_enabled_skills`'s own
      `state` event lists the new skill as enabled; the clarify path;
      refusing to shadow a built-in slug; discarding on a non-"yes"
      answer). Verified live via Playwright end to end: preview renders
      with the generated instructions as real Markdown, "yes" saves and
      shows a confirmation line, and the freshly-saved skill answers
      correctly to its own `/<slug>` in the same conversation. Full
      backend suite green (810 passed, 14 skipped).

---

## Phase 8f -- ppt-master survey; contrast check + wider shape vocabulary (shipped)

Asked to survey `github.com/hugohe3/ppt-master` (50k+ GitHub stars, a
much larger third-party PPTX-generation Skill package) for ideas worth
borrowing. Its architecture doesn't transfer -- a per-page SVG-render-
review-convert pipeline with parallel subagent visual QA and a bespoke
local web-server confirmation UI, built for decks running tens of
pages, heavy on token cost for coscribe's actual scale (single-call,
few-to-a-dozen-slide decks). Explicitly confirmed with you: don't port
the architecture. Two narrow, concrete findings picked up instead;
full comparison (Microsoft Copilot's Agent Store, Claude for Excel's
live-editing model, the design-brief "Strategist" stage, narrated-video
export) lives in chat history.

- [x] **`low_contrast_warnings`, a new objective field on
      `render_pptx_preview`** (`tools/presentations.py`). Prompted by
      ppt-master's own visual-review rubric, which flags exactly this
      defect (WCAG contrast) from a rendered screenshot -- coscribe
      doesn't need to render to check it, since write_pptx/run_node_script
      already know the exact colors used the moment they write a slide.
      Computes WCAG 2.1's real contrast-ratio formula directly (relative
      luminance -> ratio; 4.5:1 for ordinary text, 3.0:1 for WCAG's own
      "large text" definition -- >=18pt, or >=14pt bold, read straight
      from the run's own `font.size`/`font.bold`, not ppt-master's SVG-
      px-based 24px cutoff, since coscribe's object model is already in
      points). Deliberately conservative: only checks a run whose OWN
      font color is an explicit solid RGB (never a theme/scheme color,
      which needs the theme XML to resolve) against a background
      resolved the same way (the shape's own solid fill, else the
      slide's/layout's/master's, in PowerPoint's own inheritance order)
      -- a gradient/picture fill, a scheme color, or text sitting on a
      photo purely by z-order is silently skipped rather than guessed
      at; the existing scrim guidance plus a `review_work` look already
      cover that harder, pixel-level case. Joins `text_overlap_warnings`/
      `slides_missing_visual_elements` as a third always-on, no-
      LibreOffice-needed check, and the PPTX Slides skill's own
      "Before finishing" section was updated to mention it in the same
      breath as the other two. Tests: `tests/test_presentations_tool.py`
      (4 new -- a genuine light-gray-on-white hit; the large/bold-text
      3.0:1 carve-out, same color pair flagged at small size and cleared
      at large; a gradient background + a theme-color font both silently
      skipped, not false-flagged; background correctly inherited from
      the slide when the text shape has no fill of its own),
      `tests/test_pptx_templates_tool.py` (extended the existing "every
      bundled template passes the objective checks" regression test to
      also assert zero contrast hits -- confirmed live first: all four
      shipped templates, including the hand-authored third-party
      "velis" one, already pass with zero warnings).
- [x] **Documented `pptxgenjs`'s full native shape vocabulary in the
      PPTX Slides skill** (`builtin_skills/pptx/SKILL.md`) -- a pure
      documentation change, no new tool or code. Before this, the
      skill's own text only ever named two shapes (`ellipse`/
      `roundRect`, for icon circles); `run_node_script`'s real
      capability is much wider -- `pptxgenjs`'s own `ShapeType` enum
      covers directional arrows, a full flowchart set
      (`flowChartProcess`/`flowChartDecision`/`flowChartTerminator`/...),
      stars, brackets, callouts, and more, the same underlying DrawingML
      preset-geometry vocabulary ppt-master's own reference docs expose
      through python-pptx instead. A process-flow diagram or a decision
      flowchart -- content none of coscribe's four prefab layouts or
      `layout: svg`'s own path-drawing handles well -- now has a named,
      documented path instead of an approximated SVG `<path>` arrow or a
      fallback to plain bullets. Includes a worked three-step-flow
      example (a `flowChartProcess` box, a label, a `line`-type shape
      with `endArrowType: "triangle"` as the connecting arrow) --
      verified by actually installing `pptxgenjs` in a scratch directory
      and running the exact example script, then opening the resulting
      `.pptx` with python-pptx to confirm all three shapes are really
      there, not just checked against a version-agnostic doc page from
      memory. No test changes -- this is skill guidance text, not
      executable code; full backend suite re-run clean regardless (814
      passed, 14 skipped) since a builtin skill's body is exercised by
      existing coordinator/skill tests.

---

## Phase 8g -- Desktop tray + keep-alive-on-close (shipped, `office-agent-desktop`)

Discussed workflow-execution optimization inspired by Claude Cowork's
schedule feature and ChatGPT's tasks -- both notify/keep running when
you're not looking. Investigation found the real, more fundamental gap
first: `office-agent-desktop/src-tauri/src/lib.rs` killed the sidecar
process outright the moment the main window closed (`app_handle.exit(0)`
on `WindowEvent::CloseRequested`, explicit and deliberate in the
original design -- its own comment said so, reasoning "no background-
service use case here" to justify it). That reasoning predates
`ScheduledTrigger`/selfwake (Phase 4/4b) actually shipping: coscribe's
scheduled tasks/workflows fire from a poller living in the sidecar
process's own lifespan, independent of any browser tab or WS
connection -- but only for as long as that process is alive. So closing
the window was silently killing every scheduled automation the moment
you closed it, the exact opposite of "unattended." Confirmed with the
user directly: this (not a missing completion notification, the other
candidate raised) is the actual root cause worth fixing first, and the
right model to follow is Cowork/ChatGPT's own -- a background process
that keeps running -- not re-routing through the OS's own task
scheduler (the user's own prior habit, before agent tools existed) --
since either way something has to stay launchable to make the LLM API
call itself, so there's no real complexity saved by not just keeping
the app alive directly.

- [x] **Tray icon + hide-on-close** (`src-tauri/src/lib.rs`,
      `Cargo.toml`). `WindowEvent::CloseRequested` now calls
      `api.prevent_close()` and hides the window instead of exiting --
      the sidecar (and its scheduled-task poller) keeps running. A new
      tray icon (`tauri`'s `tray-icon` Cargo feature, not previously
      enabled) with a two-item menu -- "Open coscribe" (also bound to a
      left-click on the icon itself) and "Quit" -- is the only way back
      in, and "Quit" is the only remaining path that calls
      `app_handle.exit(0)` (which `RunEvent::Exit`'s existing,
      unmodified handler still turns into an actual sidecar kill).
      Reuses the existing `show_main` function for both the tray menu
      item and the click handler -- the exact same function the
      pre-existing single-instance re-launch handler already used, so
      the "bring the window back" behavior itself is proven, unchanged
      code, just wired to two new call sites. The app's own bundled
      window icon is reused for the tray rather than shipping a second
      image.
- [x] **Explicitly confirmed with the user, not assumed:** autostart-
      at-login is deliberately NOT included this round -- without it, a
      fresh reboot needs the app opened by hand once before scheduled
      tasks resume tracking; revisit if that friction turns out to
      matter in practice. A desktop notification when a background/
      scheduled run finishes while the window is hidden (the other half
      of the original Cowork/ChatGPT comparison) is also deliberately
      deferred -- it needs its own new plumbing the sidecar doesn't have
      today (an event channel this shell could subscribe to *without* a
      live browser WebSocket connection open against it), a separate
      follow-up.
- **Verified live, not just compiled**: `cargo check`/`cargo clippy
      --no-deps`/`cargo fmt --check` all clean, then built and actually
      ran under Xvfb + `fluxbox` (GTK/WebKit/appindicator dev packages
      installed fresh for this) -- `wmctrl -c` (the same
      `_NET_CLOSE_WINDOW` message a real close-button click sends)
      removed the window from the window list while `ps` confirmed both
      the app process and its (dummy, for this test) sidecar child
      stayed alive, proving the actual behavioral fix, not just that it
      compiles. **Not** verified this way, and said so directly in
      `office-agent-desktop/README.md`'s own new section rather than
      left implicit: actually clicking the tray icon -- Linux's
      AppIndicator/StatusNotifierItem protocol needs a full D-Bus tray
      host (a real desktop environment, or a proxy like `snixembed`)
      neither installed nor available in this sandbox, and is also
      genuinely the least representative platform to verify this on
      anyway, since Windows (`Shell_NotifyIcon`) and macOS
      (`NSStatusItem`) implement tray icons through entirely different,
      non-D-Bus mechanisms -- flagged as still needing a first real pass
      on your own machine, same bucket as this project's existing
      platform-specific bundling caveats.

---

## Phase 8h -- Background-completion desktop notifications (shipped, backend + `office-agent-desktop`)

The other half of Phase 8g's deferred pair: a native toast when a
background/scheduled run finishes, since closing the window no longer
kills the sidecar but also gives the user no signal anything happened
while they weren't looking. Designed before writing any code (your
explicit ask): the sidecar already runs an HTTP server on a port the
Tauri shell already knows (it spawns the sidecar and passes `--port`),
so the chosen shape is a pub/sub bus inside the sidecar plus a new
internal SSE endpoint the shell subscribes to at startup -- not a
file-polling signal (I/O contention, polling latency for no reason) and
not a new named-pipe/socket IPC surface (unnecessary when an HTTP
channel already exists). Confirmed with you: fires on **every**
completion, success or failure, not just failures or only-while-hidden --
the point right now is building confidence background runs are actually
happening at all, which a silent success wouldn't do.

- [x] **`office-agent/src/coscribe/web/background_events.py`** (new).
      `BackgroundEventBus` -- a plain `asyncio.Queue`-per-subscriber
      fan-out broadcaster, not a heavier message bus; this is one process
      talking to a handful of local subscribers (in practice: one
      desktop shell), not a distributed system. Deliberately NOT wired
      into `runtime_lg` (`poll_due_wakes`/`poll_due_scheduled_tasks`
      themselves): both already return `list[WakeRequest]`/
      `list[ScheduledTrigger]` describing exactly what fired, so
      `web/app.py`'s own poll loop publishes from that return value
      directly -- keeps `runtime_lg` ignorant of anything web-shaped,
      same boundary its own `selfwake.py` docstring already goes out of
      its way to preserve (no `web.session` import back into it).
- [x] **`_wake_poll_loop`** (`web/app.py`) now captures both polls'
      return values and publishes one `BackgroundEvent` per fired
      wake/trigger -- `kind` ("wake"/"scheduled_task"), `status`
      ("completed"/"failed", read straight off
      `ScheduledTrigger.last_run_status`; every fired wake is definitionally
      a success, since `poll_due_wakes` only appends to its own returned
      list after a turn didn't raise), `title` (the wake's `reason` or the
      trigger's `name`), `thread_id`.
- [x] **`GET /internal/events`** (`web/app.py`) -- a `StreamingResponse`
      SSE stream of that bus, under `/internal/` rather than `/api/`
      since it isn't for the bundled frontend (which already gets live
      updates over its own per-thread WebSocket); the one real subscriber
      is the desktop shell. No auth beyond "reachable on 127.0.0.1 at
      all," same as every other endpoint here.
- [x] **`office-agent-desktop/src-tauri/src/background_events.rs`**
      (new). A background task (`tauri::async_runtime::spawn`, started
      right after the sidecar itself in `.setup()`) that connects to
      `http://127.0.0.1:{port}/internal/events` with `reqwest`, parses
      each `data: <json>` frame with `serde`/`serde_json`, and shows a
      native toast via `tauri-plugin-notification` for every event.
      Reconnects on a fixed 2s delay on any failure (not up yet, dropped
      stream, non-200) -- no exponential backoff, since this is one
      process talking to one known-local port for the app's whole
      lifetime, not a client hitting a shared remote service. Explicitly
      does **not** try to make the notification click focus the window:
      that needs action-id registration and platform-specific toast-
      activation wiring this sandbox has no way to verify, so rather than
      ship an unverified click handler and call it done, this stays a
      plain fire-and-forget toast for now -- same honesty standard as
      Phase 8g's tray-icon-click gap. An in-app notification-history
      panel was considered and deliberately deferred too -- a toast is
      enough to answer "did it run," a persisted history is a real
      feature with its own scope, not a natural extension of this one.
- **Verified live, for real, not mocked**: spun up the actual
      `coscribe-web` app (real `uvicorn`, real socket, a fake chat model
      standing in for the LLM call only) with `wake_poll_seconds=1`,
      seeded a real due `ScheduledTrigger` straight into its state
      store, and streamed `GET /internal/events` with real `httpx` (not
      Starlette's `TestClient`, which -- discovered while first trying to
      write an automated test for this -- buffers an entire ASGI response
      before returning anything, so it can never observe a chunk from a
      deliberately-infinite SSE stream; that's why the endpoint's own
      test below drives its `event_source()` generator directly instead
      of over HTTP). The trigger fired on schedule and a real
      `background_run_completed` event for it came back over the wire
      within about a second, unprompted -- the exact path
      `background_events.rs`'s `reqwest` client exercises in production.
      Rust side: `cargo check`/`cargo clippy --no-deps`/`cargo fmt
      --check` all clean. **Not** verified: an actual toast appearing on
      screen (needs a real Windows machine, same bucket as every other
      "can't verify a GUI effect from a headless Linux sandbox" item in
      this file) and the reconnect-after-sidecar-restart path (only the
      steady-state connected case was exercised above).
- **Tests**: `tests/test_web.py` -- `BackgroundEventBus` fan-out/
      unsubscribe in isolation; `_wake_poll_loop`'s publish wiring
      (reached via `app.state.background_events`, driven through
      `TestClient`'s own `anyio` portal rather than the endpoint, for the
      `TestClient`-buffering reason above); `/internal/events`'s route
      registration, method, and `text/event-stream` media type, calling
      its endpoint function directly and closing the generator instead of
      awaiting a response that never completes.

---

## Phase 8i -- CI had been red for ~5 days; three unrelated causes fixed (shipped)

You noticed ("我看到最近CI很多报错") and asked -- checked the Actions tab
directly rather than guessing, and every "coscribe CI" run since
2026-08-27 (13 in a row, spanning many unrelated commits) had in fact
been failing. Three separate, unrelated causes, none of them caused by
this session's own Phase 8h work landing on top of them:

- [x] **`langchain-anthropic` moved past what
      `test_anthropic_prompt_caching_middleware_tags_system_prompt_and_tools`
      (`tests/test_runtime_lg_agent.py`) assumed.** `pyproject.toml` pins
      `langchain-anthropic>=1.0.0` with no upper bound, so every fresh CI
      install picks up whatever's newest; somewhere between 1.0 and the
      1.6.1/1.7.0 range checked here, `chat_models.py`'s `_agenerate` moved
      from calling `messages.create(...)` directly to always calling
      `messages.with_raw_response.create(...)` and parsing the result via
      `_sdk_compat._aparse` (confirmed by reading the installed package's
      source, not guessed from the traceback alone) -- real internal
      plumbing, most likely to support reading response metadata for its
      own bookkeeping. The test's mock replaced `messages.create` with a
      fake returning an already-parsed `Message`, which is no longer the
      method actually being called at all; fixed to mock
      `messages.with_raw_response.create` instead, returning a small
      object whose `.parse()` gives back the message (works against
      either `anthropic<1`'s sync `.parse()` or `>=1`'s awaitable one --
      `_aparse` handles both, so the fake doesn't need to guess which is
      installed).
- [x] **Two pre-existing `ruff` E501s**, one in `tests/test_exec_policy.py`
      (present since at least the first failing run) and one this
      session's own earlier `/saveskill` work (Phase 8e) introduced in
      `web/session.py` without a `ruff check` pass at the time -- both
      just needed wrapping, no logic changed.
- [x] **A genuinely flaky test**,
      `test_session_end_hook_fires_on_disconnect` (`tests/test_web.py`) --
      reproduced locally too, not just on CI, so chased instead of
      dismissed as runner noise. Root cause is in `starlette.testclient`
      itself, not this project's code: `WebSocketTestSession.__exit__`
      (relied on when a `with client.websocket_connect(...) as ws:` block
      is left to close the socket implicitly) sends the disconnect
      message and then *immediately* hard-cancels the whole in-flight
      ASGI app task via its `anyio` cancel scope, racing ahead of
      `web/app.py`'s own `except WebSocketDisconnect:` handler actually
      finishing `await session.run_session_end_hooks()` (which shells out
      to a real subprocess) -- confirmed by reading
      `starlette/testclient.py`'s own `__exit__`/`_run` source, not
      guessed. Fixed by closing the socket and waiting for the hook's
      output file *inside* the `with` block instead (same pattern
      `test_interrupt_hook_fires_on_a_stop_message` right above it
      already used, for the same underlying reason) -- passed 5/5 in a
      row locally after the fix, versus reproducing on the first try
      before it.
- **Verified**: `pytest -q` (full suite, twice) both clean at 831 passed;
      `ruff check src tests` and `mypy src` both clean.

---

## Phase 8j -- Approval cards show a before/after preview, not just raw JSON (shipped)

Discussed as the highest-value UX gap versus Codex/Claude Code: those
tools show a real diff before you approve an edit, but coscribe's
approval card for a destructive pptx edit (`edit_pptx_shape`,
`delete_pptx_shape`, `replace_pptx_image`, ...) just dumped the raw tool
arguments (`{"shape_index": 3, "fill_color": "38BDF8"}`) -- readable, but
not a picture of what's about to change. Chosen approach: dry-run the
*exact* same tool call against a throwaway copy of the file and render
both states, rather than a second, hand-written "what would this look
like" implementation that could drift out of sync with what actually
happens on approval.

- [x] **`tools/_thumbnail.py`**: added `render_single_page_preview`
      (renders one specific page/slide, unlike `render_all_page_previews`
      which always starts from page 1 and is capped by count) --
      factored the shared soffice-to-PDF step out into `_convert_to_pdf`
      so this and `render_all_page_previews` don't duplicate that
      subprocess call.
- [x] **`web/session.py`**: new `_build_pptx_edit_preview`, called from
      `_decide_action_request` right before sending `approval_required`
      (guarded behind `_can_resolve_approvals(websocket)` -- an
      unattended selfwake/Scheduled Task turn shouldn't pay for a render
      nobody will see). Deliberately generic, not a hardcoded per-tool
      dispatch table: applies to *any* tool name that's a
      `PresentationToolkit` method whose `path` argument names an
      existing `.pptx`/`.potx` file -- covers every current and future
      pptx edit tool with zero maintenance here, and naturally excludes
      `write_pptx`/`fill_pptx_template` (they create a *new* file, no
      "before" to diff against) without needing a denylist. The dry run
      itself: copy the real file into a fresh temp dir, instantiate a
      throwaway `PresentationToolkit` rooted there (sidesteps the real
      session's `WorkspaceScope` entirely -- no permission questions,
      it's a private toolkit touching only a private temp file), call
      the same-named method with the same arguments, render before
      (real file) and after (temp copy) via the new single-page
      renderer, targeting whichever slide the call's own `slide`
      argument names (page 1 otherwise). Never raises; any failure just
      means one or both preview images come back `None` and the
      approval flow proceeds exactly as before this existed.
- [x] **Frontend** (`wire.ts`, `reducer.ts`, `ChatLog.tsx`): new
      `before_preview`/`after_preview` fields threaded through
      `ApprovalRequiredEvent` -> the `approval` `LogItem` -> a new
      `ApprovalPreview` component (a `Before`/`After` image pair, shown
      above the existing raw-arguments dump, which stays -- not every
      argument is visually obvious). Missing on either side renders as a
      muted "no preview" placeholder rather than collapsing the layout.
- **Verified live end to end**, not just unit-tested: a real running
      `coscribe-web` (real `uvicorn`, a fake chat model standing in only
      for the LLM call) driven through an actual Chromium browser via
      Playwright -- typed a message, the fake model's scripted
      `edit_pptx_shape` call came back, and the approval card rendered a
      real "before" (plain textbox) beside a real "after" (the same
      textbox now filled `38BDF8` blue), screenshotted and inspected
      directly. Also confirmed the real file on disk is untouched by the
      dry run (`fill.type` unchanged) before approval is ever given.
- **Tests** (`tests/test_web.py`): a real WS round-trip (gated behind
      the existing `real_libreoffice` marker, same convention
      `test_presentations_tool.py` already uses) confirming
      `approval_required` carries two distinct, real preview filenames
      that exist under `state_dir/previews/`, and the real file is still
      unmodified at that point; a non-pptx tool (`write_file`) and a
      not-yet-existing target path both confirmed to fall through to
      `(None, None)` with no LibreOffice needed for either case.
- **Not done this round**: docx/xlsx aren't covered yet -- same
      mechanism generalizes (their own toolkit classes would need
      wiring in the same way), just not built.

---

## Phase 8k -- Approval preview generalized to docx/xlsx (shipped)

Phase 8j's own "not done this round" note, closed: `_build_pptx_edit_preview`
became `_build_document_edit_preview`, driven by a new
`_PREVIEWABLE_TOOLKITS_BY_EXTENSION` dict (`.pptx`/`.potx` ->
`PresentationToolkit`, `.docx`/`.dotx` -> `DocumentToolkit`,
`.xlsx`/`.xlsm`/`.xltx` -> `SpreadsheetToolkit`) instead of a hardcoded
pptx-only check -- all three toolkit classes already shared the exact
same `(root, *, state_dir=, extra_readable=, extra_writable=)`
constructor shape, which is what makes one dispatch table enough instead
of three near-duplicate methods. No frontend changes needed at all: the
WS field names (`before_preview`/`after_preview`) were already
format-agnostic from Phase 8j. `write_docx`/`write_xlsx` naturally get a
preview only when actually overwriting a file that already exists
(the same `real_path.is_file()` gate that excluded `write_pptx` on a
fresh path in 8j) -- exactly the case worth previewing, no extra
special-casing needed.

- [x] **`web/session.py`**: `_PREVIEWABLE_TOOLKITS_BY_EXTENSION`,
      `_build_document_edit_preview` (renamed from
      `_build_pptx_edit_preview`), imports for `DocumentToolkit`/
      `SpreadsheetToolkit`.
- **Tested with real, invented work scenarios, per your ask** -- not
      abstract "call the tool with some args" tests. Two live-browser
      runs (real `uvicorn`, real Chromium via Playwright, a fake model
      standing in only for the LLM call): (1) a project status report
      docx ("Status: On track...") updated to reflect a slipped launch
      date -- approval card showed the old paragraph beside the new one.
      (2) a budget xlsx (three months of spend, no chart) asked to get a
      bar chart added -- approval card showed the plain data table
      beside the *same* table now with a real bar chart rendered next to
      it. Both approved live and the real files verified afterward
      (`python-docx`/`openpyxl` read-back: the docx paragraph text and
      the xlsx's embedded `BarChart` object were both genuinely there,
      not just claimed).
- **A real bug found and fixed in the test harness itself, not the
      product** -- worth recording since it looked alarming at first: an
      early version of the two-scenario script used `browser.newPage()`
      for both, which (undocumented gotcha, confirmed by reading
      Playwright's own docs) shares one browser context's `localStorage`
      -- the second scenario's page silently reconnected to the first
      scenario's still-pending thread instead of starting fresh, and
      because the fake model's scripted-response list is one counter
      shared across every thread on a given server process, this
      produced a real "list index out of range" once exhausted and,
      after fixing the context bug, a subtler response-index
      misalignment (the first scenario's approval was never actually
      clicked, so its own "reserved" second response got consumed by the
      second scenario instead). Root-caused via the checkpointer's own
      SQLite debug logs (`logging.basicConfig(level=logging.DEBUG)`),
      not guessed -- fixed by giving each scenario its own
      `browser.newContext()` and actually clicking Approve (and waiting
      for that turn's real final reply) before moving to the next one.
      Confirms `_build_document_edit_preview`/the approval flow
      themselves were never at fault; this was purely an artifact of two
      independent conversations sharing one mutable fake-model cursor in
      a throwaway test script.
- **File-system correctness, tested explicitly** (your call that this
      is the core of the feature): a denied `edit_pptx_shape` leaves the
      real file's bytes byte-for-byte unchanged (confirms the dry run
      itself never writes through the real path, independent of whatever
      the human decides); a file that only exists via
      `settings.extra_writable_dirs` (outside the main workspace root)
      still resolves and renders correctly -- the same `WorkspaceScope`
      construction real tool calls use, not a workspace-root-only
      shortcut.
- **Verified**: `pytest -q` full suite green; `ruff check src tests`/
      `mypy src` both clean.

---

## Phase 8l -- Two settings borrowed from Claude Cowork's own panel; a Settings UI redesign pass (shipped)

You shared screenshots of Cowork's own Settings page and asked what else
was worth borrowing. Went through it row by row against coscribe's actual
Settings code (not guessing): "Cowork files"/"Trusted Cowork folders"
already have real equivalents (Default Workspace, the Directories list);
"Dispatch"/"Require trusted devices" are remote-device concepts that
don't fit coscribe's local-only design; "Preferred browser"/"Allowed
sites" don't apply -- coscribe has no first-party browser tool, browser
automation is all through the optional Playwright MCP connector. Two
were genuine, confirmed gaps, agreed to build first:

- [x] **"Only on this computer" -> "Keep Running in Background"
      toggle.** Phase 8g made hide-on-close/keep-running-in-background
      the *only* behavior, unconditionally -- Cowork makes it a real
      user choice. `COSCRIBE_BACKGROUND_ON_CLOSE` (General tab) is a
      new Rust-consumed, Settings-inert `.env` value -- Python never
      reads it, `office-agent-desktop`'s `should_keep_running_in_
      background` (lib.rs) reads the same file fresh at every window
      close, so a change takes effect at the very next close with no
      coscribe-web restart (a new `DESKTOP_ENV_VARS` list, separate from
      `COSCRIBE_ENV_VARS`, keeps it out of `update_config`'s
      `restart_required` computation, same trick `PROVIDER_DEFAULT_
      MODEL_ENV_VARS` already uses for its own no-restart-needed
      fields). Parses the file with the `dotenvy` crate (new dependency)
      rather than a hand-rolled line scanner -- the file is written by
      Python's own `set_key()`, which quotes values (`KEY='value'`), and
      a parser that didn't replicate that convention exactly would
      silently misread it.
- [x] **"Global instructions" -> a direct `MEMORY.md` content editor.**
      coscribe already had the underlying concept (`MEMORY.md`,
      injected into every session, Settings could only edit its
      *path*) -- a real, confirmed gap: no way to see or change its
      *content* without leaving the app to hand-edit the file, or
      asking the agent in chat to use `remember` (which only appends
      one bullet at a time). New `GET`/`POST /api/memory` (a full
      overwrite, matching what a text box editing "the whole thing"
      actually needs) and a new `GlobalInstructionsSection` component
      (an "Edit" button that expands an inline textarea with its own
      Save -- deliberately not wired into `SettingsModal`'s existing
      dirty/Save-bar tracking, which is keyed to plain `.env` fields;
      a multi-line content edit reads better with its own explicit
      save than bundled in with unrelated General-tab changes).
- [x] **Settings UI redesign, on top of the above** -- your own call
      that the existing panel ("差点意思，质量不高") didn't match
      Cowork's visual quality: a new shared `SettingRow` component
      (label + description stacked on the left, one control right-
      aligned, no per-row border -- a shared `divide-y` container
      supplies the rule between rows instead) replaces the old bare
      `<label><input/></label>` stacks across `GeneralTab` and
      `WorkspaceTab`. `SettingsField` gained a real `description` field
      -- explanatory text that used to be crammed into the input's own
      `placeholder` (so it vanished the moment you typed a value, e.g.
      Max Turns' old "20 (caps a single agent loop's tool-calling
      turns)") now stays visible under the label permanently. Also gave
      "Default Workspace" its own "Browse..." button (reusing the
      existing `DirBrowserModal`, previously wired only to the
      Directories list) -- direct parity with Cowork's "Cowork files...
      Change" row, and a real missing affordance, not purely cosmetic.
- **Verified live in a real browser**, not just by inspection --
      Playwright screenshots of General and Workspace confirmed the row
      layout, description text, and the new toggle/editor all render
      correctly; a second pass actually toggled "Keep Running in
      Background" off, saved, and confirmed both the UI state ("Saved."
      with no restart-required message) and the on-disk `.env` value
      matched; edited Global Instructions, saved, and confirmed
      `MEMORY.md` on disk held exactly the new content (verified after
      first catching a red herring in a sloppy debug `cat` that
      concatenated two different temp files' content together and
      briefly looked like a real bug -- it wasn't; re-checked cleanly).
- **Tests** (`tests/test_web.py`): `/api/config` surfaces and
      round-trips `COSCRIBE_BACKGROUND_ON_CLOSE` without ever marking
      `restart_required`; `/api/memory` GET/POST round-trip `MEMORY.md`'s
      real content on disk.
- **Verified**: `pytest -q` full suite green; `ruff`/`mypy`/`tsc -b`/
      `oxlint` all clean; `cargo check`/`clippy --no-deps`/`fmt --check`
      all clean.

## Phase 8m -- Self-hosted type + a real duplicate-summary bug + user-bubble contrast (shipped)

Asked for broader frontend feedback ("始终差点意思，但又说不清楚" -- always
feels a bit lacking, hard to articulate why). Found one real bug plus two
concrete design gaps, fixed the bug first, then discussed and shipped
both design items together (your call: "方案A，两个都一起做").

- [x] **Duplicate tool-summary bug.** Approving a gated tool call showed
      its collapsed summary twice ("Wrote notes.txt, Wrote notes.txt").
      Root cause: `reducer.ts`'s `tool_result` case only ever matched an
      existing `kind: "tool"` placeholder to merge a result onto: gated
      calls never have one live (only a `kind: "approval"` item exists
      until resolved), so the result always fell through to pushing a
      *second*, brand-new `tool` item describing the same action, and
      `transcriptGrouping.ts` then summarized both. First attempt
      replaced the approval item's `kind` outright with `"tool"` --
      wrong, caught by `approval.spec.ts`'s pre-existing assertion that
      "Approved" stays visible after resolution (would have also
      silently dropped the pptx/docx/xlsx before/after preview fields
      from Phase 8j/8k). Real fix: merge the result *onto* the same
      `approval`-kind item, keeping `status`/`beforePreview`/
      `afterPreview` intact. `approval.spec.ts` updated: a stale
      "Approve tool call: write_file?" assertion replaced with the
      actual current summary text, plus a new regression assertion that
      the doubled phrase never appears.
- [x] **Self-hosted type: IBM Plex Sans/Mono, replacing the bare
      `system-ui` stack.** Via `@fontsource/ibm-plex-sans`/`-mono`
      (OFL-1.1), not a Google Fonts CDN link -- coscribe is local-first/
      offline-capable by design, and a CDN font silently falls back to
      system fonts the moment there's no network, which a self-hosted
      one never does. Same mechanism the app already uses for KaTeX's
      own fonts (`Markdown.tsx`'s `katex/dist/katex.min.css` import) --
      Vite resolves the `@fontsource` CSS's own relative `url()`s and
      copies the referenced woff2 files into the build automatically.
      Retargeted both Tailwind's `font-sans`/`font-mono` utilities (via
      a new `@theme` block -- this project has no `tailwind.config.js`,
      so v4's CSS-first config is the only override point) and plain
      `body`'s own `font:` shorthand, which isn't a Tailwind utility and
      needed the same family listed by hand.
- [x] **User-bubble contrast.** `--user-bubble` used to just alias
      `--card-bg`, ~3% lighter than `--bg` and barely visible against
      it, while the agent's reply had no container at all -- everything
      read at nearly the same visual weight. Repointed `--user-bubble`
      at `--accent-soft`, an existing token from the same red-accent
      palette that no component had actually used until now. Gave the
      agent's reply a subtle `border-l-2` anchor instead of a filled
      bubble to match -- it's the longest, most frequent content on the
      page, so a full tinted background would have been the loudest
      thing in the transcript, not the quietest.
- **Verified live in a real browser**, light and dark: Playwright
      screenshots of an empty thread and a full approval-then-reply
      conversation in both color schemes confirmed the font resolves to
      IBM Plex Sans (`getComputedStyle` check, not just visual read),
      the user bubble now has real contrast against both the page and
      the agent's reply in both themes, the left-border treatment reads
      correctly, and the duplicate-summary fix holds in this same live
      scenario.
- **Verified**: `npm run build` and `npm run lint` clean (caught and
      fixed one CSS bug along the way -- a comment containing literal
      `**bold**` text closed itself early on the `**/` sequence,
      corrupting everything after it into real CSS).
- **`approval.spec.ts` actually run, follow-up.** Originally reported as
      unverified -- no configured LLM provider in that sandbox. You
      pointed out real Gemini/GLM/DeepSeek keys *are* present in the
      environment; ran the whole `frontend/tests/e2e/` suite for real
      against an isolated `coscribe-web` (its own temp workspace/state
      dir, `GEMINI_API_KEY` from the environment, `gemini-flash-latest`)
      + `vite dev`. Found the real regression assertion from this phase
      passes, but the run surfaced two pre-existing, unrelated staleness
      bugs in the e2e suite -- both predate this phase, left over from
      the earlier Nav-rail/Create-Run-split redesign, never caught
      because nothing had actually run these specs against the current
      UI since: `helpers.ts`'s `waitForConnected` waited on a "Sessions"
      button that redesign removed (replaced by `NavRail.tsx`'s
      icon-only, title-attribute-named "Toggle navigation" button) --
      fixed to wait on that instead. Fixing that unblocked every spec,
      which then surfaced `approval.spec.ts`'s own click on the bare
      "Approve" role name resolving to *two* buttons (the real action
      button, and the collapsed summary's own toggle, whose accessible
      name is now "Approve: Wrote ...") -- fixed with `exact: true`.
      And `chat.spec.ts`'s `/clear` test matched "hello" against the
      thread's own header title (a *different*, already-shipped, and
      correct feature -- the header shows the thread's derived title,
      which `/clear` correctly leaves alone) instead of the chat log --
      scoped to `getByTestId("chat-log")`, matching the sibling test
      right above it. Full suite then green: 6 passed, 2 legitimately
      skipped (persona-picker, no personas configured on that temp
      backend).

## Phase 8n -- Composer send button moved inside the input box (shipped)

You pointed at a reference screenshot: a small bordered icon button
sitting inside the text box itself, not the big filled-accent circle
this app had in its own row below the box. Composer.tsx's Send/Stop
buttons moved from that external row into the input box (`absolute
bottom-3 right-3`, same corner the reference showed), restyled from a
`h-[2.1rem]` filled `bg-[var(--accent)]` circle down to a `h-7` bordered
square (`rounded-lg border border-[var(--border)]`, muted text, no fill
until hover) -- and the icon itself changed from a plain up-arrow to
feather's `corner-down-left` ("return"/⏎) glyph, a new `ReturnIcon` in
icons.tsx, since a bare up-arrow read as a generic submit action while
the return-key glyph reads as "press Enter" -- a better fit now that
the button lives inside the text field it submits. Stop kept its own
spinning-ring treatment, resized to the new smaller footprint. Textarea
gained `pr-9` so typed text doesn't run under the button.
- **Verified live in a real browser**, idle/typing/sending states all
      screenshotted against a real backend and matched the reference
      layout; the full `frontend/tests/e2e/` suite re-run afterward
      (role/name locators only, no styling-dependent selectors) stayed
      green -- nothing keyed off the button's old size or position.
- **Verified**: `npm run build`/`lint`/`tsc -b` all clean.

## Phase 8o -- Fixed: pre-tool-call narration glued onto the final reply (shipped)

Follow-up to the diagnosis above (Phase 8n's neighbor, previously left
as "investigated, not yet fixed" pending your go-ahead): you asked to
fix it. Two independent bugs, one on each side, both needed:

- **Backend (`web/session.py`)**: `_stream_turn` now clears its own
  `text_parts` at each `ToolMessage` boundary (`text_parts.clear()`,
  the same point `_capture_segment_usage` already resets `segment` at)
  -- so its return value is always just the *most recent* model
  response's text, not every response of that astream() call
  concatenated (covers an *ungated* tool call, where pre- and post-tool
  narration used to stream within one `_stream_turn` call and get
  glued together internally). `_resolve_pending_approvals` now returns
  only its *last* resumed round's text instead of joining every round
  (covers the *gated* case, where the pre-tool narration comes from a
  separate `_stream_turn` call than the post-tool one) -- returns
  `None` specifically (not `""`) when nothing was pending at all, so a
  caller can tell "nothing to resolve, keep the initial call's text"
  apart from "resolved, and the model said nothing further." All three
  callers (`handle_user_message`'s live-turn handler,
  `resume_after_reconnect`, `_run_workflow_agent_mode`'s scheduled-run
  summary) updated to match -- each now tracks a single "latest reply
  text" variable instead of a `text_parts` list to join.
- **Frontend (`reducer.ts`)**: new `closeStreamingBubble` helper,
  called by the `tool_result`/`approval_required`/`question_required`/
  `error` cases right before each pushes its own new log item --
  finalizes (`streaming: false`) a still-streaming agent bubble that's
  about to stop being the log's last item, so its cursor doesn't stay
  stuck forever and a later `agent_message` doesn't push a *second*,
  disconnected bubble instead of finding nothing to update. This half
  alone doesn't remove the duplicated text (that's the backend fix's
  job) -- it fixes the separate "cursor never clears" symptom, and is
  what makes each narration segment across a multi-segment turn land as
  its own clean, static bubble instead of one of them silently eating
  the next one's `agent_message` update.
- **Tests** (`tests/test_web.py`): two new regression tests, one gated
  (`write_file`, split across two `_stream_turn` calls by an
  approval) and one ungated (`read_file`, both responses within one
  `_stream_turn` call) -- both assert the final `agent_message.text` is
  exactly the post-tool reply, not the glued "pre-tool-text + post-tool-
  text" shape. Verified both actually catch the bug: reverted the
  backend fix locally, confirmed both fail with the exact glued string
  ("I'll write it now.Done, I wrote the file." /
  "Let me check that file.It says hello."), then restored the fix and
  confirmed both pass again.
- **Verified**: `mypy`/`ruff check` on the touched backend file clean;
  `npm run build`/`lint`/`tsc -b` clean on the frontend side; full
  `pytest -q` suite green.

## Phase 8p -- Slack as a curated connector, Phase 5b's simplest possible cut (shipped)

You asked to expand capabilities; discussed Phase 5 (Slack/Asana) as the
concrete next step over a sandbox for `run_python_script`/`run_node_script`.
Your call, going in: "从简处理，除非用户体验极大地被增强" (keep it simple
unless it massively improves the UX) -- checked what Phase 5a's own
"click, get redirected to a login page" OAuth UX would actually require
for Slack specifically before building it, and it doesn't clear that bar
yet:

- **Researched first, not assumed**: read `@modelcontextprotocol/
  server-slack`'s own setup docs (the official server, same publisher as
  the already-catalogued memory/sequential-thinking entries). It
  authenticates with a plain Bot User OAuth Token (`xoxb-...`) the user
  copies from their own Slack app's "OAuth & Permissions" page after
  clicking "Install to Workspace" -- the *exact* shape the old (removed)
  GitHub PAT catalog entry used, not a token exchange coscribe would
  broker. A loopback-redirect flow has nothing to actually buy here:
  there's no client secret, no redirect URI, nothing for coscribe to be
  in the middle of.
- [x] **Added "slack" to `MCP_CATALOG`** (`web/app.py`), `needs_config:
      true` -- reuses the *already-live* Custom-tab prefill mechanism
      (`ConnectorsTab.tsx`'s `prefillCustomForm`) the old GitHub entry
      used, per your explicit call to reuse that path rather than write
      anything new. Clicking "+" switches to the Custom tab pre-filled
      with the command/args/env-var-*names* (`SLACK_BOT_TOKEN`,
      `SLACK_TEAM_ID`, values left blank for the user to paste in) --
      zero new mechanism, same as adding any other catalog entry.
- **Tests** (`tests/test_web.py`): extended
  `test_get_mcp_catalog_returns_curated_entries` to assert the slack
  entry is present with `needs_config: true` and the two expected env
  keys.
- **Verified live in a real browser**: Settings -> Connectors shows the
  slack card; clicking its "+" correctly switches to Custom and
  pre-fills name/command/args and both env var names exactly as
  designed (screenshotted). Could not verify the actual "post a message
  to Slack" round trip -- that needs a real Slack app + bot token only
  you can create; explicitly out of scope for me to fake.
- **Deliberately not built this round**: Phase 5a's generic OAuth
  mechanism (still worth doing *if* a future connector genuinely needs a
  real exchange -- Asana's OAuth is authorization-code-based, unlike
  Slack's bot-token setup, so 5b2 may actually be the case that clears
  the "massively improves UX" bar); Phase 5c (Slack as an approval
  channel) -- depends on this connector actually being used first.
- **Verified**: `pytest -q` full suite green; `ruff check`/`mypy` on
  `web/app.py` clean; `npm run build`/`lint`/`tsc -b` clean.

## Phase 8q -- Office 365 as a curated connector, zero-config (shipped)

Asked what else was worth adding; researched actual market-popular
connectors first (web search + `npm view` on real candidates, not
guessed) rather than picking from memory. Notion and Airtable came up as
same-shape-as-Slack candidates (paste an integration token); Office 365
turned out to be the standout find -- asked to add it first.

- [x] **Added "office365" to `MCP_CATALOG`** (`web/app.py`), using the
      community server `@softeria/ms-365-mcp-server` (MIT, 288 published
      versions, built on Microsoft's own `@azure/msal-node` rather than a
      hand-rolled OAuth client -- included despite not being an
      `@modelcontextprotocol/` package on those health signals, same bar
      the deliberately-excluded Postgres/Filesystem servers above didn't
      clear). Covers Outlook mail/calendar, OneDrive, Excel, OneNote, To
      Do, Planner (personal-account tool set; `--org-mode` for Teams/
      SharePoint left as a manual Custom-tab addition, not the default).
- **The actual find**: unlike Slack/Notion (paste a token) or Google
      Drive (Cloud Console project + OAuth client JSON you download),
      this server ships its own pre-registered Microsoft app and
      authenticates via MSAL's Device Code flow *entirely inside its own
      MCP tools* (`login`/`verify-login`) -- no coscribe-side OAuth
      mechanism, no Settings-tab config, not even `needs_config`: it's a
      plain one-click catalog entry like playwright/memory/time, and the
      agent itself walks the user through sign-in (calls `login`, shows
      a URL+code) the first time it's actually needed. The same device-
      flow shape the removed GitHub catalog entry used, just handled
      entirely by the server instead of by coscribe.
- **Tests** (`tests/test_web.py`): extended the catalog test to assert
      the entry is present with no `needs_config`/`env` keys (unlike
      slack, right above it).
- **Verified live**, actual network round trip, not just reading docs:
      added it for real against the actual npm registry and the actual
      MCP server process (`connect_one_mcp_server_lg` called directly) --
      188 tools discovered, including `office365_login`/`office365_
      verify-login` exactly as expected from the package's own docs. One
      false alarm caught and diagnosed correctly, not just retried blind:
      the first attempt failed with `SELF_SIGNED_CERT_IN_CHAIN` from a
      *stale* backend process's own environment; confirmed `npx` itself
      worked fine run directly, restarted the server fresh, and the
      identical add succeeded -- an artifact of this sandbox's own proxy
      setup, not a real bug in the catalog entry.
- **Verified**: `pytest -q` full suite green; `ruff check`/`mypy` on
      `web/app.py` clean.

---

## Phase 8r -- Persona system removed entirely (shipped)

You found the persona picker "不太顺手" (not smooth) -- it forces a
choice at the start of every new session -- and asked whether it should
be implicit instead, if kept at all. Rather than guessing at what
"implicit" should mean, researched how actual shipped products handle
this: Claude Cowork, ChatGPT (Custom Instructions + Projects), and
WorkBuddy all skip a session-start picker entirely and use one single,
always-on "Custom Instructions"/"Global Instructions" text field
instead. Coscribe already had a direct equivalent -- `MEMORY.md` /
Global Instructions, shipped in Phase 8l, unrelated to this discussion
at the time it was built. You then asked to reshape persona around that
exact Cowork wording ("Instructions here apply to all Cowork
sessions... also not called persona anymore"); offered the choice
between reshaping the mechanism or removing it outright, you picked
full removal.

- [x] **Backend**: deleted `runtime/personas.py` (the `PersonaConfig`
      loader), `tests/test_personas.py`, `scripts/verify_persona_web.py`,
      `personas/ops.md` (and the now-empty `personas/` dir). Removed
      every reference across `config.py` (`personas_dir` setting),
      `runtime/__init__.py`, `cli.py` (`--persona` flag,
      `_resolve_persona` in `--check-wakes`), `web/session.py`
      (`ChatSessionLG`'s `persona` param, `_apply_persona_to_base`,
      `select_persona`, the `persona_name` state field), and `web/app.py`
      (`GET /api/personas`, the `select_persona` WS message, the
      `?persona=` query param, the `.persona` sidecar helpers, the
      thread-listing `persona` field). Also removed a genuinely dead
      -- but real -- `personas_dir=` kwarg from three scripts/tests
      that constructed `Settings` directly.
- [x] **Frontend**: deleted `PersonaPicker.tsx` and its e2e spec.
      Removed `SelectPersonaOut`/`Persona`/`PersonasResponse` types,
      `getPersonas()`, the `personaName`/`persona_name` state field and
      reducer case, the persona badge in `ThreadHeader`, and the
      `personaPicker` prop threaded through `Composer`/`App.tsx`.
- [x] **Docs**: annotated rather than rewrote, per this file's own
      "leave history visible" convention (see the Phase 0 caching bullet
      above for the precedent) -- `runtime_lg/README.md`'s `## Personas`
      section, this file's own Phase 1 above, and the top-level
      `ARCHITECTURE.md`'s pre-existing "superseded, kept as history"
      blockquote all got a note pointing here instead of being deleted
      or silently rewritten. `office-agent/README.md` and
      `office-agent-desktop/src-tauri/src/lib.rs`'s doc comments were
      corrected directly (no history worth preserving there). Also found
      and removed one real, previously-dead line of Rust: the Tauri
      shell (`lib.rs`) was still setting `COSCRIBE_PERSONAS_DIR` for the
      Python sidecar process -- harmless (`Settings.model_config`'s
      `extra="ignore"` swallowed it) but genuinely dead, cleaned up
      regardless.
- [x] Nothing replaced the tool-whitelist-narrowing half of persona
      (the one part Global Instructions didn't already cover) -- judged
      not worth keeping on its own once the instructions half, the part
      that motivated the picker's existence, was gone.
- **Verified**: full backend `pytest -q` -- 824 passed, 1 skipped, clean.
      `ruff check` and `mypy` on every touched Python file clean.
      Frontend `npm run build`, `npm run lint` (oxlint), and `npx tsc -b`
      all clean. Repo-wide `grep -rli persona` re-scan across
      `office-agent/` and `office-agent-desktop/` source turned up
      nothing left except known-unrelated substring matches (`tools/
      spreadsheets.py`'s "personally", `runtime_lg/subagents.py`'s
      "Reviewer persona" -- the `review_work` sub-agent's own fixed
      role, a different concept entirely -- and `web/app.py`'s
      "Personal-account tool set" in the Office 365 catalog entry) and
      this doc's own history. Live-verified against a real running
      `coscribe-web` + `vite dev` (isolated tmp workspace/state/skills
      dirs, real Gemini): fresh page load shows no persona picker and no
      persona badge (header shows only the workspace badge), no
      "persona" text anywhere on the page, sending a message works
      normally, no console/page errors.

---

## Phase 8s -- Live elapsed-time + running token count in the composer (shipped)

Prompted by a screenshot of Claude Code's own status line ("2m 5s · 485
tokens · Waiting for Claude..."), asking whether coscribe could give
similarly timely feedback on a running turn. coscribe had nothing like
this before: a streaming agent bubble (the "▍" cursor) and a Stop button
were the only running-turn feedback, no elapsed time, no live token
count. Scoped down deliberately, on your call: build the two numbers
first, leave Claude Code's expandable "Editing Fundamentals.md ▾"
running action-list for a later pass (needs a live list of in-progress
actions coscribe doesn't track yet -- a separate, larger piece of work
than the two numbers below).

- [x] **Elapsed time**: pure frontend, no backend involvement --
      `RunStatus.tsx` records `Date.now()` the moment `turnInFlight`
      flips false -> true, ticks a `setInterval` every second while it
      stays true, and renders `formatElapsed` (`Xm Ys`, new in
      `lib/format.ts`). Clears back to nothing the moment `turnInFlight`
      goes false again -- no stale leftover reading.
- [x] **Live running token count**: the harder half, since it needed a
      real (small) backend change, not just new frontend state. The
      "usage" WS event (`total_tokens`, drives `ContextRing`) was only
      ever sent *once a whole turn finished* -- `web/session.py`'s
      `_stream_turn` already tracks `_last_usage_metadata` per individual
      model response internally (`_capture_segment_usage`, resets at
      each `ToolMessage` boundary so it never double-counts a multi-step
      tool-calling turn -- see that function's own docstring), it just
      never told the client about each intermediate one, only the final
      total via each caller's own post-turn send. Made
      `_capture_segment_usage` `async` and had it send the same "usage"
      WS event itself, live, at every boundary it already runs at (each
      `ToolMessage`, plus once more at `_stream_turn`'s own return) --
      two one-line `await` call-site changes, no new machinery. The
      existing post-turn sends in the three callers (`_resolve_pending_
      approvals`'s reconnect path, the scheduled-workflow run path, and
      the normal chat path) were deliberately left alone rather than
      removed as "now redundant" -- lower risk than re-auditing every
      caller for a path where that send might be the *only* one that
      fires, at the cost of one harmless duplicate final "usage" event
      per turn (same value sent twice).
      `RunStatus.tsx` renders the delta (`state.totalTokens` at render
      time minus its own snapshot of `state.totalTokens` when the turn
      started) via `formatTokenCount` (moved out of `ContextRing.tsx`
      into the same new `lib/format.ts`, so both components share one
      implementation instead of two copies).
- [x] Wired into `Composer.tsx`'s existing bottom row (mode pill +
      attach on the left, model picker + `ContextRing` on the right) --
      a small muted `<RunStatus>` in the middle, via `justify-between`,
      visible only while `turnInFlight`; renders nothing (not even a
      gap) once the elapsed clock hasn't started or the turn ends.
- [x] **Tests**: `tests/test_web.py`'s tool-call usage-event test
      (previously `test_usage_event_uses_only_the_final_segments_total_
      not_summed_across_a_tool_call`) renamed to `test_usage_events_are_
      live_per_response_not_summed_across_a_tool_call` and rewritten to
      assert the full live sequence -- `[60, 85, 85]` for a two-response
      tool-calling turn (60 live after the first response, 85 live at
      the second/final response, 85 again from the existing post-turn
      send), never `145` (the double-count a naive sum would produce),
      and that the first `60` arrives *before* that call's own
      `tool_result` event -- the actual regression test for "live, not
      batched to the end." The other two existing usage tests (single-
      response send, no-usage-metadata case) needed no changes -- their
      assertions (`next(m for m in messages if ...)`) still hold with
      the extra now-duplicated event in the stream.
- **Verified**: full backend `pytest -q` -- 825 passed, clean. `ruff
      check`/`mypy` on `web/session.py` and the test file clean.
      Frontend `npm run build`/`npm run lint`/`npx tsc -b` clean.
      Live-verified against a real running `coscribe-web` + `vite dev`
      (isolated tmp dirs, real Gemini): sent a message that triggers a
      real tool call (`list_files`) followed by more model output --
      screenshot mid-run shows "35s · 28.5k tokens" next to a still-
      spinning Stop button, confirming both numbers update live during a
      genuinely multi-step turn, not just at the end; a second run
      confirmed the status line appears the instant a turn starts and
      fully disappears (0 DOM matches) the instant it ends, no leftover
      state. No console/page errors either run.
- [ ] **Not built yet, on purpose**: the expandable running action-list
      (Claude Code's "Editing Fundamentals.md ▾" dropdown with a Read/
      Ran a command/Editing history underneath) -- you asked to discuss
      it separately later rather than build it alongside these two
      numbers.

---

## Phase 8t -- run_background_script + wake_on_task (shipped)

You pointed at Claude Code's own "Background tasks" panel (a running
`python3` OCR script, live output, a completion state) and asked whether
coscribe could do the same. Rather than guess at one harness's design,
researched how five actually handle it first: Codex CLI (a newly-added
background mode, model reads logs itself), Claude Code (`Bash
run_in_background` + `BashOutput` polling, no auto-wake), DeepSeek
Harness (containerized shell, no documented background-specific
mechanism), openworker (the "job completion" wake concept coscribe's own
Phase 4 `wake_on(job_id)` already borrowed from it, but nothing at the
raw-shell-command grain), and
[pi-background-tasks](https://github.com/earendil-works/pi) -- the one
of the five that ships this as its own separate tool (not a flag on the
normal exec tool) with durable output files, bounded on-demand reads, and
a completion event that can wake a follow-up turn. You picked Pi's shape
after seeing the comparison.

- [x] **`tools/background_tasks.py`** (new): `BackgroundTask` +
      `BackgroundTaskStore` (one JSON record + one plain-text `.log` file
      per task under `state_dir/background_tasks/`, same shape as
      `WakeStore`'s "one file per request"), plus four tools:
    - `run_background_script(language, script, description,
      timeout_seconds=1800)` -- `async def`, unlike every other built-in
      tool in this codebase (all plain `def`, run via LangChain's own
      thread-pool default): needed to be, so `asyncio.create_subprocess_exec`
      and the supervising `asyncio.create_task` it kicks off both run on
      coscribe-web's real event loop, not a worker thread with no loop of
      its own to schedule onto. Reuses the *exact* same
      `tools/script_env.py`/`tools/node_env.py` environments
      `run_python_script`/`run_node_script` already use (same baseline
      packages, same "manage extras from Settings -> Environment" rule) --
      just via a non-blocking subprocess instead of a blocking
      `subprocess.run`, and a much larger timeout ceiling (1800s default,
      21600s/6h cap, vs. the synchronous tools' 120s/600s) since the whole
      reason to reach for this instead is a script expected to outrun
      those. Returns immediately with a `task_id`, never the output.
    - `check_background_task(task_id, tail_bytes=4000)` -- bounded tail
      read (matches Pi's own `/logs <id> <bytes>`), works whether the
      task is still running (partial output) or finished (full output +
      `exit_code`).
    - `list_background_tasks()` / `kill_background_task(task_id)`.
    - Real bug caught before it shipped, not found live: a naive
      `kill_background_task` that reloaded the task record from disk to
      set `status="killed"` raced its own supervisor coroutine -- the
      supervisor holds a *different* Python object (its own disk-deserialized
      copy from when the task started), so the kill's write would get
      silently clobbered the moment the killed process actually exited
      and the supervisor finalized it as `"failed"` (a killed process's
      exit code looks like any other failure). Fixed by having
      `kill_background_task` mutate the *same* `BackgroundTask` object
      the supervisor itself holds (a small `_LIVE` registry keyed by
      task_id), not a fresh copy -- caught by
      `test_kill_background_task_marks_it_killed_not_failed` before this
      was ever run live. A second real bug caught the same way: the
      supervisor's timeout was originally two separate `asyncio.wait_for`
      calls back to back (drain output, then wait for exit), which could
      let a script run up to *2x* `timeout_seconds` before being killed --
      collapsed into one `wait_for` around both steps together.
    - Fire-and-forget, not fire-and-stream, matching Pi's own choice: no
      live WebSocket push of output while a task runs, only the bounded
      on-demand read above. Simpler, and the frontend needed zero new
      components as a result (see below).
    - **Known v1 limitation, documented not fixed**: a task's live
      `Process` handle only exists in coscribe-web's own process memory.
      A server restart mid-task orphans its on-disk record at
      `status="running"` forever -- no auto-recovery by re-adopting the
      OS process by PID. Same "the desktop app stays running for the
      length of the job" assumption Phase 4's own poller-vs-open-tab
      limitation already accepted, not new to this feature.
- [x] **`wake_on_task(task_id, reason)`** (`tools/selfwake.py`): a new
      `"task"` wake kind alongside `"timer"`/`"job"`/`"event"` -- kept
      separate from `"job"` rather than teaching `wake_on` to also accept
      a `BackgroundTask.task_id`, since the two are different stores with
      different id spaces and `_is_due` needs an unambiguous check for
      each (mirrors why `"job"` itself didn't try to reuse `"timer"`'s
      shape). `runtime_lg/selfwake.py`'s `_is_due`/`poll_due_wakes` got
      the matching `"task"` branch, checking `BackgroundTaskStore` the
      exact same way the `"job"` branch already checks `WorkflowRunStore`
      (`status != "running"` counts as due, including a vanished record).
      `coscribe-web`'s existing background poll loop (and `coscribe
      --check-wakes`) pick this up automatically -- no changes needed on
      either caller.
- [x] **Frontend**: no new components. `run_background_script` reuses
      `ChatLog.tsx`'s existing `run_python_script`/`run_node_script`
      script-approval rendering (same `script`/`description` argument
      shape); `transcriptGrouping.ts` got summary-label entries for the
      four new tools plus `wake_on_task` (`check_background_task`/
      `kill_background_task` needed one each -- no `check_`/`kill_`
      prefix in the generic fallback table; `list_background_tasks`
      didn't need one, its fallback output already matched what a
      hardcoded entry would say).
- [x] **Tests**: `tests/test_background_tasks.py` (new, 18 tests --
      success/failure/timeout/kill/partial-output-while-running/thread-
      scoping/risk-classification/store round-trip, real subprocesses
      against the real script-env venv, skipped offline same as
      `test_scripts_tool.py`); `tests/test_selfwake_tool.py` (`wake_on_task`
      create/validate, added to the risk-classification loop);
      `tests/test_selfwake_resume.py` (`"task"` wake fires once
      finished/stays pending while running, mirroring the existing `"job"`
      pair exactly).
- **Verified**: full backend `pytest -q` -- 845 passed (3 benign warnings,
      not failures: `asyncio`'s own `BaseSubprocessTransport.__del__`
      complaining about a closed event loop when a killed/finished
      subprocess's transport gets garbage-collected after pytest-asyncio
      tears down that test's loop -- a test-harness-only artifact, since
      coscribe-web's own real event loop never gets torn down mid-run the
      way a fresh-loop-per-test runner does). `ruff check`/`mypy src`
      clean. Frontend `npm run build`/`npm run lint`/`npx tsc -b` clean.
      Live-verified against a real
      running `coscribe-web` + `vite dev` (isolated tmp dirs, real
      Gemini): asked the model to call `run_background_script` directly --
      the approval card rendered the script/description correctly (reusing
      the existing script-approval UI), approving it actually ran the
      script in the background, and a follow-up `check_background_task`
      call correctly returned the real captured output ("done sleeping")
      once the sleep finished. No console/page errors.

---

## Phase 8u -- Fixed: Settings tabs went permanently blank on one transient fetch failure (shipped)

First real bug caught from the packaged Windows desktop build, not a
sandbox live-verify: after building via the `desktop-build.yml` GitHub
Actions workflow and running the actual portable `.exe` on real hardware
for the first time, you reported the Connectors tab stuck on "No
connectors match" and Skills flickering between visible and empty across
reopens, both after waiting ~15s for the app to seem to load. Root cause,
found by reading the code rather than guessing: `ConnectorsTab.tsx`/
`SkillsTab.tsx`/`ToolsTab.tsx` all did a bare `getX().then(setX)` inside a
`useEffect` keyed on the tab becoming active -- no `.catch`, no loading
state. A single transient failure (very plausible on a fresh Windows
install: the packaged sidecar's own cold start, or a security agent's
local-traffic interception, racing the Settings modal's first open) left
`catalog`/`skills`/`tools` stuck at their initial empty array forever,
indistinguishable in the UI from "genuinely nothing here" -- "No
connectors match" reads as a real search result, not a failure. This also
explains a separate thing you asked about (whether the Playwright
version-check/update UI had been removed): it hadn't -- that UI only
renders per catalog *entry*, so an empty catalog hid it along with
everything else.

- [x] **`lib/useFetchOnActive.ts`** (new): fetch once on first activation
      (not on every re-activation -- re-fetching on every open is what let
      a transient failure quietly blank an already-successfully-loaded
      list), exposes `status` (`idle`/`loading`/`error`/`success`) and a
      `retry()`. Modeled on `GlobalInstructionsSection.tsx`'s own already-
      correct hand-rolled `loaded`-guard pattern, generalized into a
      shared hook instead of three more copies of the same fix.
- [x] **`FetchRetry.tsx`** (new): shared "Loading…"/"Couldn't load --
      Retry" row, rendered by all three fixed tabs.
- [x] **`ConnectorsTab.tsx`/`SkillsTab.tsx`/`ToolsTab.tsx`**: switched to
      the hook. ConnectorsTab's own "No connectors match." empty state is
      now gated on `catalogStatus === "success"` so it can't render
      simultaneously with the new error state.
- [x] **Verified the exact failure mode, not just the happy path**:
      rebuilt the frontend, ran the real `settings.spec.ts` e2e suite
      (General/Tools tabs) against a real running `coscribe-web` -- both
      still pass unchanged. Then used Playwright's own request
      interception to abort the *first* `/api/mcp/catalog` call only
      (simulating the real transient-failure scenario, not a permanently
      broken backend): confirmed the tab now shows "Couldn't load --
      Retry" instead of "No connectors match", and that clicking Retry
      actually recovers the real catalog. A first attempt at this same
      test showed the *old* buggy behavior even after the fix was
      written -- turned out to be testing against a stale prebuilt
      `web/static/` bundle from before this fix; rebuilding and re-
      running confirmed the real fix.
- [ ] **Not fixed here, same class of bug**: `WorkflowsTab.tsx`,
      `ProvidersTab.tsx`, and `PackageListSection.tsx` (the Environment
      tab's package lists) all have the identical unguarded-fetch
      pattern -- not reported broken, left alone this pass rather than
      widening scope beyond what was actually hit live; flagged to you as
      candidates for the same fix if they ever show the same symptom.

---

## Fix -- Stop button's loading ring wasn't a circle (shipped, not a numbered phase)

Reported (with a screenshot) from the same desktop-testing pass as Phase
8u: "a weird symbol spinning" on the composer's Stop button while a turn
is running. Root cause: `Composer.tsx`'s spinner (the classic CSS trick --
a `border-2 border-transparent` ring with only `border-t` colored, spun
via `animate-spin`) was `rounded-lg`, matching the button it surrounds --
but that trick only reads as a smooth arc on a true circle. On a rounded
*rectangle*, the colored segment is a flat top edge with two corners,
which rotates into a stray blob poking out past a corner instead of an
arc -- confirmed live via Playwright screenshots at several rotation
frames before and after. Fixed by making the ring `rounded-full`
regardless of the button's own `rounded-lg` shape (the standard way this
CSS trick is actually meant to be used) -- re-verified the same way,
clean circular arc at every frame checked, no more protruding blob.

---

## Phase 8v -- Native OS folder picker for the desktop app (shipped, confirmed on real hardware)

You asked for the Workspace picker to use a real Windows folder-browse
dialog instead of `DirBrowserModal`'s own hand-rolled in-app one --
"not friendly, not easy to navigate." Desktop-only: a plain browser tab
running `coscribe-web` has no OS-level picker a served-over-HTTP page
could call, so the in-app browser stays the only option there, unchanged.

- [x] **`office-agent-desktop`**: added `tauri-plugin-dialog` (Cargo.toml
      + `lib.rs`'s `.plugin(tauri_plugin_dialog::init())`). The real work
      was `capabilities/default.json`, not the plugin registration
      itself: that file's own (accurate) docstring already explained why
      it granted nothing beyond `core:default` -- the app's one window
      navigates from the bundled splash page (a real `tauri://` asset,
      gets the IPC bridge for free) to the sidecar's own
      `http://127.0.0.1:<port>` origin once it's up, and Tauri v2 treats
      that as a *remote* origin needing an explicit capability grant
      before any `invoke()` from that page can reach anything -- nothing
      before this ever needed to call `invoke` from the running page, so
      this gap was invisible until now. Added a `remote` grant
      (`"urls": ["http://127.0.0.1:*/"]`, wildcarded since `free_port()`
      picks a fresh port per launch) plus `dialog:allow-open`.
- [x] **`office-agent/frontend`**: added `@tauri-apps/plugin-dialog` (npm)
      and a new `lib/tauri.ts` (`isTauri()` -- checks the real
      `__TAURI_INTERNALS__` bridge, not the `withGlobalTauri`-only
      `window.__TAURI__` the splash page uses, since this project has an
      actual bundler to import the real package through; `pickFolderNative()`).
      `DirBrowserModal.tsx` now tries the native picker first when
      `isTauri()`, calling `onSelect`/`onClose` straight from its result
      and rendering nothing itself while doing so; falls back to
      rendering its existing in-app browser unchanged if the native call
      throws (ACL denied, plugin not registered) or if `isTauri()` is
      false to begin with. Both existing call sites
      (`NewSessionWorkspacePicker`, `WorkspaceTab`'s "Browse...") needed
      zero changes -- same `onSelect`/`onClose` contract either way.
- **Verified**: `cargo check` (office-agent-desktop/src-tauri) compiles
      clean with the new dependency graph (`tauri-plugin-dialog` pulls in
      `rfd`/`tauri-plugin-fs`) -- confirmed by temporarily staging a
      placeholder `binaries/sidecar` file, since Tauri's own build script
      validates that resource exists at *compile* time regardless of
      whether a real sidecar was built (removed again after, not
      committed). Frontend `npm run build`/`npm run lint`/`npx tsc -b`
      clean. Live-verified the *fallback* half of this end to end (the
      only half a Linux sandbox with no Tauri runtime can exercise): a
      real running `coscribe-web` in a plain browser tab still shows the
      unchanged in-app browser at both call sites, `isTauri()` correctly
      reads false there.
- [x] **Confirmed on real Windows hardware**: rebuilt via
      `desktop-build.yml`, clicked the Workspace picker, a real native
      Windows folder-browse dialog opened -- the `remote` capability
      grant's wildcarded-port `urls` pattern works despite Tauri v2's own
      docs/community reports flagging it as a rough, not fully
      spec-compliant edge (see the commit's own references). The
      in-app-browser fallback path (ACL denied/plugin not registered)
      remains unexercised on real hardware, but with the primary path
      confirmed working there's nothing left pushing it to fire.

---

## Phase 8w -- Workspace picker is opt-in now, not forced on every new thread (shipped)

Live-tested Phase 8v, then raised a separate complaint about the picker
itself: every brand-new thread force-opened it immediately (the old
`NewSessionWorkspacePicker`), before you'd typed a word. Pointed at
Cowork's own pattern (a task starts usable with nothing connected; a
"Link to this computer" prompt only appears once you actually try to use
a local folder, via its own "Add folder" button) as the better default.
coscribe already had everything needed to make this safe: a thread with
no explicit choice already silently uses `settings.workspace_root`'s
global default (`_resolve_workspace`'s own "first choice wins, then
sticks" contract, unchanged) -- forcing the picker was pure UX, not
covering a real "nothing to fall back to" gap.

- [x] **Removed** `NewSessionWorkspacePicker.tsx` and `lib/ws.ts`'s
      `isNewThread()` (its only caller) entirely -- no replacement
      component; deleted, not disabled.
- [x] **`ThreadHeader.tsx`'s workspace badge is now the one entry
      point**, made clickable (opens the same `DirBrowserModal` used
      everywhere else, so it automatically gets Phase 8v's native-picker
      attempt too) -- but only while the thread's choice is still
      changeable. A second `select_workspace` on a thread that already
      has one is rejected server-side (unchanged, pre-existing
      contract); rendering the badge as an inert `<span>` once that's
      true avoids inviting a click that could only ever fail, rather
      than reusing send_state's `total_tokens`-style "just fires an error
      the user reads after the fact" pattern.
- [x] **New wire field**: `StateEvent.workspace_explicit` (`web/session.py`'s
      `send_state`, `ChatSessionLG._workspace_explicit` was already
      tracked internally, just never surfaced) -- the frontend's only way
      to tell "real per-thread choice" apart from "still the global
      default", since `workspace_root`'s own value can't (a user can
      deliberately pick the same path the default already points at).
      `reducer.ts` gained the matching `workspaceExplicit` field; also
      removed `stateReceived`, which turned out to be dead the moment
      `NewSessionWorkspacePicker` was -- its only consumer.
- **Verified**: backend -- `tests/test_web.py`'s existing workspace-flow
      tests extended with `workspace_explicit` assertions (query-param
      connect -> True, no-choice-yet -> False, live `select_workspace` ->
      True), full suite 845 passed. Frontend `npm run build`/`npm run
      lint`/`npx tsc -b` clean. Live-verified the whole flow against a
      real running `coscribe-web`: a brand-new thread (no `?thread=` at
      all) shows *no* popup and a clickable badge; clicking it opens the
      picker; picking a folder updates the badge and turns it into
      plain, non-clickable text -- confirmed via the raw WebSocket frames
      themselves, not just the rendered DOM, after catching a real test-
      script mistake (checking the DOM only ~500ms after picking, when
      `select_workspace` server-side actually takes ~2s -- it rebuilds
      the whole coordinator agent, not a cheap field write).

---

## Phase 8x -- Browser panel: a live, controllable embedded browser (shipped)

Asked for after Phase 8v/8w shipped, alongside the first-run API-key
item (that one is being built next, in parallel with you testing this).
Pointed at Claude Cowork's own Browser panel as the reference: a
right-docked panel toggled from the header, a real page rendered live
(not described in text), with a "select element" tool -- pick something
on the page the way F12's own element inspector does, and hand its
screenshot + text straight to the chat -- so configuring browser
automation stops being "describe the page to the model and hope," which
is what driving it purely through the existing `playwright` MCP catalog
entry already means today.

- [x] **`web/browser_panel.py`** (new) -- a hand-rolled Chrome DevTools
      Protocol client over a plain WebSocket (the `websockets` package,
      now a direct `web` extra dependency, not just transitive via
      `uvicorn[standard]`), deliberately *not* the `playwright` Python
      package: playwright's own bundled Node.js driver is a known
      PyInstaller packaging headache (`--collect-all playwright` and
      friends) for a feature that only needs a thin slice of the
      protocol -- launch, screencast, navigate, input, one element
      lookup. Launches a real, headless, throwaway-profile browser
      (reuses `browser_detect.find_windows_browser`, same lookup the
      MCP catalog's own browser-dependent entries already rely on;
      `COSCRIBE_BROWSER_PANEL_EXE` is an undocumented dev-only override
      for testing this off Windows). `Page.startScreencast` streams
      frames back with backpressure (`Page.screencastFrameAck` per
      frame, or CDP stops sending); `Runtime.evaluate` +
      `document.elementFromPoint` + a clipped `Page.captureScreenshot`
      implements element picking. `--no-sandbox` is appended to
      Chrome's own launch args only when already running as root
      (`os.geteuid() == 0`) -- true in this sandbox, never true for a
      real Windows desktop-app user, so this doesn't weaken the real
      deployment.
- [x] **Caught and fixed a real deadlock during development**: the
      frame-ack handler originally called the class's own round-trip
      `send()`, which awaits a response only the same reader loop can
      deliver -- froze solid on the very first screencast frame. Fixed
      by splitting a fire-and-forget `_write()` out from `send()`,
      used only for the ack. Found via a standalone smoke script driving
      a real headless Chromium, not by inspection.
- [x] **`/ws/browser`** (new route, `web/app.py`) -- deliberately a
      separate socket from `/ws/{thread_id}`, not new message types
      bolted onto it: screencast frames are a different traffic shape
      (many small JPEGs a second, independent of any chat turn) from
      the chat protocol's own carefully-paced stream, and mixing them
      risked one starving the other. One `BrowserPanelSession` per
      connection, closed the moment it drops -- a companion tool, not
      conversation state, so nothing about it is thread-scoped or
      persisted.
- [x] **Caught a routing bug the same way**: `/ws/browser` was first
      registered *after* `/ws/{thread_id}`, so Starlette's own
      route-order matching sent every `/ws/browser` connection into
      `ws_endpoint` instead, with `thread_id="browser"` -- it never
      reached the new handler at all. Caught by a test asserting the
      route's own message contract, which failed with
      `resolve_chat_model` rejecting `"browser"` as a model id -- proof
      of exactly where the request actually landed. Fixed by moving the
      `/ws/browser` registration above `/ws/{thread_id}`.
- [x] **`BrowserPanel.tsx`** (new) -- the right-docked panel: an address
      bar (Go/reload), a canvas rendering the live screencast (JPEG
      frames drawn as they arrive, mouse/wheel/keyboard events
      rescaled from CSS pixels back to remote coordinates and forwarded
      over the same socket), and a "Select" toggle that, on the next
      click, asks the backend to pick whatever's under that point
      instead of clicking it for real -- shows a preview card (cropped
      screenshot + text) with Discard/"Add to chat". Toggled from a new
      header button (`BrowserIcon`, `icons.tsx`) next to Settings.
- [x] **`Composer.tsx`** gained `externalImage`/`onExternalImageConsumed`
      props -- the same shape as a drag-and-dropped image, just driven
      by a prop from `App.tsx` instead of a DOM event, so "Add to chat"
      lands the picked element straight into the existing
      `pendingImages` row with no new UI or code path of its own.
- **Deliberately narrow for v1, not built**: no cookie import, no
      annotate/freehand-drawing tool, no back/forward history (reload +
      re-navigate covers the common case) -- Cowork's panel has all
      three; skipped here to ship the core "see it live, point at
      something" loop first rather than block on the full feature set.
- **Verified**: backend -- `ruff check`/`mypy src` clean;
      `tests/test_browser_panel.py` (new) covers
      `find_browser_executable`'s env-override escape hatch and the
      `/ws/browser` route's own message-routing/error contract against
      a fake `BrowserPanelSession` (launch failure closes the socket
      with an error; a mid-session `BrowserPanelError` reports and
      keeps the socket open; navigate/reload/mouse/key/text/pick_element
      all dispatch to the right session call) -- this is what caught
      the route-ordering bug above. Full suite still green. Frontend
      `npx tsc -b`/`npm run lint`/`npm run build` clean. Live-verified
      end to end against a real running `coscribe-web` with a real
      headless Chromium (`COSCRIBE_BROWSER_PANEL_EXE` override) driven
      by Playwright: opened the panel, navigated to a real page and
      watched it render live via the screencast, clicked and typed
      inside the embedded page (confirmed by the page's own JS echoing
      the typed text back), picked an element and confirmed its preview
      card, and confirmed "Add to chat" actually lands the image in the
      composer's pending-attachments row.

**Follow-up, from real-hardware testing (same phase, not a new one) --
two real bugs, both fixed:**

- [x] **Fixed: the embedded page rendered visibly squashed sideways.**
      Root cause: `launch()` fixes the remote page's own emulated
      viewport at 1280x800 (landscape) once, at startup, and never
      again, while the panel's own canvas was CSS-stretched
      (`width/height: 100%`) into whatever box its flex layout actually
      gave it -- a narrower, taller column in the real panel. The two
      aspect ratios disagreed, so every frame got squashed on the way
      in. Fixed by keeping the two in lockstep instead: a
      `ResizeObserver` on the panel's own canvas container measures its
      real on-screen size and sends a new `resize` WS message on every
      change (plus once on connect, in case the observer's first firing
      raced ahead of the socket opening); `browser_panel.py` gained a
      matching `resize(width, height)` method that just re-applies
      `Emulation.setDeviceMetricsOverride` with the reported size. The
      canvas's own `width`/`height` attributes (its backing pixel
      buffer, not its CSS box) are kept equal to the same measurement,
      so `drawFrame`'s `ctx.drawImage` is always drawing into a buffer
      whose aspect ratio already matches the frame it's given.
- [x] **Added: a live hover highlight in Select mode, matching a real
      DevTools element inspector.** You pointed out the original
      click-only "Select" didn't preview anything before you committed
      -- unlike F12's own inspector, which highlights whatever's under
      the cursor as it moves. `browser_panel.py`'s `pick_element` JS
      lookup (`document.elementFromPoint` + `getBoundingClientRect`)
      was factored into a shared `_element_at` helper, reused by a new
      `hover_element` method (same lookup, no screenshot -- cheap
      enough to call continuously) and unchanged by `pick_element`
      itself. The frontend throttles `mousemove` in Select mode to one
      `hover_element` round trip per ~60ms (each is a real CDP call
      relayed through `/ws/browser`'s single-message-at-a-time loop; at
      full mousemove rate the highlight would visibly lag the cursor)
      and draws the returned element's rect as a bordered overlay `div`
      over the canvas, scaled from remote coordinates back to display
      coordinates. A click still commits the pick exactly as before.
- **Verified**: backend -- `ruff check`/`mypy src` clean;
      `tests/test_browser_panel.py` extended with `resize`/
      `hover_element` coverage in the route's own message-dispatch
      test. Full suite still green. Frontend `npx tsc -b`/`npm run
      lint`/`npm run build` clean. Live-verified against a real running
      `coscribe-web` + headless Chromium again: confirmed
      `canvas.width`/`height` now equal the canvas's own
      `getBoundingClientRect()` exactly (no stretch left to do);
      screenshotted a test page with a grid pattern and readable text
      before/after -- text renders at correct proportions, not
      stretched; moved the cursor over a real element in Select mode
      and confirmed a tightly-fitted highlight box tracks it live,
      updating as the cursor moves, before a click commits the same
      cropped screenshot as before.

**Second follow-up (same phase) -- two small UI asks plus a real,
subtle coordinate-accuracy bug the UI asks accidentally surfaced:**

- [x] **Hover tooltip**: the highlight box now carries a small
      `tag width×height` label next to it (e.g. `button 161×67`),
      matching DevTools' own hover tooltip -- `hover_element`'s
      response already carried `tag` and `rect`, just unused by the
      frontend before; this is a pure frontend addition, no backend
      change. Clamped into the canvas's own bounds (not just flipped
      above/below the box) -- a full-page element like `<body>` has no
      "above" *or* "below" that isn't already off both edges, and the
      panel's own `overflow-hidden` silently clips anything past them;
      caught live while testing against the panel's own `<body>`.
- [x] **Drag-to-resize**: a thin handle on the panel's own left edge
      (straddling the border, `cursor-crosshair`-style affordance on
      hover) drives a `panelWidth` state between 320-900px. No new
      wiring needed on the sync side -- the resize already flows
      through the same `ResizeObserver` -> `resize` WS message ->
      `Emulation.setDeviceMetricsOverride` path Phase 8x's own
      distortion fix built, so a drag just becomes another size for
      that same pipe to carry.
- [x] **Found via that same testing, not asked for: hover/pick
      coordinates were silently wrong**, even after the aspect-ratio
      fix above. Symptom: hovering dead-center over a real button
      consistently reported `<body>` instead, no matter how carefully
      targeted (confirmed with pixel-perfect targeting -- scanned the
      canvas for the button's own rendered pixels to rule out a test-
      script aiming mistake). Root-caused with a standalone CDP script
      isolated from the rest of the app, three layers deep:
      1. **Screencast frames don't track `Emulation.setDeviceMetricsOverride`
         changes at all** -- confirmed live: a page emulated at 439x816
         kept screencasting frames at some earlier, unrelated size
         (780x441 the first time, matching neither the new nor the old
         viewport) while `Page.getLayoutMetrics` and a one-shot `Page.
         captureScreenshot` both correctly reported 439x816 throughout.
         `drawFrame`'s own `ctx.drawImage(img, 0, 0, canvas.width,
         canvas.height)` force-stretches whatever it's given to fill the
         canvas, so this *looked* fine (no visible squashing, since a
         wrong-but-proportional source still stretches cleanly) while
         every click/hover coordinate computed against that canvas was
         quietly wrong. Fixed: `Page.startScreencast` now always passes
         `maxWidth`/`maxHeight` pinned to the current viewport, and
         `resize()` stops and restarts the screencast (CDP has no
         "update the running screencast's own resolution" call) instead
         of assuming it'll pick up a later `Emulation` change on its own.
      2. **Headless Chrome's own window/compositor surface doesn't
         track it either** -- launching with no explicit `--window-size`
         left screencast frames additionally capped by the real
         (small, default) browser window, independent of any viewport
         override. Fixed: launch with a fixed, generous
         `--window-size=1920,1200` -- comfortably above
         `BrowserPanel.tsx`'s own `MAX_PANEL_WIDTH`, so no real panel
         size ever needs more.
      3. **`Page.navigate` (and defensively `Page.reload`) resets
         whatever internal state the screencast uses to size its own
         frames, more than once and asynchronously** -- confirmed the
         hard way: reapplying `Emulation.setDeviceMetricsOverride`
         immediately after `Page.navigate`'s own response still left a
         *later* frame reverted (arriving tens to ~150ms after, close
         to when `Page.loadEventFired` fires); waiting for that load
         event before resyncing helped but still didn't hold reliably
         under repeated live testing -- something can revert it again
         shortly after even that. Chased exact CDP event timing further
         than was productive; settled on a **self-healing** fix instead
         of one more precisely-timed one-shot resync: every incoming
         screencast frame's real JPEG dimensions are now read straight
         out of its own SOF header (`_jpeg_size`, no full image decode)
         and compared against the viewport we last asked for; a
         mismatch schedules a fire-and-forget resync (reapply metrics,
         restart the screencast). Converges within a frame or two of
         any future desync, from navigate/reload or any other trigger
         not yet identified, without needing to keep chasing this
         particular Chromium build's exact internal timing.
- **Verified**: backend -- `ruff check`/`mypy src` clean, full suite
      green. Frontend `npx tsc -b`/`npm run lint`/`npm run build` clean.
      Live-verified end to end again, this time checking the actual
      *data*, not just the screenshot: captured the real `/ws/browser`
      WebSocket traffic (Playwright's own `page.on("websocket")`) to
      confirm `hover_element` at a button's true center now returns
      `{"tag":"button","rect":{"x":40,"y":40,"width":160.67,"height":67}}`
      -- matching a direct CDP query of the same page byte for byte --
      instead of `<body>`; confirmed via a standalone CDP script that a
      screencast frame's own decoded JPEG size can be read directly and
      compared against the emulated viewport, which is what the shipped
      self-healing check does. Re-ran the full pick/"Add to chat" flow
      and the drag-to-resize handle afterward -- both still work with
      the coordinate fix in place, confirmed via screenshots showing the
      highlight box and its tooltip now precisely wrapping the real
      button, not floating off to one side of it.

**Third follow-up (same phase) -- verified against a real external site,
then added back/forward history navigation:**

- **Verified against a real website, not just synthetic test pages.**
      You asked to see google.com load and an icon get picked before
      committing to another packaging round. This sandbox's own egress
      proxy blocks arbitrary outbound domains outright (confirmed:
      google.com resets the connection at the TCP level, independent of
      any certificate handling), so google.com itself isn't reachable
      from here -- a sandbox-only constraint, not something the shipped
      app does differently for one domain vs another; a real Windows
      machine's normal network has no such restriction. Verified the
      same code path instead against `pypi.org` (one of this sandbox's
      own whitelisted domains, reachable with a diagnostic-only
      `--ignore-certificate-errors` launch flag added just for this
      check, never part of the shipped `launch()` args): the real PyPI
      homepage rendered with no distortion, a full-page coordinate scan
      correctly located the PyPI logo `<a>` element by tag and pixel
      rect, and `pick_element` produced a clean, precisely-cropped
      screenshot of just that icon -- run through the exact
      `BrowserPanelSession` class that ships, not a reimplementation.
- [x] **Back/forward history navigation** -- the one item Phase 8x's
      own docstring had explicitly deferred ("CDP has no direct
      Page.goBack -- needs Page.getNavigationHistory +
      Page.navigateToHistoryEntry, skipped for now"). Built now:
      `go_back`/`go_forward` (`browser_panel.py`) fetch the entry list
      via `Page.getNavigationHistory` and jump with
      `Page.navigateToHistoryEntry`, routed through the same
      `_navigate_and_resync` helper `navigate()`/`reload()` already use
      -- a history jump is still a real navigation, subject to the same
      screencast-desync class of bug the second follow-up fixed (the
      self-healing frame-size check is the real backstop either way).
      New `‹`/`›` toolbar buttons in `BrowserPanel.tsx`; a request past
      either end of history raises `BrowserPanelError` ("No page to go
      back to"/"...forward to"), surfaced the same way any other
      routine action error now is (see below).
- [x] **Fixed, while wiring the above up: any `BrowserPanelError` was
      hiding the entire live view**, not just a real launch failure.
      `pick_element` finding nothing already had this latent bug;
      back/forward running out of history was going to hit it
      constantly (a completely routine "you're at the oldest page"
      outcome, not a failure). The frontend's own `"error"` WS handler
      unconditionally set `status="error"`, which replaces the whole
      canvas with a full-panel message -- appropriate for "the browser
      never launched," wrong for "there's nothing to go back to."
      Split it: an error while still `"connecting"` stays fatal
      (unchanged); anything after that becomes a small dismissible
      banner (`actionError`, auto-clears after 4s or on manual dismiss)
      that sits above the still-live canvas instead of replacing it.
- **Verified**: backend -- `ruff check`/`mypy src` clean;
      `tests/test_browser_panel.py` extended with `back`/`forward`
      routing coverage. Full suite green (860 passed). Frontend
      `npx tsc -b`/`npm run lint`/`npm run build` clean. Live-verified
      against a real running `coscribe-web`: navigated A -> B, clicked
      Back and landed on A (confirmed by content, not just believing the
      button did something), kept clicking Back past the start of
      history and got the new dismissible banner instead of a wiped-out
      panel (the still-visible live view was correctly showing
      `about:blank`, the browser's own real oldest history entry, not a
      rendering bug), then Forward and landed back on A from there --
      matching the real `[about:blank, A, B]` history exactly.

**Fourth follow-up (same phase) -- real-hardware regression report against
Claude Cowork's own panel: 卡顿 (stutter), 分辨率低 (low resolution), only
part of the page ever visible with a stray horizontal scrollbar, and a
much smaller drag-resize range than Cowork's. Root-caused, not just
patched around the symptom:**

- [x] **Fixed: no devicePixelRatio awareness (the resolution complaint).**
      `Emulation.setDeviceMetricsOverride`'s `deviceScaleFactor` was
      hardcoded to `1` throughout -- on any Windows display with real
      scaling set (125-200% is the ordinary case, not an edge one), the
      remote page rendered at this panel's CSS-pixel size with no
      awareness of the OS then stretching that same box to more physical
      pixels to fill the screen, which is indistinguishable from "just
      looks blurry." `resize()` now takes the frontend's own
      `window.devicePixelRatio` as a `scale` parameter, threaded through
      `Emulation.setDeviceMetricsOverride` and `Page.startScreencast`'s
      own `maxWidth`/`maxHeight` alike (both need it -- confirmed
      already, from the second follow-up, that screencast frame sizing
      doesn't track viewport changes on its own); the self-healing
      frame-size check gained the same awareness so it doesn't treat a
      correctly-scaled frame as a mismatch. The canvas's own backing
      buffer is now `canvasSize x devicePixelRatio`, its CSS display
      size the original, unscaled `canvasSize` -- the standard "retina
      canvas" pattern.
- [x] **Fixed: the canvas's CSS size came from `w-full h-full`
      (percentage of parent) while its `width`/`height` *attributes*
      (now larger still, post-DPI-fix) set its intrinsic content size --
      two different sizing mechanisms relied on resolving to the same
      number.** Best-reasoned cause of "only shows part of the page,
      can't scroll back left, and the drag handle barely does anything"
      together: a canvas is a CSS "replaced element" (like `<img>`), and
      its intrinsic size (from attributes) is a known edge case for
      leaking into an ancestor flex container's own layout sizing in
      some engines unless every size in the chain is unambiguous --
      plausible, not confirmed against the actual WebView2 rendering
      this real report came from (this sandbox's own Chromium never
      reproduced it directly). Fixed regardless, and made strictly more
      robust either way: the canvas's CSS size is now set as an explicit
      inline `style.width`/`style.height` in real CSS pixels, matching
      `canvasSize` exactly -- no percentage resolution, no reliance on
      any ancestor's own stretch behavior, nothing left ambiguous for a
      differently-behaved engine to get wrong.
- [x] **Fixed: a likely real contributor to "卡顿" (stutter)** -- every
      single `ResizeObserver` firing was sending its own `resize` WS
      message immediately, and each one is a real round trip that stops
      and restarts the entire backend screencast (CDP has no "just
      change the resolution" call). A window resize or an active drag
      can fire that callback many times a second; debounced to one send
      200ms after the last firing -- `canvasSize` (state) still updates
      on every single callback so the panel's own on-screen size tracks
      a drag smoothly regardless, only the expensive backend round trip
      now waits for things to actually settle.
- [x] **Fixed: the drag-resize ceiling was a flat, hardcoded 900px** --
      already binding on a modest window, needlessly conservative on a
      large one, and part of why "Cowork可以拉得很宽" (Cowork's own panel
      can be dragged much wider) read as a real gap rather than a
      preference. Now computed from the window's own current width
      (`window.innerWidth - 300`, capped at a sanity ceiling of 1600)
      instead of a constant.
- [x] **Fixed defensively, not confirmed as the cause: `_read_loop`
      could die from a single bad message.** The screenshot you sent
      showed the panel in its fatal "Browser connection lost" state
      mid-session -- a real WS-transport-level closure, not a
      `BrowserPanelError` (those show the small banner now, see the
      third follow-up). `_read_loop` is the *only* thing that ever reads
      off the CDP socket -- every pending `send()`, every screencast
      frame, every load-event wait goes through it -- so any single
      unexpected exception there (a malformed frame, some CDP response
      shape not accounted for) used to take the whole task down
      permanently, which looks exactly like this from the frontend's
      side once nothing more ever arrives. Now wrapped per-message:
      logged and skipped instead of fatal, so one bad message can no
      longer end a session that's otherwise fine. Not confirmed as *the*
      cause of what you hit (no server-side log from that session to
      check), but a legitimate robustness gap either way, worth closing
      regardless of whether it explains this specific report.
- **Not fixed, only reasoned about**: the underlying architecture --
      real Chromium screencasted as JPEG frames over a WebSocket, decoded
      and redrawn onto a `<canvas>` -- is inherently going to feel less
      smooth than Cowork's own panel, which is almost certainly a native
      embedded WebView composited directly by the OS, not a frame-by-
      frame image stream. The debounce above removes one real, avoidable
      source of stutter; it doesn't change that this is a JPEG-streaming
      architecture by design (see browser_panel.py's own module
      docstring for why: avoiding Playwright's own PyInstaller-packaging
      weight). If perceived smoothness is still meaningfully behind
      Cowork's after this, that's the architecture, not a bug to chase
      further inside it.
- **Verified**: backend -- `ruff check`/`mypy src` clean;
      `tests/test_browser_panel.py` extended to cover `scale` being
      threaded through the route's `resize` dispatch. Full suite green.
      Frontend `npx tsc -b`/`npm run lint`/`npm run build` clean.
      Live-verified with Playwright's own `deviceScaleFactor: 1.5`
      (simulating a 150% Windows display) against a real running
      `coscribe-web`: canvas backing buffer scaled to 659x1224 for a
      439x816 CSS box (`659/439 ≈ 1.5`, `1224/816 = 1.5`, matching
      exactly), confirmed `document.documentElement.scrollWidth`
      equals `clientWidth` (no horizontal overflow), and re-ran the
      full hover/pick flow at that same scale -- still landed exactly
      on the real button (`{x:40,y:40,width:160.67,height:67}`), same
      as the unscaled case, confirming the coordinate math (now
      deliberately scaled against `canvasSize`, not the now-larger
      backing-buffer attributes) still tracks the remote page correctly.

---

## Phase 8y -- First-run API key setup, no more hand-edited .env (shipped)

The other half of the same feedback message Phase 8x came from: "首次用户
使用让配置api key... 现在还是靠.env" -- a fresh install with no
`COSCRIBE_DEFAULT_MODEL`/API key configured used to crash the whole
process outright the moment `Settings()` validation failed
(`cli.py`'s `_load_settings`), printing "Configuration error" to a
console the desktop build's user never sees. office-agent-desktop's own
splash page (`dist/index.html`) has no way to tell that apart from any
other startup failure, so it just showed "coscribe couldn't start...
check the log" and pointed at a log file -- a real dead end, exactly the
`Configuration error` traceback you first hit testing the packaged app,
several phases back.

- [x] **`cli.py`**: split `_load_settings`'s body into a shared
      `_prepare_env()` (resolve `.env`'s real location, load it, resolve
      keyring-ref sentinels) plus the existing exiting variant, and a new
      `_load_settings_or_none() -> Settings | None` that returns `None`
      on the same `ValidationError` instead of printing and exiting. The
      CLI itself is unchanged -- a terminal is already the right place to
      fix a bad `.env` by hand.
- [x] **`web/app.py`**: `create_setup_app(configured: asyncio.Future[Settings])`
      -- a small, separate FastAPI app (not routes bolted onto
      `create_app_lg`, which assumes a valid `Settings` as a constructor
      parameter) serving one self-contained page (no build step, no
      external font/asset fetch -- deliberately has to work before the
      built frontend bundle is guaranteed to mean anything): pick a
      provider, paste a model + API key, `POST /api/setup`. That handler
      writes `.env` the same way `add_provider` already does for an
      already-running app (`env_value_for_storage`, keyring-backed when
      available), then re-resolves real `Settings` and resolves
      `configured` with them.
- [x] **`main()` rewritten as `_run_web_server`**, an async function
      managing two `uvicorn.Server` instances by hand instead of the
      simpler blocking `uvicorn.run(app, ...)`: serves the setup app on
      the target host/port until `configured` resolves, stops it
      (releasing the port), then serves the real app on the *same*
      host/port -- same process, same PID, throughout. Deliberately not
      a process restart (`os.execv`/similar) once configured: confirmed
      that's unsafe specifically for this desktop app -- `os.execv` is a
      true in-place replacement on POSIX, but the Windows CRT only
      emulates it as spawn-then-exit-the-original, which would change
      the PID `office-agent-desktop`'s Rust side tracks (`ServerProcess`)
      for kill-on-quit, and Windows is the one platform this ships on.
      The brief gap between the two servers binding is invisible in
      practice: both the desktop shell's splash page and the setup
      page's own client-side poll loop already retry through a
      connection-refused blip as "still starting," not a new failure
      mode either page needed to learn.
- **Verified**: backend -- `ruff check`/`mypy src` clean.
      `tests/test_setup_app.py` (new, 9 tests): `_load_settings_or_none`
      returns `None`/`Settings` correctly; `create_setup_app`'s own
      routes (served page, rejecting blank/unknown-provider/API-key
      input without resolving `configured`, writing `.env` and
      resolving `configured` on success, rejecting a second submission
      once already configured); one full `_run_web_server` integration
      test against a real socket, proving the handover itself (setup
      page -> `POST /api/setup` -> the real chat app answering on the
      identical host:port, no process restart). Caught a real test-
      isolation bug while writing these: `load_dotenv(..., override=True)`
      writes straight into the real `os.environ`, which `monkeypatch`
      cannot track or revert -- an earlier test's configured provider/
      model leaked into a later test's supposedly-fresh environment
      until each test started explicitly clearing those specific keys.
      Full suite green. Live-verified end to end via Playwright against
      a real running process (isolated `HOME`/cwd, no real API call):
      the setup page renders correctly, the model placeholder updates
      per provider, submitting blank fields shows an inline error with
      no network call, and submitting real-shaped values hands over to
      the actual chat app on the same page/port with no manual restart
      or reload beyond the page's own automatic one. Not verified: the
      page's dark-mode palette (only exercised under the default light
      `prefers-color-scheme`) and the real desktop-shell splash-page
      handoff (this sandbox has no Windows/Tauri runtime -- same
      standing limitation as every other desktop-shell change this
      session).
- **Not built, on purpose**: no in-page "test this key" call before
      saving (the very first real chat turn is that test, same as
      hand-editing `.env` already was); no custom-OpenAI-compatible
      provider option on this page (Settings -> Providers already covers
      that once the app is running at all -- this page only needs to get
      a user from zero to *any* working provider).

---

## Phase 8z -- UX polish pass: greeting, unified delete confirmation, shortcuts help, friendlier setup copy (shipped)

A joint brainstorm on general UX gaps across the app (prompted by "还有其他
功能的用户体验...和我一起想想哪些地方可以提升用户体验度"), narrowed down to
four approved items -- a fifth (a unified toast/notification system) was
explicitly deferred for deeper discussion, not built here.

- [x] **Time-of-day greeting** (`EmptyState.tsx`): the new-thread empty
      state's static "coscribe" heading is now `greetingForHour(new
      Date().getHours())` -- "Good morning"/"Good afternoon"/"Good
      evening" (and "Good night" past 11pm/before 5am), using the
      browser's local hour, not the server's. The "coscribe" name itself
      moved into the description paragraph's opening words rather than
      being dropped outright.
- [x] **Unified delete confirmation** (`ConfirmDialog.tsx`, new shared
      component): replaces three different prior patterns -- Provider
      delete, which had *no* confirmation at all (the most urgent case,
      an immediate delete on click), and Workflow delete /
      Workflow-run delete, both raw unstyled `window.confirm()`. All four
      delete sites (Provider, Workflow, Workflow-run, and Thread --
      `NavRail.tsx`'s pre-existing `DeleteThreadDialog` was folded into
      the same shared component) now go through one styled modal
      matching `SettingsModal`'s overlay/card convention.
- [x] **Keyboard shortcuts help** (`ShortcutsDialog.tsx`, new): none of
      the app's existing shortcuts (Enter to send, Shift/Ctrl+Enter for a
      newline, Escape to cancel an edit or close autocomplete, "/" for
      commands, 1-9 in the open model picker) were discoverable anywhere
      in the UI short of reading source. Opened via a new "?" icon button
      in the header (next to Settings) or by pressing "?" itself (ignored
      while focus is in an input/textarea, so typing a literal "?" still
      works); Escape closes it. Purely documents the existing key
      handling in Composer.tsx/ChatLog.tsx/ModelPicker.tsx -- none of
      that handling changed.
- [x] **Friendlier first-run setup page** (`app.py`'s `_SETUP_PAGE_HTML`):
      reframed as "Welcome to coscribe" with a short explanation of *why*
      a provider is needed and a privacy reassurance (written locally,
      never sent anywhere but the chosen provider), grouped into two
      numbered steps ("1 Choose a provider", "2 Add your API key"), and a
      one-line description per provider under the dropdown. The model
      field now prefills its actual value (not just a placeholder) with
      a sensible default the moment a provider is chosen -- tracked via a
      `modelTouched` flag so a manual edit sticks across further provider
      switches -- turning "pick a provider, paste a key" into enough to
      finish setup in the common case. Explicitly **not** done, per a
      direct correction mid-brainstorm ("不是申请API的链接，我觉得增加一些
      引导"): no link to an external "get an API key" page was added.
- **Verified**: frontend -- `npx tsc -b`, `npm run lint` (oxlint), and
      `npm run build` all clean. Backend -- `ruff check`/`mypy src` clean
      on `app.py`; `tests/test_setup_app.py` (9 tests, unchanged
      assertions -- the `#coscribe-setup-marker` element and `/api/setup`
      contract are untouched) still green. Live-verified via Playwright
      against a real running server: the greeting renders correctly and
      matches the browser's local hour; the shortcuts dialog opens via
      both the header button and the "?" key and lists all groups
      correctly; the Provider delete flow shows the new confirm dialog
      and cancels without deleting; the setup page's model field
      prefills per provider, updates on provider switch, and stops being
      overwritten once hand-edited, in both light and dark
      `prefers-color-scheme`.
- **Explicitly deferred, not part of this batch**: a unified toast/
      notification system (the fifth brainstormed item) -- to be
      discussed in more depth before any implementation. Two more items
      raised in the same brainstorm but not approved for this batch --
      a manual light/dark theme toggle, and Model Picker metadata
      (context window/pricing) -- also not built.

---

## Phase 8aa -- Browser panel: fix Backspace, add real IME input support (shipped)

Live-reported on real hardware, in the same detailed session as the
"coscribe starts slowly" investigation (see the MCP-eager-import bullet
under "Later" below): "浏览器画布里输入法不能用，不能使用backspace退回输入
内容" (IME doesn't work in the browser canvas, Backspace doesn't delete
typed content), plus performance/resolution complaints re-tested against
the already-shipped Phase 8x DPI/debounce fix (confirmed via `git log`
and the desktop-build Actions history that the fix commit, `b4e6214`, is
an ancestor of the most recent successful build, `bc6739f`/run #15 --
not yet re-investigated further, out of scope for this pass per your own
prioritization: "先修backspace，然后输入法").

- [x] **Backspace (and other non-printable keys) actually work now.**
      Root cause, found by reading the code, not guessed:
      `browser_panel.py`'s `dispatch_key` sent `Input.dispatchKeyEvent`
      with `windowsVirtualKeyCode` hardcoded to `0` and `type` copied
      straight from the frontend's own `"keyDown"`/`"keyUp"` labels --
      Chrome's default handling for a non-printable key (actually
      deleting a character, for Backspace) depends on the real Windows
      virtual-key code, which `0` never identifies as anything. Fixed to
      match Puppeteer's own CDP keyboard implementation (confirmed by
      reading it): a new `_KEY_DEFINITIONS` table
      (Backspace/Tab/Enter/Escape/Delete/Home/End/PageUp/PageDown/arrows
      -- the actual set `BrowserPanel.tsx`'s key handling can ever send)
      supplies the real `windowsVirtualKeyCode`/`code`, and the down
      event now sends CDP's `"rawKeyDown"` (not `"keyDown"`, which CDP
      reserves for a key that also carries printable `text` -- none of
      these do, printable text goes through `insert_text` instead). An
      unrecognized key name falls back to the previous best-effort shape
      rather than being dropped.
- [x] **Real IME support (Chinese/Japanese/Korean input) in the Browser
      panel canvas -- your own OS input method, not a custom
      reimplementation.** You pushed back on my first framing of this as
      "build IME support," pointing out other tools' (Claude Cowork's)
      canvas-embedded browser views just let you type with your own
      input method directly -- correct, and that's exactly what this
      delivers, just not achievable by adding more keydown handling to
      the `<canvas>` itself: a `<canvas>` is never an editable surface by
      spec, so the OS input method never attaches a composition to it no
      matter what its own keydown/keyup code does. The only way any
      canvas-rendered remote view (this one, noVNC, Guacamole, and -- by
      inference, since it visibly works there -- whatever Cowork's own
      browser view does under the hood) can support IME is the same
      trick: a real, invisible, focusable `<input>` behind the canvas
      that the actual OS input method attaches to normally, with the
      *composed* result relayed to the remote page once composition
      ends. Implemented in `BrowserPanel.tsx`: a hidden `<input>`
      (`data-testid="browser-ime-bridge"`, `opacity: 0` -- not
      `display:none`/`visibility:hidden`, both of which would also make
      it unfocusable/un-composable) takes DOM focus (instead of the
      canvas) on every canvas click, repositioned to the click's own
      coordinates so the IME candidate window opens near where the user
      is about to type. `compositionstart`/`compositionend` track
      composing state (`composingRef`); a plain (non-IME) keypress is no
      longer forwarded from `keydown` at all (the previous shape) since
      whether a given keystroke turns out to be a standalone character or
      the first letter of an IME composition can't be told apart at
      keydown time (`isComposing` is still false for that very first
      keystroke) -- instead, `onImeInput` picks up whatever landed in the
      field (a plain typed character, or a whole pasted string) once the
      browser's own native input pipeline has resolved it, forwards it via
      `Input.insertText`, and clears the field; `onCompositionEnd` does
      the same for the final composed IME text (`e.data`), while
      intermediate composition steps are explicitly ignored (nothing
      forwarded until composition actually ends, so partial pinyin like
      "ni" never leaks through as literal Latin text). Control keys
      (Backspace/Enter/Tab/arrows/Escape) still forward via the existing
      `dispatch_key` path, but only when not mid-composition, so
      Enter/Backspace/arrows used to navigate IME candidates stay local
      to the OS input method instead of also reaching the remote page.
      `justEndedCompositionRef` swallows the one stray `keyup` that can
      follow right after `compositionend` (e.g. the Enter that just
      confirmed a candidate) -- flagged as the defensive, best-effort side
      of this fix, not independently confirmed against a real IME on
      real hardware (browsers are known to be inconsistent about exactly
      when `isComposing` flips for that specific key).
- **Verified**: `ruff check`/`mypy src` clean; `pytest tests/` full suite
      green (860+3 new `dispatch_key` regression tests, one confirming
      the exact old broken shape -- `windowsVirtualKeyCode: 0`,
      `type: "keyDown"` -- is gone). Frontend `tsc -b`/`npm run
      lint`/`npm run build` clean. Live end-to-end against a real headless
      Chromium (this sandbox's own, via `COSCRIBE_BROWSER_PANEL_EXE` --
      see `find_browser_executable`'s own docstring) driving a real
      remote `<input>` field through Playwright: plain typing ("Hello123")
      landed correctly; two Backspace presses correctly produced "Hello1";
      a synthetic IME composition sequence (`compositionstart` ->
      intermediate `input` events for "n"/"ni", correctly not forwarded
      -> `compositionend` with `data: "你好"`) correctly appended exactly
      "你好" with no "n"/"ni" leakage, landing at "Hello1你好". Playwright
      itself has no real OS-level CJK IME to drive, so this confirms the
      event-handling wiring end-to-end, not genuine OS-level IME feel --
      that needs your own real-hardware confirmation, same as the earlier
      DPI fix did.
- **Not done, out of scope for this pass per your own prioritization**:
      re-investigating the performance/resolution complaint against the
      confirmed-latest build, and the ambiguous "点上去没有" report (the
      message describing it was cut off -- asked you to clarify what
      "没有" refers to, not yet answered).

---

## Phase 8ab -- Browser panel: native second-window architecture (Tauri, desktop-only) -- superseded by Phase 8ae, see below

**Superseded, not resumed.** Six real-hardware rounds on Stage 1 (window-sync prototype) below all failed on real, reproducible Windows bugs -- `.parent()` hanging the whole app, then (with that removed) the window never becoming visible, then (bare-minimum window, finally visible) the app unable to exit cleanly even after an explicit `destroy()` on exit. A GitHub search found `tauri-apps/tauri#5611` (2022, still open) describing the exact same "second webview window prevents clean process exit on Windows" symptom -- a long-standing Tauri/WRY framework weakness, not a configuration mistake here. Phase 8ae (below) replaces this whole approach with an Electron shell (`office-agent-desktop-electron/`), using `WebContentsView` -- a genuine embedded child view, not a synced sibling window, so this entire bug class doesn't apply to it. Kept in full below as the historical record of what was tried and why each attempt failed -- a future reader should not resume this path.

Follow-up to Phase 8aa: after the Backspace/IME fixes shipped, you
re-tested and found the panel "还是很难用" -- laggy, low-resolution,
unresponsive clicks -- symptoms the CDP-screencast-into-canvas
architecture itself structurally can't fully solve, not more bugs to
patch individually. You asked whether there's mature, established
technology to borrow instead, since other tools' (Claude Cowork's) own
desktop browser panels feel exactly like a normal embedded browser.

**Architecture options researched and discussed directly with you, in
order:**
1. Tauri's native "multiwebview" (`Window::add_child`) -- confirmed via
   live web search still gated behind the `"unstable"` Cargo feature
   flag, with active, recent (Nov 2025) bugs (child webview not
   rendering on top on Windows, positioning/resize breakage) -- not
   safe to build on.
2. Switching the whole desktop shell to Electron (`WebContentsView` +
   `webContents.debugger`) -- technically more mature for embedding a
   live browser region, but means rewriting `office-agent-desktop/`'s
   entire already-shipped, real-hardware-verified Rust/Tauri code
   (sidecar lifecycle, splash page, tray, background keep-alive, native
   notifications, single-instance lock, native folder picker) and
   abandoning Tauri's lighter WebView2-reuse distribution story. You
   decided against this given the scope.
3. Launching the user's actual OS default browser as an independent,
   non-headless window (not Chrome-specific at all -- whatever's
   actually set as default) -- would trivially solve every symptom, but
   stops being a panel embedded in coscribe's own window, becoming a
   separate OS window the user manages themselves. You confirmed the
   docked-in-the-app-window look is a real requirement, not a nice-to-
   have, which rules this out.
4. **Chosen**: a second, real, top-level `WebviewWindow` (Tauri's
   long-stable window APIs only, the same ones the main window already
   uses -- `WebviewWindowBuilder`, `set_position`/`set_size`, window
   move/resize events) kept visually synced to the panel's on-screen
   region, driven by WebView2's own CDP endpoint
   (`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<port>`)
   instead of a self-spawned headless Chrome. Once the browser is a
   real, directly-interactable native surface, performance/resolution/
   click-responsiveness/IME are solved structurally, not patched --
   and CDP is only needed for "select an element to send to chat," not
   for relaying every keystroke/click. Desktop-only: the plain web
   build keeps today's screencast+canvas implementation completely
   untouched, via the already-established `isTauri()` branch pattern
   (`frontend/src/lib/tauri.ts`, already used by `DirBrowserModal.tsx`).

**Full staged plan** (Stage 0 spike -> Stage 1 window-sync prototype ->
Stage 2 CDP attach + native navigation -> Stage 3 select-element parity
-> Stage 4 cleanup/hardening), including explicit acceptance criteria
you asked for directly (single Alt+Tab entry / single taskbar icon; a
named CJK IME test matrix covering address-bar-vs-page-field and
multi-field Tab-focus state; a DPI/zoom/iframe coordinate-accuracy test
matrix with graceful-degradation error messaging instead of silent
failure) -- was written up, revised twice against your feedback, and
approved. Kept as its own document (not duplicated here in full) at
`/root/.claude/plans/gemini-3-1-flash-lite-sorted-reef.md` in the
session that wrote it -- if that's not durably reachable, the plan's
structure/content is fully recoverable from this session's own
transcript; re-derive and save it properly the next time this work is
picked up rather than relying on that ephemeral path indefinitely.

**Stage 0 (throwaway spike) code written and pushed, real-hardware
result not yet known.** `office-agent-desktop/src-tauri/src/
stage0_cdp_spike.rs` (new, clearly marked temporary/delete-once-
answered) creates a second `WebviewWindow` ~3s after startup, setting
`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` around its creation and
`eprintln!`-ing the CDP port it used; wired into `lib.rs` via one
`mod` line and one call in `setup()`. Answers the single riskiest
open question before any further work is worth doing: does
`/json/list` on that port return exactly one target (the spike
window's own page -- per-window CDP isolation works) or two (shared
process-wide with the main window)? **Verified in this sandbox**:
`cargo check`/`cargo clippy --all-targets` both clean (a real Rust
toolchain is available here even though the full Tauri/WebView2 GUI
stack isn't -- confirmed compiles, `binaries/sidecar` resource-path
placeholder used only locally to get past the build script, not
committed). **Not verified, cannot be from this sandbox**: any actual
runtime behavior at all -- needs `npm run tauri build` (a real release
build, not `npm run tauri dev` alone -- part of the open question is
release-vs-debug behavior) and running the result on real Windows
hardware, per this plan's own explicit staging discipline: each stage
gets a small, real-hardware checkpoint before the next one starts, not
a batched handoff.

**First real-hardware run: `ERR_CONNECTION_REFUSED` on port 47291 --
root-caused via live web research, not left as a mystery, and fixed.**
You built the release binary and ran it; the spike window opened, but
`http://127.0.0.1:47291/json/list` refused the connection outright --
nothing was ever listening. Confirmed against Microsoft's own WebView2
process-model documentation: `AdditionalBrowserArguments` (how the env
var gets applied) is a **browser-process-launch-time** switch, honored
only by whichever environment is the *first* to actually start the
shared browser process for a given user data folder. The main window's
own WebView2 environment starts that shared process (with no CDP flag)
well before this spike's 3s-delayed code runs, so the spike window's
env var setting was silently ignored -- it just attached to the
already-running process. Fixed by giving the spike window its own
`data_directory` (`WebviewWindowBuilder::data_directory`, a real stable
Tauri 2 API) distinct from the main window's, so WebView2 spins up a
genuinely separate browser process for it and its
`AdditionalBrowserArguments` applies normally. Re-verified clean in
this sandbox (`cargo check`/`cargo clippy --all-targets`); the actual
`/json/list` re-test is the next real-hardware checkpoint, not yet
run. **This finding also answers part of Stage 1's own design up
front, not just Stage 0's**: the real panel window will need its own
data directory too, meaning it will *not* share cookies/session/
localStorage with the main app's own webview -- a real, acceptable-
for-a-browsing-panel constraint to design around from the start, not
something discovered late.

**Second real-hardware run (with `data_directory` added): the exact
same `ERR_CONNECTION_REFUSED` -- a second, distinct bug, not the first
one recurring.** Root-caused again via live research rather than
guessed at: wry's own WebView2 backend *always* calls
`SetAdditionalBrowserArguments` with an explicit value (its own default
disables a few WebView2 UI features), for every webview it creates,
whether the caller touches this setting or not. WebView2's env-var
fallback (`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`) is only consulted
when the caller's own property is genuinely left unset -- but wry never
leaves it unset, so the env var this spike was setting was dead on
arrival every single time, independent of the `data_directory` fix and
independent of timing (there was no race to chase here either). **Real
fix**: Tauri exposes a direct, non-env-var API for exactly this,
`WebviewWindowBuilder::additional_browser_args(&str)` -- confirmed
present on the exact `tauri` version this project pins by fetching its
real docs.rs page, not assumed from memory -- which sets the WebView2
environment option directly, no process-global mutable state involved
at all. Switched to it; kept `data_directory` (still required
independently -- webviews sharing a data folder must use *identical*
environment options, so giving this window different browser args than
the main window's still needs its own data folder or environment
creation fails outright with `ERROR_INVALID_STATE`). Also folded wry's
own default `--disable-features=...` flags into the new call, since
calling `additional_browser_args` at all replaces wry's defaults rather
than appending to them (documented behavior, not guessed). Re-verified
clean in this sandbox (`cargo check`/`cargo clippy --all-targets`,
confirms `additional_browser_args` is available directly on
`WebviewWindowBuilder` with no extra trait import needed on this
version); the `/json/list` re-test is again the next real-hardware
checkpoint. Two real, independent bugs found and fixed across two
rounds here, both root-caused against primary documentation before
writing the fix rather than shipped as a guess and left for the next
round to discover -- worth naming plainly rather than papering over,
since the pattern (WebView2 has more than one way for
"set this per-window option" to silently no-op instead of erroring)
is itself useful context if Stage 1+ hits something in the same family.

**Third real-hardware run: Stage 0's actual question answered, `[
{"title": "Example Domain", "url": "https://example.com/", ...} ]` --
exactly ONE target, the spike window's own page, not two.** Built via
GitHub Actions' `desktop build (Windows)` workflow this time (this
machine didn't have the local Rust/VS Build Tools/PyInstaller setup the
earlier rounds used -- confirmed the CI-built portable artifact is
just as valid a way to test this as a local build, since the question
being answered is real-hardware WebView2 runtime behavior, not
anything about how the binary was produced). **Per-window CDP
isolation works**: the main coscribe UI window's own page never
appeared in `/json/list`, only the spike window's -- Stage 0's fallback
branch (shared process-wide, needing a design reconsideration) does not
apply. Stage 1 can proceed as originally planned.

Spike code deleted per its own docstring's instruction ("delete once
Stage 0 is resolved") -- `stage0_cdp_spike.rs` removed entirely, its
`mod stage0_cdp_spike;` line and `spawn_spike(...)` call in `lib.rs`
removed too. Re-verified clean in this sandbox after deletion
(`cargo check`/`cargo clippy --all-targets`).

**Stage 1 (window-sync prototype), code written, real-hardware check
not yet run.** New `office-agent-desktop/src-tauri/src/
browser_panel_window.rs` manages a second, real, top-level
`WebviewWindow` (label `browser-panel`), with three `#[tauri::command]`s
wired into `lib.rs`'s `.invoke_handler()`: `browser_panel_open(rect) ->
Result<u16, String>` (idempotent -- creates the window on first call,
just repositions+shows it on every later call, returns its own CDP port
either way), `browser_panel_reposition(rect)`, `browser_panel_close()`
(hides, doesn't destroy -- see the module's own docs for why: avoids
losing page state on every toggle once Stage 2+ puts real content
there; the resource-usage-vs-destroy-on-close tradeoff is explicitly
left to Stage 4's own open question #4, not decided here).

Reuses Stage 0's two confirmed findings directly: a distinct
`data_directory` and `additional_browser_args` (not the env var) for
the panel window's own CDP port, allocated via the same `free_port()`
the sidecar itself uses (made `pub(crate)` for this). `.decorations(false)`
(no OS chrome of its own) and `.skip_taskbar(true)` (macOS: unsupported,
a no-op, not an error -- Windows-only project per this doc's own
"Standing constraint"). `.parent(&main_window)` sets a real Win32 owner
relationship -- per Tauri's own documented platform behavior (checked,
not assumed): owned windows on Windows are always above their owner in
z-order and are destroyed automatically when the owner is destroyed,
which is exactly what "one coscribe entry in Alt+Tab, panel never
outlives the main window" needs.

`build.rs` needed a real change too, not just `lib.rs`/the new module:
`tauri_build::build()` alone does **not** auto-detect an app's own
`#[tauri::command]`s for ACL purposes (checked against tauri-build's
own docs before assuming, since the plain `tauri_build::build()` call
already in this file gave no obvious sign either way) -- without
declaring them via `tauri_build::Attributes::new().app_manifest(
tauri_build::AppManifest::new().commands(&[...]))`, `capabilities/
default.json` would have nothing valid to grant these three commands
with. Fixed, and confirmed for real (not just "no compile error"): the
three permission tomls this generates
(`permissions/autogenerated/browser_panel_*.toml`) were inspected
directly after a clean `cargo check`, each with an
`allow-browser-panel-<name>` identifier, matching exactly what
`capabilities/default.json` now grants to the `main` window (the
`browser-panel` window itself stays absent from that capability's
`windows` list entirely -- default-deny, per the plan: nothing needs to
call back into Rust from that webview).

Frontend: `frontend/src/lib/tauri.ts` gained thin `invoke()` wrappers
(`browserPanelOpen`/`browserPanelReposition`/`browserPanelClose`,
`PanelRect`). `BrowserPanel.tsx` gained an `isTauri()`-gated effect that
computes this panel's own on-screen rect and keeps the native window
synced to it -- one deliberate deviation from this plan's own original
design note, made once the real API was checked rather than assumed:
`getCurrentWindow().innerPosition()` (the webview *content* area's own
screen origin), not `outerPosition()` (the whole OS window including
title bar/borders) -- `innerPosition()` already excludes the chrome
thickness, which differs by OS theme/window state and isn't otherwise
computable from JS, so there's no separate offset math to get wrong.
Three independent signals can each move this panel's own on-screen
rect, so all three trigger a reposition: the main window moving
(`onMoved`), the main window's own content area resizing (`onResized`),
and the placeholder div's own layout changing with no window move at
all -- a `ResizeObserver` on the same container ref, reused for this
instead of `canvasSize` state the way the non-Tauri path uses it. The
existing WS-based screencast effect is now skipped entirely in Tauri
mode (`if (tauriMode) return`) -- Stage 1 wires no backend to the panel
window at all yet, so there is nothing for that connection to do until
Stage 2. Render-wise, the Tauri path shows only the shared header
(title/close) and a placeholder div where the real native window
renders on top once positioned -- deliberately no toolbar/canvas/IME-
bridge/pick-UI yet, since none of it is wired to anything for this path
until Stage 2 (navigation) and Stage 3 (element picking); the non-Tauri
canvas+screencast path is untouched, confirmed by reading through the
diff rather than assumed from "it's just a branch."

`@tauri-apps/api` added as an explicit frontend dependency (was already
present transitively via `@tauri-apps/plugin-dialog`, but importing
`invoke`/`getCurrentWindow` directly from it deserves its own entry in
`package.json`, not reliance on another package's own transitive pull).

**Verified in this sandbox**: `cargo check`/`cargo clippy --all-targets`/
`cargo fmt --check` all clean for the new Rust module and `build.rs`
change; `npx tsc -b`, `npm run lint` (oxlint), and `npm run build` all
clean for the frontend change, including after fixing two real
`react-hooks/exhaustive-deps` warnings (not errors, but worth fixing
rather than leaving a genuine warning unaddressed) by adding `tauriMode`
to both new effects' dependency arrays. **Not verified, cannot be from
this sandbox**: any actual runtime behavior at all -- this is a purely
visual milestone (per the plan's own staging, no browsing functionality
yet), so the real-hardware checkpoint here is watching the native window
actually track the panel's own on-screen region correctly through:
initial open, dragging the panel's own resize handle, moving the main
window, resizing the main window, maximize/restore, and close -- plus
the plan's own explicit acceptance criteria (exactly one Alt+Tab entry
and one taskbar icon for coscribe in every one of those states, and an
honest assessment of how bad the expected drag-lag is, not just whether
it's zero).

**First real-hardware run: the placeholder's own text ("Native browser
window") stayed visible instead of getting covered by a real window --
a real signal (from a screenshot, not just a verbal description) that
`browser_panel_open` likely failed rather than succeeded silently,
since a correctly-positioned real window would fully occlude that text,
not let it show through. The header ("Browser" + close button) *did*
render correctly, confirming the panel component itself mounted fine --
only the native-window half of Stage 1 didn't visibly work.** The real
gap: this failure was silent -- the `browser_panel_open` promise was
never wrapped in a `try`/`catch`, so a rejection (main window not
found, `.build()` erroring, anything) went nowhere but the devtools
console, which a release build doesn't expose at all (the exact same
"console output goes nowhere in a windowed release build" trap the now-
deleted Stage 0 spike's `eprintln!` hit first). Fixed: the Tauri-mode
effect now catches that rejection and a new `nativeError` state shows
the actual error string in the placeholder area instead of the generic
"Native browser window" text -- turns the placeholder into a real
diagnostic surface instead of an indistinguishable-from-success blank
state. Checked (not just assumed) that this isn't an ACL/permissions
gap: `core:default` already includes `allow-inner-position` and every
other window-query permission this effect's `computeRect()` calls, so
that's very unlikely to be the actual failure -- most likely candidate
is something in `browser_panel_open`'s own Rust body (`.build()`
erroring against the `data_directory`/`additional_browser_args`
combination, or something else), which the next real-hardware run's
actual error string will confirm or rule out directly instead of
continuing to guess. Verified in this sandbox: `tsc -b`/`oxlint`/
`npm run build` all clean.

**Third real-hardware round: no error text, but the placeholder still
never got covered -- and then the whole app hung, requiring Task
Manager to kill it.** More serious than a cosmetic miss: closing the
main window stopped responding and its own WS reconnect banner
("Connection lost") appeared, meaning the app's own main-thread message
loop had genuinely stalled, not just the panel window failing quietly.
Root-caused by reasoning back from the evidence (not directly
debuggable from this sandbox): the panel window's `data_directory` +
`additional_browser_args` (added in the second round to reuse Stage
0's CDP findings up front) force WebView2 to spin up a wholly separate,
cold browser subprocess, since it can't share the main window's
already-warm one. `WebviewWindowBuilder::build()` creates a webview
synchronously on the main thread -- a slow or stuck cold-start there
stalls the app's *entire* message loop, not just this one window,
which fits every symptom (frozen close button, the main WS banner, the
panel's own placeholder never getting a chance to be covered).

**Fixed by removing scope that Stage 1 never actually needed, not by
patching around the hang.** Stage 1's own goal is verifying the window
visually tracks the panel's on-screen region -- it never needed a CDP
port at all; that requirement belongs to Stage 2 (real navigation
attached over CDP), which is exactly when `data_directory`/
`additional_browser_args` come back. `browser_panel_open`'s signature
simplified from `Result<u16, String>` back to `Result<(), String>`
(frontend's `browserPanelOpen`/`nativeDebug` updated to match, dropping
the now-meaningless "cdp N" suffix); `PanelState`'s CDP-port-tracking
`Mutex` removed from both `browser_panel_window.rs` and `lib.rs`'s own
`.manage()` call -- `get_webview_window(PANEL_LABEL)` alone is enough
to know whether the window already exists. The panel window now reuses
the main window's own already-warm WebView2 environment (no custom
data directory, no extra browser args), which should create close to
instantly since there's no new browser subprocess to spin up at all.
Verified in this sandbox: `cargo check`/`clippy`/`fmt` and `tsc -b`/
`oxlint`/`npm run build` all clean after the simplification. Real-
hardware re-test (does the window now appear promptly, and does the
app stay responsive) is the next checkpoint -- this round's fix is
reasoned from the evidence available, not confirmed by directly
reproducing the hang in this sandbox (which can't run WebView2 at all).

**Fourth real-hardware round: same hang, exact same symptoms, after the
CDP/`data_directory` removal above.** That theory is now ruled out, not
confirmed -- it wasn't the (sole) cause. Re-examined what's left in
`browser_panel_open` and found a real, previously-unflagged difference
from Stage 0's own spike window (which *did* successfully open, CDP
included): the spike never called `.parent(&main_window)`. This plan's
own Stage 1 write-up (open question #3, this doc) had already flagged
`.parent()` -- a real, stable Tauri 2 API on paper, not the `"unstable"`
multiwebview one this whole plan deliberately avoids -- as untested on
real hardware, and it shipped without its own dedicated check first.
Removed now. This is circumstantial (it's the only remaining thing this
file does that the working spike didn't), not a confirmed root cause --
this sandbox cannot run WebView2 at all, so nothing here is directly
reproducible, only reasoned from the pattern of what changed between a
working state (Stage 0) and a hanging one (Stage 1). Real, accepted
cost of removing it for now: the panel window is an independent
top-level window with no Win32 owner relationship, so the plan's own
"one Alt+Tab entry, panel never outlives the main window" acceptance
criterion is unmet and stays open -- deliberately deferred until window
creation itself is confirmed to work at all, rather than debugging two
unconfirmed things at once. `MAIN_LABEL`/the main-window lookup this
existed to support are removed too (no longer used for anything).
Verified in this sandbox: `cargo check`/`clippy`/`fmt` clean. Real-
hardware re-test is, again, the actual next checkpoint -- if this still
hangs, the next thing worth isolating is whether creating *any* second
top-level webview window at all hangs on this specific machine, with
every custom builder option stripped away, rather than continuing to
remove one option at a time.

**Fifth real-hardware round: `.parent()`'s removal was real, partial
progress -- the main window stayed fully responsive this time (typing,
clicking, no lag) -- but the panel window still never became visible,
and the app still couldn't exit cleanly (tray "Quit" did nothing, Task
Manager the only way out). A "Connection lost -- reconnecting..."
banner also appeared on the main chat WS mid-session -- not yet
understood, possibly unrelated to this window at all (this test's own
next round should help tell those apart).** Reasoned explanation: this
window's own WebView2 controller most likely never finishes
initializing at all (consistent with it never painting over the
placeholder), and the app's own shutdown path then blocks trying to
tear down that stuck webview, independent of the main UI thread's own
responsiveness the rest of the time -- explains both symptoms as one
thing rather than two. `.decorations(false)`/`.skip_taskbar(true)` were
the only custom options left after the last two rounds' removals;
stripped now too, leaving the plainest possible second window (a fully
default, OS-decorated, normal top-level window) to answer one narrow
question before anything else: does creating *any* second webview
window work on this machine at all? If even this doesn't work, that's
a strong signal to stop iterating on individual options and reconsider
this plan's own original fallback branch (keep today's screencast+
canvas approach for desktop too -- see this doc's Stage 0 section).
Verified in this sandbox: `cargo check`/`clippy`/`fmt` clean.

**Sixth real-hardware round: real progress -- the bare-minimum window
actually appeared (plain white, decorated).** Answers this whole
sub-thread's own narrow question: creating a second webview window
does work on this machine; the earlier failures to appear were
something about the specific options tried, not the platform itself.
The close/exit hang is a **separate** bug, confirmed by this round --
window creation now visibly works, but quitting still didn't (tray
"Quit" did nothing, Task Manager the only way out), so the two
symptoms don't share the root cause originally guessed. New theory:
this codebase never explicitly closes or destroys the panel window
anywhere -- `browser_panel_close` only `hide()`s it (see that module's
own "hide-and-reuse" lifecycle docs), so on quit it's simply left open,
and whatever Tauri/WRY's own default exit teardown does with a second
still-open `WebviewWindow` apparently hangs on this platform. Fixed by
explicitly `destroy()`-ing the panel window in `lib.rs`'s own
`RunEvent::ExitRequested`/`RunEvent::Exit` handler, alongside the
existing sidecar-kill logic already there -- `destroy()` specifically
(not `close()`, which just emits a `CloseRequested` event through the
normal interceptable flow, exactly what evidently wasn't already
happening on its own) forces it closed immediately and
unconditionally. This also gives a plausible explanation for the
"Connection lost" banner seen on recent rounds: the sidecar-kill call
already runs *before* this new panel-destroy call in the same handler,
so a user clicking "Quit" would see the sidecar's own WS drop right
away (expected, that's what killing it does) followed by the app
simply never finishing the rest of its own exit sequence -- consistent
with, not a separate bug from, the same underlying hang. `PANEL_LABEL`
made `pub(crate)` so `lib.rs` can reference it without duplicating the
label string. Verified in this sandbox: `cargo check`/`clippy`/`fmt`
clean. `.decorations(false)`/`.skip_taskbar(true)` deliberately still
left out for this round -- isolating the exit fix on its own before
reintroducing the chrome options next.

---

## Phase 8ac -- Browser panel (current screencast implementation): real mouse-drag support, element text now reaches chat, a truncated-preview crop bug fixed, a near-miss reverted (shipped)

While waiting on Stage 0's real-hardware Windows test (Phase 8ab), you
kept exercising the *current*, already-shipped screencast+canvas
implementation directly and reported four more things in one pass:
a 10+ minute GitHub Actions round trip feels too slow to iterate with;
resolution still felt low; picked-element screenshots were showing
"content displayed incompletely" (a mostly-blank wide strip); a
horizontal scrollbar "can't be dragged incrementally, only jumps to the
end"; and a product question -- does the browser panel only ever add a
screenshot, with the AI never getting the actual page content/structure
alongside it?

- **Dev-loop speed**: pointed out that everything in this pass (drag
  relay, the picked-screenshot investigation, the text-to-chat wiring)
  is pure Python/TypeScript, not Tauri/Rust-specific -- none of it
  needs a desktop rebuild to test. Only Phase 8ab's native-window Stage
  0+ work genuinely requires the Tauri/Windows round trip; everything
  in *this* phase was verified directly in this sandbox against a real
  headless Chromium via `COSCRIBE_BROWSER_PANEL_EXE`, the same way
  every other Browser-panel pass this session was.
- [x] **Real bug found and fixed: no actual mouse-drag relay existed at
  all.** `BrowserPanel.tsx`'s old `onCanvasClick` sent a synthetic
  `mousePressed` immediately followed by `mouseReleased` on every
  click -- there was no way for the remote page to ever see a real
  "button held down while the pointer moves" gesture, which is exactly
  what a scrollbar-thumb drag (or text selection, or a slider) needs.
  Clicking a scrollbar *track* without a real drag is correctly a
  jump-to-that-position scroll in every browser -- "只能变换到底" was the
  literal correct behavior for what was actually being sent, not a
  separate bug. Fixed with a real mousedown -> however-many-mousemoves
  -> mouseup relay: `onCanvasMouseDown` sends `mousePressed` and sets a
  `mouseDownRef`; a new window-level `mouseup` listener (same "listen on
  window, not just the canvas" pattern the panel's own resize-handle
  drag already used, so a drag that leaves the canvas mid-gesture still
  completes correctly) sends `mouseReleased`; `onCanvasMouseMove`
  (unchanged) keeps sending `mouseMoved` throughout. `onCanvasClick`
  now only handles `pickMode`'s click-to-commit. **Verified live**: a
  real `<input type="range">` dragged via Playwright's own
  mousedown -> 10 incremental mousemoves -> mouseup landed at roughly
  the middle of the track (screenshot-confirmed), not snapped to either
  end.
- [x] **Real gap found and fixed: the picked element's own extracted
  text never reached the chat, only the screenshot did.** Confirmed by
  reading the code: `pick_element` (backend) already returns `text`
  (the element's own innerText) alongside the screenshot, and the
  frontend's own preview even *displays* it -- but "Add to chat"'s
  `onSendToChat({ name, dataUrl })` call only ever forwarded the image,
  dropping `text`/`tag` on the floor. This is the real answer to "AI
  还是不能理解页面架构，不会把元素内容一起发过去": the AI genuinely was
  never given the extracted text, only a picture it has to visually
  read back out (unreliable for small text, icon labels, anything
  cropped tight). `BrowserCapture`/`PendingImage` gained optional
  `text`/`tag` fields; `Composer.tsx`'s `submit()` now appends a
  `(Selected <tag> from the browser panel -- text: "...")` note to
  `outgoingText` (same "(...)" contextual-note convention the existing
  file-attachment note already uses, not a new mechanism), never
  `displayText` -- the chat bubble stays clean, same as the file-
  attachment note's own behavior. **Verified live**: intercepted the
  real outgoing WebSocket `user_message` frame and confirmed it now
  reads `"what is this?\n\n(Selected <button> from the browser panel --
  text: \"OK\")"` alongside the image, where it previously carried no
  text about the pick at all. **Scope, honestly stated**: this is still
  only the one clicked element's own text, not the whole page's DOM/
  structure -- "AI 还是不能理解页面架构" in the broader sense (full page
  structure, not just one picked element) is accurate and out of scope
  for this fix; the panel was designed as a targeted point-and-show
  tool, not a page-structure-extraction one (see browser_panel.py's own
  module docstring).
- **A near-miss, caught before shipping, not after**: first suspected
  `pick_element`'s screenshot capture (`clip.scale` hardcoded to `1`)
  was *also* a DPI bug, matching the live view's own earlier DPI fix,
  and initially "fixed" it to `self._viewport_scale`. Verified live
  before committing (the same discipline as everything else this
  session) by measuring a picked element's actual output pixel
  dimensions against a real Chromium instance at `deviceScaleFactor=2`
  -- and found the "fix" was wrong: `clip.scale` is a multiplier *on
  top of* whatever `deviceScaleFactor` emulation already applies, not a
  replacement for it. `scale: 1` (the original code) already produced a
  correctly 2x-scaled, DPI-aware 80x80 image for a 40x40 CSS-pixel
  element; `scale: self._viewport_scale` produced a doubly-scaled,
  wrong 160x160 image -- a real resolution regression that would have
  shipped if not verified. Reverted cleanly, with `pick_element`'s own
  docstring and a test
  (`test_pick_element_screenshot_clip_scale_stays_1_regardless_of_viewport_scale`)
  now explaining exactly why, so this specific wrong "fix" doesn't get
  reintroduced later by someone reading the code and having the same
  plausible-but-wrong idea.
- [x] **Real bug found and fixed, once you clarified the report**:
  "内容总是显示不全" turned out to specifically mean the Add-to-chat
  *preview* itself (the picked-element `<img>`, before deciding whether
  to send it), not the final image the model receives or which element
  got picked. Reproduced directly: picked a synthetic element wider
  than the panel's own emulated viewport (`BrowserPanel.tsx`'s
  `DEFAULT_PANEL_WIDTH` is 440) and inspected the raw returned JPEG
  bytes -- a correctly-formed, correctly-dimensioned file, but the
  pixel content itself was genuinely blank past the viewport's own
  right edge, not a frontend CSS/decode bug at all (ruled out
  `object-contain` misbehaving by checking the raw bytes before ever
  touching frontend code). Root cause:
  `Page.captureScreenshot`'s `clip` only rasterizes what Chrome
  actually composited within the *current* emulated viewport's real
  bounds -- any clip extending past it comes back correct up to that
  edge and blank beyond it, unless told otherwise. Fixed with CDP's own
  `captureBeyondViewport: true` on that same call.
  **A transient artifact along the way, not chased further**: one
  early re-test (through the full app, screencast running) came back
  tiled/repeated instead of blank -- but three follow-up raw-CDP
  isolation tests (with and without an active `Page.startScreencast`
  session, matching the real panel's live condition) and a clean
  re-test through the full app afterward all came back correct with no
  tiling. Read as a one-off timing race (most likely the debounced
  resize message and pick_element's own viewport-width read landing on
  either side of a resize's stop/restart-screencast round trip, the
  same general class of quirk this file's own `_check_frame_size`/
  self-healing resync logic already exists to catch for the *live
  view* -- not re-verified as unresolvable, just not reproducible on
  demand, so not worth blocking this fix on chasing further right now.
- **Not resolved, needs a fresh look, not explained by anything above**:
  the general "resolution still feels low" complaint -- the live-view
  DPI fix, the picked-screenshot scale, and now the crop-bounds bug
  above were all independently investigated and are each either already
  correct or now fixed, so this specific complaint still needs a fresh,
  more specific report (which page, which part looked blurry) rather
  than further guessing.
- **Verified**: `ruff check`/`mypy src` clean; full `pytest tests/`
  suite green (868 total, including the new drag-relay coverage
  implicit in the mousedown/mouseup split, the corrected scale-
  regression-guard test, and a new
  `test_pick_element_captures_beyond_the_panels_own_narrow_viewport`).
  Frontend `tsc -b`/`npm run lint`/`npm run build` clean. Every backend
  behavior in this phase -- drag relay, the scale near-miss, and the
  crop-bounds fix -- was live-verified against a real headless
  Chromium in this sandbox (including reading raw JPEG bytes directly,
  not just checking dimensions), not just unit-tested.

---

## Phase 8ad -- Browser panel: emulated viewport is now the user's real screen resolution, not the panel's own narrow display width (shipped)

Follow-up to Phase 8ac's fixes: re-tested and reported "整体分辨率都低，
至少和电脑分辨率不同...我希望达到用户默认电脑分辨率" (the overall
resolution is still low, at least different from my computer's own --
I want it to reach the user's default computer resolution). Root cause,
once correctly understood: not a sharpness/DPI problem (already fixed in
Phase 8x), a *layout* one -- the remote page was emulated at exactly this
panel's own on-screen CSS width (`DEFAULT_PANEL_WIDTH` = 440, up to 1600
if dragged), so real sites correctly rendered their own narrow/"mobile"
responsive layout (hamburger menus, single-column, larger relative text)
instead of the normal desktop layout the user sees in their own browser
-- looks completely different from "my computer's resolution" even
though every pixel was already DPI-correct.

- [x] **Decoupled the remote-emulated viewport from the panel's own
  on-screen display size.** New `remoteViewportSize()` in
  `BrowserPanel.tsx` returns `window.screen.width`/`height` (already CSS
  pixels per spec, matching what `Emulation.setDeviceMetricsOverride`
  expects) -- sent once on WS connect as the `resize` message's
  width/height, no longer re-sent when the panel is merely dragged wider/
  narrower (the remote viewport doesn't depend on panel width anymore, so
  there's nothing to re-tell the backend). Same pattern any remote-
  desktop viewer (VNC/RDP/Chrome Remote Desktop) uses: render at the real
  target resolution, display a scaled-down view, resize the *viewer*
  window to see more detail -- this panel already supported dragging up
  to `MAX_PANEL_WIDTH_ABSOLUTE` (1600) for exactly that. `toRemoteCoords`/
  `remoteCoordsFromRefs`/the hover-highlight scaling math all switched
  from scaling against `canvasSize` (display) to `remoteViewportSize()`
  (remote) -- the canvas's own CSS size/backing buffer stay tied to
  `canvasSize` unchanged (still just the panel's own on-screen box;
  `ctx.drawImage` resamples the now-higher-resolution incoming frames
  down to fit, the same way a scaled-down remote-desktop view works).
  Net effect on backend chatter: strictly less than before, not more --
  panel drag-resize now triggers zero backend round trips (previously a
  debounced-but-still-real `resize` call per drag session).
  **Considered but not the approach taken**: adopted your framing
  directly ("claude cowork 是如何做的，我不太确定") -- checked
  Anthropic's own public posts about Cowork's browser panel for any
  published technical detail; none exists, so this isn't "matching
  Cowork's exact implementation," it's the standard remote-desktop-
  viewer pattern applied here, the most defensible default absent
  Cowork's own actual source.
- [x] **A real regression this exact change would have shipped, caught
  by testing it live before assuming it worked**: launch()'s headless
  Chrome window is a **fixed** `--window-size=1920,1200` -- fine when
  the emulated viewport could never exceed this panel's own on-screen
  width, not fine now that it's the user's real screen resolution,
  which plenty of ordinary monitors (2560x1440, 4K, ultrawide) already
  exceed. Confirmed live in this sandbox: a screencast requested at
  2560x1440 against the unresized 1920x1200 window came back clipped to
  1920x1061 -- the *exact* class of bug `launch()`'s own long-standing
  comment already documented for the old, narrower case, reproduced
  fresh by this change. Fixed with a new `_ensure_real_window_at_least`
  (`browser_panel.py`), called from `resize()`: `Browser.getWindowForTarget`
  reads the real window's current bounds; if too small,
  `Browser.setWindowBounds` grows it, padded by `_WINDOW_SIZE_MARGIN`
  (200px) past the exact target size -- also empirically necessary, not
  guessed: asking for exactly the target size still left the screencast
  ~140px short (window chrome/decoration overhead `Browser.setWindowBounds`'s
  own width/height don't account for, confirmed live, not assumed
  stable across platforms so padded generously instead of computed
  exactly). `resize()`'s own width/height clamp raised from
  `[200, 4000]` to `[200, 6000]` to comfortably cover a 5K-class screen
  width in CSS pixels.
- **Verified**: `ruff check`/`mypy src` clean; full `pytest tests/` suite
  green (871 total, including two new tests for
  `_ensure_real_window_at_least`'s grow/skip-growing branches). Frontend
  `tsc -b`/`npm run lint`/`npm run build` clean (a redeploy without the
  build step was caught mid-verification -- the live test kept showing
  the *old* resize payload shape until the frontend was actually
  rebuilt, a reminder to always rebuild before live-testing a frontend
  change, not just type-check it). Live end-to-end against a real
  headless Chromium with Playwright's `screen: {width:2560,height:1440}`
  simulating a real monitor while the page's own `viewport` (the panel's
  actual on-screen box) stayed small: confirmed the outgoing `resize` WS
  message now carries `{width:2560,height:1440}` (not the panel's own
  ~439-pixel display width), confirmed dragging the panel wider sends no
  further `resize` message at all, and -- the real proof -- placed a
  marker element at remote (2400,1300), far outside what the old narrow
  viewport could ever have reached, and successfully hovered/picked it
  (correct crop, correct extracted text "FAR CORNER"), which would have
  been geometrically impossible before this change and silently clipped
  to blank without the window-resize fix above.
- **Real tradeoff, disclosed, not hidden**: screencast frames are now
  sized to the real screen resolution instead of the panel's own narrow
  display -- genuinely more data per JPEG frame than before. Not
  verified against real Windows hardware whether this reintroduces any
  of the performance complaints Phase 8x's own debounced-resize fix
  addressed (a different mechanism -- that was about resize-triggered
  screencast churn, this is about steady-state per-frame size) --
  worth specifically watching for on the next real-hardware test.

---

## Phase 8ae -- Browser panel: Tauri -> Electron shell migration, Phase 1 (shell parity) written and sandbox-verified

Following Phase 8ab's six failed real-hardware rounds, researched how other desktop AI agents with an embedded live browser panel solve this (`DeepFundAI/ai-browser`, `DeepFundAI/manus-electron`, and others) -- all Electron, using `WebContentsView` (the current, non-deprecated replacement for `BrowserView`), a genuine first-party embedded-child-view API with no known "can't exit" bug class, unlike Tauri's still-`"unstable"` `Window::add_child`. Rejected a hybrid (keep the Tauri shell, spawn a separate Electron helper process just for the panel, synced via IPC) -- confirmed, not just suspected: a separate Electron `BrowserWindow` synced via position events is the *same kind of thing* as the sibling-window approach that already failed, just on a framework without Tauri's specific exit bug; it would not deliver true embedding, the actual point of this migration, and would run three runtimes (Tauri+Electron+Python) for no better an outcome than "the bug is gone." Full plan (phase breakdown, bundle-size tradeoff, open questions) written to `/root/.claude/plans/gemini-3-1-flash-lite-sorted-reef.md` after a fresh, thorough codebase inventory and both `browser_panel.py`/`/ws/browser` being read in full -- key finding there: most of the CDP-relay/screencast machinery isn't ported to the Electron path, it's **deleted**, since a `WebContentsView` is a real, natively-interactive surface with no synthetic-input relay or IME-bridge hack needed at all. `browser_panel.py` and `/ws/browser` are left completely untouched by this migration -- they keep serving the plain-browser-tab use case and the Tauri build during the parallel-testing window.

**Phase 1 (shell parity, minus the Browser panel) written**: new `office-agent-desktop-electron/` package, every `lib.rs`/`background_events.rs` responsibility ported to a TypeScript main-process module (`paths.ts`/`sidecar.ts`/`backgroundEvents.ts`/`tray.ts`/`mainWindow.ts`/`dialog.ts`/`index.ts`, each naming the exact Rust file/function it ports from in its own header). `appDataDir()` ported byte-identical to `app_data_dir()` (same `%APPDATA%\coscribe`/`COSCRIBE_APP_DATA_DIR` convention the Python side also relies on); sidecar spawn uses the same args/env vars (`COSCRIBE_WORKSPACE_ROOT`/`STATE_DIR`/`SKILLS_DIR`/`MEMORY_PATH`/`EXIT_WITH_PARENT`/`PARENT_PID`); `shouldKeepRunningInBackground()` reads `.env` fresh on every close via the `dotenv` package's own `parse()`, same reasoning `dotenvy` was chosen for on the Rust side; splash-then-redirect startup preserves the exact fresh-install failure-detection semantics (distinguish "process already exited" from "still starting," 180s timeout) while simplifying the mechanism (main process drives `loadURL()` directly once ready, rather than the page polling itself the way Tauri's design needed).

**A real correctness bug found and fixed via genuine, if limited, sandbox verification, not just type-checking.** This Linux sandbox can run Electron headless (Xvfb + `--no-sandbox`, confirmed both are available), unlike Tauri/WebView2 which has no Linux story at all -- a real, if partial, verification capability this migration gains that the previous one never had. Two real bugs caught this way before ever reaching a real-hardware test:
1. `child_process.spawn()`'s `stdio` option needs an already-open file descriptor (a number), not a fresh `fs.WriteStream` -- `createWriteStream()` opens its own fd *asynchronously*, so handing it straight to `spawn()` races the spawn call and threw `ERR_INVALID_ARG_VALUE` (`fd: null`) on every run in this sandbox. Fixed by opening the log file synchronously via `fs.openSync()` and passing the raw fd instead (`paths.ts`'s `serverLogFd()`).
2. **Tray "Quit" would have silently done nothing** whenever background-on-close is enabled (the shipped default): `app.quit()` cancels its whole quit sequence if any window's `close` handler calls `preventDefault()` (which the close-hides-not-quits handler always does by default) -- the exact "quit button doesn't quit" failure class this whole migration exists to escape, just a different cause than Tauri's. Fixed with the standard Electron pattern for this: an `isQuitting` flag (`mainWindow.ts`'s `markQuitting()`), set by the tray's Quit handler before calling `app.quit()`, checked by the `close` handler to let that specific close proceed unconditionally. (Separately: `app.exit()` was considered and rejected for the tray Quit handler -- it skips `before-quit`/`will-quit` entirely, which would have skipped `killSidecar()`, reintroducing the exact "orphaned sidecar process" bug class `lib.rs`'s own Rust code was written to prevent.)

**Verified live in this sandbox, end to end, not just compiled**: `npm run typecheck`/`npm run build` clean; a full Xvfb smoke test (fresh `%APPDATA%`-equivalent, simulating a first-run install) confirmed the app launches, `startSidecar()` correctly resolves the dev-fallback path and spawns the *real* Python `coscribe-web` backend with the correct args/env vars, the splash page correctly detects the sidecar coming up and navigates via `loadURL()`, and the real "Set up coscribe" first-run page renders inside the Electron window (confirmed via its own exposed CDP port, not assumed from log output alone). Incidental further confirmation: sending SIGTERM to the Electron main process, while the app itself didn't have a SIGTERM handler and stayed alive (expected -- no handler installed, matches Tauri's own real-quit paths being tray-Quit/window-close only, never a raw OS signal), the sidecar shut down cleanly regardless -- consistent with `COSCRIBE_EXIT_WITH_PARENT`/`COSCRIBE_PARENT_PID`'s own parent-death watchdog on the Python side (`_exit_when_orphaned`) working correctly against this new launcher, unchanged from how it already worked against the Tauri one. One unrelated, pre-existing observation, not a regression from this work: `/internal/events` 404'd against whatever `coscribe-web` build happens to be installed in this sandbox's own venv -- `backgroundEvents.ts`'s reconnect loop handled it exactly as designed (retried indefinitely, never crashed), and this is a sandbox-environment characteristic to note, not something this migration caused or needs to chase.

Frontend: `frontend/src/lib/electron.ts` (parity with `tauri.ts`'s `isElectron()`/`pickFolderNative()`) and `frontend/src/lib/desktop.ts` (a thin `isTauri()`/`isElectron()`/neither dispatcher) added; `DirBrowserModal.tsx` now calls `desktop.ts`'s `isDesktop()`/`pickFolderNative()` instead of `tauri.ts`'s directly, so it doesn't grow three-way branching of its own and Phase 4's eventual Tauri-branch deletion is a clean, isolated removal. `BrowserPanel.tsx` deliberately untouched in this phase (per the plan) -- with no `isElectron()` branch added yet, it falls through to the existing plain-browser-tab WebSocket/canvas path, which already works unmodified inside an Electron window loading the sidecar's real origin, confirmed live in the same smoke test above. `tsc -b`/`oxlint`/`npm run build` all clean for these frontend changes.

Packaging: `electron-builder` config in `package.json` (portable + NSIS Windows targets, `extraResources` staging the sidecar the same way Tauri's `bundle.resources` does, distinct `appId` from Tauri's `com.coscribe.desktop` so both can install side-by-side during the parallel-testing window). `office-agent-desktop/scripts/stage-sidecar.sh` parameterized to accept an optional destination directory (defaults to its original Tauri path, so every existing caller keeps working unchanged) rather than duplicated; `office-agent-desktop-electron/scripts/stage-sidecar.sh` is a thin wrapper calling it with the Electron destination. New `.github/workflows/desktop-electron-build.yml`, `workflow_dispatch`-only on `windows-latest`, mirroring `desktop-build.yml`'s frontend/venv/PyInstaller-stage steps with the Rust/Tauri build step swapped for the Electron one. **Not runnable in this sandbox** (no PyInstaller installed in this sandbox's venv, and the real point of this script -- producing a Windows PyInstaller build -- has never been a Linux-sandbox-verifiable step even for the Tauri shell): bash syntax checked (`bash -n`), parameterization logic read-verified, not executed end-to-end here.

**Not yet built**: Phase 2 (`WebContentsView` embedding + native navigation -- the actual Browser panel replacement), Phase 3 (element-picking), Phase 4 (cutover). **Next real-hardware checkpoint** (Phase 1's own, per the plan): sidecar spawns/is killed cleanly on quit; splash->redirect with no visible connection-refused flash; tray open/quit (the `isQuitting`-flag fix above specifically needs a real click on the tray's "Quit" item, not just code review, to confirm); native toast on a scheduled task completion; second launch focuses the existing window; close hides (and the Settings toggle flips it back to a real quit); native folder picker works; both portable exe and NSIS installer produce a working app; the existing (fallback) Browser panel still works exactly as in a plain browser tab.

**First real-hardware round, via GitHub Actions (a new `.github/workflows/desktop-electron-build.yml`, added to `main` on its own via a small separate PR since GitHub only lists a `workflow_dispatch` workflow in the Actions UI once it exists on the default branch -- not the actual Phase 1 code, which stayed on this feature branch and is selected as the build `ref`): the "portable" build took roughly three minutes from double-click to the window appearing.** Not a hang, not a bug in the app code -- root-caused directly: `electron-builder`'s own `"portable"` target is an NSIS *self-extracting* exe, which unpacks its entire payload (Electron's runtime + the ~146MB sidecar, ~300MB total) to a temp directory on **every single launch**, not once. This is the exact same "onefile self-extraction" tax the project's own PyInstaller spec already documented avoiding on the Python side (onedir chosen specifically over onefile for this reason) -- just reintroduced one layer up, in the packaging config, not the app itself. Fixed: `dist:portable` now runs `electron-builder --win dir` instead of the `"portable"` target, producing the plain unpacked `win-unpacked/` folder -- exactly Tauri's own portable model (exe + resources folder, zip and ship, no extraction step ever, instant launch every time). CI's artifact upload updated to zip that folder directly rather than the old self-extracting exe. Verified in this sandbox: `npm run typecheck` clean after the config change; the actual "does it now launch instantly" re-test is the next real-hardware checkpoint, not yet run.

**Second real-hardware finding, on the same (pre-`--win dir`-fix) build: after the ~3-minute self-extraction, the window showed a blank page with a single line of native-looking error text ("internet connect error") instead of either the styled splash spinner or a styled failure message.** Root-caused via code review of `mainWindow.ts`'s `waitForSidecarThenNavigate()`: the success path called `await win.loadURL(...)` with **no error handling at all** once the port-open check passed. Chromium on Windows has a known false-positive failure mode for this exact shape of request -- `ERR_INTERNET_DISCONNECTED` can fire even for a pure loopback (`127.0.0.1`) navigation when Windows' own Network List Manager briefly reports "no network," most plausible right as a freshly-started process's first request lands (exactly the moment a self-extracting portable build's window first appears). When that happened, Chromium replaced the still-showing splash page with its own native error interstitial -- matching the reported symptom exactly -- and because nothing ever retried the navigation, the app was stuck on that native page even though the sidecar itself was healthy a moment later. Sandbox investigation (a real packaged Linux build under Xvfb, CDP-verified via both `/json/list` and a raw `Runtime.evaluate` call reading `document.body.innerText`) confirmed the splash page itself renders correctly and ruled out "packaged splash file unresolvable" as a cause, which pointed the investigation at the navigation call instead. **Fixed**: `loadURL()` is now wrapped in try/catch inside the same poll loop -- a failure falls through to the existing poll/backoff/deadline handling (same as "port not open yet") instead of leaving whatever Chromium rendered in place, so a transient failure gets retried automatically up to the same 180s deadline rather than stranding the user on a native error page. Verified in this sandbox: `npm run typecheck` clean. **Not yet re-tested on real hardware** -- next real-hardware checkpoint should specifically retry the portable build (now with the `--win dir` fix too) and confirm this error no longer appears, or if it does, that the app recovers on its own without a restart.

**Third real-hardware finding, on the still-pre-`--win dir`-fix (self-extracting "portable") build: the "internet connect error" text above turned out to be a mis-transcription -- the real, exact text was "Internal Server Error", and the real cause was different.** Confirmed by reading the sidecar's own log file the user pasted: `GET / HTTP/1.1" 500`, with a Starlette traceback ending `RuntimeError: File at path ...\resources\sidecar\_internal\coscribe\web\static\index.html does not exist`. Not a packaging config bug (reproduced `collect_data_files("coscribe")` directly against a real, matching PyInstaller version in this sandbox -- `index.html` is correctly collected) -- the temp-directory path in the traceback is the same self-extracting-portable signature Second real-hardware finding above already root-caused and fixed via `--win dir`, so this is very likely the same already-fixed build being retested, not a new bug. Flagged as the next thing to specifically re-confirm is actually gone once a fresh `--win dir` build is tested, not assumed fixed by inference alone.

**Fourth and fifth real-hardware-adjacent findings, both backend bugs unrelated to Electron packaging, found from a live user report (Gemini free-tier quota exhausted mid-turn: spinner wouldn't stop, model switch had no visible effect, "继续" produced no activity) and fixed in `office-agent/src/coscribe/`, not the Electron shell:**
1. **Gemini quota errors triggered ~60-90s of invisible retrying.** `langchain_google_genai`'s own retry decorator retries `ResourceExhausted` with the same exponential backoff as a genuinely transient error, default 6 attempts -- but a daily-quota-exhausted account can't recover within that window no matter how many retries, so all 6 were dead time with zero user-facing feedback. This exact shape was previously observed and documented, not fixed, in this file's own "coscribe-web-lg slow startup" investigation ("burning through most of a run's remaining budget on one 429"). Fixed: `runtime_lg/providers.py`'s `resolve_chat_model` now passes `max_retries=2` to `ChatGoogleGenerativeAI`, capping the worst case at a few seconds instead of over a minute. 180 backend tests pass unaffected (this constructor kwarg isn't exercised by existing mocked-`resolve_chat_model` tests).
2. **Stop was purely cooperative, so it did nothing for a turn stuck inside a raw model call.** `request_stop()` only set a flag `_stream_turn` checks between already-*yielded* stream chunks -- none exist while stuck inside a provider SDK's own internal retry loop, so Stop, model-switch, and a fresh "继续" message all appeared to do nothing during that whole window (switch and "继续" both did register, just with no visible effect on the already-stuck turn). Every comparable agent tool (Claude Code itself, browser-based chat UIs) instead binds Stop directly to cancelling the actual in-flight call (`AbortController` in JS, `asyncio.Task.cancel()` in Python) -- `request_stop()` now does the same, hard-cancelling `_current_turn_task` whenever there's no pending approval/question to resolve instead (a pending interrupt is left to unwind through its own `Command(resume=...)` path unchanged, the same reasoning `abandon_orphaned_turn`'s docstring already uses for the WebSocket-disconnect case). New regression test (`test_stop_hard_cancels_a_turn_stuck_inside_the_model_call`) uses a fake model whose `_astream` genuinely suspends on `asyncio.sleep(999)` -- verified as a true negative first (temporarily reverting the fix made this exact test hang past a 20s timeout instead of passing in ~1s).

**Sixth real-hardware finding, a genuine Electron-shell bug, not a backend one: the native OS folder picker silently fell back to the old in-app browser, with no native dialog ever appearing.** DevTools console (`Ctrl+Shift+I`) showed the actual cause: `Unable to load preload script ... Error: module not found: ../main/dialog`, and `window.coscribeDesktop` was `undefined`. Root cause: `mainWindow.ts` sets `sandbox: true` on the window's `webPreferences` (deliberately, the safer default) -- and Electron's own docs are explicit that a *sandboxed* preload's `require()` only polyfills `electron`/`events`/`timers`/`url`, nothing else, not even a one-line local constants file. `preload/index.ts` imported `PICK_FOLDER_CHANNEL` from `../main/dialog`, a local project file -- that import crashed the preload's entire module load on every single launch, so `contextBridge.exposeInMainWorld` never ran at all. This broke not just the folder picker but everything the preload exposes, including `onSidecarStatus` (the styled startup-failure UI), silently, since a crashed preload throws no error the splash page itself could show. Fixed by duplicating the channel string directly in `preload/index.ts` instead of importing it cross-file. **Verified live in this sandbox, not just type-checked**: ran the real Electron app under Xvfb with the actual sandboxed `webPreferences`, confirmed via a CDP `Runtime.evaluate` call that `window.coscribeDesktop` now resolves to `{onSidecarStatus, pickFolder}` instead of `undefined` -- the same check the user ran manually via DevTools that first surfaced this bug.

**Seventh real-hardware round: the preload fix, Stop hard-cancel, and tray/close-to-tray all confirmed working on real Windows hardware.** Native OS folder picker now genuinely pops the system dialog (no more silent fallback); Stop during an in-flight turn stops immediately; tray quit/close-hide both behave as designed. The earlier "~3-minute self-extracting portable build" symptom didn't recur (double-click-to-window-appearing was slow once, ~fast on every relaunch after) -- root-caused as Windows Defender's real-time scan of a large (~300MB+), unsigned, freshly-downloaded binary tree on its *first* execution, not an app-level bug: confirmed by the user directly (Defender's own scanning process active during the wait, and an immediate re-launch of the exact same exe afterward took only seconds). This is the SmartScreen/Defender risk the migration plan's own Open Question 3 flagged in advance, not a surprise.

**Eighth real-hardware finding, backend again: even after the above, every fresh cold start was slow to become usable, independent of Electron vs Tauri.** Root cause: `web/app.py`'s `lifespan()` awaited `connect_mcp_tools_lg(...)` directly before the app would serve *anything*, so a configured MCP connector slow to connect (Playwright launching a real browser is the worst case) meant a real, repeatable, every-single-time wait, unrelated to the desktop shell. Researched how other agent tools handle this before implementing anything (Claude Code's own real, documented fix for the identical problem: `MCP_CONNECT_TIMEOUT_MS`, default 5s, bound the wait then let a slow server finish connecting in the background) -- mirrored that exact design rather than either extreme (block forever, or never wait at all). Fixed: `lifespan()` now bounds the wait at `MCP_STARTUP_TIMEOUT_SECONDS` (5s) via `asyncio.wait_for(asyncio.shield(...), ...)` -- `shield()` matters here specifically, since a plain `wait_for` would cancel the connect attempt entirely just because the bounded wait gave up on it, rather than letting it keep running. A still-connecting server's tools get spliced in once it finishes, reusing the exact mechanism a live mid-conversation connector-add already relies on (`_refresh_all_sessions_extra_tools`) -- a session opened during that window just starts with fewer tools momentarily, the same non-issue a mid-conversation connector add/remove already is. New regression test proves both halves (opening a session doesn't block on a slow connector, timed not just eventually-passed; that same session picks up the tool once the background connect finishes) and was verified as a true negative first (reverting the fix makes it fail). 154 backend tests pass.

**Project-management note, not a code finding**: to work around a real GitHub Actions free-tier storage-quota wall hit during this phase's real-hardware testing (documented as time-weighted GB-hours billing, not a live snapshot -- deleting old artifacts doesn't retroactively free an already-elevated month's usage), the user forked the working tree (as of the Sixth finding above) into a new **public** repository, `OICWS/coscribe` -- public repos get unlimited free Actions minutes/storage. `office-agent-desktop/` (the Tauri shell being retired) and its own CI workflows were deliberately left out of that fork (not needed by the Electron path this phase is actually testing); `office-agent-desktop-electron/scripts/stage-sidecar.sh` was made self-contained there instead of delegating to the (now-absent) Tauri script. Real-hardware testing continues from `OICWS/coscribe` going forward -- fixes land in both repos until/unless the private `OICWS/project` repo is formally retired in favor of the public one.

**Ninth real-hardware finding, backend: `run_python_script`'s venv creation failed on a fresh Windows machine with "unrecognized arguments: -m venv \<path\>".** Root-caused (background agent) to `sys.executable` resolving to the packaged `coscribe-server.exe` itself in a PyInstaller build, not a real `python.exe` -- `subprocess.run([sys.executable, "-m", "venv", path])` never reaches venv's module runner at all, it's caught by this app's own `--host`/`--port` argparse instead. Fixed: `tools/script_env.py`'s `_venv_create_candidates()` tries `sys.executable` first (correct in dev), then falls back through `shutil.which("py"/"python3"/"python")` (Windows-ordered, `py` first since the official installer always registers it even without "Add to PATH" checked) -- deliberately not gated on `sys.frozen` (PyInstaller-specific; this project has also experimented with Nuitka) so the same logic works regardless of freezing tool. New regression test monkeypatches `sys.executable` to a failing binary and confirms the fallback chain still produces a working venv; verified as a true negative (reverting the fix reproduces the exact reported error text).

**Tenth real-hardware finding, frontend: a new message could be sent while a turn was still in flight.** The Send-button-to-Stop-button swap in `Composer.tsx` was purely cosmetic -- the Enter-key submit path never actually checked `turnInFlight`. Fixed by gating `submit()` on it, while explicitly preserving the `/stop`-as-text escape hatch (`App.tsx`'s deliberate bypass so typing "/stop" always reaches the backend even mid-turn). Verified live against a real running server + real browser (Playwright): sent message 1, immediately attempted message 2 -- it stayed in the draft box, unsent, until turn 1 actually finished, then sent cleanly on retry.

**Eleventh, a new feature, not a bug fix: prompt-cache-hit-rate display.** Requested after a real report that one GLM 4.6v conversation burned 5M+ tokens with "no visible caching happening." Researched how comparable agent tools surface this before designing coscribe's own version (per your explicit ask -- pi, DeepSeek Harness, Cowork, Claude Code, Codex): landed on Codex's own metric definition, `cache_hit_rate = cached_tokens / input_tokens` (not `/total_tokens`, since output is never cacheable). `web/session.py`'s `_usage_event()` reads LangChain's standard `UsageMetadata.input_token_details.cache_read` (a cross-provider field -- `langchain_openai` already maps GLM's OpenAI-compatible `prompt_tokens_details.cached_tokens` into it automatically, no coscribe-side GLM-specific code needed) and adds a "Cache hit (last call)" row to `ContextRing.tsx`'s popover. Confirmed live against real GLM traffic: 0% on an early turn (expected -- nothing cached yet), rising to 92% after several turns in the same conversation, confirming the underlying mechanism works and the earlier 0% reports were just "not enough turns yet," not a bug.

**Twelfth real-hardware finding, frontend: the Environment tab's Add/Remove/list-load calls only had `try/finally`, no `catch`.** An unexpected failure (a 500 from the venv failing to create, a dropped connection) threw from `rest.ts`'s `checkOk()` and became a silent unhandled rejection -- the spinner stopped, but the actual error message never reached the user ("clicked Add, nothing happened"). This turned out to be the same underlying symptom as an earlier pasted DevTools log showing `/api/script-env/packages` 500s with no visible UI reaction. Fixed by adding `catch` blocks to `install()`/`remove()`/`refresh()` in `PackageListSection.tsx`, surfacing the real error via the existing status banner.

**Thirteenth, a new feature: manual Python-interpreter override.** A real machine with Python genuinely installed still failed every auto-detected candidate -- root-caused to two independent, compounding causes: (1) a GUI app's inherited PATH can be stale relative to a freshly-installed Python (Explorer's own environment block doesn't refresh until logoff/logon), and (2) Windows' `python.exe`/`python3.exe` "app execution alias" stubs under `WindowsApps` resolve via `shutil.which` without being real interpreters. No amount of smarter auto-detection closes either gap -- researched how comparable tools solve this (VS Code's Python extension hits the identical wall) and mirrored its answer: auto-detect as the default, plus an always-available manual "enter interpreter path" escape hatch. `tools/script_env.py` gained `get_interpreter_override`/`set_interpreter_override` (validated by actually running `--version`, never trusting a path that merely exists; a configured override outranks even `sys.executable`; setting a new one deletes the existing script-env venv so it actually rebuilds with the chosen interpreter). New `PythonInterpreterSection.tsx` in the Environment tab: a path field plus clickable auto-detected chips. Separately found and fixed: the auto-detected list itself could show a candidate guaranteed to fail if clicked (a packaged build's `sys.executable` *is* the app's own frozen exe -- live-reported: the picker listed `coscribe-server.exe` as "auto-detected") -- added `working_interpreters()` to filter the *display* list to candidates that actually run `--version` successfully, while leaving `_venv_create_candidates`' own try-in-order fallback chain unfiltered (a bad candidate there is instant and harmless, unlike offering it to a user as a "use this" chip). Confirmed live: selecting one's own external venv as the base interpreter builds a fresh, fully isolated coscribe-owned venv from it (does not inherit that venv's existing packages) -- discussed with the user and confirmed this isolation is the intended, wanted behavior, not a gap to fix.

**Fourteenth real-hardware finding, and the most severe of this round: the script-env/node-env REST endpoints were blocking the entire process's single asyncio event loop.** `install_package`/`list_packages`/`uninstall_package`/`set_interpreter_override` all shell out via `subprocess.run` with multi-minute timeouts (venv creation allows 120s, baseline package seeding 300s) -- called directly inside their `async def` FastAPI handlers (no `asyncio.to_thread`), the blocking wait froze *every other request this process served*, not just that one response: other Settings tabs came back empty (their GETs were queued behind it), and a chat message sent over the WebSocket got no response at all, looking exactly like a dropped connection. This bug already existed before the interpreter picker (any first-ever venv creation hit it too), just rarely enough to go unnoticed -- the picker's rebuild-on-override-change behavior made it trivial to trigger, and landed squarely in the exact recovery flow meant to fix a broken setup: live-reported, setting an interpreter override then clicking Add froze the whole app for over a minute. Fixed by wrapping each call in `asyncio.to_thread`, matching the pattern already used elsewhere in the same file (`install_browser`, `npm_latest_version`). Proven with a regression test that races a slow `install_package` against a concurrent unrelated request via `client.portal` (anyio's `BlockingPortal`, the same mechanism `TestClient` itself runs the app's event loop through) -- confirmed as a true negative (fails without the fix, reproducing the exact ~0.5s-queued-behind symptom) -- and re-confirmed against a real running server + real subprocess calls in this sandbox (a real package install completed in ~1s while a concurrent unrelated request returned in ~0.16s, not queued behind it).

**Phase 1 now considered real-hardware-stable.** Fourteen real-hardware/live-report rounds in, no open Electron-shell-specific bugs remain (the Ninth through Fourteenth findings above are all backend/frontend, not shell-specific -- confirming the shell itself, not just its surrounding features, has settled). Phase 2 (`WebContentsView` embedding, replacing the Browser panel's screencast/CDP-relay implementation with a true native child view) starts next.

---

## Phase 8af -- Browser panel: Electron shell Phase 2 (`WebContentsView` embedding), first real-hardware round

Built per the plan's own Phase 2 scope: `office-agent-desktop-electron/src/main/browserPanel.ts` (one singleton `WebContentsView`, attached via `mainWindow.contentView.addChildView()`/positioned via `setBounds()`, IPC handlers for open/reposition/close/navigate/back/forward/reload routed straight to the view's own `webContents` methods, no CDP), an intentionally empty `src/preload/browserPanelContent.ts` wired in now so Phase 3 doesn't need a second real-hardware round just to add it, and a new `electronMode` branch in `BrowserPanel.tsx` alongside the untouched `tauriMode`/non-desktop paths. `browser_panel.py`/`/ws/browser` left completely untouched, exactly as planned. Verified in this sandbox: `tsc`/`build`/`lint` clean on both sides; a headless Xvfb + Playwright `_electron` smoke test was attempted but never produced output and was killed after running far past its own timeouts -- inconclusive, not a pass or a fail, most likely this sandbox's own lack of a real GPU/compositor for a natively-embedded child view specifically (plain BrowserWindow rendering worked fine in Phase 1's own Xvfb test) rather than an app bug, but not confirmed either way. Real hardware is what actually matters here regardless.

**First real-hardware round, all of the plan's own Real-hardware checkpoint 2 items confirmed working**: real page rendering (sharp, scrolls correctly); window resize, drag-to-resize the panel's own width, and moving the window all track correctly with no visible lag; minimize/restore survives; closing and reopening the panel preserves the page's state instead of reloading ("就好像网页放在后台" -- exactly the intended `removeChildView`-not-`destroy` design); native text input (including IME) confirmed working with zero extra code, as expected for a true interactive surface. **The pass/fail bar itself passed**: quit via tray "Quit" with the panel open and previously used exits the app cleanly, no Task Manager needed -- the actual regression this whole migration exists to fix.

**One real bug found and fixed**: clicking a real `target="_blank"` link (a Google search result) spawned a whole separate native OS window showing that page instead of navigating within the panel -- Electron's own default behavior for any `webContents` that doesn't override it via `setWindowOpenHandler`, wrong for a single embedded panel with no browser chrome/tabs concept. Fixed: `setWindowOpenHandler` now denies the popup and navigates the same view instead, keeping every link the user clicks inside the panel (matching the old screencast implementation, which had no "new window" concept at all to get wrong).

**One confirmed pre-existing, non-regressing gap, not fixed**: the embedded page's own Ctrl+scroll/Ctrl+Plus-Minus zoom doesn't do anything. Not a regression from the old screencast path either (that one only ever forwarded raw wheel deltas as CDP mouse-wheel events, with no ctrl-modifier zoom handling of its own) -- genuinely never implemented in either version. Deferred, not started -- see "Later" section below.

**One confirmed, known characteristic, addressed with an install-time mitigation**: every freshly-unpacked build's very first launch was slow enough to look hung (the splash page's own 180s failure UI fires, and the sidecar's log file is empty the whole time -- confirmed live, this is not new). Matches this file's own Phase 8ae "Seventh real-hardware round" finding (Windows Defender's real-time scan of a large, unsigned, freshly-downloaded binary tree on its first execution) exactly, and the user confirmed this now happens on *every* fresh build's first run, not a one-off: "每次新包第一次运行都是要启动很久...第一次进不去，或者要等很久时间." A second launch of the same already-scanned build is fast (seconds) -- Defender caches a clean verdict per file (hash + mtime), it doesn't re-scan an unchanged file it already cleared. The underlying OS/AV cost can't be eliminated in code (the only *real* fix is code-signing, a cost/process decision, not done here), but *when* it's paid can move: `web/app.py` gained a `--version` flag (exits instantly, no port bind/server start) and `office-agent-desktop-electron/build/installer.nsh`'s new `customInstall` step (wired via `package.json`'s `nsis.include`) runs the sidecar exe once, right after files are extracted during install, specifically to trigger-and-cache that scan during the install step -- where a moment's wait is already expected -- instead of at the user's first real launch, where it looks like the app is broken. Only helps the NSIS installer build; the portable "dir" build has no install step to hook this into at all, so it keeps paying this cost at first real launch regardless. **Not yet verified on real hardware** -- next round should specifically retest a fresh NSIS install's first launch.

**Second real-hardware round: the `setWindowOpenHandler` fix confirmed working** -- clicking a link that previously spawned a separate window now stays inside the panel, as intended. **Phase 2 is now considered real-hardware-confirmed**, all of the plan's own checkpoint items passed with one real bug found and fixed along the way -- Phase 3 starts next.

The install-time AV-scan warm-up (`installer.nsh`) remains unverified -- the user's current real-hardware testing is against the portable ("dir") build, which has no install step for this fix to hook into at all, so it genuinely can't be exercised yet. The user confirmed the underlying symptom recurs identically on the portable build (first launch: "starting coscribe" indefinitely, then "coscribe couldn't start / The server is taking far longer than expected," close+reopen fixes it) and asked to record this as low priority for now rather than chase further -- noted, not abandoned; verifying the NSIS-build fix is still the next real-hardware step for this specific item whenever an installer build is actually tested.

**Not yet built**: Phase 3 (element-picking, reusing `browser_panel.py`'s own `_element_at()` JS as a content script in the now-wired-in `browserPanelContent.ts`), Phase 4 (cutover/cleanup).

---

## Phase 8ag -- Browser panel: Electron shell Phase 3 (element-picking), first real-hardware round

Built per the plan's own Phase 3 scope: `browserPanelContent.ts` (the panel's own preload, re-injected on every navigation like a content script) draws the hover highlight/label directly inside the live page's own DOM (`document.elementFromPoint`/`getBoundingClientRect`, mirroring `browser_panel.py`'s `_element_at()` exactly) since the host window's React can't paint over a natively-composited child view; `browserPanel.ts` forwards pick-mode on/off and turns a committed pick into a real screenshot via `webContents.capturePage()`, no CDP/DPI math needed. `BrowserPanel.tsx`'s electron branch gained the "Select" button and reused the existing picked-preview/"Add to chat" UI.

**First real-hardware round: smooth and correct across the board** -- Select tracks the cursor with no lag, tested against Google (search, click a result, back -- all correct), cropping looked right, and the click-while-picking-never-actually-navigates behavior held (no stray new windows, no accidental navigation while selecting). **Second real-hardware round, specifically answering the plan's own Open Question 1**: tested against two sites with real, non-trivial CSP -- Select worked normally on both. Confirms preload-injected content scripts run in Chromium's isolated-world context regardless of the page's own `script-src` CSP, as the plan's own research suggested but explicitly hadn't verified before calling this done (this project has been burned by an "reads as safe in the docs" assumption going untested before -- Phase 8ab's `.parent()` webview hang -- so this got a real check rather than being assumed).

**One real bug found, unrelated to the picking mechanism itself, in the surrounding agent behavior**: sending a picked screenshot with zero accompanying text (a bare attachment, no instruction) led the model to list the workspace, find an unrelated `.pptx` file left over from earlier testing, and start working on that instead of asking what the screenshot was for. Not a state-leak or cross-thread bug (confirmed: the file genuinely already existed in that same workspace from earlier testing) -- `ask_user_question`'s own docstring already covers exactly this case ("use this when you're about to guess at a genuinely ambiguous requirement"), but nothing in `coordinator.py`'s own `INSTRUCTIONS` anchored a wordless attachment specifically as that case, and the model's own judgment this particular time was to explore rather than ask. Fixed with an explicit rule appended to `INSTRUCTIONS`: describe what the image shows and ask what to do with it, don't treat an unrelated file already in the workspace as evidence of intent for a fresh, wordless attachment. Not a hard guarantee (still model judgment, not a constraint the graph enforces), just a more direct anchor than the tool's own generic guidance alone.

**Phase 3 is now considered real-hardware-confirmed** -- every checkpoint item from the plan passed, including the last one (a real click while picking never actually follows the link/triggers the button underneath, confirmed directly against Google's own search-result links: "真的只是选中，然后截图"). Phase 4 (cutover: retire the Tauri shell, promote `office-agent-desktop-electron` to the primary build, update docs/CI) starts next.

---

## Phase 8ah -- Browser panel: Electron migration Phase 4 (cutover), migration complete

All three build phases (shell parity, `WebContentsView` embedding + navigation, element-picking) cleared their own real-hardware checkpoints across Phases 8ae/8af/8ag above. Cutover executed:

- **`office-agent-desktop/` (Tauri) renamed to `office-agent-desktop-tauri-legacy/`; `office-agent-desktop-electron/` renamed to `office-agent-desktop/`** (`git mv`, history preserved on both). The Electron shell is now the primary desktop distribution.
- **App identifiers un-collided, not left sharing one**: the Tauri shell's own `tauri.conf.json` `identifier` changed from `com.coscribe.desktop` to `com.coscribe.desktop.tauri-legacy` (it had been the original holder of the bare id); Electron's `package.json` `appId` reclaimed the bare `com.coscribe.desktop` (dropping its own migration-era `.electron` suffix, which existed only so the two could install side-by-side during parallel testing). Keeps both independently installable during the legacy shell's one-release-cycle rollback window without one silently registering as an "update" of the other under Windows' own installed-program tracking. `appDataDir()`/`_app_data_dir()` are unaffected either way -- both hardcode the `coscribe` folder name directly, never derived from either identifier.
- **`office-agent-desktop/scripts/stage-sidecar.sh` made self-contained** (previously delegated to the Tauri directory by hardcoded relative path, which would have silently broken pointing at a now-renamed-away directory) -- same self-contained PyInstaller-build content already in place on the public `coscribe` repo's own fork, reused here rather than reinvented.
- **CI workflows renamed and repointed**: `.github/workflows/desktop-build.yml` (previously the Tauri build) is now `desktop-build-tauri-legacy.yml`, pointed at the renamed legacy directory; `desktop-electron-build.yml` (previously Electron) is now `desktop-build.yml`, the primary build workflow, pointed at the renamed primary directory. Incidentally fixed a real, pre-existing YAML structure bug in the old Tauri workflow's installer-artifact `path:`/`retention-days:` ordering while rewriting it (a block-scalar list value split across two keys) -- not something ever run since it predates this cutover, not a regression introduced by it.
- **`nuitka-sidecar-build.yml`/`nuitka-module-repro.yml` path references fixed** -- both experiment workflows had hardcoded `office-agent-desktop/...` paths pointing at scripts that moved to `office-agent-desktop-tauri-legacy/` in the rename; left unnoticed, both would have failed outright on their next manual run.
- **Frontend Tauri removal**: `frontend/src/lib/tauri.ts` deleted; `desktop.ts` collapsed to a thin `electron.ts` re-export (exactly as its own prior docstring said it would once this day came); `BrowserPanel.tsx`'s `tauriMode` branch (the Stage-1-only placeholder, never wired past that) and its `getCurrentWindow()`/`@tauri-apps/api` imports removed entirely; `DirBrowserModal.tsx` needed no logic changes at all -- it already went through `desktop.ts`'s dispatcher, exactly the clean isolation Phase 1 built this for. `@tauri-apps/api`/`@tauri-apps/plugin-dialog` removed from `package.json` (bundle size: `app.js` dropped from 335.04 kB to 317.93 kB). `npm run build`/`lint` clean; `mypy src`/`ruff check src tests`/`pytest -q` (886 tests) all clean on the backend side (a handful of comments in `cli.py`/`web/app.py`/`packaging/coscribe-server.spec` describing "the Tauri shell" as the current desktop consumer were also updated to describe Electron, since those behaviors -- app-data-dir fallback, `COSCRIBE_EXIT_WITH_PARENT`, `COSCRIBE_BACKGROUND_ON_CLOSE`, console-window suppression -- are genuinely shell-agnostic and now actually driven by Electron in production).
- **Docs updated**: `office-agent-desktop/README.md` rewritten (no longer "parallel-built, not yet primary" -- now the primary distribution, with the install-time AV-scan warm-up documented); `office-agent-desktop-tauri-legacy/README.md` gained a deprecation notice at the top; `office-agent/README.md`'s "Desktop app" section and `ARCHITECTURE.md`'s own historical-note correction block updated to describe Electron. `runtime_lg/README.md`'s own extensive historical narrative of *why Tauri was originally chosen* (Phase 6's real research/decision record) deliberately left untouched, same reasoning this file's own past phase entries are never retroactively edited -- it's an accurate record of a real decision made at the time, not a currently-wrong claim.
- **Left alone, per the plan**: `office-agent/src/coscribe/web/browser_panel.py`, `/ws/browser`, and `app.py` -- untouched throughout the entire migration, still serving the plain-browser-tab case exactly as before.

**Migration complete.** Electron is the primary desktop shell; Tauri lives on as a rollback net for one release cycle. Next real-hardware round should do one final full pass on the renamed/repointed build (confirm `desktop-build.yml` still produces a working portable + NSIS build under its new name/paths) before considering the legacy Tauri directory eligible for actual deletion.

**This public repo's own cutover is simpler**, since it never carried the Tauri shell in the first place (left out of the fork deliberately): just `office-agent-desktop-electron/` renamed to `office-agent-desktop/`, `desktop-electron-build.yml` renamed to `desktop-build.yml`, `package.json`'s `name`/`appId` reclaiming the plain `coscribe-desktop`/`com.coscribe.desktop` (no legacy-shell collision to avoid here), and the same frontend/backend Tauri-reference cleanup applied identically. No legacy directory, no legacy CI workflow, nothing to keep as a rollback net -- there was never a second shell in this repo to roll back to.

---

## Phase 8ai -- Startup latency: MCP client no longer imported unconditionally (sandbox-verified)

Picks up the "Later" backlog's startup-latency finding (item 2, above -- found while investigating your "即使不添加任何connector...非常缓慢" report). `runtime_lg/__init__.py`'s own top-level `from .mcp import connect_mcp_tools_lg` wasn't the whole story once actually traced through: `web/app.py` also directly imported `..runtime_lg.mcp` at its own top level (for `McpServerConnection`/`connect_one_mcp_server_lg`), and `cli.py` imported `connect_mcp_tools_lg` from `.runtime_lg.mcp` directly too -- so fixing only `__init__.py` would not have saved anything for either real entry point, since both re-imported the same heavy submodule themselves regardless. Fixed all three:

- **`runtime_lg/__init__.py`**: `from .mcp import connect_mcp_tools_lg` replaced with a PEP 562 module `__getattr__` that imports `.mcp` lazily, only when something actually reaches for `connect_mcp_tools_lg` via the package.
- **`web/app.py`**: `McpServerConnection` (only ever used as a local-variable type annotation, never evaluated at runtime under `from __future__ import annotations`) moved under `if TYPE_CHECKING:`. `connect_one_mcp_server_lg` and `connect_mcp_tools_lg` moved to local imports at their two real call sites (`_connect_and_register_mcp_server_lg`, and `lifespan`'s `if settings.mcp_config_path is not None:` block) -- both call sites were already gated on MCP actually being configured, so this is a pure "don't pay for what you don't use" change, not a behavior change.
- **`cli.py`**: same treatment -- `connect_mcp_tools_lg` moved to a local import at its two call sites, both already gated on `settings.mcp_config_path is not None`.
- Confirmed `agent.py`/`audit.py`/`exec_policy.py`/`messages.py`/`providers.py`/`scheduled_tasks.py`/`selfwake.py`/`skill_authoring.py`/`subagents.py`/`workflows.py` -- the rest of `runtime_lg/__init__.py`'s eager imports -- have no transitive import of `.mcp` themselves, so none of them would have silently re-opened the door this closes.
- **Test fallout, fixed**: `test_web.py`/`test_cli.py` monkeypatched the now-removed re-exported names (`coscribe.web.app.connect_mcp_tools_lg`/`connect_one_mcp_server_lg`, `coscribe.cli.connect_mcp_tools_lg`) -- a local `from ..runtime_lg.mcp import x` inside a function reads whatever `runtime_lg.mcp.x` currently is at call time, so retargeting those three monkeypatch calls at `coscribe.runtime_lg.mcp.connect_mcp_tools_lg`/`connect_one_mcp_server_lg` directly (the true source, same module `test_mcp_lg.py` already imports from) keeps every existing test's stubbing intact with no behavior change.
- **Verified in the private repo's sandbox** (identical code applied here; this public repo's own clone has no local Python venv to re-run the suite in, per this repo's established verification pattern -- Python changes are syntax-checked here via `py_compile`, with the full test/lint/type-check pass done once in the private repo before mirroring): `ruff check src tests` clean; `mypy src` diffed line-for-line against the pre-change baseline -- identical 192 errors, all pre-existing `untyped-decorator`/pydantic-BaseModel-subclass noise shifted by a few line numbers, zero new errors; full `pytest -q` suite passes (886 tests). Measured directly, not assumed: `python3 -X importtime -c "import coscribe.web.app"` no longer shows `langchain_mcp_adapters`/`mcp.types` anywhere in the trace at all (previously ~250-360ms combined); a plain wall-clock `import coscribe.web.app` dropped from ~1.9-2.3s to ~1.7-1.9s across repeated runs (noisy sandbox timing, but consistently faster, never slower).
- Items 1 (structural LangChain/LangGraph/MCP/FastAPI-stack import weight) and 3 (first-access Defender-scan cost on the desktop build's `_internal/` DLLs) from that same backlog bullet are unrelated causes, still open -- this phase only closes the one concretely-fixable piece.

---

## Phase 8aj -- Unified corporate-proxy support: web_search + the Browser panel now honor it too

Picks up the "Later" backlog's proxy finding (above -- from your out-of-session "怎么配置代理" ask, recorded then, not acted on until now). LLM API calls already worked with no code needed (each provider SDK reads `HTTP_PROXY`/`HTTPS_PROXY` itself); the other two outbound paths didn't:

- **New shared helper, `runtime/proxy.py`'s `configured_proxy()`**: reads `HTTPS_PROXY` (any case), falling back to `HTTP_PROXY`, via `urllib.request.getproxies()` -- also picks up Windows' own system/registry proxy setting, not just an env var. One helper, two callers, wired into `runtime/__init__.py`'s existing re-export pattern alongside `secrets.py`/`hooks.py`/etc.
- **`tools/websearch.py`**: `DDGS()` -> `DDGS(proxy=configured_proxy())`. `ddgs`'s own constructor has a `proxy=` kwarg completely separate from the `DDGS_PROXY` env var its docs mention (confirmed by reading `ddgs.ddgs.DDGS.__init__` directly, as the backlog item already had) -- a typical corporate `.env` won't have that second, library-specific name, so forwarding the standard one explicitly is the fix, not documenting a new env var to set.
- **`web/browser_panel.py`**: `launch()` now appends `--proxy-server=<value>` to Chrome's launch args when `configured_proxy()` returns one. Chromium's own env-var-based proxy auto-detection is platform-dependent -- reliable enough on Linux, not on Windows (this project's actual target), where it normally needs a real system/registry setting or this explicit flag instead.
- **Verified live in the private repo's sandbox** (identical code applied here; this public repo's own clone has no local Python venv to re-run the suite in, per this repo's established verification pattern): spawned a real headless Chromium (a pre-installed Playwright browser, pointed at via `COSCRIBE_BROWSER_PANEL_EXE`) with `HTTPS_PROXY` set, spied on the actual `asyncio.create_subprocess_exec` args, and confirmed `--proxy-server=...` was present *and* the CDP session still connected successfully afterward (the flag only affects page navigation, not the debugging-port control channel). Also confirmed via a bare `curl -x $HTTPS_PROXY` that a sandbox's own network egress mechanically works for arbitrary outbound hosts (a plain `https://example.com` succeeded); a live `web_search()` call reached `html.duckduckgo.com`/`en.wikipedia.org` but got a connection reset from each -- confirmed as that sandbox's own network-policy/anti-bot reality (the *same* reset happens with a bare `curl` to those exact hosts through the identical proxy, nothing to do with this fix), not something a further code change here could address.
- **Tests**: `tests/test_proxy.py` (new -- uppercase/lowercase env var reading, `HTTP_PROXY` fallback, `HTTPS_PROXY` precedence over `HTTP_PROXY`, unset-is-None), two new cases in `tests/test_websearch_tool.py` (proxy forwarded to `DDGS.__init__` when set, `None` when not). No new test added directly against `browser_panel.py`'s `launch()` -- that module's own existing test file deliberately keeps real-Chromium-dependent behavior out of the automated suite (see its docstring: verified via a standalone smoke test during development instead), a precedent this phase followed rather than broke.
- **Docs**: `.env.example` gained a real (commented-out) `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY` example with an explanation of why `.env` works (the `override=True` load). `README.md`'s Settings-panel section had a stale claim ("proxy vars have to be set in the shell before the process starts") predating that same `override=True` fix -- corrected to say `.env` works, and explain why proxy vars are still left out of the Settings UI itself (a masked/never-round-tripped text field is a worse editing experience for a plain URL, not a functional limitation).
- **Verified in the private repo's sandbox**: `ruff check src tests` clean; `mypy src` -- identical 192 pre-existing errors (one more file now checked, no new errors); full `pytest -q` -- 893 passed (886 + 7 new). This public repo's own clone was syntax-checked via `py_compile` only, per its established pattern (no local venv to run the full suite in here). A real corporate-proxy round-trip (an actual authenticating corporate proxy, not a sandbox's own passthrough one) is still something only your real environment can confirm -- nothing to test further here beyond what's above.

---

## Phase 8ak -- Browser panel: blurry/low-res content on a 3440x1440 monitor, fix applied, not yet real-hardware-confirmed

First real build+test from the renamed Phase 4 paths, on a genuinely new machine/monitor combination -- every prior real-hardware round happened on a different, apparently more standard-resolution display (testing itself happened against the private `OICWS/project` checkout; this repo gets the identical code fix mirrored here).

The embedded page's own content rendered visibly blurry/low-resolution compared to the panel's own UI chrome (address bar, buttons) in the very same window -- confirmed as a real asymmetry, not a subjective impression, via a direct side-by-side legibility comparison (address bar text crisp, page text underneath visibly softer) at a real 3440x1440 resolution, reproducing at both 125% and 100% Windows scaling (ruling out a simple "wrong OS scale setting" explanation) and regardless of panel width or closing/reopening the panel.

**Root-caused against upstream, not guessed**: [electron/electron#39993](https://github.com/electron/electron/issues/39993), a real, confirmed Electron/Chromium bug -- a single `WebContentsView`/`BrowserView.setBounds()` call can leave the view's internal compositor surface painting at its *previous* size even though `getBounds()` immediately reports the new one, so content renders from a stale (smaller) backing texture stretched to fill the real on-screen size -- matching this session's exact reported symptom, and plausibly explaining why it never surfaced on an earlier, presumably lower-native-resolution test monitor. The issue's own confirmed workaround, quoted directly from its resolution: "setting the same bounds twice will make BrowserView paint correctly."

**Fix applied**: `browserPanel.ts`'s new `setBoundsReliably()` helper calls `view.setBounds(rect)` twice in a row, used in both `openBrowserPanel` (the initial open) and `repositionBrowserPanel` (every subsequent resize/reposition) in place of the single call each previously made. `npm run typecheck`/`build` clean in the private repo's sandbox -- this is a rendering/GPU-compositor behavior with no way to visually verify blur or sharpness from a Linux sandbox with no GUI at all, so **this fix is unverified pending the next real-hardware build+test** on the same high-res monitor that reproduced the bug.

Also corrected during this round: a report that Ctrl+scroll/Ctrl+Plus-Minus zoom was "previously fixed, now broken" -- checked against this file's own Phase 8af/"Later" entries and confirmed it was never implemented in either the screencast or Electron path, in any version.

**Correction, found immediately after the above was written -- the real root cause of this whole round's symptoms was something else, and it puts the `setBoundsReliably()` fix's own necessity in doubt.** The first local build (against the private repo's checkout) ran against a git checkout that was 30 commits behind (never pulled before that first `npm run build` in `office-agent/frontend`), predating this migration's frontend changes. Frontend source and build output aren't coupled by any check in this build pipeline -- `stage-sidecar.sh` bundles whatever is currently sitting in `office-agent/frontend/dist/` into the PyInstaller sidecar with no staleness check against the actual git HEAD -- so once the branch was corrected to HEAD but the frontend was told to skip a rebuild (advice reasoning "nothing frontend-facing changed in *this session's* fixes" -- true, but blind to the fact the *very first* build had already baked in stale frontend code from before the pull), the packaged app kept serving that stale bundle indefinitely. A live symptom nailed this down precisely: the Browser panel's own error banner showed `Page.startScreencast failed: 'Screencast is already active'` -- a CDP protocol message that can only come from `browser_panel.py`'s plain-browser-tab/screencast implementation, meaning `isElectron()` was resolving false and the *entire* Electron-native `WebContentsView` code path was never actually running at all. Every symptom this phase attributed to a `WebContentsView` bug is fully and independently explained by "running the wrong, legacy Browser-panel implementation entirely." After rebuilding the frontend from the correct HEAD *and* with the `setBoundsReliably()` fix already in place, the panel confirmed working well on the same 3440x1440 monitor -- but because both changes landed together in the same rebuild, there is no clean way to attribute that success to the frontend fix alone versus the `setBounds()` workaround also mattering. The fix is left in place regardless (a real, confirmed upstream Electron bug per #39993, harmless even where not strictly needed) -- but this phase should not be read as having confirmed that specific bug's presence in this app.

**Structural fix, not just a lesson-learned note**: `scripts/stage-sidecar.sh` now rebuilds the frontend itself (`npm install && npm run build` in `office-agent/frontend`) as its own unconditional first step, before ever invoking PyInstaller -- previously that was a separate manual step nobody automated, easy to skip precisely because the sidecar-staging script itself gave no indication that skipping it was unsafe. Now there's no way to run `stage-sidecar.sh` (which every local dev-loop and every `dist:portable`/`dist:nsis` build already requires) without also getting a frontend build from whatever the current checkout actually is -- closing the gap that caused this whole phase, structurally, rather than relying on remembering to do it by hand next time. `.github/workflows/desktop-build.yml`'s own separate "Build frontend" step was removed as redundant now that `stage-sidecar.sh` does it internally. `office-agent-desktop/README.md`'s "Dev loop" section updated to describe the new behavior and explicitly call out why skipping it is unsafe.

---

## Phase 8al -- PPTX quality/completeness discussion; click-a-shape-in-the-preview-to-target-it (small tier), sandbox-verified end-to-end

Prompted by direct feedback: PPTX generation quality/aesthetics still unsatisfying, plus concrete gaps (SmartArt authoring, video/embedded-object support, some complex templates). Researched real open-source prior art before proposing anything, per an explicit "调研一下市面上所有开源高分高星的项目...不要重复造轮子" ask -- not guessed, not vibes:

- **[ppt-master](https://github.com/hugohe3/ppt-master)** (MIT, independently verified live -- 53.9k stars/4.3k forks/2029 commits, real technical substance, not a star-farmed clone despite several near-identical forks under other usernames muddying the first search results): generates each slide as SVG (a model's own visual-design strength), then converts SVG shapes to native DrawingML -- real, editable PowerPoint objects, not a rasterized image. Distributed as an agent-driven skill/workflow (prompts + scripts a coding agent like Claude Code runs), not an installable library -- same shape as this project's own Skills mechanism, so "adopt the idea, not the code" is the natural fit here regardless of its permissive license.
- **[PPTAgent](https://github.com/icip-cas/PPTAgent)** (real academic project, EMNLP 2025, not a random GitHub repo): generates by analyzing a library of *real, human-designed* reference slides, picking the closest match, then editing its content in place -- the starting point is always a real design, never an LLM inventing colors/layout from scratch. Most directly relevant to the aesthetics complaint specifically.
- **[Presenton](https://github.com/presenton/presenton)** (Apache 2.0, active, 10.2k stars, ships its own MCP server) -- technically pluggable into coscribe's existing MCP-connector mechanism with no new code, but it's a full standalone web app (async job queue, its own upload endpoints) that overlaps heavily with coscribe's existing native pptx tools -- same "overlaps with an existing capability" reasoning that already excluded `markitdown-mcp` from the connector catalog (see the "Later" backlog's extensibility bullet). Not recommended for direct adoption; noted so the option is on record.

Also clarified live: OLE-embedded objects (the missing-capability item) means embedding an actually-editable other-program document (a live Excel table, say) inside a slide, not a picture -- `python-pptx` already exposes this natively (`shapes.add_movie()`/`add_ole_object()`, confirmed by inspecting a real `SlideShapes` instance), unlike SmartArt authoring, which `python-pptx` has zero support for at all (no `add_smartart` method exists) -- creating/editing arbitrary SmartArt from scratch would mean reimplementing a chunk of PowerPoint's own diagram-layout algorithm. One idea confirmed sound and consistent with how `add_pptx_animation` already works in this codebase (`_new_timing_tree`/`_add_animation_step` manipulate raw `<p:timing>` XML directly, since python-pptx has no animation API either): a generic "read/edit the pptx's own internal XML" capability would cover *editing text inside an already-existing SmartArt diagram* (its layout is already valid, only the data-model text nodes need changing) without needing to author new diagram layouts -- and would generalize past SmartArt to any OOXML feature python-pptx hasn't wrapped. Real idea, not started yet -- see "Later" below for the deferred pieces.

Agreed sequencing, three tracks, smallest first:

1. **[x] Click a shape in the rendered preview to target it for a follow-up edit -- shipped, this phase.**
2. [x] Theme-picker (`ask_user_question`) over the real reference-slide-template library -- shipped, this phase (see below; the library itself, it turned out, already existed).
3. Generic pptx-internal-XML read/edit tool (borrowing the SVG/XML-manipulation idea, inspired by ppt-master and this codebase's own animation precedent) -- not started, see "Later".

### Track 1, shipped: PptxShapeOverlay

Mirrors the Browser panel's own "Select an element, add to chat" interaction exactly (same `{name, dataUrl, text, tag}` capture shape Composer.tsx already consumes) rather than inventing a new one -- but a saved `.pptx`'s `shape_index` is a stable, precise address in a way a live DOM element index never is, so the attached note carries the model everything it needs to call `edit_pptx_shape`/`edit_pptx_text` directly (`"deck.pptx", slide 2, shape_index 3 (title) -- text: "..."`), not just a loose description the way a picked web element's text is.

- **`PresentationToolkit.list_pptx_shapes`** (`presentations.py`) gained two new top-level fields, `slide_width_in`/`slide_height_in` -- the whole deck's canvas size, needed to turn each shape's existing inch-based bbox into an on-screen percentage overlay independent of the rendered preview's actual pixel dimensions. Backward-compatible addition; every existing field/consumer (the LLM-facing tool) unaffected.
- **New `GET /api/pptx-shapes` REST endpoint** (`web/app.py`) -- a plain UI-facing read (never reaches the model, never touches the audit log a real tool call would), calling the exact same `PresentationToolkit.list_pptx_shapes` method the LLM-facing tool uses. Workspace-scoped the same way `/api/upload` already is.
- **New `PptxShapeOverlay.tsx`** -- wraps an already-rendered slide preview `<img>` with one invisible clickable region per shape (fetched from the new endpoint), highlighting on hover. Clicking crops the *already-loaded* preview image client-side via canvas (no extra network round trip) and hands the crop + a structured locator note to `onPick`.
- **`ChatLog.tsx`** wires this in wherever a pptx preview already renders -- both `ToolCallRow`'s always-visible completed-call thumbnail and `ApprovalPreview`'s before/after grid (an edit's own before/after, both clickable, so targeting a *follow-up* edit doesn't require waiting for the pending one to resolve first). Detects "is this a pptx preview" via `arguments.path` ending in `.pptx`; targets `arguments.slide` when present (every edit tool), defaulting to slide 1 for a fresh `write_pptx` (whose own preview is always the deck's first slide).
- **`Composer.tsx`** gained a second external-capture prop pair (`externalPptxCapture`/`onExternalPptxCaptureConsumed`), mirroring the Browser panel's existing one rather than generalizing into a shared queue (exactly two sources exist today, each with its own dedicated `App.tsx` state slot already) -- and the outgoing-note-building logic now branches on a new `source: "pptx"` tag so the note reads "(Selected from the pptx preview: ...)" instead of the Browser-panel-specific wording, using the pptx capture's own pre-built locator text verbatim rather than re-wrapping it.

**Verified in the private repo's sandbox, not just typechecked -- a real end-to-end browser test, since this is a UI feature** (identical code mirrored here): no live Anthropic key available there either, so rather than skip real verification, stood up the actual FastAPI app with `web/session.py`'s `resolve_chat_model` swapped for a scripted fake model (the exact technique `tests/test_web.py` already uses, just driven by a real headless-Chromium Playwright session instead of `TestClient`) and drove a real `write_pptx` turn through the real WS protocol. Confirmed live: the approval card renders a real preview image; clicking the "Hello World" title region produces overlay buttons whose own tooltips show the exact real shape text (proving the `/api/pptx-shapes` fetch and percentage-bbox math both work); clicking one crops correctly and the composer shows the resulting attachment chip; a follow-up message's actual outgoing WebSocket frame read exactly `"make this bigger\n\n(Selected from the pptx preview: \"test.pptx\", slide 1, shape_index 0 (PLACEHOLDER (14)) -- text: \"Hello World\")"` with the cropped PNG attached as a real image. `ruff check`/`mypy` clean in the private repo (mypy: 193 errors, identical pre-existing noise plus one new but harmless `untyped-decorator` hit on the new route, same class every other `@app.get` route already has); 4 new backend tests (`test_get_pptx_shapes_*`) plus the full suite (897 passed) all green there. This public repo's own clone: frontend `tsc -b && vite build` and `oxlint` clean, backend Python syntax-checked via `py_compile` only, per this repo's established pattern (no local venv here).

**Real limitation, not yet addressed**: the shape-type label shown in the note (`"PLACEHOLDER (14)"` for a title placeholder) is python-pptx's own raw enum `str()`, same slightly-technical format `list_pptx_shapes` has always exposed to the model -- functionally fine (the model only needs `shape_index` to act), but not the friendliest label a human would want to see if this ever grows a visible on-hover tooltip beyond a debug `title` attribute. Left as-is for this pass; worth a small label-mapping table if this becomes more visible later.

**Not yet real-hardware-confirmed**: whether the interaction *feels* good on an actual click (hit-target sizing on a small thumbnail, hover discoverability, whether cropping via canvas produces acceptable image quality at typical preview resolutions) -- the plumbing is proven correct end-to-end, but "does this feel like a good editing affordance" is a real-usage judgment call this sandbox can't make.

### Track 2, shipped: theme-picker over the already-existing template library

**Real correction before any code was written**: track 2 was originally scoped as "build a real reference-slide-template library," borrowing PPTAgent's idea from scratch. Checking the actual codebase first (not assumed) found that library already exists and was already wired into generation as the *default* path: `tools/pptx_templates.py`'s `load_builtin_templates()`/`fill_pptx_template` -- four real, hand-designed `.pptx` templates (`bold-statement`, `minimal-light`, `modern-block`, plus `velis`, a real CC0-licensed third-party design) with named/described identities, already the coordinator's own preferred tool over `write_pptx` for any deck a user will actually look at, already picked by tone-match description rather than the model inventing colors from scratch. So this phase's real gap was narrower than first scoped: the *model* already picks from real templates, but silently -- the user is never actually asked, even though a real, curated four-way choice already exists every time.

**Fix**: extended `coordinator.py`'s `INSTRUCTIONS` -- when building a brand-new deck the user will actually look at (not editing an existing file, not a small ancillary deck, and the user hasn't already named a template or style themselves), call `ask_user_question` with each available template's own name as a short option (`ask_user_question`'s options are plain short labels, confirmed no per-option preview-image support exists today -- resolved that open question from this phase's own research write-up above by going with the "v1, name/description only" path rather than investing in per-option preview thumbnails first) -- but since a bare name alone ("Bold Statement" vs "Minimal Light") isn't a real choice with zero context, also instructed the model to say a one-line hint per option in its own ordinary reply text immediately before the tool call, drawn from that template's own description it already has in its system prompt.

**Verified with a real model, not a script** -- no live Anthropic credit in this sandbox, but real GLM/DeepSeek/Gemini API keys became available mid-session; used the real Gemini key (`gemini:gemini-3.6-flash` -- `gemini-2.5-flash` itself 404s now, deprecated in favor of that model) end-to-end through a real WS turn (`TestClient`, no fake model, no mocking) for "Make me a short slide deck about our Q3 sales results." Confirmed live: the model listed files first, then replied in plain text with an accurate one-line description of each of the three 16:9 templates (correctly excluding the non-16:9 `velis` from the default set, matching the *existing*, unrelated 16:9-default instruction), then called `ask_user_question` with `header: "Style"`, a question naming the deck's actual subject ("Which visual style would you prefer for your Q3 sales results presentation?"), and `options: ["Bold Statement", "Minimal Light", "Modern Block"]` -- exactly the intended shape, on the first real try, no iteration needed. `ruff check`/`test_coordinator.py`'s 17 tests (schema-compatibility tests included) stayed green in the private repo's sandbox -- a pure system-prompt string change, no code path touched.

---

## Phase 8am -- Frontend redesign direction: attachments, nav rail, Settings restructure, approval-dialog cleanup, copy/rewind placement (all 8 items shipped)

PPTX/ppt-master alignment work is paused here (per explicit user call,
"没有就告一段落" -- if there's nothing left, wrap that chapter up), not
because it's finished for all time but because the user's own live test
("用 deepseek flash 效果就挺好") confirmed deck quality now tracks the
model choice more than any remaining coscribe-side gap. Attention shifts
to frontend/architecture. Everything below was deliberately **discussed
and recorded only** -- explicit instruction: "先不用做，先理解和记录一
下" (don't build it yet, just understand and record it first). Each item
is grounded in the real current code (read live, not assumed) so a
future implementation round doesn't have to re-derive any of it.

**The five reference screenshots this section describes are saved as
real files, not just described in prose** -- `docs/ui-references/
{nav-rail,settings-modal,skills-tab,connectors-tab,approval-dialog}.png`
(private repo only, not synced to the public `coscribe` mirror, per
explicit user call). Written prose is lossy for pixel-level UI
reference; a future session (compacted or brand new) should open these
files directly (a plain `Read` on the path) before touching any of
items 2-6 below, rather than trusting this section's own textual
description alone.

### 1. Attachment paste/preview -- three real, confirmed gaps

- **Copy-a-file-then-paste-into-the-composer does nothing.**
  `Composer.tsx`'s own `onPaste` handler reads only
  `clipboardData.getData("text/plain")` and, in its own words, lets
  anything else "behave natively" -- for a plain `<textarea>`, pasted
  file clipboard data has no native behavior at all, so it's silently
  dropped. Drag-and-drop (`onDrop` -> `onFileChosen`) already works;
  paste needs the equivalent path wired from `event.clipboardData.files`.
- **Pasted/attached images render as a bare text pill, not a
  thumbnail, even though the actual image bytes are already sitting in
  memory.** `onFileChosen` already reads an image `File` into a full
  data URL (`pendingImages` holds `{name, dataUrl}`) before ever
  rendering anything -- the composer's own pending-attachment row just
  renders `{img.name}` as text (same generic pill component every
  non-image file also uses) instead of `<img src={img.dataUrl}>`. This
  is a real, low-effort fix: the data is already there, nothing needs
  fetching.
- **No click-to-open/preview for an attached image, before or after
  sending** -- wanted: a small thumbnail in the pending-attachment row,
  click it to open an enlarged preview (a lightbox); after sending, the
  same should apply to the persisted message (`ChatLog.tsx`'s
  `UserMessageView` currently folds every attachment into the plain-
  text message body via a "(attachment sent)" note -- there's no
  separate attachment element in the sent-message view at all to click,
  for images or otherwise).
- **Non-image files staying as a plain card (no preview) is correct
  as-is** -- explicit user call, matches current behavior for
  `pendingFiles`, nothing to change there.

### 2. Sidebar nav rail -- restructure toward the target screenshot

Target (screenshot 1): hover-to-expand, a pin-to-keep-open click; two
small icons top-right of the collapsed rail that toggle between a
"chat" view and a "workflow" view; below that, a flat list -- New,
Projects, Artifacts, Scheduled, Customize. Explicit scope call: **build
New + Scheduled; skip Projects, Artifacts, and Customize as a sidebar
nav item** (Customize's *content* -- Skills/Connectors/Plugins -- is
wanted, just inside Settings, not as its own rail entry; see item 3).

Current reality (`NavRail.tsx`): hover-to-expand already exists
(`onMouseEnter`/`onMouseLeave` on the rail's own wrapper div) but there
is **no click-to-pin** -- moving the mouse away always collapses it,
no persisted-open state today. The "chat vs workflow" split already
exists too, but as a **Create/Run pill-button toggle inside the
expanded panel** (`mode: "create" | "run"`), not as a pair of icon
buttons on the always-visible collapsed rail the way the target
screenshot shows it. "Scheduled" already exists, but nested as one of
three sub-tabs under Run mode (`RunTab: "workflows" | "scheduled" |
"history"`, reading `getScheduledTasks()`) rather than as its own
top-level flat-list item alongside New -- the target's flatter
structure decouples "which view" (the top icon toggle) from "which
nav item" (the flat list), which is a real structural change from
today's nested-tabs shape, not just a re-skin.

### 3. Settings modal -- restructure toward the target screenshot

Target (screenshot 2): a search bar at the top, sections grouped under
labeled headers (a general "Settings" group, then a distinct
"Customize" group, then "Platform"), skip Billing/Privacy-equivalent
items. The explicit ask: mirror this shape for coscribe's own
categories, with **Skills/Connectors/Plugins specifically pulled out
as the "Customize" group** -- called out as the main thing to align on
here.

Current reality (`SettingsModal.tsx`): a single flat, ungrouped
vertical list (General/Workspace/Providers/Tools/Skills/Connectors/
Workflows/Environment) in a small fixed-size modal (760x640), no
search, no grouping, no visual distinction between "core settings" and
"customize" categories. Skills/Connectors already exist as tabs
(`SkillsTab.tsx`/`ConnectorsTab.tsx`) -- they'd move under a new
"Customize" grouping rather than being new work themselves. **Plugins
has no coscribe equivalent today at all** -- the user didn't send the
plugins reference screenshot yet either ("plugin界面先不发你"), so
scope for that specific piece is still open.

### 4. Skills settings page -- target shape

Target (screenshot 3): "Your skills"/"Discover" tabs; within "Your
skills," a "Created by you" section and a separate "From Anthropic &
Partners" section (coscribe's equivalent: user-authored skills under
`settings.skills_dir` vs the bundled built-in four); an "Add" button
top-right; each row a card (icon, name, author/source line, one-line
description, last-edited date).

Current reality (`SkillsTab.tsx`): a flat list, no grouping by
source, no cards -- just a name/description row with a plain
`ToggleSwitch` (the same shared toggle component `RunPanel.tsx` uses
for scheduled-task enable/disable; this component itself is not the
"ugly slider" from item 6 below, confirmed by reading its source --
that's a separate element, see that item). No "Add"/create-new-skill
entry point in the UI today (skill creation currently happens by the
agent itself via the bundled Skill Creator skill, not a UI form).

- [x] **Click-through to a file browser -- shipped.** Live-reported gap:
  ("为什么不像仓库的截图一样，每个skill可以点开，可以看到文件夹") -- the
  reference screenshots' own "Contents" tab (`docs/ui-references/skills-
  discover-contents.png`) is claude.ai's *remote plugin marketplace*
  detail page (Overview/Skills/Connectors tabs, categories, "try it"
  prompts, version/sync metadata), none of which applies to coscribe's
  local-folder skill model -- discussed and scoped down with the user to
  just the applicable part: a real file-tree browser (folders expand,
  files select) plus a content-preview pane, for one skill's own real
  directory (`SkillInfo.dir`). New `GET /api/skills/{name}/files` (flat
  relative-path list) + `GET /api/skills/{name}/files/{path}` (one
  file's raw text, reusing `WorkspaceScope` for the same path-traversal
  guard every other file-serving endpoint already has -- confirmed live,
  not just tested: `curl .../files/../../../../etc/passwd` 404s).
  Clicking a `SkillRow` now opens `SkillDetailView` instead of only
  toggling it. One addition beyond a plain browser, also discussed
  first: "Tools mentioned in this skill" -- a real string match of every
  registered tool's own name (`GET /api/tools`) against that skill's own
  file contents (all files fetched up front, not just the selected one),
  not a guess. Connectors were explicitly left out of that same scan --
  there's no fixed name list to match against (an MCP connector's tool
  names are dynamic per server), and no existing skill convention for
  declaring one either; skipped for this pass rather than faked. 7 new
  backend tests, full suite green, `ruff`/`mypy`/`tsc`/`oxlint` clean,
  verified live end-to-end (real backend + real Playwright click-through
  of the actual Settings UI, not a synthetic harness).

### 5. Connectors settings page -- target shape

Target (screenshot 4): a table-ish list (Connector / Type / Status
columns, a checkmark or a "Connect" button per row), plus a "Popular
for [category ▾]" suggestion row beneath it. Coscribe's own MCP-server
catalog+configured-list concept (`ConnectorsTab.tsx`,
`getMcpCatalog`/`getMcpServers`) is conceptually the same idea already
-- this is a visual-layout alignment more than new functionality,
unlike Skills/nav rail above which need real structural additions.

### 6. Approval-dialog cleanup -- gray-bar mystery resolved, tightened

Resolved by actually doing the "reproduce a real in-flight approval"
check this section itself called for, rather than guessing further:
built a throwaway Playwright harness mounting the real `ChatLog`
component with a synthetic `run_python_script` approval item, driven
through all three real states in one live mount (`pending` -- open,
Approve/Deny visible; `executing` -- approved, `result` still
`undefined`, same open card; `done` -- `result` populated, matching
`approval-dialog.png`'s own exact content almost verbatim, including
the OMML stdout). **No gray bar rendered in any of the three states.**
This rules out hypothesis (a) outright (there is no distinct loading-
placeholder frame -- the card looks identical through the whole
pending-to-done transition, just with the button row swapped for
"Approved" + the result once resolved) and, by elimination, confirms
(b): `approval-dialog.png` was captured from a build of `ChatLog.tsx`
genuinely older than the current one -- there is nothing left in
today's code to "fix" a slider motion on, because the element itself
no longer exists.

What *was* still real and actionable: the general "read closer to
Claude.ai's own thinner, tighter styling" ask. Tightened `ToolCallRow`
(pending-approval padding `px-3.5 py-2.5` -> `px-3 py-2`, matching the
already-tighter resolved-card padding -- no principled reason for
pending to be larger) and the script `<pre>` block inside
`ApprovalDetail` (`p-2 leading-relaxed` -> `px-2 py-1.5 leading-normal`,
closer to the reference's own denser code-block line spacing).

### 7. Copy button -- move from per-message to per-turn (shipped)

Built. `transcriptGrouping.ts` gained `groupTurns` -- the coarser
grouping this item's own original text said didn't exist yet: splits
a thread's flat `LogItem[]` into per-turn spans (one full user-
message-to-next-user-message range), each carrying its `userItem`,
its `finalAgentItem` (the last agent reply so far, if any), and
whether it still holds an unresolved approval. `ChatLog.tsx`'s
top-level render now maps over turns (`TurnView`) instead of the flat
`groupToolRuns` output directly -- `TurnView` runs that same
`groupToolRuns` pass internally, scoped to just its own turn's items,
so every existing per-item render path (tool-run groups, approval
cards, questions, user/agent bubbles) is unchanged. The old per-
message `CopyButton` (`LogItemView`'s agent branch, hover-revealed) is
gone; `TurnView` renders one `CopyButton` bottom-left of the turn
instead, copying `finalAgentItem.text` -- only once the turn is
actually done (see item 8's own "done" definition below).

### 8. Rewind -- new feature, placed next to the relocated copy button (shipped)

Built as a thin relocation of the existing edit-and-resubmit
mechanism, per this item's own open question -- resolved in favor of
reuse rather than a new backend capability: rewind calls the exact
same `onEditMessage(turnIndex, text)` the pencil-icon edit already
uses, just with the turn's own *original, unedited* text
(`turn.userItem.text`) instead of an edited draft. Real backend
consequence: clicking rewind truncates history back to (and
including) that turn's `HumanMessage` and re-runs it as a fresh turn
(`handle_edit_message`'s real, already-tested truncate-then-resubmit
path) -- in effect "regenerate this reply," discarding whatever came
after. A turn counts as "done" (footer renders at all) when it has a
`finalAgentItem` that isn't still streaming *and* holds no unresolved
approval -- the same real rule `handle_edit_message` itself enforces
server-side ("Resolve the pending approval before editing"), so
rewind is never offered somewhere it would just bounce as a WS error.
New `RewindIcon` (feather `rotate-ccw`). Known, pre-existing gap
carried over unchanged from the pencil-edit mechanism it reuses: a
turn's own attached images aren't threaded through `onEditMessage`
(text-only signature) -- rewinding a turn that had an image attachment
loses it, exactly like editing one already did.

Verified end-to-end via a throwaway Playwright harness mounting the
real `ChatLog` with synthetic multi-turn data (a done turn, and a
second turn still mid-stream): confirmed the footer renders only on
the done turn, Copy actually writes the right text to the clipboard,
and clicking Rewind invokes `onEditMessage` with the exact expected
`(turnIndex, originalText)` pair -- both light and dark themes.

---

## Phase 8an -- Real-usage bug pass: model switcher, PPTX direct-edit gap (investigated, deferred), chat history pagination, tool-call collapse redesign, six small UX fixes

Prompted by direct feedback from actually using the app (six numbered
findings from one message, three more discovered live while working
through them). Each item below was independently confirmed against the
real code before being called a bug -- see the investigation notes for
what was actually read, not assumed.

**Gemini disappears from the model switcher after switching away from
it once (shipped).** Root cause: `ModelPicker.tsx`'s dropdown list comes
from `GET /api/providers`, which reads a provider's `default_model` off
its own per-provider env var (`COSCRIBE_GEMINI_DEFAULT_MODEL`, etc.) --
but first-run setup (`/api/setup`) only ever wrote the *global*
`COSCRIBE_DEFAULT_MODEL`, never the per-provider one. So whichever
provider first-run setup configured was never actually a member of the
switcher's own list -- it only ever *looked* present because the pill's
own label reads `state.model` directly, independent of the list. The
instant a different, properly-added provider became active and the user
tried switching back, the gap became visible. Fixed both ends:
`/api/setup` now writes the per-provider var alongside the global one,
and `GET /api/providers` falls back to the global default's own model
portion when a provider's per-provider var is unset but the global
default names that provider (covers `.env` files written before this
fix too). 2 new `test_web.py` tests plus 1 new `test_setup_app.py`
assertion.

**Tool/function names leaking into the model's own chat prose (shipped).**
Real complaint: replies naming raw tool names ("I'll use
`fill_pptx_template`...") read as confusing and technical to a
non-technical user. `coordinator.py`'s `INSTRUCTIONS` had zero guidance
on this -- confirmed absent, not just unfound. Fixed with a new rule at
the very top of the prompt (general, not pptx-specific), explicitly
mirroring an existing precedent already in this file for the same class
of problem (a template's own internal `"[16:9]"` tag, which the prompt
already told the model never to echo verbatim).

**Tool-call summary row doesn't wrap, runs off the right edge of the
screen (shipped).** `ChatLog.tsx`'s `ToolRunGroupView` collapsed-group
label span had `className="truncate"` -- Tailwind's `truncate` forces
`white-space: nowrap`, which doesn't shrink long text, it just lets it
overflow. Dropped the class (and switched the row to `items-start` so
the chevron aligns with a wrapped label's first line instead of its
vertical center).

**"workplace" folder-emoji badge looked bad (shipped).** `ThreadHeader.tsx`
rendered the workspace badge as `📁 {workspaceLabel}` -- dropped the
emoji, plain text only.

**Scrollbars looked crude/thick (shipped).** No custom scrollbar CSS
existed anywhere in the app -- confirmed via a full-project grep, the
default OS/Electron scrollbar (thick track, distinct up/down arrow
buttons) was just never touched. Added global thin-scrollbar CSS to
`index.css` (`scrollbar-width: thin` for Firefox, `::-webkit-scrollbar`
overrides for the Chromium engine every actual target here runs on).

**A turn-ending error (e.g. hitting a token-usage limit) rendered as a
raw red line spanning the full window width from the left edge, instead
of aligning with the chat column (shipped).** `App.tsx`'s `state.error`
div was a bare sibling of `<ChatLog>`/`<Composer>`, never wrapped in
either one's own centered `mx-auto max-w-[760px] px-4` column. Wrapped
it in the same classes.

**PPTX: "modify my existing PPTX to match this reference template" ends
up building a whole new deck instead of editing the real file in place
(investigated; explicitly not building it).** Real capability gap, not
an instruction-following mistake -- confirmed by reading every slide/
shape-editing tool in `presentations.py`: `delete_pptx_slide`/
`delete_pptx_shape` are strictly one-at-a-time (matches the model's own
"no permission for a batch delete" framing literally), and
`extract_pptx_template` + `fill_pptx_template` together only ever
produce a *new* deck built from a template's shell -- `fill_pptx_template`
opens the *template's* file and pours new content into it, it never
reads or preserves an existing file's own current content. Researched
whether `hugohe3/ppt-master` (the 53.9k-star reference project this
session has drawn on before) solves this -- it doesn't, for the same
architectural reason: its own template-materialization tool
(`mirror_template_materialize.py`) is the same one-directional
"distill a template from a deck" shape as coscribe's `extract_pptx_
template`, never a "reskin an existing file's real content using a
*different* file's theme" tool; its own "beautify" profile explicitly
states "this regenerates a native deck ... it never edits the source in
place." Given a mature, actively-developed reference project hasn't
built this either, and the write-up here identified real, non-trivial
scope for it (transplant `<a:clrScheme>`/`<a:fontScheme>` from a
reference deck onto an existing file's own slide masters, honestly
excluding structural/background/layout transplant as a further, much
larger lift) -- **explicit user call: don't build this.** Left here so
the investigation doesn't need repeating if this comes up again.

**Chat history pagination: /compact permanently hid the earlier
conversation, with no way to scroll up and see it (shipped).** Real,
live-reported complaint -- unlike every other AI product the user had
used, where scrolling up always loads more. The underlying messages
were never actually gone: LangGraph's checkpointer is append-only/
versioned (`aupdate_state`'s `RemoveMessage(id=REMOVE_ALL_MESSAGES)`
sentinel writes a *new* checkpoint, never deletes old ones) -- this was
purely a read-side gap: `send_history` only ever reads the *latest*
checkpoint, and nothing anywhere called `aget_state_history` to read
further back.

New WS message type `load_older_messages` (no payload) ->
`older_messages` reply (`ChatSessionLG.load_older_messages`, `web/
session.py`). Mechanism, verified empirically against a real LangGraph
graph before wiring this in: within one "epoch" (the stretch of
checkpoints between two `RemoveMessage(ALL)` resets, or since the
thread's start), the `add_messages` reducer only ever *appends* --
every older checkpoint's message-id set is a subset of the current
one's. The moment a backward walk crosses a `RemoveMessage(ALL)`
boundary, that breaks: the checkpoint immediately on the other side
holds the *previous* epoch's entire accumulated message list, wholesale
and disjoint from anything already known. So the handler walks
`aget_state_history` backward from a cursor (`_history_boundary_
config`) and returns the first checkpoint whose message ids aren't
already a subset of everything already revealed (`_history_known_
message_ids`, which accumulates across repeated calls so a second
"load older" click on a multiply-compacted thread correctly skips past
the whole batch just revealed rather than re-finding it) -- the whole
previous epoch in one shot, never a partial slice.

Real bug caught by this feature's own tests, not shipped on the first
attempt: the cursor was originally seeded once, eagerly, inside
`send_history` (which only ever runs once, right at connect time) --
for a thread with any messages sent *after* connecting (i.e. every real
thread), that left the cursor frozen at a stale, near-empty snapshot,
so `load_older_messages` found nothing at all. Fixed by seeding the
cursor lazily, on the method's own first call, to *whatever is
currently live* at that moment -- the only version of "already shown to
the user" that's actually correct regardless of how much happened since
connect.

Frontend: new `olderItems`/`olderStatus` state (`reducer.ts`), kept
deliberately separate from the live `items` array rather than prepended
into it -- these are read-only replay (no `turnIndex` an edit could
target) and load from the *opposite* end of the log a live event ever
appends to; keeping them apart also means revealing older history never
touches the existing "scroll to bottom on new item" effect's own
dependency array. `ChatLog.tsx` renders them above a divider, behind a
"Load earlier messages" pill that hides once a call returns empty
entries (`has_more` is a "try again" hint, not a real lookahead -- true
whenever a call found something, even on the genuinely last batch,
since confirming "truly nothing left" would mean walking one more epoch
just to check every time). A `useLayoutEffect` keeps the viewport
visually anchored when a batch is prepended (captures `scrollHeight`
before the DOM update, restores the equivalent `scrollTop` after) --
without it, a prepend this large visibly jumps the scroll position.

5 new backend tests (`test_web.py`) covering: a single compaction's full
epoch revealed correctly; a never-compacted thread correctly reports
nothing to load; and -- the real regression target -- two compactions
in one thread, confirming each "load older" click reveals exactly one
epoch and never re-reveals or skips one (this is also where the
lazy-cursor bug above was actually caught). Full backend suite green,
`ruff`/`mypy` clean, `tsc`/`oxlint` clean. Verified live end-to-end: a
real backend (FastAPI + a scripted fake model, same technique `test_web
.py`'s own `_client_lg` uses, just driven by a real `uvicorn` server
instead of `TestClient` so a real browser could connect) plus a real
`vite dev` frontend plus a real headless-Chromium session -- two turns,
`/compact`, click "Load earlier messages" (confirmed the pre-compact
turns render above a divider, scroll position stays anchored), click
again (confirmed it correctly reports nothing left and the button
disappears).

**Tool-call collapse redesign: an isolated tool call rendered as an
inconsistent bordered box next to every grouped run's plain summary
line (shipped).** Direct complaint against a reference screenshot of
the target UI (claude.ai's own transcript rows): every tool-call
disclosure there is a flat "chevron + one-line summary" row that
expands to a plain, unboxed list of individual steps, with no visual
difference between a single tool call and a run of several. Reading
the real code found this was *already* true for a run of 2+ items
(`ToolRunGroupView`, no rounded box, plain expanded sub-items via
`ToolCallRow`'s `compact` prop -- both from an earlier real-user
complaint about "a wall of boxes") -- the inconsistency was narrower
than first framed: `groupToolRuns` only wrapped a *run of 2 or more*
into a `ToolRunGroup`; a lone tool call between two agent replies (a
"run of one") fell through unwrapped and rendered via `ToolCallRow`'s
own *default*, bordered-card styling instead -- the one path that
still looked like the "before" screenshot.

Also checked (per this file's own "grounded in the real current code"
discipline) whether the group-header *summary text* itself needed new
generic phrasing ("Used X tools", "Checked XX") for a mixed-tool-type
run -- it didn't: `summarizeGroupParts` already lists each clause
individually, capped at 3 plus "and N more" ("Listed files, Read
`a.txt`, Read `b.txt`, and 2 more"), which is exactly the phrasing the
reference screenshots themselves show ("Ran a command, Added a task:
..., Added a task: ..., and 3 more") -- nothing to add there.

**Fix**: `groupToolRuns` now always wraps a tool/approval run into a
`ToolRunGroup`, including a run of exactly one -- removed the `length
=== 1 passes through unwrapped` special case. `TurnView`'s own
`entries.map` dropped the now-dead `entry.kind === "tool" ||
"approval"` branch (a bare tool/approval `LogItem` can no longer reach
it). `ToolCallRow`'s bordered-card styling is now reachable only via
`ToolRunGroupView`'s still-unresolved-pending-approval path (an
actionable item genuinely warrants more visual weight than a plain
history row) -- every already-resolved tool call, singular or grouped,
now renders through the identical flat `compact` row.

`tsc`/`oxlint` clean, full backend suite green (no backend code
touched). Verified live end-to-end against a real backend (scripted
fake model issuing two isolated single-tool-call turns plus a three-
call grouped turn) and a real headless-Chromium session: confirmed both
the isolated calls and the group render as identical flat chevron rows
with no border/background anywhere, and expand to the same plain
nested-list treatment.

**Follow-up round on the two features above, from continued real usage
(shipped).** Four more real, live-reported issues:

- **"Load earlier messages" appeared on every thread, including a
  brand-new one with nothing to load.** `olderStatus` had no way to
  distinguish "never tried, nothing to try" from "never tried, but
  there's real reason to believe there's more" -- both just read
  "idle". Fixed with a real, cheap (O(1)) signal from the backend:
  `send_history` now checks whether the current checkpoint's own first
  message is a compaction summary note (the exact text `_handle_compact`
  writes, `_COMPACT_NOTE_PREFIX`, shared between both) and reports
  `has_older` in the `history` WS event -- true only for a thread that's
  actually been `/compact`'d at least once. `olderStatus` gained a
  fourth state, `"none"`, that the frontend never auto-loads from.
- **Manual button replaced with scroll-triggered auto-load.** Real
  preference: infinite-scroll-up, not a click. `ChatLog.tsx`'s new
  effect only re-subscribes when `olderStatus` itself changes (a ref
  holds the latest `onLoadOlder` callback rather than putting it in the
  dependency array) -- real bug caught before shipping: without that,
  every unrelated parent re-render while still "idle" would re-fire the
  effect and could send a duplicate load request before the first one's
  own "loading" status had propagated back down to stop it. Checks once
  immediately on entering "idle" too, not just on a scroll event, so a
  thread short enough that its content doesn't fill the viewport still
  loads without requiring an actual scroll gesture.
- **A tool-call "group" of exactly one item still showed a group header
  plus an expanded child repeating the identical label** -- two clicks
  to see anything, and duplicated text. `ToolRunGroupView` now skips
  straight to rendering a single `ToolCallRow` for a length-1 group;
  `ToolCallRow`'s own chevron/label/expand-to-result is already the
  exact interaction a "group" of one needs.
- **`run_python_script`/`run_node_script` summaries ignored the tool's
  own required `description` argument**, always reading as a bare "Ran
  a command" -- every command in a group looked identical until
  individually expanded. `TOOL_SUMMARIES` now threads `description`
  through as the object, matching the reference UI's own Background
  Tasks panel (which always names what a command was for) and the
  precedent `run_background_script`'s entry already set.

5 new/updated backend tests (`test_web.py`, including a real
`has_older=True` case: compact a thread, reconnect with a fresh WS
connection -- the actual scenario that matters, not just the flag in
isolation). Full suite green (1111 passed), `ruff`/`mypy`/`tsc`/`oxlint`
clean, `npm run build` (not just `tsc --noEmit`) verified clean after
the `tsc -b`-only build failure the previous round shipped with (see
that round's own follow-up commit). Verified live end-to-end: a real
backend + real headless-Chromium session confirmed no button/indicator
on a fresh thread, an isolated approved command expands to one clean
block with its real description shown, and after a real page reload
(the actual "reconnect" this feature targets) scrolling to the top
auto-loads the pre-compact turns with zero clicks.

---

## Later -- real intentions, not actively scheduled

Deliberately un-numbered per your call: backend/foundation (Phases 2-6
above) comes first; these get picked back up once that's done and there's
a concrete reason to prioritize a new surface.

- [x] **PPTX quality: a real reference-slide-template library, replacing
  generate-colors-from-scratch** -- shipped, stale "not started" note
  corrected. Found already done (commits predate this file's own catch-
  up) while about to start it as this session's "next phase": real OMML
  equations (`add_pptx_formula`, vendored LaTeX compiler) and
  `extract_pptx_template` (distills a reusable template from any
  reference deck, PPTAgent-inspired) are both live, tested, and visually
  verified through the real LibreOffice pipeline -- see `PPTX_DESIGN.md`
  §25/§26 for the full design and verification record. §27 went further
  still, expanding `set_pptx_transition` from 4 to 48 real PowerPoint
  transitions. The `ask_user_question` per-option-preview-image
  sub-question this bullet used to raise was superseded by
  `extract_pptx_template` itself being immediately usable in the same
  turn, no picker needed. `image_search.py` (zero-API-key Openverse/
  Wikimedia sourcing, flagged in §27 as "not yet acted on") is the one
  genuinely still-open thread from that same round of research.
- [x] **PPTX quality/completeness: a generic pptx-internal-XML read/edit
  tool** -- shipped as `read_pptx_xml`/`edit_pptx_xml`. Went with the
  narrower of the two open options this bullet itself named: an
  element-scoped edit (an `xpath` must match exactly one real element,
  same discipline `edit_file`'s own `old_text` uses), not a full-file
  `apply_patch`-for-OOXML tool. Closes the concrete SmartArt-text-editing
  case this bullet named, plus generalizes to any other OOXML feature
  python-pptx hasn't wrapped. See `PPTX_DESIGN.md` §36 for the full
  design and two real bugs found and fixed before shipping (SmartArt's
  real text lives in a *linked package part*, not the shape's own inline
  XML; a naive `//xpath` on an attached element silently searches the
  *whole slide*, not just that shape -- both verified empirically, not
  assumed, and both have regression tests).
- [x] **Electron Browser panel: Ctrl+scroll/Ctrl+Plus-Minus zoom on the
  embedded page doesn't do anything** -- shipped. `browserPanelContent.ts`
  (the panel's own preload) relays a ctrl-held wheel event's `deltaY` to
  `browserPanel.ts` via IPC (`webContents` has no wheel event of its own
  in the main process); Ctrl+Plus/Minus/0 is caught directly there via
  `before-input-event`. Verified live under xvfb, not just type-checked
  -- launched the real app, opened the panel via the same contextBridge
  API the UI itself uses, dispatched a real ctrl-held `WheelEvent` inside
  the embedded page and sent real synthetic ctrl+0/ctrl+plus key events,
  confirmed the exact expected zoom factors each time (1.3x, then reset
  to 1.0x, then 1.1x).

- **Stop can't actually interrupt a Playwright MCP action already in
  progress** -- not started; noted here per your request ("先记录到
  roadmap", explicitly "不投入" for now). Live-reported: `/stop` during a
  `playwright_browser_*` call left the tool visibly still running
  underneath a UI that showed the turn as stopped, until a hard app
  restart. Researched real prior art before proposing anything (MCP spec,
  the installed `mcp` Python SDK's own source, playwright-mcp's issue
  tracker, an "Agent Patterns Catalog" stop/cancel writeup, and a Claude
  Code issue showing the *same* class of bug in a more mature
  implementation): the MCP spec does define client-sent
  `notifications/cancelled` (stdio transport; HTTP transport instead
  treats closing the SSE stream as the cancel signal) but a server
  receiving it "MAY ignore ... if the request cannot be cancelled" -- no
  guarantee. The installed SDK (`mcp` 1.29.0, read directly:
  `shared/session.py`'s `send_request()`) never sends that notification
  when the caller's own asyncio Task is cancelled, and exposes no public
  request-id hook a caller could use to send it manually without
  reaching into private internals. Separately, playwright-mcp itself
  (Microsoft's own) shows no evidence in its docs/issues of honoring
  mid-action cancellation at all -- its only long-running-operation
  tools are timeouts (`PLAYWRIGHT_MCP_TIMEOUT_ACTION`/`_NAVIGATION`) and
  progress notifications, not real interruption. The Agent Patterns
  Catalog's own "Stop/Cancel" pattern write-up explicitly excludes this
  exact situation from its recommended "propagate a cancellation token"
  approach ("when cancellation cannot propagate cleanly and would leave
  inconsistent state"). Recommended path when this gets picked back up:
  an honest UX message on Stop ("stopped waiting, but this step may
  still be finishing in the background") rather than sinking effort into
  a protocol-level fix the server would likely ignore anyway. One
  genuine silver lining confirmed live: with accept-edits on, hitting
  Stop in the narrow window *before* a queued tool call actually starts
  executing does cleanly abort it ("User rejected the tool call ... The
  tool was not executed") -- only an already-*executing* call can't be
  recalled.

- **Prompt-cache hit rate could likely be improved, not just displayed**
  -- not started; noted here per your request. The display feature
  itself (this file's Phase 8ae, Eleventh finding) is confirmed working
  (92% after several turns in one real GLM conversation), but no
  investigation has happened yet into *raising* that rate (e.g. whether
  anything in a turn's request shape avoidably varies and caps how much
  of the prefix stays stable). Deliberately deferred -- explicitly not
  blocking on this ("暂时现在不管").

- **A one-time font-swap flash on load** -- not started; noted here per
  your request. Live-reported: a few seconds after the app becomes
  interactive, right around when the model picker/provider list finishes
  loading, the whole UI's font visibly flashes once. Not yet root-caused
  with certainty, but the likely cause: `@fontsource/ibm-plex-sans`'s
  default `font-display: swap` renders with a fallback system font
  first, then swaps to the real webfont once it downloads -- a classic
  FOUC-shaped flash, though the exact timing coincidence with the model
  list specifically hasn't been confirmed as causal versus two unrelated
  events just landing close together. Low severity (the user's own
  description: "看起来变换不大") -- deferred rather than guessing at a
  `font-display`/preload fix without being able to visually verify it in
  this sandbox (no real browser rendering available here beyond
  automated, non-visual checks).

- **Startup latency: MCP client imported unconditionally, even with zero
  connectors configured** -- not started; noted here per your request
  ("先记录到roadmap"), found while investigating "coscribe starts slowly
  even with no connectors added" (asked directly, not guessed). Measured
  live with `python3 -X importtime -c "import coscribe.web.app"` in this
  sandbox: plain `import coscribe.web.app` alone now takes **~2.4-2.9s**
  (4 runs), up from the ~1.1-1.4s "start-to-`/api/tools`-responding"
  figure `runtime_lg/README.md`'s "slow startup" section documented after
  its own optimization pass -- real growth since then, not the same
  number re-measured. Two distinct causes, confirmed separately:
  1. **Structural, not really fixable**: self-time-sorted `-X importtime`
     output is dominated by third-party framework modules building
     pydantic v2 schemas for lots of typed models -- `mcp.types` 123ms,
     `fastapi.openapi.models` 100ms, `langchain_core.documents.base`
     89ms, `langsmith.schemas` 81ms, `langgraph_sdk.auth.types` 47ms,
     `anthropic.lib.streaming` 37ms, and more in the tens-of-ms range --
     spread across the whole `langchain`/`langgraph`/`mcp`/`fastapi`/
     provider-SDK dependency tree, not concentrated in one coscribe-owned
     module. This is the same "death by a thousand cuts" story
     `runtime_lg/README.md` already documented, just with a heavier
     dependency tree than when that was written. Structurally different
     from Codex/Claude Code (compiled binaries, no Python interpreter
     startup or import tree of this size to pay at all) -- some gap here
     is inherent to the LangChain/LangGraph/MCP/FastAPI stack this project
     is built on, not a coscribe bug.
  2. [x] **Concrete and fixable, same lazy-import pattern already proven
     for docx/pptx/xlsx -- fixed, see Phase 8ai below.** Was:
     `runtime_lg/__init__.py` line 8, `from .mcp import
     connect_mcp_tools_lg`, an unconditional module-level import, pulled
     in unconditionally by both `cli.py` and `web/app.py` at their own top
     level -- so `langchain_mcp_adapters` (and the `mcp` SDK it drags in,
     including that 123ms `mcp.types` line above) got imported at every
     single startup, regardless of whether the user had any MCP server
     configured at all. Directly matched what you reported ("即使不添加任
     何connector...非常缓慢").
  3. **Unverified in this sandbox, plausibly also real**: the desktop
     build is a PyInstaller `onedir` bundle (deliberately not `onefile`,
     see `packaging/coscribe-server.spec`'s own docstring on why) -- but
     a fresh Windows machine, especially a locked-down corporate one,
     commonly pays real-time antivirus/Defender scanning cost on first
     access to a newly-extracted `.exe` + its `_internal/` DLLs/`.pyd`s,
     separately from anything in coscribe's own Python import graph. No
     way to measure this from this sandbox.
  Not yet investigated further per your own "先记录到roadmap" -- next
  pass should also check whether anything added since the documented
  optimization pass (Browser panel, workflows, scheduled tasks,
  background tasks, node scripts) reintroduced an eager heavy import the
  same audit would catch, not just the MCP one found here.

- **TUI**: build only once the WebSocket protocol has settled from the
  phases above (persona selection, selfwake/approval message types,
  etc.), so it's "a new client on an unchanged protocol," not a third
  parallel implementation. Retire `cli.py`'s separate REPL loop once TUI
  covers its use cases. Needs your testing either way -- terminal
  look-and-feel isn't something a screenshot/Playwright check can
  meaningfully evaluate.
- [x] **Production packaging** -- shipped, ahead of this bucket's original
      "last, once everything above has settled" sequencing (prioritized
      directly on request instead). `../office-agent-desktop/` is a Tauri
      shell wrapping this *same* web UI + a local server sidecar (not a
      separate app to maintain) -- see `office-agent-desktop/README.md`
      and `README.md`'s "Desktop app" section for the design
      (`WebviewUrl::External`, no bundled frontend of its own). The
      primary distribution mode is a **portable, no-install build**: a
      plain `cargo build --release` already stages the PyInstaller-frozen
      sidecar next to the compiled binary for any profile (confirmed by
      actually building and running it, not inferred from docs -- Tauri
      has no official "portable" bundle target); zip
      `coscribe-desktop.exe` + the `sidecar/` folder together and that
      zip *is* the distributable -- extract anywhere, double-click, no
      installer/admin prompt/Program Files entry. A traditional NSIS/MSI
      installer is also produced as a secondary, optional path.
      `.github/workflows/desktop-build.yml` builds both on `windows-latest`,
      manual-trigger only (`workflow_dispatch`, deliberately not on every
      push -- a full Rust+Tauri+PyInstaller build is heavy) -- run it from
      the repo's Actions tab ("desktop build (Windows)" -> "Run workflow"),
      then download the `coscribe-desktop-windows-portable` artifact for
      the no-install exe+sidecar, or `coscribe-desktop-windows-installer`
      for the NSIS/MSI. Verified working on a real Windows runner (6 runs,
      most recent green as of this writing). Confirmed with you: web stays
      a first-class way to use Coscribe even with a desktop build --
      the desktop build is this UI in a native shell, not a replacement
      for it.
- **A real sandbox for `run_python_script`/`run_node_script`**: today they
  run directly on the host, no isolation -- "no sandbox, the approval
  prompt IS the safety mechanism" (see `tools/scripts.py`'s docstring).
  Not scheduled -- noted here from comparing notes against deepseek-ai's
  `deepseek-harness` (a Cordis-plugin-based agent framework; coscribe is
  deliberately *not* adopting its "everything is a plugin" architecture,
  confirmed with you). Its one idea worth remembering independent of that
  architecture: tool execution routes through swappable "capability seam"
  interfaces (`ctx.subprocess`/`ctx.sandbox`) instead of a tool calling
  `subprocess` directly, so a real sandbox backend (Docker/gVisor/...)
  can be dropped in later by swapping the interface, not rewriting the
  tool. If/when a real sandbox actually gets prioritized, do the same:
  give `run_python_script`/`run_node_script` a thin, swappable execution
  interface instead of touching `subprocess` inline, before wiring in
  whatever sandbox backend gets picked.
- **Extensibility/plugin system** -- scope decided in discussion; item 2
  below has since shipped (see Phase 8c). Items 1/3/4 still not started.
  Explicitly blocked on the same sandbox gap as the bullet directly
  above -- see "Explicitly not adopting" below for the scope that was
  rejected and why. Converged scope, four independent pieces, none
  requiring the sandbox work first since none of them run untrusted code:
  1. **Curated office-relevant MCP connectors** -- extend the existing
     hardcoded `MCP_CATALOG` in `web/app.py` (currently playwright/fetch/
     memory/sequential-thinking/time -- general-purpose/dev-oriented, not
     office-specific) with hand-picked, version-pinned entries (same
     "pinned, not `@latest`" discipline the existing `playwright` entry
     already documents) sourced from Anthropic's own official/community
     plugin marketplace -- reuses Anthropic's existing submission
     validation ("automated validation + safety screening," per their
     docs) as the base trust layer instead of coscribe building its own
     review pipeline; coscribe's own added value is just "which of
     these are actually useful for office work, verified to run here."
     No infrastructure to build -- periodic manual curation against
     Anthropic's marketplace listing, same low-infra spirit as the
     existing catalog.

     **Researched during Phase 8c, deferred -- nothing genuinely fit.**
     Went looking for real candidates instead of guessing package names:
     Google Workspace (Calendar/Drive/Gmail) only has an OAuth-
     authenticated *remote* MCP -- needs Phase 6 (remote MCP OAuth),
     still paused. Slack overlaps with the still-paused Phase 5b, not
     something to sneak in through the catalog instead. The official
     `@modelcontextprotocol/server-gdrive` package is npm-flagged
     "no longer supported" (confirmed via the npm registry directly, not
     assumed). Notion's official server needs an integration token --
     the catalog's one-click add flow has no "prompts for an API key"
     UI yet (only `needs_browser_check` exists as a special case), so
     wiring it in would mean building that UI too, not just adding a
     catalog entry; Notion's own docs also say they're prioritizing the
     remote server and may sunset the local one. `markitdown-mcp`
     (Microsoft, no-auth, otherwise a strong candidate) converts Office
     docs/PDFs to Markdown -- directly overlaps with coscribe's own
     dedicated docx/pdf/pptx/xlsx tools and Skills, the same "overlaps
     with an existing capability" reason Filesystem/Postgres are already
     excluded from the catalog. Asked you directly rather than force a
     mediocre pick; you chose to skip this item for now. Revisit once
     Phase 6 lands (unlocks Google Workspace properly) or a genuinely
     good no-auth candidate turns up.
  2. [x] **A coscribe-native `skill-creator` meta-skill -- shipped as
     "Skill Creator" (Phase 8c).** See that section below for the full
     write-up.
  3. **Hooks stay an internal implementation mechanism, never
     user-authored/selected** -- e.g. a scheduled-workflow feature could
     use PreToolUse/PostToolUse hooks internally (audit logging, a
     guardrail) as invisible plumbing, but there is no "write your own
     hook" or "browse hooks to enable" surface for end users. Hooks are
     the one piece of this whole system that runs unapproved, silently,
     on every matching event (unlike a tool call, which always shows an
     approval card) -- keeping them first-party-only is what lets the
     rest of this scope skip the sandbox work entirely.
  4. **No public, anyone-publishes plugin marketplace for now** -- see
     "Explicitly not adopting" below.
- [x] **Unified corporate-proxy support -- fixed, see Phase 8aj below.**
  Was: not started, noted here per your explicit request (asked "怎么配置
  代理" out of session, then said "记下来" for this exact scope before any
  code changed). **Requirement: when this gets built, adding a proxy must
  cover all three outbound paths at once -- LLM API calls, web_search, and
  the Browser panel -- not just LLM calls.** Current state per surface, as
  it stood before that phase, from actually reading the code (not
  assumed):
  - **LLM API calls (Anthropic/OpenAI/Gemini) already work today**, no
    code needed -- each provider SDK (httpx for Anthropic/OpenAI, grpc for
    Gemini) reads the standard `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` env
    vars on its own. `cli.py`'s `_prepare_env`/`web/app.py`'s
    `create_app_lg` both call `load_dotenv(dotenv_path, override=True)`
    specifically so a value written into `.env` reliably beats a stale
    OS-level proxy already set (a real corporate-machine bug already hit
    and fixed -- see that comment). Deliberately not exposed in the
    Settings UI (`GeneralTab.tsx`'s own note, `/api/config`'s `allowed`
    set excludes these two keys) -- `.env` (or a real shell env var) is
    the only way to set them today.
  - **`web_search` does not honor them at all** -- `tools/websearch.py`
    constructs `DDGS()` with no `proxy=` argument; `ddgs.DDGS.__init__`
    (confirmed by reading its actual source) only reads its own
    `DDGS_PROXY` env var or an explicit constructor kwarg, completely
    independent of `HTTP_PROXY`/`HTTPS_PROXY`. Fix would be forwarding
    `HTTPS_PROXY`/`HTTP_PROXY` into `DDGS_PROXY` (or passing `proxy=`
    directly) when set, inside `build_websearch_tools`.
  - **The Browser panel's headless Chrome likely does not honor them
    either, at least on Windows** -- `web/browser_panel.py`'s `launch()`
    spawns Chrome via `asyncio.create_subprocess_exec` with no `env=`
    kwarg, so it does inherit the whole process environment (including
    `HTTP_PROXY`/`HTTPS_PROXY` if set), but no `--proxy-server=` flag is
    ever passed, and Chromium's own env-var-based proxy auto-detection is
    platform-dependent -- reasonably reliable on Linux, not reliable on
    Windows (normally needs system/registry proxy settings or an explicit
    `--proxy-server=host:port` launch flag instead), and Windows desktop
    is this project's actual target platform. Not verified against a real
    Windows machine, just read from the code and Chromium's documented
    per-platform behavior. Fix would be adding
    `--proxy-server=<HTTPS_PROXY or HTTP_PROXY>` to `launch()`'s `args`
    when one of those env vars is set.
  - Docs gap either way: `.env.example` didn't mention
    `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` at all (not even as a comment),
    and `README.md`'s existing proxy note ("have to be set in the shell
    before the process starts") predated the `override=True` fix above
    and was misleading by that point -- `.env` works too.

---

## Explicitly not adopting (found in openworker, deliberately skipping)

- **`cloud.py` / any hosted component** -- conflicts with the
  local-first, purely-on-your-machine identity. Revisit *only* if Phase
  5c's Slack round-trip turns out to genuinely need something reachable
  from outside the local network that isn't already solved by the
  webhook/event model -- not planned preemptively.
- **`stt/` (voice input)** -- a different modality entirely, unrelated to
  this transition, not in scope.
- **A public, anyone-publishes/anyone-installs plugin marketplace**
  (bundling skill + slash command + MCP server + hooks as one
  installable unit, importable from an arbitrary GitHub repo) -- the
  natural next step once Skills/MCP/Hooks/Workflows all existed
  independently, and the obvious reference model (this environment's own
  Claude Code plugin ecosystem). Rejected for now on one specific
  finding, not a vague "seems risky": hooks and MCP servers both already
  run with the *full* privileges of the OS user coscribe runs as, no
  sandbox (same gap the `run_python_script`/`run_node_script` bullet
  above describes) -- a marketplace changes the trust model from
  "deliberately self-authored config" to "one click, trust a stranger's
  packaged code," with nothing technical stopping a malicious or
  compromised plugin from doing real damage (up to and including
  deleting arbitrary files the OS user can reach). Explicitly checked
  whether the reference platform (Claude Code) already solves this
  before assuming coscribe would need to solve it from scratch --
  researched and confirmed it doesn't: no formal MCP certification (only
  light "automated validation + undocumented safety screening" for
  community-marketplace *submissions*; anything added directly via
  `claude mcp add` isn't reviewed at all), no AI-driven plugin/MCP
  discovery-and-install (contrary to an initial assumption -- the one
  real conversational-scaffolding feature that exists,
  `mcp-server-dev`'s `build-mcp-server` skill, helps an *author* write a
  brand-new MCP server, it doesn't help a *user* discover and install an
  existing one), and no sandboxing of hook/MCP execution at all --
  fully decentralized, explicitly "user-beware." So there's no existing
  design to borrow for the hard part; building it for real would mean
  solving sandboxing first, as its own separate, larger effort (see the
  bullet above). Revisit only after that sandbox work happens, if ever --
  not planned preemptively. The scoped-down alternative that *is* planned
  (curated first-party MCP picks + a skill-creator + internal-only
  hooks, all not touching untrusted code) is the bullet directly above
  this section.
