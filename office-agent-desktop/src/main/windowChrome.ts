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
const SET_THEME_SOURCE_CHANNEL = "window:set-theme-source";

// What the page does for a menu item: the page owns conversations,
// Settings and the composer, so the menu only names the command. Sent as
// a page event with a user gesture rather than over IPC: Chromium opens a
// file chooser only in response to one, and Open File needs it to.
type MenuCommand =
  | "new-session"
  | "open-file"
  | "open-folder"
  | "settings"
  | "close-session"
  | "find"
  | "find-next"
  | "find-previous";

function pageCommand(command: MenuCommand) {
  return (_item: unknown, win: unknown) => {
    const detail = JSON.stringify(command);
    void (win as BrowserWindow | undefined)?.webContents.executeJavaScript(
      `window.dispatchEvent(new CustomEvent("coscribe:menu-command", { detail: ${detail} }))`,
      true,
    );
  };
}

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
    {
      label: "File",
      submenu: [
        { label: "New Session", accelerator: "Ctrl+N", click: pageCommand("new-session") },
        { type: "separator" },
        { label: "Open File…", click: pageCommand("open-file") },
        { label: "Open Folder…", accelerator: "Ctrl+Shift+O", click: pageCommand("open-folder") },
        { type: "separator" },
        { label: "Settings…", accelerator: "Ctrl+,", click: pageCommand("settings") },
        { type: "separator" },
        { label: "Close Session", accelerator: "Ctrl+W", click: pageCommand("close-session") },
        { role: "quit", label: "Exit" },
      ],
    },
    {
      label: "Edit",
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "delete" },
        { role: "selectAll" },
        { type: "separator" },
        { label: "Find…", accelerator: "Ctrl+F", click: pageCommand("find") },
        { label: "Find Next", accelerator: "F3", click: pageCommand("find-next") },
        { label: "Find Previous", accelerator: "Shift+F3", click: pageCommand("find-previous") },
      ],
    },
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

  // The page's theme choice, so native menus and dialogs match it.
  ipcMain.on(SET_THEME_SOURCE_CHANNEL, (_event, theme: unknown) => {
    if (theme === "system" || theme === "light" || theme === "dark") nativeTheme.themeSource = theme;
  });

  ipcMain.on(SET_TITLE_BAR_COLORS_CHANNEL, (event, colors: TitleBarColors) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (!win) return;
    if (!isColor(colors?.color) || !isColor(colors?.symbolColor)) return;
    win.setBackgroundColor(colors.color);
    win.setTitleBarOverlay({ color: colors.color, symbolColor: colors.symbolColor, height: TITLE_BAR_HEIGHT });
  });
}
