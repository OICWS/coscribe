# The connectors, threads, internal and scheduled routes move out of web/app.py (shipped)

Why: next step of splitting `web/app.py` (`docs/plan.md` item 1), the same way as the settings,
files and skills routes (`2026-10-09-routes-settings-files-skills.md`).

## Shipped
- `web/routes/connectors.py` (`/api/mcp/*`: the catalog, OAuth apps, servers, sign-in),
  `threads.py` (`/api/threads*`, thread groups, sub-agents, background tasks, a thread's files
  and environment), `internal.py` (`/internal/*`), `scheduled.py` (`/api/scheduled-tasks*`,
  `/api/workflows/*`). The helpers only one of them uses moved with it (`_mask_value`,
  `_NPM_PACKAGE_NAME_RE`, `_NoSocket`, `_thread_status`, ...).
- `AppState` (`web/state.py`) now carries what these need as well: the session getters, the
  thread and sign-in stores, the MCP connections and OAuth objects, and the scheduled-run
  helpers.
- `app.py` is about 1150 lines shorter (1330 left); the WebSocket routes and the setup
  shared by everything stay.
- Every non-blank line of the old `app.py` is still in `app.py` or a route module, unchanged apart
  from `@app.` becoming `@router.`, relative imports gaining a dot, and the function-level import of
  the OAuth names becoming a module-level one in `connectors.py` (the one non-verbatim line group).
- Test patch target: `coscribe.web.routes.threads.open_in_os`.

## Not verified
- The desktop app and a real connector sign-in; the route table, the full suite and the
  line-by-line check are the evidence.

## Follow-ups
- The WebSocket routes, then `tests/test_web.py`.
