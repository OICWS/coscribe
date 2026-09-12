/**
 * Tray icon -- ported from office-agent-desktop's (Tauri)
 * src-tauri/src/lib.rs (the "4. Tray icon" section of `run()`'s
 * `setup()`). The way back in once the window is hidden, and the only
 * remaining path to a real quit.
 */

import { Tray, Menu, nativeImage, app } from "electron";
import { join } from "node:path";

let tray: Tray | undefined;

export function createTray(iconsDir: string, showMain: () => void, quit: () => void): void {
  const icon = nativeImage.createFromPath(join(iconsDir, "icon.png"));
  tray = new Tray(icon.isEmpty() ? nativeImage.createEmpty() : icon);
  tray.setToolTip("coscribe");

  const menu = Menu.buildFromTemplate([
    { label: "Open coscribe", click: () => showMain() },
    { label: "Quit", click: () => quit() },
  ]);
  tray.setContextMenu(menu);

  // Left-click shows the window, same as a second app launch already
  // does via the single-instance handler -- the Rust version restricts
  // this to left-click specifically since right-click already opens the
  // context menu on the platforms where that's the OS default; Electron's
  // own `click` event already only fires for the primary button, right-
  // click is handled separately via `setContextMenu` above, so no extra
  // button check is needed here the way the Rust version's own
  // `MouseButton::Left` match does.
  tray.on("click", () => showMain());

  app.on("before-quit", () => {
    tray?.destroy();
    tray = undefined;
  });
}
