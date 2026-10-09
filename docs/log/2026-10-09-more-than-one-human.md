# Proposed how several people and their AI sessions work together (partly shipped)

Why: a second person with their own GitHub and Claude accounts is joining. 0006
assumed one identity, and `send_message` does not cross Claude accounts.

## Shipped
- Decision record 0007 (status proposed), `.github/CODEOWNERS` for the invariant
  paths (maintainer only for now), a section in `CONTRIBUTING.md`, per-person
  commit authorship, a clearer claim line in the `task` issue template.

## Not verified
- Nothing is enforced yet: the ruleset is unchanged, and code-owner review needs a
  second owner and an admin. Not tried with a second real account.

## Follow-ups
- Maintainer accepts 0007; add the second handle to CODEOWNERS; change the ruleset.
