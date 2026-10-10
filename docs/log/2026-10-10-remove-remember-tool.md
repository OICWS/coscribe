# The model can no longer write the global instructions (shipped)

Why: the `remember` tool appended facts to `MEMORY.md`, the file Settings > Global instructions
edits and every conversation reads. The maintainer found text in it that they had not written,
and does not want the model to have that ability: it changed every later conversation, and
auto-approving modes (accept edits, auto) let it through without a prompt they would notice.

## Shipped
- `remember` is gone: the tool (`tools/memory.py` keeps only `load_memory` and
  `format_memory_section`), its line in the coordinator prompt, and its export. The file is now
  written only by the Settings editor.
- The section heading sent with the user's message is "The user's global instructions:" (it was
  "Remembered facts from earlier sessions:").
- Docs and comments that described `remember` say the file is the user's alone (README, ARCHITECTURE,
  decision 0001's example, the setting's description).
- Tests: the coordinator has no `remember` tool and no mention of it in its prompt; the memory
  tests cover loading and the heading only.

## Not verified
- An existing `MEMORY.md` is left as it is, including anything `remember` wrote; the user
  removes what they do not want in Settings.
- A model that was taught `remember` by an older conversation's history may still try to call it;
  the call fails as an unknown tool.

## Follow-ups
- None.
