/** Thin dispatcher over the two desktop shells this app can run inside
 * (Tauri, being phased out; Electron, its replacement -- see
 * office-agent/ROADMAP.md's Browser panel native-window migration plan
 * for why) plus the plain-browser-tab case. Exists so components like
 * DirBrowserModal.tsx don't grow their own three-way isTauri()/
 * isElectron()/neither branching -- they call this module's functions
 * once, and this is the only place that needs to know both shells
 * exist. Once the Tauri shell is retired (migration Phase 4), this
 * collapses to a thin electron.ts re-export and tauri.ts is deleted
 * entirely. */

import { isTauri, pickFolderNative as pickFolderNativeTauri } from "./tauri";
import { isElectron, pickFolderNative as pickFolderNativeElectron } from "./electron";

export type DesktopKind = "tauri" | "electron" | "web";

export function desktopKind(): DesktopKind {
  if (isTauri()) return "tauri";
  if (isElectron()) return "electron";
  return "web";
}

export function isDesktop(): boolean {
  return desktopKind() !== "web";
}

/** Native OS folder picker, whichever desktop shell (if any) is active.
 * Callers should still catch a rejection as a fallback signal, not just
 * check isDesktop() first -- see tauri.ts's own pickFolderNative
 * docstring for why a denied-by-ACL error is a real, not just
 * theoretical, case on that shell specifically. */
export async function pickFolderNative(): Promise<string | null> {
  const kind = desktopKind();
  if (kind === "tauri") return pickFolderNativeTauri();
  if (kind === "electron") return pickFolderNativeElectron();
  throw new Error("pickFolderNative() called outside a desktop shell");
}
