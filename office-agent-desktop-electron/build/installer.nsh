; Custom NSIS install step -- electron-builder invokes `customInstall`
; (if defined) right after installApplicationFiles and before the app is
; ever launched for the user (see this package's own node_modules/
; app-builder-lib/templates/nsis/installSection.nsh for the exact hook
; point). Wired in via package.json's build.nsis.include.
;
; Real, live-reported problem this addresses: every freshly-installed/
; -updated build's very first real launch was slow enough to look hung
; (the splash page's own 180s failure UI fired, the sidecar's log file
; completely empty the whole time) -- root-caused to Windows Defender's
; real-time scan of a large, unsigned, freshly-extracted exe on its very
; first execution (see office-agent/ROADMAP.md's Phase 8af entry). A
; second launch of the *same, already-scanned* binary was always fast --
; Defender caches a clean verdict per file (hash + mtime), it doesn't
; re-scan an unchanged file it already cleared.
;
; This runs the sidecar exe once, right here, purely to trigger (and let
; Windows cache) that same scan during the install step -- where a
; moment's extra wait is already expected and shown as installer
; progress -- instead of at the user's first real launch, where it looks
; exactly like the app is broken. --version (web/app.py's main()) exits
; immediately with no side effects: no port bind, no config load, no
; server start.
!macro customInstall
  ExecWait '"$INSTDIR\resources\sidecar\coscribe-server.exe" --version'
!macroend
