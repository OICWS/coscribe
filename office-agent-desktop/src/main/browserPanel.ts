/**
 * The Browser panel: real browser tabs, each a `WebContentsView` child of
 * the main window, of which only the active tab's view is attached, laid
 * over the panel's content area. The React panel draws the tab strip and
 * address bar and reports where the content area is; this file owns the
 * tabs. Both the user and coscribe's AI (browserAgent.ts) drive the same
 * tabs.
 *
 * Tabs share one persistent session ("persist:coscribe-browser"), apart
 * from the app's own pages, so sign-ins survive restarts and stay out of
 * coscribe's UI.
 *
 * Element picking draws inside the page (browserPanelContent.ts, each
 * tab's preload): the page is a separately composited native surface the
 * React DOM can't paint over. This side forwards pick mode and turns a
 * picked element's rect into a screenshot with `capturePage`, which works
 * in the same CSS-pixel space getBoundingClientRect returns.
 */

import {
  Menu,
  app,
  WebContentsView,
  dialog,
  ipcMain,
  session,
  shell,
  type BrowserWindow,
  type Rectangle,
  type DownloadItem,
  type Session,
  type WebContents,
} from "electron";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { browserSettings, updateBrowserSettings } from "./browserPermissions";

// preload/index.ts repeats these strings: a sandboxed preload can only
// require electron's own modules, never a project file.
export const BROWSER_PANEL_OPEN_CHANNEL = "browser-panel:open";
export const BROWSER_PANEL_REPOSITION_CHANNEL = "browser-panel:reposition";
export const BROWSER_PANEL_CLOSE_CHANNEL = "browser-panel:close";
export const BROWSER_PANEL_NAVIGATE_CHANNEL = "browser-panel:navigate";
export const BROWSER_PANEL_BACK_CHANNEL = "browser-panel:back";
export const BROWSER_PANEL_FORWARD_CHANNEL = "browser-panel:forward";
export const BROWSER_PANEL_RELOAD_CHANNEL = "browser-panel:reload";
export const BROWSER_PANEL_SET_PICK_MODE_CHANNEL = "browser-panel:set-pick-mode";
export const BROWSER_PANEL_NEW_TAB_CHANNEL = "browser-panel:new-tab";
export const BROWSER_PANEL_SELECT_TAB_CHANNEL = "browser-panel:select-tab";
export const BROWSER_PANEL_CLOSE_TAB_CHANNEL = "browser-panel:close-tab";
export const BROWSER_PANEL_OPEN_EXTERNAL_CHANNEL = "browser-panel:open-external";
export const BROWSER_PANEL_SHOW_MENU_CHANNEL = "browser-panel:show-menu";
export const BROWSER_PANEL_CAPTURE_CHANNEL = "browser-panel:capture";
export const BROWSER_PANEL_SET_VIEW_HIDDEN_CHANNEL = "browser-panel:set-view-hidden";
export const BROWSER_PANEL_TABS_EVENT = "browser-panel:tabs";
export const BROWSER_PANEL_PICKED_EVENT = "browser-panel:picked";
// Asks the panel to open (the AI started, or a link is opening in it) and
// brackets each of the AI's steps -- see browserAgent.ts.
export const BROWSER_AGENT_EVENT = "browser-panel:agent";
export const BROWSER_PANEL_SHOW_ALLOWED_SITES_EVENT = "browser-panel:show-allowed-sites";

// Between this file and browserPanelContent.ts, which repeats them.
const CONTENT_SET_PICK_MODE_CHANNEL = "browser-panel-content:set-pick-mode";
const CONTENT_PICKED_CHANNEL = "browser-panel-content:picked";
const CONTENT_WHEEL_ZOOM_CHANNEL = "browser-panel-content:wheel-zoom";

const PARTITION = "persist:coscribe-browser";
// As in Claude's browser: past this the tab strip can't show a usable tab.
export const MAX_TABS = 9;
const MIN_ZOOM_FACTOR = 0.25;
const MAX_ZOOM_FACTOR = 5;
const ZOOM_STEP_FACTOR = 1.1;

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

export interface Tab {
  id: number;
  view: WebContentsView;
  // A tab that hasn't loaded anything shows the panel's own "new tab"
  // page, so its (blank white) view stays detached.
  blank: boolean;
  loadError: string | null;
}

export interface TabInfo {
  id: number;
  title: string;
  url: string;
  favicon: string | null;
  loading: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  loadError: string | null;
}

