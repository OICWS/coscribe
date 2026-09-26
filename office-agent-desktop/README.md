# coscribe-desktop

An Electron shell around `coscribe-web` -- not a second frontend. It
starts the existing server as a supervised local sidecar process on a
free port and opens a window pointed directly at it. See
`src/main/index.ts`'s own module docs for the entry point.

This is the **primary (and only, in this repo) desktop distribution**
as of the Electron migration's Phase 4 cutover -- see
`office-agent/ROADMAP.md`'s "Browser panel: native second-window
architecture" migration plan for the full history of **why this
package exists**: the original Tauri shell it replaced (not part of
this repo -- see below) hit six real-hardware rounds of unresolved,
framework-level bugs trying to embed a live browser panel as a second
Tauri window on Windows. This package replaces that shell entirely,
using Electron's `WebContentsView` (a genuine embedded child view, not
a synced sibling window) instead, and cleared its own real-hardware
checkpoints across all three build phases (shell parity,
`WebContentsView` embedding + navigation, element-picking) before the
cutover.

**Platform: Windows only** -- same standing constraint the original
Tauri shell had. The `mac` block in `package.json`'s `build` config
exists only because electron-builder expects one to be present-but-
valid even when unused; it is not an active target, and Windows is the
only platform this has been (or will be) verified on.

**This repo never carried the Tauri shell.** Real-hardware testing for
this migration continues from this public repo (forked from the
private working tree at this migration's Sixth real-hardware finding,
to work around a private-repo GitHub Actions storage-quota wall --
public repos get unlimited free Actions minutes/storage), which
deliberately left the Tauri shell and its own CI workflow out of the
fork -- it was never needed to test the Electron path this repo
actually exists for. The private `OICWS/project` repo keeps the
retired Tauri shell as a rollback net for one release cycle; this repo
doesn't need one.

## Window chrome

The window has no OS title bar or menu bar (`src/main/windowChrome.ts`):
`titleBarStyle: "hidden"` plus `titleBarOverlay`, so Windows still draws
minimize/maximize/close -- snapping and double-click-to-maximize keep
working -- on the page's own background color, which the page re-sends
when the theme changes. The page draws a 40px title bar across the whole
window: on the left, the sidebar's top row ("☰", which pops up the
application menu -- File/Edit/View/Go/Window/Help, whose shortcuts still
work with the menu bar hidden -- the sidebar toggle, and Back/Forward
through the pages visited); on the right, "⋮" (Settings) beside the OS
buttons; the rest drags the window. Help > Keyboard Shortcuts (Ctrl+/)
opens the page's shortcuts list. The splash page is cleared from history
once the app loads, so Back never returns to it. Only the desktop
shell's preload exposes `platform`, so a browser tab keeps its plain
layout.

Windows computes the drag area in page order -- each no-drag element
subtracts from the drag regions before it -- so the sidebar (whose
buttons sit over the title bar) is rendered last in the page. A test
that clicks through Playwright/CDP skips this hit-testing entirely;
check clicks with real input (xdotool under Xvfb, or Windows itself).

## Compared to the original Tauri shell

- **No Rust toolchain, no `tauri-plugin-*` crates, no `capabilities/`
  ACL files.** Everything native this shell needs is a plain Node
  main-process module (`src/main/*.ts`) plus a `contextBridge` preload
  (`src/preload/index.ts`) -- see the file-by-file mapping in each
  module's own header comment (each names the exact Tauri/Rust file it
  was originally ported from).
- **No `remote` capability grant needed for the sidecar's own origin.**
  Tauri's IPC bridge doesn't extend to a `WebviewUrl::External` origin
  by default, which the Tauri shell's own `capabilities/default.json`
  `remote.urls` grant existed solely to work around. Electron's preload
  re-executes on every navigation of the same window (including the one
  to the sidecar's own `http://127.0.0.1:<port>` origin) -- there's no
  equivalent origin-ACL problem here at all.
- **Bundle size is real, permanent, and roughly double.** The Tauri
  shell's portable package was a few-MB shell exe (reusing the OS's own
  WebView2 runtime) plus the ~146MB PyInstaller-frozen sidecar, ≈150MB
  total. This package adds its own bundled Chromium+Node runtime on top
  of the *same* unchanged sidecar, ≈300-350MB total. This is the
  direct, known cost of a first-party, non-buggy embedding API -- see
  the migration plan for why that trade was made deliberately, not
  discovered late.
