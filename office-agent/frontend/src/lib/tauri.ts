import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

/** True when running inside the Tauri desktop shell's webview, checked
 * via the actual injected IPC bridge (`__TAURI_INTERNALS__`) rather than
 * the `withGlobalTauri`-only convenience object (`window.__TAURI__`,
 * used by office-agent-desktop's own bundled splash page, which has no
 * bundler to import this package through) -- this app does have a
 * bundler, so it goes through the real npm package instead, and this is
 * the check that package's own source uses internally to decide whether
 * `invoke` can do anything at all.
 *
 * Note this only starts returning true for the *real* app's own page
 * (not just the splash page) once office-agent-desktop's
 * capabilities/default.json grants a `remote` capability for its
 * http://127.0.0.1:<port> origin -- see that file's own docstring for
 * why a plain http:// origin gets no IPC bridge by default in Tauri v2,
 * unlike the bundled splash page. */
export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/** Native OS folder picker (Tauri only). Returns the chosen absolute
 * path, or null if the user cancelled. Callers should check isTauri()
 * first and fall back to something else (DirBrowserModal's own in-app
 * browser) rather than call this outside Tauri -- and should still catch
 * a rejection here as a *further* fallback signal, not just skip the
 * check: the capabilities grant this depends on is new and genuinely
 * unverified on real hardware as of this writing (see this project's own
 * ROADMAP.md), so a denied-by-ACL error is a real possibility this
 * function deliberately lets propagate rather than swallowing, for the
 * caller to react to. */
export async function pickFolderNative(): Promise<string | null> {
  const result = await open({ directory: true, multiple: false, title: "Select a folder" });
  return typeof result === "string" ? result : null;
}

/** Physical-pixel screen rect for the Browser panel's native second
 * window (browser_panel_window.rs, Browser panel native-window plan
 * Stage 1+, see ROADMAP.md). Always physical, never logical -- see
 * BrowserPanel.tsx's own rect-computation comment for why. */
export interface PanelRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Opens the native panel window if it doesn't exist yet, or just
 * repositions and shows it if it does -- idempotent either way (mirrors
 * the Rust command's own docstring). No CDP port here (Stage 1 only) --
 * see browser_panel_window.rs's own module docs for why that was
 * deliberately dropped after a real-hardware hang; Stage 2 reintroduces
 * it once real navigation is actually being attached. */
export async function browserPanelOpen(rect: PanelRect): Promise<void> {
  await invoke("browser_panel_open", { rect });
}

export async function browserPanelReposition(rect: PanelRect): Promise<void> {
  await invoke("browser_panel_reposition", { rect });
}

export async function browserPanelClose(): Promise<void> {
  await invoke("browser_panel_close");
}
