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
- The real-Windows checks set the sandbox up on the runner and run a command in it (writes only
  the workspace, can't read the profile, runs as the sandbox user) and stop a sandboxed process
  tree.

## Not verified
- Setting it up from the app on a standard-user laptop, where Windows shows the administrator
  prompt; the runner is an administrator and asks nobody.
- Stopping a sandboxed command on a real machine beyond the runner check.
