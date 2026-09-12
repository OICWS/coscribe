/** Thin re-export over electron.ts, plus the plain-browser-tab case.
 * Used to also dispatch between Tauri and Electron (the two desktop
 * shells during the migration -- see office-agent/ROADMAP.md's Browser
 * panel native-window migration plan) so components like
 * DirBrowserModal.tsx didn't need their own isTauri()/isElectron()/
 * neither branching; collapsed to just this, per this file's own prior
 * plan, once the Tauri shell was retired at the migration's Phase 4
 * cutover (tauri.ts deleted entirely). Kept as its own module rather
 * than having callers import electron.ts directly -- if another desktop
 * shell is ever added again, this is the one place that would need to
 * know. */

import { isElectron, pickFolderNative as pickFolderNativeElectron } from "./electron";

export type DesktopKind = "electron" | "web";

export function desktopKind(): DesktopKind {
  if (isElectron()) return "electron";
  return "web";
}

export function isDesktop(): boolean {
  return desktopKind() !== "web";
}

/** Native OS folder picker, whichever desktop shell (if any) is active.
 * Callers should still catch a rejection as a fallback signal, not just
 * check isDesktop() first. */
export async function pickFolderNative(): Promise<string | null> {
  const kind = desktopKind();
  if (kind === "electron") return pickFolderNativeElectron();
  throw new Error("pickFolderNative() called outside a desktop shell");
}
