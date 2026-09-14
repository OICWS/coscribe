# coscribe-frontend

React + TypeScript + Vite rewrite of `coscribe`'s web UI. Replaced
the old `coscribe/web/static/` vanilla HTML/JS frontend at cutover
(see history below). The backend (`coscribe.web.app`) is unchanged by
this rewrite -- this project is a pure frontend that speaks the same
WebSocket/REST contract, hand-encoded in `src/types/wire.ts` from a
direct read of `web/app.py`/`web/session.py`.

## Dev loop

```sh
npm install
npm run dev
```

`vite dev` proxies `/api/*` and `/ws/*` to a real, separately-running
`coscribe-web` process (default `http://127.0.0.1:8000`, override with
`COSCRIBE_BACKEND`). Every feature here is built and manually verified
against real backend traffic, not mocks:

```sh
# in office-agent/
COSCRIBE_DEFAULT_MODEL=gemini:gemini-flash-latest .venv/bin/coscribe-web --port 8000
# in office-agent/frontend/
npm run dev
```

## Production build

```sh
npm run build
```

Compiles straight into `../src/coscribe/web/static/` (`vite.config.ts`'s
`build.outDir`, `base: "/static/"` matching `web/app.py`'s
`app.mount("/static", ...)`) -- that directory is a build artifact now,
gitignored in `office-agent/.gitignore`, not hand-edited source. Fixed
(non-hashed) output filenames `app.js`/`style.css`: `web/app.py`'s
`_NoCacheStaticFiles` already sends `Cache-Control: no-store` on
everything under `/static/` specifically because these files change on
every `git pull` + restart with no other cache-bust mechanism, so
content-hash filenames would be redundant. `coscribe-web` then just
serves this directory directly -- `GET /` returns its `index.html` as a
plain `FileResponse`, no dev proxy involved. Run `npm run build` once
after `npm install`, and again after any frontend change, before
starting `coscribe-web` for real use.

## Status: Phases A, B, C, D, and Cutover shipped

Phase A (project scaffolding + the core chat experience) is live-verified
against a real backend and a real Gemini key: multi-turn conversation,
streaming, an approval-gated tool call (`write_file`), mode-pill
(`/plan`↔`/accept-edits`↔normal) round-tripping through `state` events,
the model picker reading `/api/providers`, the usage bar reading real
`usage` events, and `/clear`.

Phase B (the 6-tab settings modal) is also live-verified against a real
backend: General field save round-tripped through `/api/config`, a
custom provider added/removed through `/api/providers`, tools listed
grouped by category, a real MCP connector added via the Built-in tab
(`uvx`-based `fetch` server, confirmed actually connected -- `uvx` is
available in this dev environment) and removed again, and the `git`
catalog card's `needs_config` redirect into the prefilled Custom form.
`src/types/settings.ts` + `src/lib/rest.ts` hand-encode every settings
REST endpoint from a direct read of `web/app.py`, same discipline as
`wire.ts`. The Workflows tab's live-update wiring (`workflowEventTick`
in `reducer.ts`, bumped on `tasks_changed`/`workflow_saved`/
`workflow_run_progress`) is implemented but only verified against the
empty-state response -- this dev backend had no saved workflows to
exercise the populated-list rendering against.

### Feature-parity checklist (chat core only -- see "Deferred" below for
what Phase A intentionally does not cover)

