# Plan: what is open now

Short on purpose. What shipped lives in [`docs/log/`](log/) (newest) and
[`office-agent/ROADMAP.md`](../office-agent/ROADMAP.md) (short index of the older history); why things
are the way they are lives in [`docs/decisions/`](decisions/) and
[`ARCHITECTURE.md`](../ARCHITECTURE.md). Update this file in the PR that
changes the picture; keep it under about 150 lines.

Last reviewed: 2026-10-09.

## Next, in order

1. **Make the repository easy to join** (governance review, 2026-10-08).
   - Done: `main` protected by a ruleset (PR only, squash, `test` required),
     `scripts/check.sh` shared by CI and local, root `CLAUDE.md` and
     `AGENTS.md`, templates.
   - Done: `ROADMAP.md` compacted (old one archived in `docs/history/`), plan,
     decisions and log in place, package dependencies checked in CI.
   - Done: `web/session.py` and its helpers moved to `conversation/` (the CLI uses
     it; it no longer imports `cli` or FastAPI).
   - Done: the setup page, request models and provider catalog are out of `web/app.py`;
     every HTTP route is in `web/routes/` (one `router(state)` each).
   - Done: the two WebSocket routes are in `web/routes/ws.py`.
   - Done: `tests/test_web.py` is split into `tests/web/` by domain.
2. **Working with more than one person** (record 0007, proposed): the maintainer
   accepts or changes it; then an admin adds the second collaborator, their handle
   goes into `.github/CODEOWNERS`, and the ruleset requires code-owner review for
   those paths.
3. **Background commands**: the Background tasks panel lists sub-agents and
   scripts; add `run_background_command` so a shell command can run there too
   (touches `coordinator.py` and the approval rules).

## Needs the maintainer's testing (could not be verified here)

- Sign-in of the 13 connectors added for the skill plugins (only the
  registration was tested), and of the 19 added before them.
- The guided setup for HubSpot and Google (Gmail, Calendar, Drive): follows the
  vendors' guides, tested only against a local OAuth server. Google's Workspace
  MCP servers are a Developer Preview; whether HubSpot accepts a `localhost`
  redirect is not documented.
- Secrets against a real Windows Credential Manager, and a real remote connector
  with a header from Settings > Secrets.
- The attachment cards and the new question-card colours in the desktop app; the
  thin scrollbar on pages in the built-in browser.
- The code module on real Windows.

## Open problems, not reproduced or not solved

- Notion showed no tools for the maintainer; cause not found.
- The built-in browser panel opened during a connector's first connection
  (sign-in pages should open in the system browser); not reproduced.
- A Hugging Face connector call once hung a turn; a 120 s limit and an automatic
  reconnect were added, the cause is unknown.
- Slack is not offered: its MCP is said to be limited to Marketplace or internal
  apps (third-party source, not checked against Slack's docs).
- A sent message does not keep its attached files as cards; the file picker takes
  one file at a time.

## Later (wanted, not scheduled)

- Conversations messaging each other (record 0008, proposed; design only).

- Network access per session (an Edit environment setting). Without a sandbox it
  could only switch off coscribe's own web tools; it must say so on its face.
- A callback helper so a script can use a secret (needs its own local
  authentication).
- Splitting `tools/presentations.py` (7k lines) and `ChatSessionLG`, when
  presentation work resumes.
- A terminal UI.
