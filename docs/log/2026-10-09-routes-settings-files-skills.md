# The settings, files and skills routes move out of web/app.py (shipped)

Why: third step of splitting `web/app.py` (`docs/plan.md` item 1). The routes were closures
inside `create_app_lg`; they need only a handful of its objects (`settings`, the session
getter, a few helpers), so each area becomes a module with a `router(state)` function and the
route bodies move verbatim.

## Shipped
- `web/state.py`: `AppState`, what the routers are built from (`settings`,
  `context_window_client`, `get_session`, `session_extra_tools`, `skills_by_name`,
  `providers_info`). Each router binds the names it uses to local variables with their old
  names at the top of `router(state)`, so the route code is unchanged.
- `web/routes/settings.py` (config, providers, code, secrets, memory, script and Node
  environments, commands, tools), `files.py` (upload, attachments, previews, screenshots,
  pptx shapes, directory browsing), `skills.py`. `routes/shared.py` holds the four helpers more than
  one module (or `app.py`) uses: `_mask`, `_read_mcp_servers_raw`, `_read_providers_raw`,
  `MAX_UPLOAD_BYTES`.
- `app.py` is 1000 lines shorter; it builds `AppState` at the end of `create_app_lg` and includes
  the three routers before the static mount. Every line of the old file outside the import block is
  still in `app.py` or a route module, unchanged apart from `@app.` becoming `@router.` and the
  relative imports gaining a dot (checked line by line).
- `tests/test_web_routes.py` reads routes inside included routers (FastAPI 0.142 keeps them in an
  `_IncludedRouter`). Test patch targets moved with the code: `coscribe.web.routes.settings.install_package`,
  `coscribe.web.routes.skills.load_catalog`, `coscribe.web.routes.files.MAX_UPLOAD_BYTES`.

## Not verified
- The desktop app; the route table, the full pytest run and the line-by-line check are the evidence.

## Follow-ups
- Connectors, threads, internal, scheduled and WebSocket routes.
