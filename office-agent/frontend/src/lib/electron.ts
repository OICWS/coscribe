/** True when running inside the Electron desktop shell's window, checked
 * via the `coscribeDesktop` object exposed by that shell's own preload
 * script (`office-agent-desktop-electron/src/preload/index.ts`) through
 * `contextBridge` -- present only there, never in a plain browser tab or
 * inside the (parallel, being phased out) Tauri shell. */
export function isElectron(): boolean {
  return typeof window !== "undefined" && "coscribeDesktop" in window;
}

/** Native OS folder picker (Electron only). Returns the chosen absolute
 * path, or null if the user cancelled. See tauri.ts's pickFolderNative
 * for the equivalent Tauri call -- desktop.ts dispatches between the two
 * so callers don't need to branch themselves. */
export async function pickFolderNative(): Promise<string | null> {
  return window.coscribeDesktop.pickFolder();
}

/** Physical/CSS-pixel rect for the Browser panel's native
 * `WebContentsView` (Electron migration Phase 2). Unlike tauri.ts's own
 * `PanelRect` (a top-level OS window's screen position, always physical
 * pixels), `WebContentsView.setBounds()` takes coordinates relative to
 * the *parent window's own content area*, in the same CSS-pixel space
 * `getBoundingClientRect()` already returns -- no devicePixelRatio/
 * innerPosition() math needed at all, see BrowserPanel.tsx's electron
 * branch for the resulting much simpler rect computation. */
export interface BrowserPanelRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export async function browserPanelOpen(rect: BrowserPanelRect): Promise<void> {
  await window.coscribeDesktop.browserPanelOpen(rect);
}

export async function browserPanelReposition(rect: BrowserPanelRect): Promise<void> {
  await window.coscribeDesktop.browserPanelReposition(rect);
}

export async function browserPanelClose(): Promise<void> {
  await window.coscribeDesktop.browserPanelClose();
}

export async function browserPanelNavigate(url: string): Promise<void> {
  await window.coscribeDesktop.browserPanelNavigate(url);
}

export async function browserPanelBack(): Promise<void> {
  await window.coscribeDesktop.browserPanelBack();
}

export async function browserPanelForward(): Promise<void> {
  await window.coscribeDesktop.browserPanelForward();
}

export async function browserPanelReload(): Promise<void> {
  await window.coscribeDesktop.browserPanelReload();
}

export interface BrowserTab {
  id: number;
  title: string;
  url: string;
  favicon: string | null;
  loading: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  loadError: string | null;
}

export interface BrowserTabsPayload {
  tabs: BrowserTab[];
  activeId: number | null;
}

/** From the desktop app while coscribe's AI uses the browser: `open`
 * asks for the panel to be shown, `busy` brackets each step. */
export interface BrowserAgentPayload {
  open?: boolean;
  busy?: boolean;
  threadId?: string;
  action?: string;
}

export async function browserPanelNewTab(): Promise<void> {
  await window.coscribeDesktop.browserPanelNewTab();
}

export async function browserPanelSelectTab(id: number): Promise<void> {
  await window.coscribeDesktop.browserPanelSelectTab(id);
}

export async function browserPanelCloseTab(id: number): Promise<void> {
  await window.coscribeDesktop.browserPanelCloseTab(id);
}

export interface BrowserPageCapture {
  dataUrl: string;
  title: string;
  url: string;
}

/** A screenshot of the active tab, or null for an empty tab. */
export async function browserPanelCapture(): Promise<BrowserPageCapture | null> {
  return (await window.coscribeDesktop.browserPanelCapture?.()) as BrowserPageCapture | null;
}

/** Hides the live page so the panel can show its own drawing layer there. */
export async function browserPanelSetViewHidden(hidden: boolean): Promise<void> {
  await window.coscribeDesktop.browserPanelSetViewHidden?.(hidden);
}

export function browserPanelShowMenu(x: number, y: number): void {
  window.coscribeDesktop.browserPanelShowMenu?.(x, y);
}

export async function browserPanelOpenExternal(): Promise<void> {
  await window.coscribeDesktop.browserPanelOpenExternal();
}

export function onBrowserPanelTabs(callback: (payload: BrowserTabsPayload) => void): () => void {
  return window.coscribeDesktop.onBrowserPanelTabs(callback as (payload: unknown) => void);
}

/** A no-op outside the desktop app, where there's no built-in browser. */
export function onBrowserAgent(callback: (payload: BrowserAgentPayload) => void): () => void {
  if (!isElectron() || !window.coscribeDesktop.onBrowserAgent) return () => {};
  return window.coscribeDesktop.onBrowserAgent(callback as (payload: unknown) => void);
}

/** Element-picking, Electron migration Phase 3. Unlike the non-desktop
 * canvas path's own hoverElement (React state driving a React-rendered
 * overlay), there is no hover-rect data to read back here at all -- the
 * highlight is drawn directly inside the live page's own DOM by
 * browserPanelContent.ts (this window's React can't paint on top of a
 * natively-composited child view either way), so this side only ever
 * needs to turn pick mode on/off and receive the final committed pick. */
export interface BrowserPanelPickedPayload {
  screenshot: string;
  text: string;
  tag: string;
}

export async function browserPanelSetPickMode(enabled: boolean): Promise<void> {
  await window.coscribeDesktop.browserPanelSetPickMode(enabled);
}

export function onBrowserPanelPicked(callback: (payload: BrowserPanelPickedPayload) => void): () => void {
  return window.coscribeDesktop.onBrowserPanelPicked(callback);
}

/** The OS the desktop shell runs on when it draws its own title bar, or
 * null in a browser tab or a shell from before the title bar moved into
 * the page. */
export function titleBarPlatform(): string | null {
  return isElectron() ? (window.coscribeDesktop.platform ?? null) : null;
}

export function showAppMenu(x: number, y: number): void {
  window.coscribeDesktop?.showAppMenu?.(x, y);
}

export function setTitleBarColors(color: string, symbolColor: string): void {
  window.coscribeDesktop?.setTitleBarColors?.(color, symbolColor);
}

export function onShowShortcuts(callback: () => void): void {
  window.coscribeDesktop?.onShowShortcuts?.(callback);
}

declare global {
  interface Window {
    coscribeDesktop: {
      platform?: string;
      showAppMenu?(x: number, y: number): void;
      setTitleBarColors?(color: string, symbolColor: string): void;
      onShowShortcuts?(callback: () => void): void;
      pickFolder(): Promise<string | null>;
      browserPanelOpen(rect: BrowserPanelRect): Promise<void>;
      browserPanelReposition(rect: BrowserPanelRect): Promise<void>;
      browserPanelClose(): Promise<void>;
      browserPanelNavigate(url: string): Promise<void>;
      browserPanelBack(): Promise<void>;
      browserPanelForward(): Promise<void>;
      browserPanelReload(): Promise<void>;
      browserPanelNewTab(): Promise<void>;
      browserPanelSelectTab(id: number): Promise<void>;
      browserPanelCloseTab(id: number): Promise<void>;
      browserPanelOpenExternal(): Promise<void>;
      browserPanelShowMenu?(x: number, y: number): void;
      browserPanelCapture?(): Promise<unknown>;
      browserPanelSetViewHidden?(hidden: boolean): Promise<void>;
      onBrowserPanelTabs(callback: (payload: unknown) => void): () => void;
      onBrowserAgent?(callback: (payload: unknown) => void): () => void;
      browserPanelSetPickMode(enabled: boolean): Promise<void>;
      onBrowserPanelPicked(callback: (payload: BrowserPanelPickedPayload) => void): () => void;
    };
  }
}