const tabs: Tab[] = [];
const favicons = new Map<number, string>();
let activeId: number | null = null;
let nextId = 1;
let win: BrowserWindow | undefined;
let panelRect: PanelRect | null = null;
let pickModeActive = false;
// While the user annotates, the panel shows a still screenshot and its
// own drawing layer, which the live view would otherwise cover.
let viewHidden = false;
const openWaiters: Array<() => void> = [];
let browserSession: Session | undefined;

function clampZoomFactor(factor: number): number {
  return Math.min(MAX_ZOOM_FACTOR, Math.max(MIN_ZOOM_FACTOR, factor));
}

/** What the address bar (or the AI) typed, as a URL: a full URL or a
 * special scheme stays as is; "baidu.com" gets https://; a local address
 * like "localhost:3000" gets http://; anything that isn't an address is
 * searched for. */
export function normalizeUrl(input: string): string {
  const text = input.trim();
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(text) || /^(about|data|file|mailto|view-source):/i.test(text)) return text;
  const host = text.split(/[/?#]/)[0];
  if (/^(localhost|127(\.\d+){3}|\[::1\])(:\d+)?$/i.test(host)) return `http://${text}`;
  const looksLikeHost = /^[^\s:]+\.[^\s:]+(:\d+)?$/.test(host) || /^[^\s:]+:\d+$/.test(host);
  if (looksLikeHost && !/\s/.test(text)) return `https://${text}`;
  return `https://www.bing.com/search?q=${encodeURIComponent(text)}`;
}

let downloadHandler: ((item: DownloadItem, wc: WebContents) => void) | undefined;

/** Called for every download a tab starts, before Electron asks where to
 * save it; setting a save path there skips the question. */
export function onTabDownload(handler: (item: DownloadItem, wc: WebContents) => void): void {
  downloadHandler = handler;
}

function tabSession(): Session {
  if (browserSession) return browserSession;
  browserSession = session.fromPartition(PARTITION);
  browserSession.on("will-download", (_event, item, wc) => downloadHandler?.(item, wc));
  // Electron grants every permission by default; a site shouldn't get the
  // camera, microphone or location just by asking.
  const allowed = new Set(["clipboard-sanitized-write", "fullscreen", "pointerLock"]);
  browserSession.setPermissionRequestHandler((_wc, permission, callback) => callback(allowed.has(permission)));
  browserSession.setPermissionCheckHandler((_wc, permission) => allowed.has(permission));
  return browserSession;
}

export function tabInfo(tab: Tab): TabInfo {
  const wc = tab.view.webContents;
  const url = tab.blank ? "" : wc.getURL();
  return {
    id: tab.id,
    title: tab.blank ? "New tab" : wc.getTitle() || url,
    url,
    favicon: favicons.get(tab.id) ?? null,
    loading: wc.isLoading(),
    canGoBack: wc.navigationHistory.canGoBack(),
    canGoForward: wc.navigationHistory.canGoForward(),
    loadError: tab.loadError,
  };
}

export function sendTabs(): void {
  if (!win || win.isDestroyed()) return;
  win.webContents.send(BROWSER_PANEL_TABS_EVENT, { tabs: tabs.map(tabInfo), activeId });
}

function tabFor(sender: WebContents): Tab | undefined {
  return tabs.find((t) => t.view.webContents === sender);
}

export function activeTab(): Tab | undefined {
  return tabs.find((t) => t.id === activeId);
}

export function allTabs(): readonly Tab[] {
  return tabs;
}

export function isPanelOpen(): boolean {
  return panelRect !== null;
}

/** Resolves once the React panel has opened and reported its bounds, or
 * after `timeoutMs`. */
export function waitForPanelOpen(timeoutMs: number): Promise<boolean> {
  if (panelRect) return Promise.resolve(true);
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      const index = openWaiters.indexOf(done);
      if (index >= 0) openWaiters.splice(index, 1);
      resolve(false);
    }, timeoutMs);
    const done = () => {
      clearTimeout(timer);
      resolve(true);
    };
    openWaiters.push(done);
  });
}

function setBoundsReliably(view: WebContentsView, rect: PanelRect): void {
  // electron/electron#39993: one setBounds can leave the view painting at
  // its old size, stretched and blurry; the second call forces the layout.
  view.setBounds(rect);
  view.setBounds(rect);
}

export function setViewHidden(hidden: boolean): void {
  if (viewHidden === hidden) return;
  viewHidden = hidden;
  layout();
}

