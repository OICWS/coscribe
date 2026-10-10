# run_background_command: a shell command runs in the Background tasks panel (shipped)

Why: the panel listed sub-agents and scripts, but a plain command line (a build, a download, a
long-running tool) could only run as a script wrapping `subprocess`.

## Shipped
- `run_background_command(command, description, timeout_seconds)` in `tools/background_tasks.py`,
  next to `run_background_script`: same risk category (EXEC, so it always asks, like a script),
  same record, log, `check_background_task`, `wake_on_task` and panel entry (language `shell`,
  labelled "Command"). It runs through the machine's shell in the workspace root with the
  session's connector environment.
- It is stopped as a tree (own process group; `taskkill /T` on Windows), on Stop and on timeout,
  so a command that started children does not leave them running.
- The approval card shows the command and says plainly that nothing limits where it writes:
  unlike a script, no write safeguard applies. The script exec-policy rules do not apply to it,
  so it is never auto-approved by them.
- `tests/test_background_command.py` (no network needed): output and exit code, working
  directory, empty command, per-conversation listing, kill reaching a child, timeout.

## Not verified
- Windows (`cmd`, `taskkill`): not run here.
- Not added to the tool list the model sees up front: it is a deferred tool like the other
  background ones.

## Follow-ups
- None.
