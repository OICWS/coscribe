# tests/test_web.py is split into tests/web/ by domain (shipped)

Why: last step of splitting `web/app.py` (`docs/plan.md` item 1): one 8.7k-line test file was
the hot file every web change collided on.

## Shipped
- `tests/web/test_{chat,threads,connectors,settings,skills,scheduled,subagents,internal}.py`
  and `tests/web/helpers.py` (the fake models, `_settings`, `_client_lg`, `_receive_until` and
  the helpers used by more than one file). Tests moved verbatim; a helper used by one domain
  lives in that domain's file.
- The two other files that imported from `test_web` import from `web.helpers`.
- Rule for new tests in `office-agent/CLAUDE.md`: they go into the file of their domain.
- Checked: `pytest --collect-only` lists the same 1832 tests (compared by test name, ignoring
  the file).

## Not verified
- Which domain a test belongs to was decided by its name; a few are arguable (chat vs threads).

## Follow-ups
- `test_chat.py` and `test_threads.py` are still about 2k lines; split them when a third
  area grows in them.