/** Attaches the active tab's view over the panel (if it has a page to
 * show) and detaches every other tab's. */
function layout(): void {
  if (!win || win.isDestroyed()) return;
  const children = win.contentView.children;
  for (const tab of tabs) {
    const show = panelRect !== null && tab.id === activeId && !tab.blank && !viewHidden;
    const attached = children.includes(tab.view);
    if (show) {
      if (!attached) win.contentView.addChildView(tab.view);
      setBoundsReliably(tab.view, panelRect!);
    } else if (attached) {
      win.contentView.removeChildView(tab.view);
    }
  }
}

export function tabLimitReached(): boolean {
  return tabs.length >= MAX_TABS;
}

export function createTab(url?: string): Tab {
  const view = new WebContentsView({
    webPreferences: {
      preload: join(__dirname, "..", "preload", "browserPanelContent.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      session: tabSession(),
      // The AI keeps working while the window is in the tray or behind
      // another app; a throttled page would stall its steps.
      backgroundThrottling: false,
    },
  });
  // Matches the rounded page area the panel draws (rounded-lg).
  view.setBorderRadius(8);
  const tab: Tab = { id: nextId++, view, blank: true, loadError: null };
  const wc = view.webContents;
  wc.on("did-start-navigation", (details) => {
    if (details.isMainFrame && !details.isSameDocument && tab.blank && details.url !== "about:blank") {
      tab.blank = false;
      layout();
    }
  });
  wc.on("did-navigate", () => {
    tab.loadError = null;
    if (pickModeActive && tab.id === activeId) wc.send(CONTENT_SET_PICK_MODE_CHANNEL, true);
    sendTabs();
  });
  wc.on("did-navigate-in-page", () => sendTabs());
  wc.on("page-title-updated", () => sendTabs());
  wc.on("page-favicon-updated", (_event, urls) => {
    if (urls[0]) favicons.set(tab.id, urls[0]);
    sendTabs();
  });
  wc.on("did-start-loading", () => sendTabs());
  wc.on("did-stop-loading", () => sendTabs());
  wc.on("did-fail-load", (_event, errorCode, errorDescription, _url, isMainFrame) => {
    // Sub-frame failures (ads, trackers) are routine; -3 is ERR_ABORTED,
    // a navigation superseded by another.
    if (!isMainFrame || errorCode === -3) return;
    tab.loadError = errorDescription;
    sendTabs();
  });
  // target="_blank" and window.open open a tab in the panel rather than a
  // separate native window without any browser chrome.
  wc.setWindowOpenHandler(({ url: target }) => {
    // With every tab in use, the link replaces this page instead.
    if (tabLimitReached()) void wc.loadURL(normalizeUrl(target)).catch(() => undefined);
    else selectTab(createTab(target).id);
    return { action: "deny" };
  });
  wc.on("before-input-event", (_event, input) => {
    if (input.type !== "keyDown" || (!input.control && !input.meta)) return;
    if (input.key === "+" || input.key === "=") wc.setZoomFactor(clampZoomFactor(wc.getZoomFactor() * ZOOM_STEP_FACTOR));
    else if (input.key === "-") wc.setZoomFactor(clampZoomFactor(wc.getZoomFactor() / ZOOM_STEP_FACTOR));
    else if (input.key === "0") wc.setZoomFactor(1);
  });
  tabs.push(tab);
  if (activeId === null) activeId = tab.id;
  // about:blank rather than nothing: a webContents that has never loaded
  // a document doesn't answer DevTools, which stalls tools attaching to
  // the app (and the page reads as "" either way).
  void wc.loadURL(url ? normalizeUrl(url) : "about:blank").catch(() => undefined);
  sendTabs();
  return tab;
}

export function selectTab(id: number): Tab | undefined {
  const tab = tabs.find((t) => t.id === id);
  if (!tab) return undefined;
  if (activeId !== null && activeId !== id) activeTab()?.view.webContents.send(CONTENT_SET_PICK_MODE_CHANNEL, false);
  activeId = id;
  if (pickModeActive) tab.view.webContents.send(CONTENT_SET_PICK_MODE_CHANNEL, true);
  layout();
  sendTabs();
  return tab;
}

export function closeTab(id: number): void {
  const index = tabs.findIndex((t) => t.id === id);
  if (index < 0) return;
  const [tab] = tabs.splice(index, 1);
  if (win && !win.isDestroyed() && win.contentView.children.includes(tab.view)) {
    win.contentView.removeChildView(tab.view);
  }
  tab.view.webContents.close();
  favicons.delete(id);
  if (activeId === id) activeId = tabs[Math.min(index, tabs.length - 1)]?.id ?? null;
  // The panel always has a tab to show, even after the last one closes.
  if (!tabs.length) createTab();
  layout();
  sendTabs();
}

/** The active tab, making one if there are none. */
export function ensureActiveTab(): Tab {
  return activeTab() ?? selectTab(createTab().id)!;
}

async function handlePicked(tab: Tab, info: PickedElementInfo): Promise<void> {
  if (!win || win.isDestroyed()) return;
  const rect: Rectangle = {
    x: Math.round(info.rect.x),
    y: Math.round(info.rect.y),
    width: Math.round(info.rect.width),
    height: Math.round(info.rect.height),
  };
  const image = await tab.view.webContents.capturePage(rect);
  win.webContents.send(BROWSER_PANEL_PICKED_EVENT, {
    screenshot: image.toJPEG(90).toString("base64"),
    text: info.text,
    tag: info.tag,
  });
}

function openPanel(window: BrowserWindow, rect: PanelRect): void {
  win = window;
  panelRect = rect;
  ensureActiveTab();
  layout();
  sendTabs();
  for (const done of openWaiters.splice(0)) done();
}

function closePanel(): void {
  panelRect = null;
  viewHidden = false;
  layout();
  // Reopening starts with Select off, matching the panel's fresh state.
  pickModeActive = false;
  for (const tab of tabs) tab.view.webContents.send(CONTENT_SET_PICK_MODE_CHANNEL, false);
}

/** App quit: closes every tab's renderer explicitly rather than trusting
 * the window's teardown to. */
export function destroyBrowserPanel(): void {
  closePanel();
  for (const tab of tabs.splice(0)) tab.view.webContents.close();
  activeId = null;
  win = undefined;
}

export function registerBrowserPanelHandlers(window: BrowserWindow): void {
  win = window;
  // A reload of the app page starts it with the panel closed; the tab
  // view, a native layer, would otherwise stay painted over whatever the
  // page shows next.
  window.webContents.on("did-start-navigation", (details) => {
    if (details.isMainFrame && !details.isSameDocument && panelRect) closePanel();
  });
  ipcMain.on(CONTENT_PICKED_CHANNEL, (event, info: PickedElementInfo) => {
    const tab = tabFor(event.sender);
    if (tab) void handlePicked(tab, info);
  });
  // A page swallows its own ctrl+wheel, so the content script reports it.
  ipcMain.on(CONTENT_WHEEL_ZOOM_CHANNEL, (event, deltaY: number) => {
    const wc = tabFor(event.sender)?.view.webContents;
    if (wc) wc.setZoomFactor(clampZoomFactor(wc.getZoomFactor() * (1 - deltaY * 0.001)));
  });
  ipcMain.handle(BROWSER_PANEL_OPEN_CHANNEL, (_event, rect: PanelRect) => openPanel(window, rect));
  ipcMain.handle(BROWSER_PANEL_REPOSITION_CHANNEL, (_event, rect: PanelRect) => {
    if (!panelRect) return;
    panelRect = rect;
    layout();
  });
  ipcMain.handle(BROWSER_PANEL_CLOSE_CHANNEL, () => closePanel());
  ipcMain.handle(BROWSER_PANEL_NAVIGATE_CHANNEL, (_event, url: string) => {
    void ensureActiveTab().view.webContents.loadURL(normalizeUrl(url)).catch(() => undefined);
  });
  ipcMain.handle(BROWSER_PANEL_BACK_CHANNEL, () => {
    const history = activeTab()?.view.webContents.navigationHistory;
    if (history?.canGoBack()) history.goBack();
  });
  ipcMain.handle(BROWSER_PANEL_FORWARD_CHANNEL, () => {
    const history = activeTab()?.view.webContents.navigationHistory;
    if (history?.canGoForward()) history.goForward();
  });
  ipcMain.handle(BROWSER_PANEL_RELOAD_CHANNEL, () => activeTab()?.view.webContents.reload());
  ipcMain.handle(BROWSER_PANEL_SET_PICK_MODE_CHANNEL, (_event, enabled: boolean) => {
    pickModeActive = enabled;
    activeTab()?.view.webContents.send(CONTENT_SET_PICK_MODE_CHANNEL, enabled);
  });
  ipcMain.handle(BROWSER_PANEL_NEW_TAB_CHANNEL, () => {
    if (!tabLimitReached()) selectTab(createTab().id);
  });
  ipcMain.handle(BROWSER_PANEL_SELECT_TAB_CHANNEL, (_event, id: number) => {
    selectTab(Number(id));
  });
  ipcMain.handle(BROWSER_PANEL_CLOSE_TAB_CHANNEL, (_event, id: number) => closeTab(Number(id)));
  ipcMain.handle(BROWSER_PANEL_OPEN_EXTERNAL_CHANNEL, () => openActiveExternally());
  ipcMain.handle(BROWSER_PANEL_CAPTURE_CHANNEL, async () => {
    const tab = activeTab();
    if (!tab || tab.blank) return null;
    const image = await tab.view.webContents.capturePage();
    const info = tabInfo(tab);
    return { dataUrl: image.toDataURL(), title: info.title, url: info.url };
  });
  ipcMain.handle(BROWSER_PANEL_SET_VIEW_HIDDEN_CHANNEL, (_event, hidden: boolean) => setViewHidden(Boolean(hidden)));
  ipcMain.on(BROWSER_PANEL_SHOW_MENU_CHANNEL, (_event, position: { x: number; y: number }) => {
    const tab = activeTab();
    Menu.buildFromTemplate([
      { label: "Save screenshot…", enabled: Boolean(tab && !tab.blank), click: () => void saveScreenshot(window) },
      {
        label: "Close other tabs",
        enabled: tabs.length > 1,
        click: () => {
          for (const other of [...tabs]) if (other.id !== activeId) closeTab(other.id);
        },
      },
      { type: "separator" },
      {
        label: "Open links in built-in browser",
        type: "checkbox",
        checked: browserSettings().openLinksInBuiltIn,
        click: (item) => void updateBrowserSettings({ openLinksInBuiltIn: item.checked }),
      },
      {
        label: "Manage allowed sites…",
        click: () => window.webContents.send(BROWSER_PANEL_SHOW_ALLOWED_SITES_EVENT),
      },
      { type: "separator" },
      { label: "Clear browsing data…", click: () => void clearBrowsingData(window) },
    ]).popup({ window, x: Math.round(Number(position?.x) || 0), y: Math.round(Number(position?.y) || 0) });
  });
}

async function saveScreenshot(window: BrowserWindow): Promise<void> {
  const tab = activeTab();
  if (!tab || tab.blank) return;
  const image = await tab.view.webContents.capturePage();
  const name = (tab.view.webContents.getTitle() || "screenshot").replace(/[\\/:*?"<>|]+/g, " ").trim().slice(0, 80);
  const { canceled, filePath } = await dialog.showSaveDialog(window, {
    defaultPath: join(app.getPath("downloads"), `${name || "screenshot"}.png`),
    filters: [{ name: "PNG image", extensions: ["png"] }],
  });
  if (!canceled && filePath) await writeFile(filePath, image.toPNG());
}

/** A link clicked in the app's own pages (a chat message, say): into a
 * tab of the Browser panel, or the user's own browser if they've turned
 * that off. The app window itself never navigates away. */
export function openLinkFromApp(url: string): void {
  if (!/^https?:\/\//i.test(url)) {
    if (/^mailto:/i.test(url)) void shell.openExternal(url);
    return;
  }
  if (!browserSettings().openLinksInBuiltIn || !win || win.isDestroyed()) {
    void shell.openExternal(url);
    return;
  }
  if (tabLimitReached()) void ensureActiveTab().view.webContents.loadURL(url).catch(() => undefined);
  else selectTab(createTab(url).id);
  win.webContents.send(BROWSER_AGENT_EVENT, { open: true });
}

function openActiveExternally(): void {
  const tab = activeTab();
  const url = tab && !tab.blank ? tab.view.webContents.getURL() : "";
  if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
}

async function clearBrowsingData(window: BrowserWindow): Promise<void> {
  const { response } = await dialog.showMessageBox(window, {
    type: "question",
    buttons: ["Clear", "Cancel"],
    defaultId: 1,
    cancelId: 1,
    message: "Clear coscribe's browsing data?",
    detail: "This signs you out of every site in coscribe's browser by clearing its cookies and site data. Your own browser isn't affected.",
  });
  if (response !== 0) return;
  await tabSession().clearStorageData();
  for (const tab of tabs) if (!tab.blank) tab.view.webContents.reload();
}
