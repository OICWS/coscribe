# The route test checks the order that matters, not the whole order (shipped)

Why: `web/app.py` is being split into routers that will register their routes in a
different sequence than the one file did. The first version of `tests/test_web_routes.py`
compared the table in registration order, which would fail on every harmless re-sequencing.

## Shipped
- `tests/web_routes.txt` is sorted; the test compares the sorted table.
- A second test fails when a fixed path comes after a pattern that would also match it for
  the same method (`/ws/{thread_id}` before `/ws/browser`), the case where order is behaviour.
- A third test: the static-files mount is last.

## Not verified
- Routes whose paths overlap only through a custom path converter: none exist.
