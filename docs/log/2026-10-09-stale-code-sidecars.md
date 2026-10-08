# Leftover files of the removed code conversations are cleaned up (shipped)

Why: code conversations kept their Codex thread id in `code-<id>.codex` in the
state folder. Removing them (the fold into chat) left those files behind for
every conversation made while they existed; nothing reads them.

## Shipped
- At server start, `code-*.codex` files in the state folder are removed
  (`code_runtime/service.py: remove_stale_code_sidecars`, called from the
  lifespan). Only files with that name; a directory, `codex/` (the installed
  Codex) and any other `.codex` file are left alone.
- The conversations themselves are untouched; they open as ordinary chats.
- Test: removes two, keeps a chat's files, `notes.codex`, the `codex` directory
  and a directory named `code-dir.codex`; a second run removes none.

## Not verified
- Nothing beyond the unit test: the folder is small and the change only deletes.
