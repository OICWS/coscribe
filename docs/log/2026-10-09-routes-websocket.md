# The WebSocket routes move out of web/app.py (shipped)

Why: last route step of splitting `web/app.py` (`docs/plan.md` item 1); after it only the app
setup, `lifespan` and the shared helpers are left there (1108 lines, from 4059).

## Shipped
- `web/routes/ws.py` holds `/ws/browser` and `/ws/{thread_id}`, moved verbatim, in that order
  (the fixed path must come first). `app.py` includes the router like the others.
- Patch target `BrowserPanelSession` is now `coscribe.web.routes.ws` in `test_browser_panel.py`.
- The route test read an empty path for included WebSocket routes, so its shadowing check did
  not cover them; it now reads the real path (`_path`). The route table is unchanged.

## Not verified
- A real chat and browser panel in the desktop app (the WebSocket tests pass: 290 in the
  web, browser panel and wake files).

## Follow-ups
- Split `tests/test_web.py` into `tests/web/` (in `docs/plan.md`).
