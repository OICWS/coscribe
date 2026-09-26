/**
 * Main window: splash-then-redirect startup + close-hides-not-quits --
 * ported from office-agent-desktop's (Tauri) src-tauri/src/lib.rs
 * ("2. Build the window" / "3. Watch for a startup failure" / the
 * close-hides section of `run()`'s `setup()`).
 *
 * One real simplification over the Rust version, not just a port: there
 * the splash *page itself* had to poll the sidecar via `fetch()`,
 * because Rust had no cheap way to push "ready" into the page except a
 * Tauri event, and the failure watcher was a separate background thread
 * emitting a *different* event for the failure case -- two paths, two
 * mechanisms. Here the main process already owns both the readiness
 * poll and `loadURL()`, so it drives the whole thing directly: one poll
 * loop, ending in either a real navigation (success) or a message
 * pushed to the still-showing splash page (failure). The splash page
 * itself only ever needs to *display* a failure, never detect one.
 * The exact failure-detection semantics are preserved though -- this
 * was hard-won territory (a real fresh-install bug where Settings
 * validation failing with no `.env` yet exits the sidecar within
 * milliseconds), not decorative: distinguish "process already exited"
 * from "still starting," 180s timeout.
 */

import { BrowserWindow, app } from "electron";
import { connect } from "node:net";
import { join } from "node:path";
import { splashPagePath } from "./paths";
import { titleBarWindowOptions } from "./windowChrome";
import { sidecarProcess, shouldKeepRunningInBackground } from "./sidecar";

const STARTUP_TIMEOUT_MS = 180_000;
const POLL_INTERVAL_MS = 300;

let mainWindow: BrowserWindow | undefined;

// Set by the tray's "Quit" handler before calling `app.quit()` --
// distinguishes a real quit from an ordinary window-close click. Without
// this, `app.quit()` would be blocked by the `close` handler below
// itself (Electron cancels the whole quit sequence if any window's
// `close` event calls `preventDefault()`), so tray Quit would silently
// do nothing whenever background-on-close is enabled (the default) --
// the same "quit button doesn't quit" failure class this whole
// migration exists to get away from, just a different cause. The Rust/
// Tauri version doesn't need this flag because `app_handle.exit(0)` is
// a wholly separate path from `WindowEvent::CloseRequested` there;
// Electron's `close` event fires for *any* attempt to close the window,
// including one `app.quit()` itself initiates.
let isQuitting = false;

export function markQuitting(): void {
  isQuitting = true;
}

function portIsOpen(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = connect({ host: "127.0.0.1", port, timeout: 1000 });
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => resolve(false));
    socket.once("timeout", () => {
      socket.destroy();
      resolve(false);
    });
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Polls until the sidecar's port opens, the sidecar process exits
 * first, or 180s elapses -- then either navigates the window to the
 * sidecar's real origin (success) or pushes a failure message to the
 * still-showing splash page, same distinction the Rust watcher thread
 * makes.
 *
 * The `loadURL()` call itself is retried on failure, not just the port
 * check before it -- a real bug found from a user report, not a
 * theoretical one: Chromium on Windows can fail a *loopback*
 * (127.0.0.1) navigation with `ERR_INTERNET_DISCONNECTED` when Windows'
 * own Network List Manager briefly reports "no network," a known false
 * positive most likely right as a freshly-started process's first
 * request lands. With no retry, that single transient failure replaced
 * the splash page with Chromium's own native error interstitial
 * permanently -- blank page, one line of native error text, exactly
 * what was reported -- even though the sidecar itself was fine a moment
 * later and nothing was ever going to navigate again. */
async function waitForSidecarThenNavigate(win: BrowserWindow, port: number): Promise<void> {
  const deadline = Date.now() + STARTUP_TIMEOUT_MS;
  for (;;) {
    if (await portIsOpen(port)) {
      try {
        await win.loadURL(`http://127.0.0.1:${port}/`);
        // The splash page stays behind in history otherwise, one Back
        // away from the app.
        win.webContents.navigationHistory.clear();
        return;
      } catch (err) {
        console.error("loadURL failed, will retry:", err);
        // Fall through to the same poll/backoff/deadline handling below
        // rather than leaving whatever Chromium rendered in place.
      }
    }
    const proc = sidecarProcess();
    const exited = proc === undefined || proc.exitCode !== null || proc.signalCode !== null;
    const timedOut = Date.now() >= deadline;
    if (exited || timedOut) {
      const reason = exited ? "exited" : "timeout";
      if (!win.isDestroyed()) {
        win.webContents.send("sidecar-status", reason);
      }
      return;
    }
    await sleep(POLL_INTERVAL_MS);
  }
}

export function createMainWindow(port: number, preloadPath: string): BrowserWindow {
  mainWindow = new BrowserWindow({
    title: "coscribe",
    width: 1280,
    height: 860,
    minWidth: 960,
    minHeight: 620,
    ...titleBarWindowOptions(),
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  void mainWindow.loadFile(splashPagePath());
  void waitForSidecarThenNavigate(mainWindow, port);

  // Close hides instead of quitting by default -- see this file's own
  // header for why: the sidecar's own scheduled-task poller has to keep
  // running after the window closes for "unattended" to mean anything.
  // shouldKeepRunningInBackground() lets the user opt out from the web
  // Settings panel; when they have, this lets the close proceed as a
  // real quit instead (still going through the same before-quit
  // sidecar-kill path, not a separate quit call here). The tray's
  // "Quit" item is always the other way back to a real quit either way.
  mainWindow.on("close", (event) => {
    if (!isQuitting && shouldKeepRunningInBackground()) {
      event.preventDefault();
      mainWindow?.hide();
    }
  });

  return mainWindow;
}

export function showMainWindow(): void {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

export function getMainWindow(): BrowserWindow | undefined {
  return mainWindow;
}

export function preloadPath(): string {
  return join(__dirname, "..", "preload", "index.js");
}

/** electron-builder's `win.icon` config bakes `icon.ico` into the exe
 * itself for the taskbar/titlebar, but the tray still needs a loadable
 * image file at runtime -- `icons/` is included in package.json's
 * `build.files`, so it ships inside app.asar the same way `src/splash/`
 * does, reachable via `app.getAppPath()` in both dev and packaged
 * builds (asar preserves the source directory structure). */
export function iconsDirFromApp(): string {
  return join(app.getAppPath(), "icons");
}
