# 0006: Changes reach `main` only through pull requests

Status: accepted (2026-10-08).

## Context
One human maintainer and several AI sessions share one GitHub identity. `main`
went red because a session ran a local check that checked nothing CI checks
(`tsc --noEmit -p .` on a solution file) and pushed without waiting for CI, which
blocked another session's PR. Review gates tied to identity cannot work (the
author and the only reviewer are the same account).

## Decision
A ruleset on `main`: pull request required (0 approvals, squash only), status check
`test` required, up-to-date not required, no deletion, no force push, empty bypass
list. `scripts/check.sh` is the one command CI and sessions run. The maintainer
merges; sessions never merge, enable auto-merge or push to `main`. Branches
`<area>/<slug>`, a draft PR early naming the hot files, `git merge origin/main` to
update. A PR touching an invariant path needs an independent review by a separate
session.

## Consequences
- "The maintainer merges" is a written rule, not something GitHub can enforce.
- If `main` is red, only the fix merges; revert first if no fix PR within 15 minutes.
- CI has no `paths:` filter: a required check that never starts would block
  docs-only PRs forever.
- Full text in `office-agent/CLAUDE.md`, "how a change reaches main".
