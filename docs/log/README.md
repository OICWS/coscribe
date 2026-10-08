# Log of what shipped

One file per piece of work, named `<date>-<slug>.md` (for example
`2026-10-08-secrets-ui.md`). Newest last in the directory listing. This
replaces appending to `office-agent/ROADMAP.md`, which was one long file every
session edited at the end, so entries collided on phase ids and on merge.

Write the entry in the PR that ships the work. Keep it to what a later reader
needs, in this shape:

```markdown
# <What shipped, in a line> (shipped | partly shipped)

Why: <the problem or the request, a few lines>

## Shipped
- <what changed and where, with the numbers that matter>

## Not verified
- <what could not be tested here (a real account, a platform, a vendor flow)>

## Follow-ups
- <open items; also add them to docs/plan.md>
```

Rules: say plainly what was not verified; no history of the conversation that
led to it (that is the commit message); link the decision record if one was
needed. Work before 2026-10-08 is in `office-agent/ROADMAP.md`.

When a decision record is required (see [`../decisions/`](../decisions/)): a
change to one of the invariants in the root `CLAUDE.md`, a new dependency
direction between packages, a new place that stores a secret, or anything that
a future session would otherwise "fix" because it looks wrong.