| `app.js` behavior | Phase A status |
| --- | --- |
| User/agent bubbles | ✅ `ChatLog` |
| Streaming two-phase finalize (`agent_delta` accumulate → `agent_message` replace) | ✅ `reducer.ts` |
| Tool-call/tool-result lines | ✅ `ChatLog` (tool kind) |
| Approval cards (Approve/Deny → `approval_response`) | ✅ `ChatLog` + `App.tsx` |
| Composer: Enter to send, Shift+Enter newline, Ctrl/Cmd+Enter insert-newline, IME composition guard | ✅ `Composer.tsx` |
| Auto-grow textarea | ✅ `Composer.tsx` |
| Drag-and-drop file attach (any file, not just via the picker) | ✅ `Composer.tsx` -- real dragenter/dragover/drop wiring on the whole composer, `onFileChosen` reused so a dropped file goes through the exact same image-vs-upload branch a picked one does |
| Large paste collapses into a removable "Pasted (N chars)" pill instead of filling the textarea (mirrors claude.ai) | ✅ `Composer.tsx` -- threshold-gated (`PASTE_CARD_MIN_CHARS`/`PASTE_CARD_MIN_LINES`), full text still reaches the model via `outgoingText`, only the composer/chat-bubble stay uncluttered |
| Send/Stop button swap | ✅ `Composer.tsx` |
| `/stop` special-cased as a dedicated `stop` message (not queued) | ✅ `App.tsx` |
| Instant commands (`/plan`, `/clear`, ...) don't set turnInFlight | ✅ `reducer.ts` |
| Mode pill (Normal/Plan/Accept-Edits, diff-based `/plan`+`/accept-edits` toggling) | ✅ `ModePill.tsx` |
| Model picker (`/api/providers`, `switch_model`) | ✅ `ModelPicker.tsx`, including 1-9 keyboard shortcuts (Phase C) |
| Usage bar (`state.context_window` + running `usage.total_tokens`) | ✅ `UsageBar.tsx` |
| Thread-id-in-URL convention | ✅ `lib/ws.ts`'s `resolveThreadId()` |
| History replay on connect (`history` event) | ✅ `reducer.ts` |
| `/compact` (`compacted` event) | ✅ rendered as a system note in `ChatLog` |

### Phase B tab-by-tab status

| Tab | Status |
| --- | --- |
| General | ✅ shared Save/dirty-scan with Workspace |
| Workspace | ✅ directories field (typed rows + browser sub-modal) |
| Providers | ✅ catalog prefill, configured list, builtin-name-collision handling |
| Tools | ✅ read-only, grouped by category |
| Connectors | ✅ search/filter/sort, pending-add survives modal close, browser-check flow, npm version-pin updates |
| Workflows | ✅ saved workflows + recent runs, live-updates via `workflowEventTick` (verified against empty state only -- see below) |

### Phase C status

Live-verified against a real backend: the persona picker showed on a
genuinely new thread (no `?thread=` param), sent `select_persona`, and
the header updated once the confirmatory `state` event arrived; the
session menu opened/closed and listed sessions; slash-command
autocomplete filtered on `/pl`, and arrow-down + Tab correctly selected
`/compact`; a real file attachment round-tripped through `/api/upload`
and the workspace-note text was folded into the outgoing message; and
the model-picker's `1` shortcut sent `switch_model` with the exact
`providerKey:default_model` wire format (a second configured provider
was added via Settings to have something to pick, then removed after).

While verifying this, found and fixed a real gap that predates Phase C:
`ModePill`, `ModelPicker`, and the new `SessionMenu` didn't close on an
outside click at all -- app.js has a single shared "click anywhere else
closes the open popup" listener that Phase A/B never ported. Added
`src/lib/useClickOutside.ts` and wired it into all three.

Not ported: the "+"-menu chrome around attachments/autocomplete (app.js
has a `+` button with "Add files or photos" and "Slash commands" menu
items; here, attaching is a single always-visible 📎 button and
autocomplete triggers directly off typing `/`) -- same end capability,
less chrome, a deliberate simplification not a gap.

### Phase D status

**WebSocket reconnect/resume** (genuinely new -- `app.js` has no
equivalent, a dropped socket there needs a manual page reload):
`lib/ws.ts`'s `connect()` now retries with exponential backoff (1s, 2s,
4s, ... capped at 15s) on any unexpected close, and relies entirely on
the backend already sending a fresh `state` + `history` pair on every
(re)connection to resync -- no client-side message-replay bookkeeping
needed. A "Connection lost -- reconnecting..." banner shows while down.
Live-verified by actually killing and restarting the real dev backend
mid-conversation: the banner appeared and disappeared at the right
times, and a follow-up message after reconnecting got a real answer
that correctly referenced something said before the outage (conversation
state survives on disk, independent of the socket).

