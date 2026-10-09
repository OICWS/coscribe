# The conversation engine moves out of web/ (shipped)

Why: `web/session.py` is not part of the server: the CLI runs conversations through it
too, which made `cli` depend on `web` and, through it, on FastAPI, and made
`web.session` import `cli` (a cycle that cli.py's import order hid). It also had to move
before `web/app.py` is split: a plain rename is followed by merges, so other sessions'
edits to it carry over.

## Shipped
- New package `coscribe/conversation/`: `session.py` (`ChatSessionLG`), `activity.py`,
  `context_usage.py`, `thread_meta.py`, `turn_lock.py`, moved with `git mv`; `events.py`
  (`EventSink`, the one method of the WebSocket the session uses, so it no longer
  imports FastAPI) and `prompts.py` (`INIT_PROMPT`, from `cli.py`).
- `pyproject.toml`: `conversation` is a layer between `web` and `coordinator |
  runtime_lg`, in the keychain and HTTP contracts. Two ignored imports are gone
  (`web.session -> cli`, `cli -> web.session`), and the HTTP contract has none left;
  three known upward imports remain.
- `cli.py` imports `ChatSessionLG` at the top: the circular-import ordering it needed
  is gone, and so are the `import coscribe.cli` lines tests had for it. The
  `type: ignore[arg-type]` on the CLI's own socket stand-in went too.
- Tests: patch targets `coscribe.web.session.*` are now `coscribe.conversation.session.*`.
  There is no re-export left at the old path: it would stop git following the rename.
  A branch that edits `web/session.py` merges `origin/main` into
  `conversation/session.py` by itself; a test string that patches `coscribe.web.session`
  needs the new name.

## Not verified
- The desktop app and a real model: the move changes no behaviour, and the whole suite
  is the check.

## Follow-ups
- `docs/plan.md` item 1: the route-table test, `AppState`, then the routes.
