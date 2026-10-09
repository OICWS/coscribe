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
   - Next: move `web/session.py` out of `web/` (it is the conversation engine and
     the CLI uses it), then split `web/app.py` into routers by domain, then
     `tests/test_web.py`.
     `app.py` and `session.py` are hot files: announce a freeze in the PR before
     moving them.
2. **Background commands**: the Background tasks panel lists sub-agents and
   scripts; add `run_background_command` so a shell command can run there too
   (touches `coordinator.py` and the approval rules).
3. **Connector add form: pick a secret** (frontend). The backend takes
   `{{secret:NAME}}` in a connector's header or env value; the form should offer
   the secrets from Settings > Secrets and say a header's secret must be allowed
   for that connector's host. Also show the `connectors` list of the 409 when a
   secret in use is deleted, and drop "Scripts and the code module don't receive
   these yet" from `EnvironmentDialog.tsx`.

## Needs the maintainer's testing (could not be verified here)

- Sign-in of the 13 connectors added for the skill plugins (only the
  registration was tested), and of the 19 added before them.
- The guided setup for HubSpot and Google (Gmail, Calendar, Drive): follows the
  vendors' guides, tested only against a local OAuth server. Google's Workspace
  MCP servers are a Developer Preview; whether HubSpot accepts a `localhost`
  redirect is not documented.
- Secrets against a real Windows Credential Manager.
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

- Network access per session (an Edit environment setting). Without a sandbox it
  could only switch off coscribe's own web tools; it must say so on its face.
- A callback helper so a script can use a secret (needs its own local
  authentication).
- Splitting `tools/presentations.py` (7k lines) and `ChatSessionLG`, when
  presentation work resumes.
- A terminal UI.
