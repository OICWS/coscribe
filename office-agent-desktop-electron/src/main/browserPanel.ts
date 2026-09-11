/**
 * Browser panel, Electron migration Phase 2 -- a real, natively-embedded
 * `WebContentsView` child of the main window, replacing the screencast/
 * CDP-relay implementation (`office-agent/src/coscribe/web/browser_panel.py`
 * + `/ws/browser`) that the plain-browser-tab and Tauri paths still use.
 * See office-agent/ROADMAP.md's "Browser panel native-window migration
 * plan" for the full design; this file is Phase 2's entire scope: real
 * embedding + navigation, no CDP, no screencast, no synthetic input relay
 * -- a `WebContentsView` is a real, natively-interactive surface, so
 * mouse/keyboard/IME all just work with zero code here. Element-picking
 * (Phase 3) is not implemented yet.
 *
 * One singleton view for the life of the app, not recreated per open/
 * close -- `closeBrowserPanel` only detaches it from the window
 * (`removeChildView`), it doesn't destroy the underlying `webContents`,
 * so the page's own navigation state/history survives a close+reopen
 * exactly like a real browser tab would (this was an explicit real-
 * hardware checkpoint: "closed, reopened, and left open across at least
 * one full close cycle").
 */

import { WebContentsView, ipcMain, type BrowserWindow } from "electron";
import { join } from "node:path";

// preload/index.ts duplicates each of these exact strings rather than
// importing them from here -- see dialog.ts's own comment on why a
// sandboxed preload can only require electron/events/timers/url, never a
// local project file. Keep every copy in sync if these ever change.
export const BROWSER_PANEL_OPEN_CHANNEL = "browser-panel:open";
export const BROWSER_PANEL_REPOSITION_CHANNEL = "browser-panel:reposition";
export const BROWSER_PANEL_CLOSE_CHANNEL = "browser-panel:close";
export const BROWSER_PANEL_NAVIGATE_CHANNEL = "browser-panel:navigate";
export const BROWSER_PANEL_BACK_CHANNEL = "browser-panel:back";
export const BROWSER_PANEL_FORWARD_CHANNEL = "browser-panel:forward";
export const BROWSER_PANEL_RELOAD_CHANNEL = "browser-panel:reload";
// Main -> renderer events (webContents.send / ipcRenderer.on), not
// ipcMain.handle -- same shape as mainWindow.ts's own "sidecar-status".
export const BROWSER_PANEL_NAVIGATED_EVENT = "browser-panel:navigated";
export const BROWSER_PANEL_LOAD_ERROR_EVENT = "browser-panel:load-error";

export interface PanelRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

let panelView: WebContentsView | undefined;
let attachedWindow: BrowserWindow | undefined;

function browserPanelContentPreloadPath(): string {
  return join(__dirname, "..", "preload", "browserPanelContent.js");
}

/** Bare-domain convenience ("baidu.com" -> "https://baidu.com"), same as
 * typing into a real browser's address bar -- ported byte-for-byte from
 * browser_panel.py's own `navigate()` (the plain-browser-tab/Tauri path's
 * equivalent) so both paths behave identically. Only applies when there's
 * no scheme at all, so an already-schemed URL (any "xyz:" prefix --
 * data:, about:, file:, chrome:, not just http(s)) passes through
 * untouched instead of getting "https://" wrongly glued onto its front. */
function normalizeUrl(url: string): string {
  if (!url.includes("://") && !url.split("/")[0].includes(":")) {
    return `https://${url}`;
  }
  return url;
}

