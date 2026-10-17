# frontend: the React + TypeScript UI, built into the server's static directory

## Purpose
The UI of coscribe. `src/main.tsx` mounts `App.tsx` (or `components/dock/PanelWindow.tsx` in a
detached panel window); `App.tsx` owns the chat socket and the reducer and is the hot file.
`src/state/reducer.ts` turns server events into the chat log; `src/components/` holds the views
(`chatlog/` the transcript, `settings/` the settings modal, `workflow/` the workflow editor and
run view, `dock/` the panel dock); `src/lib/` the transport and helpers (`ws.ts` the chat socket
with reconnect, `rest.ts` the typed `/api` calls, `launchToken.ts` the launch token, `electron.ts`
and `desktop.ts` the bridge to the Electron shell, `dockLayout.ts` and friends the panel layout);
`src/types/` the wire and REST shapes. Does not own the server (`web/`), a conversation's turns
(`conversation/`) or the Electron shell (`office-agent-desktop/`).

## Public interface
None: no package exports. The contract is the wire. `src/types/wire.gen.ts` is generated from
`conversation/wire.py` by `python scripts/gen_wire_ts.py` (never edit it); `wire.ts` re-exports it
as `WsServerEvent` and `WsClientMessage`. `types/session.ts`, `settings.ts` and `workflow.ts` are
hand-written REST shapes pinned only by the e2e specs, so change them with the route.

## Depends on
No Python package. It talks to `web/` over `/api/*` and `/ws/{thread_id}` (the Vite dev server
proxies both to `COSCRIBE_BACKEND`, default `http://127.0.0.1:8000`) and, in the desktop app, to
the preload object `coscribeDesktop` of `office-agent-desktop/`. `npm run build` writes into
`../src/coscribe/web/static/`, which is gitignored.

## Invariants
- A new or changed WebSocket event starts in `conversation/wire.py`; regenerate the types, then
  fix the type errors. `check.sh` fails on a stale `wire.gen.ts`.
- A WebSocket and every non-read request must carry the launch token: `ws.ts` builds its URL with
  `socketUrl`, `main.tsx` calls `sendTokenWithWrites` to wrap `fetch`. Do not call `fetch`
  against a write route another way. Dev against a token-guarded backend needs the same
  `COSCRIBE_LAUNCH_TOKEN` in both processes.
- `ws.ts` holds a send until the connection's own `state` and `history` arrive, because `history`
  replaces the log wholesale (`reducer.ts`); keep new sends behind that.
- Hot files: `App.tsx` (about 1,100 lines, ratcheted in `quality/baseline.json`) and
  `components/dock/*` change often (Slides work); look at the open pull requests first.
- UI text is English only. Unused files and exports fail `knip` (`knip.json`).
- A build must pass `tsc -b`, not only `tsc --noEmit`: use `npm run build`.

## Tests
Unit tests (vitest, `npm test`) sit next to the file as `x.test.ts`: `src/state/reducer.test.ts`,
`src/lib/launchToken.test.ts`, `src/lib/dockLayout.test.ts`, `src/lib/dockArrangement.test.ts`,
`src/lib/docxReview.test.ts`, `src/lib/slides.test.ts`, `src/lib/transcriptGrouping.test.ts`,
`src/components/chatlog/helpers.test.ts`. `tests/e2e/*.spec.ts` (Playwright, `npm run test:e2e`)
runs against a live `coscribe-web` and `npm run dev` (nightly in `.github/workflows/e2e.yml` with
a stub model, not a required check). `office-agent/tests/test_wire_ts.py` covers the generator.

## Verify by hand
The desktop-only paths (native folder picker, browser panel bounds, detached panel windows) in
the Electron app on Windows; layout and the dock in a real window; a reconnect after the server
restarts.

## Reviewed
2026-10-17 (first card).
