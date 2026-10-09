# Decision records

One short file per decision that a future session would otherwise reopen or
"fix" because it looks wrong. At most 40 lines: Context, Decision,
Consequences, Status. Name: `NNNN-slug.md`. A record is added in the PR that
makes the decision (or, for the first ones, writes down a decision that was
already made). A decision that changes gets a new record that supersedes the
old one; the old one stays, marked "Superseded by".

| # | Decision |
|---|---|
| [0001](0001-fixed-cached-tool-list.md) | The agent's tool list is fixed; tools are deferred |
| [0002](0002-approvals-not-a-sandbox.md) | Approvals and risk tiers, not an OS sandbox |
| [0003](0003-secrets-promise.md) | Secrets: the model cannot see a value; nothing more is promised |
| [0004](0004-connectors-sign-in-once-in-the-browser.md) | Connectors sign in once, in the user's own browser |
| [0005](0005-code-is-a-second-runtime.md) | Code is a second runtime, never inside a fixed workflow |
| [0006](0006-changes-reach-main-through-pull-requests.md) | Changes reach `main` only through pull requests |
| [0007](0007-working-with-more-than-one-human.md) | Working with more than one human and their AI sessions (proposed) |
| [0008](0008-conversations-can-message-each-other.md) | Conversations can message each other (proposed) |
