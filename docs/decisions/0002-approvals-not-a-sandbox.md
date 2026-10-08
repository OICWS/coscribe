# 0002: Approvals and risk tiers, not an OS sandbox

Status: accepted (recorded 2026-10-08). See `ARCHITECTURE.md`, "Approvals instead of a sandbox".

## Context
coscribe runs scripts the model writes (`run_python_script`, `run_node_script`,
Codex commands) on the user's own computer, for people who don't write code.

## Decision
No OS sandbox. Every tool carries a risk tier (`READ`, `WRITE_LOCAL`, `EXEC`,
`EXTERNAL`); the tier decides whether a call is gated; the user picks a
permission mode per conversation (Manual, Accept Edits, Plan, Auto). A folder
guard refuses script writes outside the conversation's folders. Every decision is
audited. No import allow or deny lists (easy to bypass, only look like safety).

## Consequences
- The folder guard catches mistakes; it is not a security boundary. A script can
  read anywhere, use the network, or bypass it. The UI and docs say so; do not
  write a sentence that promises more.
- Anything claiming isolation (secrets, network access per session) must state
  what an approved script could still do.
- Revisit only with a real sandbox design, as a new record.
