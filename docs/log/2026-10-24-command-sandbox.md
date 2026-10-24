# Commands run in Codex's Windows sandbox (shipped, decision proposed)

Why: a background command (cmd) ran as the user and could change any file, because cmd can't
work inside the AppContainer that holds scripts. Codex's Windows sandbox can hold it (#588,
step 4; decision 0022).

## Shipped
- Settings › Capabilities › Run commands has a "Command sandbox" row: Set up downloads Codex if
  needed and runs `codex sandbox setup --elevated --current-user` (an administrator approves it
  once); Remove runs `codex sandbox uninstall`. Both are audited. The sandbox keeps its own
  `CODEX_HOME` under the state dir.
- Where it is set up, `run_background_command` runs `codex sandbox --permission-profile` with a
  profile for the conversation's folders: it writes the read-write ones, reads the read-only
  ones and the disk outside the person's profile, and is online only when the environment's
  network is Full.
- Where it isn't, commands need a second switch, "Run commands without the sandbox", with its own
  confirmation; then each command is approved by the person in every mode, and an unattended
  run refuses it.
- The Codex download on Windows also keeps the two helpers `codex sandbox` starts
  (`codex-resources/codex-command-runner.exe`, `codex-windows-sandbox-setup.exe`), pinned by
  `scripts/pin_codex.py`; an install made before them is completed by the next download.
- Codex is pinned at 0.163.0 (was 0.160.0): `codex sandbox uninstall`, which Remove runs, first
  shipped in 0.162.0. Its new default-on `browser_annotation_api` and `in_app_voice` are turned
  off like the other app features. Locally, `codex app-server` 0.163.0 initialized and started a
  thread with coscribe's config.
- The real-Windows checks set the sandbox up on the runner and run a command in it (writes only
  the workspace, can't read the profile, runs as the sandbox user, is offline) and stop a
  sandboxed process tree; a sandbox removed behind the app's back makes a command fail, not run
  as the person.

## Not verified
- A real code conversation on Codex 0.163.0 (the code module's tests use a fake app-server).
- Setting it up from the app on a standard-user laptop, where Windows shows the administrator
  prompt; the runner is an administrator and asks nobody.
- Stopping a sandboxed command on a real machine beyond the runner check.
