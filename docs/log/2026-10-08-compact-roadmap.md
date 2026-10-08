# ROADMAP.md compacted; AGENTS.md is the canonical instructions file (shipped)

Why: a new session had to search an 8.8k-line append-only log to find anything, and
sessions collided on phase ids and at the end of the file.

## Shipped
- The old `office-agent/ROADMAP.md` (8.9k lines, 126 sections) moved unchanged, with a
  note on top, to `docs/history/roadmap-through-2026-10-08.md`; a test pins its hash so
  it stays frozen.
- `office-agent/ROADMAP.md` is now about 125 lines: how to find things, the Windows-only
  constraint, what was built by phase family (every phase id appears, so code comments
  that name one can be looked up), the old open items that are still open, those that
  are superseded, and what is deliberately not being done.
- `AGENTS.md` holds the instructions every session reads; `CLAUDE.md` is one line,
  `@AGENTS.md`, so Claude Code reads the same text. Both root and `office-agent/`
  instructions, the PR template and CONTRIBUTING point to `docs/plan.md`, `docs/log/` and
  the archive instead of ROADMAP phase ids.
- `ARCHITECTURE.md` (already short) gets the package layers and the secrets and
  registered-app connector decisions; it was not rewritten.
- `tests/test_docs_layout.py`: ROADMAP at most 300 lines, the archive unchanged, log files
  dated and titled, `CLAUDE.md` importing `AGENTS.md`.

## Not verified
- That Claude Code resolves the `@AGENTS.md` import in a fresh session (documented
  behaviour; not tried in a new session here).
- The phase-family summaries are mine, written from the section titles; the archive is
  the source of truth.

## Follow-ups
- Move `web/session.py` out of `web/`, then split `web/app.py` (docs/plan.md).
