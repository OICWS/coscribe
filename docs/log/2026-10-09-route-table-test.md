# The server's route table is checked against a list (shipped)

Why: `web/app.py` is about to be split into routers. FastAPI matches routes in the
order they were added (`/ws/browser` has to come before `/ws/{thread_id}`), so a route
dropped or moved during the split changes behaviour while every other test still passes.

## Shipped
- `tests/test_web_routes.py` builds the app and compares its routes, in registration
  order, with `tests/web_routes.txt` (111 lines: methods and path; WebSocket routes and
  the static mount marked). A change that adds, removes or moves a route edits the list
  in the same PR; `UPDATE_ROUTES=1 pytest tests/test_web_routes.py` rewrites it.

## Not verified
- Routes that only exist for some settings (none are known; the list is for the default
  ones).