**Chat-log system notices** ported from app.js that Phase A/B/C had
missed: `workflow_run_started` ("Running workflow..."), `recording_started`
(two text variants), `workflow_saved` (chain-with-step-count vs. agent
variants), and a `compacted` message that now correctly reads "N
messages" (it was mislabeled "tokens" before) with the real `elapsed_ms`
duration app.js shows. Also fixed a real functional bug found in the
process: `tasks_changed` didn't clear `turnInFlight`, which is the
*only* completion signal a `/runworkflow` turn sends (no `agent_message`
of its own) -- before this fix, running a workflow from the session menu
left the composer stuck showing "Stop" forever.

**Playwright smoke suite** (`tests/e2e/`, `npm run test:e2e`): no
frontend test infra existed before this. Five specs against a real
backend, no mocks -- chat round trip, `/clear`, an approval-gated tool
call, a General-tab settings save, and the persona picker (skips itself
if the dev backend has no personas configured). All required real fixes
to be reliable, not just pass once:
- `waitForConnected()` (`tests/e2e/helpers.ts`) waits for the model pill
  to show a real model before interacting -- React 18 StrictMode's
  double-effect-invoke in dev opens a throwaway first socket that's torn
  down almost immediately, and typing before the surviving second socket
  is up can silently race that teardown.
- `data-testid="chat-log"` / `data-testid="thread-header"` added to
  scope text assertions precisely -- ambiguous `getByText` matches (e.g.
  a bare "4" matching both a chat reply and the usage bar's "4.9k") or a
  persona name transiently existing in two DOM regions across a
  transition both looked like real bugs before scoping fixed them.
- `workers: 1` -- all specs share one real backend process; running them
  concurrently made real LLM/tool-call turns contend with each other.
- A generous default `expect.timeout` (15s, 30s+ on assertions chaining
  two model calls): this session made a very high volume of real Gemini
  calls across all four phases' live verification, and actually hit real
  429 rate limits (`quota_value: 5`, `retry_delay: 30s`) partway through
  writing this suite -- confirmed via the backend's own log, not
  guessed. Tight timeouts flake on that; it isn't a bug in the app.
- `/clear wipes the chat log` sends `/clear` right after `hello` --
  found that `/clear` respects the turn lock (only `/stop` bypasses it,
  per `request_stop()`'s docstring in `web/session.py`), so it can queue
  behind an in-flight turn instead of running immediately. The spec now
  waits for `hello`'s turn to actually finish first, same as a real user
  naturally would.

**Honest status, not just a "run `npm run test:e2e`" claim**: all 8
specs passed cleanly together at least once during this work (screenshots
and the clean run are what the fixes above are grounded in), but by the
end of this session the dev backend's Gemini quota was sufficiently
exhausted from the sheer cumulative call volume across Phases A-D's live
verification that re-runs started genuinely timing out on real 429
retry delays, not on anything the suite or the app does wrong -- confirmed
each time via the backend's own log and via screenshots showing the
correct end state, just later than the assertion's timeout. A quieter
API key/quota (or just a different day) should run this suite green
without needing any of that context. Backend's own `pytest` suite (245
tests, no external API calls) stayed green throughout.

### Cutover status

Done. `vite.config.ts`'s `build.outDir` points at the real `STATIC_DIR`
(`base: "/static/"`, fixed `app.js`/`style.css` filenames -- see
"Production build" above); the old hand-written `app.js`/`index.html`/
`style.css` are removed from git (`git rm --cached`, directory now
gitignored). Live-verified against the real production path, not just
`vite dev`'s proxy: started a real `coscribe-web` process, hit
`http://127.0.0.1:8000/` directly (no Vite dev server at all), confirmed
`GET /static/app.js` returns `Cache-Control: no-store`, and drove a real
multi-turn conversation plus opening Settings → Connectors through the
built bundle. Backend's 245 `pytest` tests stayed green throughout --
this was a pure frontend-build-and-serve-path change, no backend/protocol
code touched.

### Known dev-mode-only artifact

React 18 `StrictMode` double-invokes effects in dev, so the WebSocket
connect/cleanup in `App.tsx` briefly opens two connections per page load
(visible as two "WebSocket ... accepted" lines in the backend log) --
the first is torn down immediately by the cleanup function. Production
builds don't double-invoke effects; this is not a bug to fix.

## Type-checking and lint

```sh
npx tsc -b     # strict TypeScript, clean
npx oxlint .   # clean
```
