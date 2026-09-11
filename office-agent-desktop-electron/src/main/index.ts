/**
 * App entry/lifecycle -- ported from office-agent-desktop's (Tauri)
 * src-tauri/src/lib.rs's `run()`. See that file's own module docs for
 * the full design rationale this mirrors; comments here focus on what's
 * different in the Electron port, not restating identical reasoning.
 */

import { app } from "electron";
import { freePort } from "./paths";
import { startSidecar, killSidecar } from "./sidecar";
import { watchBackgroundEvents } from "./backgroundEvents";
import { createMainWindow, showMainWindow, preloadPath, iconsDirFromApp, markQuitting } from "./mainWindow";
import { createTray } from "./tray";
import { registerDialogHandlers } from "./dialog";

// MUST run before anything else touches the window: a second launch
// fires the 'second-instance' handler below in the ALREADY-running
// instance instead, and this process exits before it can spawn a
// duplicate sidecar -- same ordering requirement Tauri's own
// single-instance plugin docs call out.
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    showMainWindow();
  });

  app.whenReady().then(async () => {
    const port = await freePort();

    startSidecar(port);
    void watchBackgroundEvents(port);

    const win = createMainWindow(port, preloadPath());
    registerDialogHandlers(win);
    createTray(iconsDirFromApp(), showMainWindow, () => {
      markQuitting();
      app.quit();
    });
  });

  app.on("before-quit", () => {
    killSidecar();
  });

  // macOS-only by convention (Cmd+Q / dock quit) -- left in place rather
  // than stripped out, same "documentation, not an active target" stance
  // office-agent-desktop's own README takes on its existing macOS code
  // paths; this project's actual target is Windows only.
  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
}
