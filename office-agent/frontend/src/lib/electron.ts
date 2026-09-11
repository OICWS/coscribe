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

declare global {
  interface Window {
    coscribeDesktop: {
      pickFolder(): Promise<string | null>;
    };
  }
}
