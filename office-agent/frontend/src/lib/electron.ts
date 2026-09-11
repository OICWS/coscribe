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

export interface BrowserPanelNavigatedPayload {
  url: string;
  canGoBack: boolean;
  canGoForward: boolean;
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

export function onBrowserPanelNavigated(callback: (payload: BrowserPanelNavigatedPayload) => void): void {
  window.coscribeDesktop.onBrowserPanelNavigated(callback);
}

export function onBrowserPanelLoadError(callback: (errorDescription: string) => void): void {
  window.coscribeDesktop.onBrowserPanelLoadError(callback);
}

declare global {
  interface Window {
    coscribeDesktop: {
      pickFolder(): Promise<string | null>;
      browserPanelOpen(rect: BrowserPanelRect): Promise<void>;
      browserPanelReposition(rect: BrowserPanelRect): Promise<void>;
      browserPanelClose(): Promise<void>;
      browserPanelNavigate(url: string): Promise<void>;
      browserPanelBack(): Promise<void>;
      browserPanelForward(): Promise<void>;
      browserPanelReload(): Promise<void>;
      onBrowserPanelNavigated(callback: (payload: BrowserPanelNavigatedPayload) => void): void;
      onBrowserPanelLoadError(callback: (errorDescription: string) => void): void;
    };
  }
}
