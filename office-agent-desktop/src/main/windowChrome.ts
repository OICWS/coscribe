/**
 * The main window draws its own top bar: the OS title bar and menu bar
 * are hidden, Windows keeps drawing minimize/maximize/close, and the
 * page's top row is the drag area. The application menu still exists --
 * its shortcuts keep working -- and the page opens it from its "☰"
 * button.
 */

import { BrowserWindow, Menu, ipcMain, nativeTheme, shell, type BrowserWindowConstructorOptions } from "electron";

/** Matches the frontend's title bar (h-10) so the OS buttons sit centered in it. */
export const TITLE_BAR_HEIGHT = 40;

const SHOW_APP_MENU_CHANNEL = "window:show-app-menu";
const SET_TITLE_BAR_COLORS_CHANNEL = "window:set-title-bar-colors";
const SHOW_SHORTCUTS_CHANNEL = "app:show-shortcuts";

interface TitleBarColors {
  color: string;
  symbolColor: string;
}

// The frontend's --bg and --muted tokens, used until the page reports the
// theme it actually rendered.
function defaultColors(): TitleBarColors {
  return nativeTheme.shouldUseDarkColors
    ? { color: "#232120", symbolColor: "#a19d9b" }
    : { color: "#fcfcfb", symbolColor: "#7d7979" };
}

export function titleBarWindowOptions(): BrowserWindowConstructorOptions {
  const colors = defaultColors();
  return {
    titleBarStyle: "hidden",
    backgroundColor: colors.color,
    autoHideMenuBar: true,
    titleBarOverlay: { ...colors, height: TITLE_BAR_HEIGHT },
  };
}

function goBy(win: BrowserWindow | undefined, offset: 1 | -1): void {
  const history = win?.webContents.navigationHistory;
  if (!history) return;
  if (offset < 0 && history.canGoBack()) history.goBack();
  if (offset > 0 && history.canGoForward()) history.goForward();
}

function buildAppMenu(): Menu {
  return Menu.buildFromTemplate([
    { role: "fileMenu" },
    { role: "editMenu" },
    { role: "viewMenu" },
    {
      label: "Go",
      submenu: [
        {
          label: "Back",
          accelerator: "Alt+Left",
          click: (_item, win) => goBy(win as BrowserWindow | undefined, -1),
        },
        {
          label: "Forward",
          accelerator: "Alt+Right",
          click: (_item, win) => goBy(win as BrowserWindow | undefined, 1),
        },
      ],
    },
    { role: "windowMenu" },
    {
      role: "help",
      submenu: [
        {
          label: "Keyboard Shortcuts",
          accelerator: "Ctrl+/",
          click: (_item, win) => (win as BrowserWindow | undefined)?.webContents.send(SHOW_SHORTCUTS_CHANNEL),
        },
        { type: "separator" },
        {
          label: "coscribe on GitHub",
          click: () => void shell.openExternal("https://github.com/OICWS/coscribe"),
        },
      ],
    },
  ]);
}

function isColor(value: unknown): value is string {
  return typeof value === "string" && /^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/.test(value);
}

export function registerWindowChromeHandlers(): void {
  Menu.setApplicationMenu(buildAppMenu());

  ipcMain.on(SHOW_APP_MENU_CHANNEL, (event, position: { x: number; y: number }) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (!win) return;
    Menu.getApplicationMenu()?.popup({
      window: win,
      x: Math.round(Number(position?.x) || 0),
      y: Math.round(Number(position?.y) || 0),
    });
  });

  ipcMain.on(SET_TITLE_BAR_COLORS_CHANNEL, (event, colors: TitleBarColors) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (!win) return;
    if (!isColor(colors?.color) || !isColor(colors?.symbolColor)) return;
    win.setBackgroundColor(colors.color);
    win.setTitleBarOverlay({ color: colors.color, symbolColor: colors.symbolColor, height: TITLE_BAR_HEIGHT });
  });
}
