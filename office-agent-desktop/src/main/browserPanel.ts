/**
 * Browser panel, Electron migration Phases 2-3 -- a real, natively-
 * embedded `WebContentsView` child of the main window, replacing the
 * screencast/CDP-relay implementation
 * (`office-agent/src/coscribe/web/browser_panel.py` + `/ws/browser`)
 * that the plain-browser-tab and Tauri paths still use. See
 * office-agent/ROADMAP.md's "Browser panel native-window migration
 * plan" for the full design. Phase 2: real embedding + navigation, no
 * CDP, no screencast, no synthetic input relay -- a `WebContentsView` is
 * a real, natively-interactive surface, so mouse/keyboard/IME all just
 * work with zero code here. Phase 3: element-picking, see the section
 * below.
 *
 * One singleton view for the life of the app, not recreated per open/
 * close -- `closeBrowserPanel` only detaches it from the window
 * (`removeChildView`), it doesn't destroy the underlying `webContents`,
 * so the page's own navigation state/history survives a close+reopen
 * exactly like a real browser tab would (this was an explicit real-
 * hardware checkpoint: "closed, reopened, and left open across at least
 * one full close cycle").
 *
 * Element-picking (Phase 3): the actual hover-highlight/click-capture
 * logic lives in browserPanelContent.ts (the panel's own preload,
 * re-injected on every navigation like a content script) since only
 * that context can draw *inside* the live page's own DOM -- see that
 * file's own module docs for why. This file's role is the plumbing
 * either side of that: forwarding pick-mode on/off down to the content
 * script, and turning a picked element's rect into a real screenshot
 * (`capturePage`, no CDP/DPI math needed -- see this project's own
 * migration plan for why that whole problem class doesn't exist here)
 * once the content script reports one.
 */

import { WebContentsView, ipcMain, type BrowserWindow, type Rectangle } from "electron";
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
export const BROWSER_PANEL_SET_PICK_MODE_CHANNEL = "browser-panel:set-pick-mode";
// Main -> renderer events (webContents.send / ipcRenderer.on), not
// ipcMain.handle -- same shape as mainWindow.ts's own "sidecar-status".
export const BROWSER_PANEL_NAVIGATED_EVENT = "browser-panel:navigated";
export const BROWSER_PANEL_LOAD_ERROR_EVENT = "browser-panel:load-error";
export const BROWSER_PANEL_PICKED_EVENT = "browser-panel:picked";

// Content-script-facing channels (this file <-> browserPanelContent.ts,
// the panel's *own* preload -- not the main window's). Duplicated as
// plain literals in that file too, same "sandboxed preload can't import
// a local file" constraint dialog.ts's own comment explains -- keep both
// copies in sync if these ever change.
const CONTENT_SET_PICK_MODE_CHANNEL = "browser-panel-content:set-pick-mode";
const CONTENT_PICKED_CHANNEL = "browser-panel-content:picked";

export interface PanelRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface PickedElementInfo {
  tag: string;
  text: string;
  rect: Rectangle;
}

let panelView: WebContentsView | undefined;
let attachedWindow: BrowserWindow | undefined;
// Mirrors what was last told to the content script -- re-sent on every
// navigation (did-navigate handler below), since browserPanelContent.ts
// re-executes fresh on each new page load and would otherwise forget
// pick mode was on the moment the user navigates while picking.
let pickModeActive = false;

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
    // The content script this just reloaded has forgotten pick mode was
    // on -- see pickModeActive's own comment above.
    if (pickModeActive) view.webContents.send(CONTENT_SET_PICK_MODE_CHANNEL, true);
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
  // Real-hardware-reported: clicking a target="_blank" link (or anything
  // calling window.open()) on a real site (e.g. a Google search result)
  // spawned a whole separate native OS window showing that page --
  // Electron's own default behavior for any webContents that doesn't
  // override this, and exactly the wrong UX for a *single embedded*
  // panel (no browser chrome, no tabs -- one view). Denying the popup
  // and navigating this same view instead keeps every link the user
  // clicks inside the panel, matching how the old screencast
  // implementation behaved (it had no concept of "new window" at all,
  // being driven by raw CDP navigate/click commands).
  view.webContents.setWindowOpenHandler(({ url }) => {
    void view.webContents.loadURL(url);
    return { action: "deny" };
  });
  ipcMain.on(CONTENT_PICKED_CHANNEL, (event, info: PickedElementInfo) => {
    if (event.sender !== view.webContents) return;
    void handlePicked(view, info);
  });
  panelView = view;
  return view;
}

/** capturePage's rect is in the same CSS-pixel/DIP space
 * getBoundingClientRect() already returns, and the returned NativeImage
 * is already DPI-correct -- no Emulation.setDeviceMetricsOverride /
 * captureBeyondViewport dance to get right here at all, unlike
 * browser_panel.py's own pick_element (see that function's own docstring
 * for the CDP-side DPI/viewport gotchas this sidesteps entirely: there's
 * no separate "emulated viewport" concept for a real native view in the
 * first place). */
async function handlePicked(view: WebContentsView, info: PickedElementInfo): Promise<void> {
  if (!attachedWindow || attachedWindow.isDestroyed()) return;
  const rect: Rectangle = {
    x: Math.round(info.rect.x),
    y: Math.round(info.rect.y),
    width: Math.round(info.rect.width),
    height: Math.round(info.rect.height),
  };
  const image = await view.webContents.capturePage(rect);
  attachedWindow.webContents.send(BROWSER_PANEL_PICKED_EVENT, {
    screenshot: image.toJPEG(90).toString("base64"),
    text: info.text,
    tag: info.tag,
  });
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
  // BrowserPanel.tsx unmounts entirely on close (App.tsx only renders it
  // while its own open flag is true), so its own pickMode React state
  // always starts fresh (false) on the next open -- reset here too, or a
  // panel closed mid-pick would silently keep highlighting on reopen
  // with a "Select" button that no longer looks active.
  pickModeActive = false;
  panelView?.webContents.send(CONTENT_SET_PICK_MODE_CHANNEL, false);
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
  ipcMain.handle(BROWSER_PANEL_SET_PICK_MODE_CHANNEL, (_event, enabled: boolean) => {
    pickModeActive = enabled;
    panelView?.webContents.send(CONTENT_SET_PICK_MODE_CHANNEL, enabled);
  });
}
