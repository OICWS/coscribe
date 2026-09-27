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
const BROWSER_PANEL_NEW_TAB_CHANNEL = "browser-panel:new-tab";
const BROWSER_PANEL_SELECT_TAB_CHANNEL = "browser-panel:select-tab";
const BROWSER_PANEL_CLOSE_TAB_CHANNEL = "browser-panel:close-tab";
const BROWSER_PANEL_OPEN_EXTERNAL_CHANNEL = "browser-panel:open-external";
const BROWSER_PANEL_TABS_EVENT = "browser-panel:tabs";
const BROWSER_PANEL_SHOW_MENU_CHANNEL = "browser-panel:show-menu";
const BROWSER_PANEL_CAPTURE_CHANNEL = "browser-panel:capture";
const BROWSER_PANEL_SET_VIEW_HIDDEN_CHANNEL = "browser-panel:set-view-hidden";
const BROWSER_PANEL_PICKED_EVENT = "browser-panel:picked";
// browserAgent.ts's BROWSER_AGENT_EVENT.
const BROWSER_PANEL_AGENT_EVENT = "browser-panel:agent";

/** Subscribes, and returns the unsubscribe a React effect's cleanup
 * needs; without it every remount would add another listener. */
function subscribe<T>(channel: string, callback: (payload: T) => void): () => void {
  const listener = (_event: unknown, payload: T) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => {
    ipcRenderer.removeListener(channel, listener);
  };
}

interface BrowserPanelRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface BrowserPanelPickedPayload {
  screenshot: string;
  text: string;
  tag: string;
}

const SHOW_APP_MENU_CHANNEL = "window:show-app-menu";
const SET_TITLE_BAR_COLORS_CHANNEL = "window:set-title-bar-colors";
const SHOW_SHORTCUTS_CHANNEL = "app:show-shortcuts";

contextBridge.exposeInMainWorld("coscribeDesktop", {
  /** The page draws the window's top bar (see main/windowChrome.ts). */
  platform: process.platform,

  /** Opens the application menu at a point in the window, in CSS pixels. */
  showAppMenu(x: number, y: number): void {
    ipcRenderer.send(SHOW_APP_MENU_CHANNEL, { x, y });
  },

  /** Recolors the OS window buttons to match the page's theme, as #rrggbb. */
  setTitleBarColors(color: string, symbolColor: string): void {
    ipcRenderer.send(SET_TITLE_BAR_COLORS_CHANNEL, { color, symbolColor });
  },

  /** Help > Keyboard Shortcuts in the application menu. */
  onShowShortcuts(callback: () => void): void {
    ipcRenderer.on(SHOW_SHORTCUTS_CHANNEL, () => callback());
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

  /** The Browser panel's tabs -- see browserPanel.ts. */
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
  browserPanelNewTab(): Promise<void> {
    return ipcRenderer.invoke(BROWSER_PANEL_NEW_TAB_CHANNEL);
  },
  browserPanelSelectTab(id: number): Promise<void> {
    return ipcRenderer.invoke(BROWSER_PANEL_SELECT_TAB_CHANNEL, id);
  },
  browserPanelCloseTab(id: number): Promise<void> {
    return ipcRenderer.invoke(BROWSER_PANEL_CLOSE_TAB_CHANNEL, id);
  },
  browserPanelOpenExternal(): Promise<void> {
    return ipcRenderer.invoke(BROWSER_PANEL_OPEN_EXTERNAL_CHANNEL);
  },
  onBrowserPanelTabs(callback: (payload: unknown) => void): () => void {
    return subscribe(BROWSER_PANEL_TABS_EVENT, callback);
  },
  browserPanelCapture(): Promise<unknown> {
    return ipcRenderer.invoke(BROWSER_PANEL_CAPTURE_CHANNEL);
  },
  browserPanelSetViewHidden(hidden: boolean): Promise<void> {
    return ipcRenderer.invoke(BROWSER_PANEL_SET_VIEW_HIDDEN_CHANNEL, hidden);
  },
  browserPanelShowMenu(x: number, y: number): void {
    ipcRenderer.send(BROWSER_PANEL_SHOW_MENU_CHANNEL, { x, y });
  },
  onBrowserAgent(callback: (payload: unknown) => void): () => void {
    return subscribe(BROWSER_PANEL_AGENT_EVENT, callback);
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
  onBrowserPanelPicked(callback: (payload: BrowserPanelPickedPayload) => void): () => void {
    return subscribe(BROWSER_PANEL_PICKED_EVENT, callback);
  },
});
