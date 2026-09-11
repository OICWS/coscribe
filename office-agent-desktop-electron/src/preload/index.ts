/**
 * contextBridge surface exposed to the main window's page (both the
 * splash page and, once it navigates there, the sidecar's own real
 * origin -- this preload re-executes on every navigation of the same
 * window, including that one, so there is no Tauri-style "remote
 * capability grant for a `WebviewUrl::External` origin" problem to
 * solve here at all; see office-agent-desktop-electron/README.md's
 * "Compared to the Tauri shell" section).
 *
 * `contextIsolation: true` (set in mainWindow.ts) means this is the
 * *only* way the page can reach anything in this file -- no direct
 * `require`/Node global leaks into page JS.
 *
 * Real, user-reported bug fixed here: this file must not `import`/
 * `require` any other local project file. mainWindow.ts also sets
 * `sandbox: true` on the window (deliberately, the safer default) --
 * and Electron's own docs are explicit that a *sandboxed* preload's
 * `require` is a polyfill covering only `electron`/`events`/`timers`/
 * `url`, nothing else, not even a one-line local constants file. This
 * used to `import { PICK_FOLDER_CHANNEL } from "../main/dialog"`,
 * which crashed the preload's own module load entirely on startup
 * ("module not found: ../main/dialog", confirmed live via DevTools) --
 * silently breaking not just the folder picker but every single thing
 * in this file, including onSidecarStatus, since
 * contextBridge.exposeInMainWorld below never even ran.
 * PICK_FOLDER_CHANNEL's value is duplicated below instead of shared --
 * see dialog.ts's own copy for the "why one string, twice" note.
 */

import { contextBridge, ipcRenderer } from "electron";

// Keep this in sync with dialog.ts's own PICK_FOLDER_CHANNEL -- cannot be
// a shared import, see this file's own header comment above.
const PICK_FOLDER_CHANNEL = "dialog:pick-folder";

contextBridge.exposeInMainWorld("coscribeDesktop", {
  /** Fires once, only on a real startup failure (sidecar process
   * exited, or a 180s timeout) -- mirrors the splash page's own
   * "sidecar-status" Tauri-event listener, see mainWindow.ts. */
  onSidecarStatus(callback: (reason: "exited" | "timeout") => void): void {
    ipcRenderer.on("sidecar-status", (_event, reason: "exited" | "timeout") => callback(reason));
  },

  /** Native OS folder picker -- mirrors `pickFolderNative()` in the
   * frontend's tauri.ts. Returns the chosen absolute path, or null if
   * the user cancelled. */
  pickFolder(): Promise<string | null> {
    return ipcRenderer.invoke(PICK_FOLDER_CHANNEL);
  },
});
