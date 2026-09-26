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

// Keep these in sync with browserPanel.ts's own exported channel/event
// constants -- same "cannot be a shared import" constraint.
const BROWSER_PANEL_OPEN_CHANNEL = "browser-panel:open";
const BROWSER_PANEL_REPOSITION_CHANNEL = "browser-panel:reposition";
const BROWSER_PANEL_CLOSE_CHANNEL = "browser-panel:close";
const BROWSER_PANEL_NAVIGATE_CHANNEL = "browser-panel:navigate";
const BROWSER_PANEL_BACK_CHANNEL = "browser-panel:back";
const BROWSER_PANEL_FORWARD_CHANNEL = "browser-panel:forward";
const BROWSER_PANEL_RELOAD_CHANNEL = "browser-panel:reload";
const BROWSER_PANEL_SET_PICK_MODE_CHANNEL = "browser-panel:set-pick-mode";
const BROWSER_PANEL_NAVIGATED_EVENT = "browser-panel:navigated";
const BROWSER_PANEL_LOAD_ERROR_EVENT = "browser-panel:load-error";
const BROWSER_PANEL_PICKED_EVENT = "browser-panel:picked";

interface BrowserPanelRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface BrowserPanelNavigatedPayload {
  url: string;
  canGoBack: boolean;
  canGoForward: boolean;
}

interface BrowserPanelPickedPayload {
  screenshot: string;
  text: string;
  tag: string;
}

const SHOW_APP_MENU_CHANNEL = "window:show-app-menu";
const SET_TITLE_BAR_COLORS_CHANNEL = "window:set-title-bar-colors";

contextBridge.exposeInMainWorld("coscribeDesktop", {
  /** The page draws the window's top bar (see main/windowChrome.ts). */
  platform: process.platform,

  /** Opens the application menu at a point in the window, in CSS pixels. */
  showAppMenu(x: number, y: number): void {
    ipcRenderer.send(SHOW_APP_MENU_CHANNEL, { x, y });
  },

  /** Recolors the OS window buttons to match the page's theme, as
   * #rrggbb. No effect on macOS, whose traffic lights keep their colors. */
  setTitleBarColors(color: string, symbolColor: string): void {
    ipcRenderer.send(SET_TITLE_BAR_COLORS_CHANNEL, { color, symbolColor });
  },

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

  /** Browser panel, Electron migration Phase 2 -- see browserPanel.ts's
   * own module docs. Opens/repositions/closes the native WebContentsView
   * and drives its navigation; onNavigated/onLoadError mirror the plain-
   * browser-tab path's own WS "frame"/"error" messages closely enough
   * that BrowserPanel.tsx's electron branch can reuse the same UI states
   * (address bar, back/forward-enabled, error banner). */
  browserPanelOpen(rect: BrowserPanelRect): Promise<void> {
    return ipcRenderer.invoke(BROWSER_PANEL_OPEN_CHANNEL, rect);
  },
  browserPanelReposition(rect: BrowserPanelRect): Promise<void> {
    return ipcRenderer.invoke(BROWSER_PANEL_REPOSITION_CHANNEL, rect);
  },
  browserPanelClose(): Promise<void> {
    return ipcRenderer.invoke(BROWSER_PANEL_CLOSE_CHANNEL);
  },
  browserPanelNavigate(url: string): Promise<void> {
    return ipcRenderer.invoke(BROWSER_PANEL_NAVIGATE_CHANNEL, url);
  },
  browserPanelBack(): Promise<void> {
    return ipcRenderer.invoke(BROWSER_PANEL_BACK_CHANNEL);
  },
  browserPanelForward(): Promise<void> {
    return ipcRenderer.invoke(BROWSER_PANEL_FORWARD_CHANNEL);
  },
  browserPanelReload(): Promise<void> {
    return ipcRenderer.invoke(BROWSER_PANEL_RELOAD_CHANNEL);
  },
  onBrowserPanelNavigated(callback: (payload: BrowserPanelNavigatedPayload) => void): void {
    ipcRenderer.on(BROWSER_PANEL_NAVIGATED_EVENT, (_event, payload: BrowserPanelNavigatedPayload) =>
      callback(payload),
    );
  },
  onBrowserPanelLoadError(callback: (errorDescription: string) => void): void {
    ipcRenderer.on(BROWSER_PANEL_LOAD_ERROR_EVENT, (_event, errorDescription: string) =>
      callback(errorDescription),
    );
  },

  /** Element-picking, Electron migration Phase 3. Toggling this forwards
   * down to the panel's own content script (browserPanel.ts's own
   * set-pick-mode handler), which draws the hover highlight directly in
   * the live page's own DOM -- there is no highlight-rect state to read
   * back here the way the old canvas path's hoverElement was, since this
   * window's React can't draw on top of a natively-composited child view
   * either way. onPicked fires once per commit (a click while picking),
   * already carrying the cropped screenshot -- same shape
   * BrowserCapture/onSendToChat already expect. */
  browserPanelSetPickMode(enabled: boolean): Promise<void> {
    return ipcRenderer.invoke(BROWSER_PANEL_SET_PICK_MODE_CHANNEL, enabled);
  },
  onBrowserPanelPicked(callback: (payload: BrowserPanelPickedPayload) => void): void {
    ipcRenderer.on(BROWSER_PANEL_PICKED_EVENT, (_event, payload: BrowserPanelPickedPayload) =>
      callback(payload),
    );
  },
});