- **The Browser panel is a true native embedded view, not a screencast.**
  `src/main/browserPanel.ts`'s `WebContentsView` -- real rendering, real
  input/IME, element-picking via a content-script preload
  (`src/preload/browserPanelContent.ts`) drawing directly in the live
  page's own DOM. `office-agent/src/coscribe/web/browser_panel.py` and
  `/ws/browser` (the CDP-relay/screencast implementation) stay
  completely untouched -- they still serve the plain-browser-tab case.

## Prerequisites

- Node.js (no Rust toolchain needed at all).
- `office-agent`'s own venv, with `pyinstaller` installed on top of its
  normal dependencies (`pip install pyinstaller` inside
  `office-agent/.venv`) -- needed to freeze the sidecar.
- Icons were copied from the original Tauri shell's own `src-tauri/
  icons/` (not part of this repo) -- if that placeholder branding ever
  gets replaced with real artwork, regenerate from whatever the real
  source becomes; there is no separate icon generation step for this
  package.

## Dev loop

```bash
npm install                # once
bash scripts/stage-sidecar.sh   # rebuilds the frontend, then coscribe-web
                                  # itself via PyInstaller, stages the result
                                  # into resources/sidecar/
npm start                  # tsc build + launch electron .
```

`stage-sidecar.sh` must be re-run whenever `office-agent`'s own source
changes (frontend or backend) and you want this shell to pick it up --
`npm start` does **not** rebuild the Python sidecar itself, only the
Electron main process. Re-run it on each target platform separately --
PyInstaller does not cross-compile.

**Always run it after pulling, even if you think nothing frontend-
facing changed** -- a real, costly live bug came from assuming a prior
build's frontend output was still valid because "this session's own
changes didn't touch it," when the checkout that produced that output
had actually been several commits behind at build time. `stage-sidecar.sh`
rebuilding the frontend unconditionally, every run, is what actually
closes that gap -- see the script's own comment.

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
the plain unpacked `win-unpacked/` folder -- an exe + resources folder,
zip and ship, no extraction step ever, instant launch every time. Zip
`release/win-unpacked/` yourself for distribution, or use
`.github/workflows/desktop-build.yml`'s own artifact upload, which
already does this.

**The NSIS installer also runs a one-time warm-up** (`build/
installer.nsh`'s `customInstall` step): it executes the freshly-
installed sidecar exe once with `--version` (exits instantly, no
server start) purely to trigger Windows Defender's real-time scan of
that large, unsigned, freshly-extracted binary *during* the install
step -- where a moment's extra wait is already expected -- instead of
at the user's first real launch, where a live-reported real-hardware
round found it looked exactly like the app was broken (the splash
page's own 180s failure UI firing, sidecar log completely empty the
whole time). Only helps the NSIS build; `dist:portable`'s unpacked
folder has no install step to hook this into. Not yet confirmed on
real hardware -- see `office-agent/ROADMAP.md`.

Both outputs are unsigned (no code-signing secrets configured) --
Windows SmartScreen will show its usual "unrecognized app" warning on
first run. `.github/workflows/desktop-build.yml` runs both build steps
in CI, `workflow_dispatch`-only (manual trigger from the Actions tab),
uploading both as separate artifacts.

## Verification

Nothing in this package is verifiable from a Linux sandbox beyond
`npm run typecheck`/`npm run build` (no Windows, no real GUI). A
headless smoke test (Electron under Xvfb with `--no-sandbox`) can catch
startup-level bugs -- e.g. `child_process.spawn()`'s `stdio` option
needing an already-open file descriptor, not a `fs.WriteStream` whose
own fd opens asynchronously, a real bug this exact technique caught
before it ever reached a real-hardware test. It cannot substitute for
the actual point of each phase in the migration plan: real window/tray/
notification/embedding behavior on Windows, which is where every real
bug found across Phases 1-3 was actually caught (see
`office-agent/ROADMAP.md`'s Phase 8ae/8af/8ag entries).
