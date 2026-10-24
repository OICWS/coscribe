# 0022: Commands run in Codex's Windows sandbox

Status: proposed (2026-10-24, issue #588, step 4); accepted once the maintainer's independent
review passes. Changes 0021 for commands: 0021 kept command lines out of the AppContainer and
let them run as the user.

## Context
`run_background_command` runs a cmd line. cmd can't list folders inside the AppContainer, so
0021 left commands running as the user, able to change any file. A probe on a Windows runner
(Codex 0.164.0) showed that `codex sandbox --permission-profile` in elevated mode holds a cmd or
PowerShell line to the folders a profile names: it runs as a local sandbox user that writes only
those folders, can't read the person's profile, reads the rest of the disk, and has no network
unless the profile allows it. It needs `codex sandbox setup --elevated` once, which an
administrator approves.

## Decision
- Settings › Capabilities sets the sandbox up (downloading Codex if needed) and removes it
  (`codex sandbox uninstall`); both are audited. The sandbox has its own `CODEX_HOME`, apart from
  the code module's and the person's.
- Where it is set up, a command runs as `codex sandbox --permission-profile <p> -C <folder> --
  cmd /d /s /c <line>`. Profile `<p>` writes the conversation's read-write folders, reads its
  read-only ones, and has network only when the environment's network is Full; one profile per
  set of folders, named after them.
- Where it isn't (not set up, or not Windows), turning commands on is not enough: the person
  also confirms "Run commands without the sandbox". Then a command runs as the user and every
  one is approved by the person in every mode (pre-approved, Accept Edits, Auto, a scheduled
  run that skips approval); the Auto reviewer is never asked, and an unattended run refuses it.
- Scripts stay in the AppContainer (0021).

## Consequences
- The sandbox user reads whatever the disk's ACLs let a local user read: other drives, the
  Public folders, Program Files. Only the person's profile is closed to it (checked on the
  runner's profile). The UI's "can't read your files elsewhere" means the profile.
- "Ready" means the setup's Codex is still where it was recorded. A Codex upgrade installs to a
  new folder, so the old record stops counting; a sandbox removed outside the app makes the
  command fail rather than run as the person (a real-Windows check).
- The command gets the server's environment variables, as scripts do; coscribe keeps provider
  keys in the Credential Manager, not there.
- A limited network list can't be held by the sandbox, so a command is offline unless the
  network is Full.
- Setting up needs an administrator once per machine; a standard user without one keeps the
  unsandboxed path with its approvals.
- Codex's own commands in the code module are still off; running them in this sandbox is the
  next step (4b).
