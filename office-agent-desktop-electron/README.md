# coscribe-desktop-electron

An Electron shell around `coscribe-web` -- not a second frontend, same
design as `../office-agent-desktop/` (Tauri). It starts the existing
server as a supervised local sidecar process on a free port and opens a
window pointed directly at it. See `src/main/index.ts`'s own module docs
for the entry point, and `office-agent/ROADMAP.md`'s "Browser panel:
native second-window architecture" migration plan for **why this
package exists**: `../office-agent-desktop/` (Tauri) hit six real-
hardware rounds of unresolved, framework-level bugs trying to embed a
live browser panel as a second Tauri window on Windows -- this package
replaces that shell entirely, using Electron's `WebContentsView` (a
genuine embedded child view, not a synced sibling window) instead.

**Platform: Windows only** -- same standing constraint as the Tauri
shell (`office-agent/ROADMAP.md`). The `mac` block in `package.json`'s
`build` config exists only because electron-builder expects one to be
present-but-valid even when unused; it is not an active target, and
Windows is the only platform this has been (or will be) verified on.

**Status: parallel-built alongside the Tauri shell, not yet the
primary distribution.** Keep shipping `../office-agent-desktop/` until
this package clears its own real-hardware checkpoints (see the
migration plan's phase breakdown) -- do not retire the Tauri shell
before then.

## Compared to the Tauri shell

- **No Rust toolchain, no `tauri-plugin-*` crates, no `capabilities/`
  ACL files.** Everything native this shell needs is a plain Node
  main-process module (`src/main/*.ts`) plus a `contextBridge` preload
  (`src/preload/index.ts`) -- see the file-by-file mapping in each
  module's own header comment (each names the exact Tauri/Rust file it
  ports from).
- **No `remote` capability grant needed for the sidecar's own origin.**
  Tauri's IPC bridge doesn't extend to a `WebviewUrl::External` origin
  by default, which `capabilities/default.json`'s `remote.urls` grant
  exists solely to work around. Electron's preload re-executes on
  every navigation of the same window (including the one to the
  sidecar's own `http://127.0.0.1:<port>` origin) -- there's no
  equivalent origin-ACL problem here at all.
- **Bundle size is real, permanent, and roughly double.** Today's Tauri
  portable package is a few-MB shell exe (reuses the OS's own WebView2
  runtime) plus the ~146MB PyInstaller-frozen sidecar, ≈150MB total.
  This package adds its own bundled Chromium+Node runtime on top of the
  *same* unchanged sidecar, ≈300-350MB total. This is the direct,
  known cost of a first-party, non-buggy embedding API -- see the
  migration plan for why that trade was made deliberately, not
  discovered late.

## Prerequisites

- Node.js (no Rust toolchain needed at all).
- `office-agent`'s own venv, with `pyinstaller` installed on top of its
  normal dependencies (`pip install pyinstaller` inside
  `office-agent/.venv`) -- needed to freeze the sidecar, identical
  requirement to the Tauri shell's own.
- Icons are copied from `../office-agent-desktop/src-tauri/icons/` --
  regenerate both from the same source if that placeholder branding
  ever gets replaced with real artwork; there is no separate icon
  generation step for this package.

## Dev loop

```bash
npm install                # once
bash scripts/stage-sidecar.sh   # builds coscribe-web via PyInstaller,
                                  # stages it into resources/sidecar/
                                  # (a thin wrapper around the Tauri
                                  # shell's own stage-sidecar.sh)
npm start                  # tsc build + launch electron .
```

`stage-sidecar.sh` must be re-run whenever `office-agent`'s own source
changes and you want this shell to pick it up -- `npm start` does
**not** rebuild the Python sidecar itself, only the Electron main
process. Re-run it on each target platform separately, same
non-cross-compiling constraint as the Tauri shell's own script.

## Building distributables

```bash
bash scripts/stage-sidecar.sh
npm run dist:portable   # release/win-unpacked/ (exe + resources, no installer)
npm run dist:nsis        # release/coscribe-Setup-<version>.exe
```

**`dist:portable` deliberately does not use electron-builder's own
`"portable"` target -- real-hardware finding, not a naming
coincidence.** That target is actually an NSIS self-extracting exe,
which unpacks its *entire* payload (Electron's runtime + the sidecar,
~300MB) to a temp directory on **every single launch** -- confirmed
live: a real double-click-to-window-appearing time of about 3 minutes.
`dist:portable` instead runs `electron-builder --win dir`, producing
the plain unpacked `win-unpacked/` folder -- exactly Tauri's own
"portable" model (an exe + resources folder, zip and ship, no
extraction step ever, instant launch every time). Zip
`release/win-unpacked/` yourself for distribution, or use
`.github/workflows/desktop-electron-build.yml`'s own artifact upload,
which already does this.

Both outputs are unsigned (no code-signing secrets configured, same as
the Tauri shell) -- Windows SmartScreen will show its usual
"unrecognized app" warning on first run.
`.github/workflows/desktop-electron-build.yml` runs both build steps in
CI, `workflow_dispatch`-only (manual trigger from the Actions tab),
uploading both as separate artifacts.

## Verification

Nothing in this package is verifiable from a Linux sandbox beyond
`npm run typecheck`/`npm run build` (no Windows, no real GUI). A
headless smoke test (Electron under Xvfb with `--no-sandbox`, no
attached window content beyond confirming the process tree stays
healthy and the sidecar spawns/is reachable) can catch startup-level
bugs -- e.g. `child_process.spawn()`'s `stdio` option needing an
already-open file descriptor, not a `fs.WriteStream` whose own fd opens
asynchronously, a real bug this exact technique caught before it ever
reached a real-hardware test. It cannot substitute for the actual
point of each phase in the migration plan: real window/tray/
notification/embedding behavior on Windows.