function ensurePanelView(): WebContentsView {
  if (panelView) return panelView;
  const view = new WebContentsView({
    webPreferences: {
      preload: browserPanelContentPreloadPath(),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  view.webContents.on("did-navigate", (_event, url) => {
    sendStatus(url);
  });
  view.webContents.on("did-navigate-in-page", (_event, url) => {
    sendStatus(url);
  });
  view.webContents.on("did-fail-load", (_event, errorCode, errorDescription, _url, isMainFrame) => {
    // A sub-frame (an ad iframe, a tracking pixel) failing to load is
    // routine noise on plenty of real sites -- only a main-frame failure
    // is the page-level error this panel's own error banner should show.
    // -3 is Chromium's ERR_ABORTED, fired for e.g. a navigation that gets
    // superseded by another one before it finishes -- not a real failure
    // the user needs to see, same reasoning any browser's own UI applies.
    if (!isMainFrame || errorCode === -3) return;
    attachedWindow?.webContents.send(BROWSER_PANEL_LOAD_ERROR_EVENT, errorDescription);
  });
  panelView = view;
  return view;
}

function sendStatus(url: string): void {
  if (!attachedWindow || attachedWindow.isDestroyed() || !panelView) return;
  attachedWindow.webContents.send(BROWSER_PANEL_NAVIGATED_EVENT, {
    url,
    canGoBack: panelView.webContents.canGoBack(),
    canGoForward: panelView.webContents.canGoForward(),
  });
}

function openBrowserPanel(win: BrowserWindow, rect: PanelRect): void {
  const view = ensurePanelView();
  if (attachedWindow !== win) {
    // A previous window (shouldn't happen in this single-main-window
    // app, but defensive rather than assumed) still has this view
    // attached -- detach before reattaching elsewhere.
    if (attachedWindow && !attachedWindow.isDestroyed()) {
      attachedWindow.contentView.removeChildView(view);
    }
    win.contentView.addChildView(view);
    attachedWindow = win;
  } else if (!win.contentView.children.includes(view)) {
    win.contentView.addChildView(view);
  }
  view.setBounds(rect);
}

function repositionBrowserPanel(rect: PanelRect): void {
  panelView?.setBounds(rect);
}

function closeBrowserPanel(): void {
  if (panelView && attachedWindow && !attachedWindow.isDestroyed()) {
    attachedWindow.contentView.removeChildView(panelView);
  }
}

/** Explicit teardown for app quit -- mirrors sidecar.ts's own
 * killSidecar() being called from index.ts's before-quit handler rather
 * than assumed to happen automatically. WebContentsView's underlying
 * webContents is a real Chromium renderer; closing it explicitly here is
 * the same "don't just hope the parent window's own teardown handles it"
 * caution this whole migration exists because of (see this project's own
 * ROADMAP.md on the Tauri sibling-window exit bug it replaced). */
export function destroyBrowserPanel(): void {
  if (!panelView) return;
  closeBrowserPanel();
  panelView.webContents.close();
  panelView = undefined;
  attachedWindow = undefined;
}

export function registerBrowserPanelHandlers(win: BrowserWindow): void {
  ipcMain.handle(BROWSER_PANEL_OPEN_CHANNEL, (_event, rect: PanelRect) => {
    openBrowserPanel(win, rect);
  });
  ipcMain.handle(BROWSER_PANEL_REPOSITION_CHANNEL, (_event, rect: PanelRect) => {
    repositionBrowserPanel(rect);
  });
  ipcMain.handle(BROWSER_PANEL_CLOSE_CHANNEL, () => {
    closeBrowserPanel();
  });
  ipcMain.handle(BROWSER_PANEL_NAVIGATE_CHANNEL, (_event, url: string) => {
    void ensurePanelView().webContents.loadURL(normalizeUrl(url));
  });
  ipcMain.handle(BROWSER_PANEL_BACK_CHANNEL, () => {
    const wc = panelView?.webContents;
    if (wc?.canGoBack()) wc.goBack();
  });
  ipcMain.handle(BROWSER_PANEL_FORWARD_CHANNEL, () => {
    const wc = panelView?.webContents;
    if (wc?.canGoForward()) wc.goForward();
  });
  ipcMain.handle(BROWSER_PANEL_RELOAD_CHANNEL, () => {
    panelView?.webContents.reload();
  });
}
