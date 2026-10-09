# A conversation with no title yet is named by the start of its first message (shipped)

Why: until the model has named a conversation (or when it couldn't), the name was the whole
first message, up to 200 characters, in the sidebar, the header and the dialogs.

## Shipped
- `shortLabel` (`lib/threadStatus.ts`): the first non-empty line, whitespace collapsed, cut at 40
  characters (a Chinese, Japanese or Korean one counts as two) at a word when one is near the
  end, with "…". `threadLabel` adds the thread id as the last resort.
- Used for the sidebar row, the header, the rename field's starting value, the options menu's
  label and the delete and Edit environment dialogs. Search still matches the whole message.
- Spec: `tests/e2e/labels.spec.ts` (the function, loaded from the dev server).

## Not verified
- Titles the model writes are not shortened here: `docs/plan.md` has the follow-up (server).
