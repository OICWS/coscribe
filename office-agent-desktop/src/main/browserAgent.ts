/**
 * Carries out coscribe's browser_* tool calls on the Browser panel's
 * tabs. Commands arrive over the sidecar's /internal/browser-host
 * Server-Sent Events stream (authenticated with the token this app
 * started the sidecar with) and each result is POSTed back; see the
 * sidecar's tools/browser.py. No remote-debugging port is opened: that
 * would let any local program drive the user's signed-in pages.
 *
 * Reading the page and locating elements run in the page
 * (browserAgentPage.ts); clicks and keystrokes are sent as real input
 * events with `sendInputEvent`, which pages can't tell from the user's.
 */

import { Readable } from "node:stream";
import { type BrowserWindow, type WebContents } from "electron";
import { pageAgent } from "./browserAgentPage";
import {
  activeTab,
  allTabs,
  closeTab,
  createTab,
  ensureActiveTab,
  normalizeUrl,
  selectTab,
  tabInfo,
  waitForPanelOpen,
  isPanelOpen,
  type Tab,
} from "./browserPanel";

export const BROWSER_AGENT_EVENT = "browser-panel:agent";

const RECONNECT_DELAY_MS = 2000;
// Its own world: separate from the page's scripts and from the content
// preload's (999), so neither can see or clobber the agent's state.
const AGENT_WORLD_ID = 1337;
const LOAD_TIMEOUT_MS = 45_000;
const SETTLE_TIMEOUT_MS = 10_000;

interface Command {
  id: string;
  thread_id: string;
  action: string;
  args: Record<string, unknown>;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function inPage<T>(wc: WebContents, action: string, args: Record<string, unknown> = {}): Promise<T> {
  const code = `(${pageAgent.toString()})(${JSON.stringify(action)}, ${JSON.stringify(args)})`;
  try {
    return (await wc.executeJavaScriptInIsolatedWorld(AGENT_WORLD_ID, [{ code }])) as T;
  } catch (err) {
    // Errors thrown in the page come back wrapped; the model needs just
    // the sentence it can act on.
    const message = err instanceof Error ? err.message : String(err);
    throw new Error(message.replace(/^(Uncaught )?Error:\s*/, ""));
  }
}

/** Until the tab stops loading, up to `timeoutMs`; returns early if it
 * never started. */
async function settle(wc: WebContents, timeoutMs = SETTLE_TIMEOUT_MS): Promise<void> {
  await sleep(250);
  const deadline = Date.now() + timeoutMs;
  while (wc.isLoading() && Date.now() < deadline) await sleep(100);
}

async function load(wc: WebContents, url: string): Promise<void> {
  const target = normalizeUrl(url);
  // Web pages only: file:// would let the model read files outside the
  // folders the conversation was given.
  if (!/^https?:\/\//i.test(target)) throw new Error(`Only http and https pages can be opened, not ${url}.`);
  try {
    await Promise.race([
      wc.loadURL(target),
      sleep(LOAD_TIMEOUT_MS).then(() => {
        throw new Error(`${url} took more than ${LOAD_TIMEOUT_MS / 1000}s to load.`);
      }),
    ]);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    // A redirect or a script navigating straight away aborts the first
    // load; the page that replaced it is what the model wants.
    if (!message.includes("ERR_ABORTED")) throw new Error(message.replace(/^Error invoking remote method.*?: /, ""));
    await settle(wc, LOAD_TIMEOUT_MS);
  }
}

function toViewPoint(wc: WebContents, point: { x: number; y: number }): { x: number; y: number } {
  // The page reports CSS pixels; input events are in the view's DIPs.
  const zoom = wc.getZoomFactor();
  return { x: Math.round(point.x * zoom), y: Math.round(point.y * zoom) };
}

async function click(wc: WebContents, point: { x: number; y: number }, clickCount: number): Promise<void> {
  void inPage(wc, "show_cursor", point).catch(() => undefined);
  const { x, y } = toViewPoint(wc, point);
  wc.sendInputEvent({ type: "mouseMove", x, y });
  for (let n = 1; n <= clickCount; n++) {
    wc.sendInputEvent({ type: "mouseDown", x, y, button: "left", clickCount: n });
    wc.sendInputEvent({ type: "mouseUp", x, y, button: "left", clickCount: n });
  }
}

const KEY_NAMES: Record<string, string> = {
  arrowdown: "Down",
  arrowup: "Up",
  arrowleft: "Left",
  arrowright: "Right",
  esc: "Escape",
  escape: "Escape",
  enter: "Enter",
  return: "Enter",
  tab: "Tab",
  backspace: "Backspace",
  delete: "Delete",
  home: "Home",
  end: "End",
  pageup: "PageUp",
  pagedown: "PageDown",
  space: "Space",
  " ": "Space",
};
const MODIFIERS: Record<string, "control" | "shift" | "alt" | "meta"> = {
  control: "control",
  ctrl: "control",
  shift: "shift",
  alt: "alt",
  meta: "meta",
  cmd: "meta",
};

function pressKey(wc: WebContents, combo: string): void {
  const parts = combo.split("+").map((p) => p.trim()).filter(Boolean);
  const keyPart = parts.pop() ?? "";
  const modifiers = parts.map((p) => {
    const modifier = MODIFIERS[p.toLowerCase()];
    if (!modifier) throw new Error(`Unknown modifier "${p}" in "${combo}".`);
    return modifier;
  });
  const keyCode = KEY_NAMES[keyPart.toLowerCase()] ?? keyPart;
  wc.sendInputEvent({ type: "keyDown", keyCode, modifiers });
  const printable = keyPart.length === 1 || keyCode === "Space" || keyCode === "Enter";
  if (printable && !modifiers.some((m) => m !== "shift")) {
    const char = keyCode === "Space" ? " " : keyCode === "Enter" ? "\r" : keyPart;
    wc.sendInputEvent({ type: "char", keyCode: char, modifiers });
  }
  wc.sendInputEvent({ type: "keyUp", keyCode, modifiers });
}

function withTab(tab: Tab, extra: Record<string, unknown> = {}): Record<string, unknown> {
  const info = tabInfo(tab);
  return { ...extra, tab: { id: info.id, title: info.title, url: info.url } };
}

function listTabs(): Record<string, unknown> {
  const current = activeTab()?.id;
  return {
    tabs: allTabs().map((t) => {
      const info = tabInfo(t);
      return { id: info.id, title: info.title, url: info.url, active: info.id === current };
    }),
  };
}

async function run(command: Command): Promise<Record<string, unknown>> {
  const args = command.args ?? {};
  if (command.action === "tabs") {
    const op = String(args.op);
    if (op === "new") {
      const tab = createTab();
      selectTab(tab.id);
      if (args.url) await load(tab.view.webContents, String(args.url));
    } else if (op === "select") {
      if (!selectTab(Number(args.tab_id))) throw new Error(`There's no tab ${args.tab_id}.`);
    } else if (op === "close") {
      if (!allTabs().some((t) => t.id === Number(args.tab_id))) throw new Error(`There's no tab ${args.tab_id}.`);
      closeTab(Number(args.tab_id));
    }
    return listTabs();
  }

  const tab = ensureActiveTab();
  const wc = tab.view.webContents;
  const needsPage = command.action !== "navigate";
  if (needsPage && tab.blank) throw new Error("The current tab is empty -- open a page with browser_navigate first.");

  switch (command.action) {
    case "navigate":
      await load(wc, String(args.url));
      return withTab(tab);
    case "back":
      if (!wc.navigationHistory.canGoBack()) throw new Error("There's no earlier page in this tab.");
      wc.navigationHistory.goBack();
      await settle(wc, LOAD_TIMEOUT_MS);
      return withTab(tab);
    case "snapshot": {
      await settle(wc);
      const result = await inPage<Record<string, unknown>>(wc, "snapshot", args);
      return withTab(tab, result);
    }
    case "click": {
      const point = await inPage<{ x: number; y: number; element: string }>(wc, "locate", { ref: args.ref });
      await click(wc, point, args.double ? 2 : 1);
      await settle(wc);
      return withTab(tab, { element: point.element });
    }
    case "hover": {
      const point = await inPage<{ x: number; y: number; element: string }>(wc, "locate", { ref: args.ref });
      const { x, y } = toViewPoint(wc, point);
      wc.sendInputEvent({ type: "mouseMove", x, y });
      await sleep(300);
      return withTab(tab, { element: point.element });
    }
    case "type": {
      const point = await inPage<{ x: number; y: number; element: string }>(wc, "locate", { ref: args.ref });
      await click(wc, point, 1);
      await sleep(100);
      await inPage(wc, "select_all_in_focus");
      const text = String(args.text ?? "");
      if (text) wc.insertText(text);
      else pressKey(wc, "Backspace");
      if (args.submit) {
        await sleep(100);
        pressKey(wc, "Enter");
      }
      await settle(wc);
      return withTab(tab, { element: point.element });
    }
    case "press_key":
      pressKey(wc, String(args.key));
      await settle(wc);
      return withTab(tab);
    case "select_option": {
      const result = await inPage<Record<string, unknown>>(wc, "select_option", args);
      await settle(wc);
      return withTab(tab, result);
    }
    case "scroll": {
      if (args.ref) {
        const point = await inPage<{ element: string }>(wc, "locate", { ref: args.ref });
        return withTab(tab, { note: `Scrolled ${point.element} into view.` });
      }
      return withTab(tab, await inPage<Record<string, unknown>>(wc, "scroll", { direction: args.direction }));
    }
    case "wait_for": {
      const deadline = Date.now() + Number(args.seconds ?? 5) * 1000;
      if (!args.text) {
        await sleep(Math.max(0, deadline - Date.now()));
        return withTab(tab);
      }
      for (;;) {
        const { found } = await inPage<{ found: boolean }>(wc, "has_text", { text: args.text }).catch(() => ({ found: false }));
        if (found || Date.now() >= deadline) return withTab(tab, { found });
        await sleep(500);
      }
    }
    default:
      throw new Error(`Unknown browser action ${command.action}.`);
  }
}

/** Starts the connection loop for the life of the app. */
export function startBrowserAgent(port: number, token: string, getWindow: () => BrowserWindow | undefined): void {
  const base = `http://127.0.0.1:${port}/internal/browser-host`;
  const headers = { "x-coscribe-browser-token": token };
  let queue: Promise<void> = Promise.resolve();
  let frameOffTimer: ReturnType<typeof setTimeout> | undefined;

  const notify = (payload: Record<string, unknown>) => {
    const win = getWindow();
    if (win && !win.isDestroyed()) win.webContents.send(BROWSER_AGENT_EVENT, payload);
  };

  const reply = async (id: string, body: Record<string, unknown>) => {
    try {
      await fetch(`${base}/result`, {
        method: "POST",
        headers: { ...headers, "content-type": "application/json" },
        body: JSON.stringify({ id, ...body }),
      });
    } catch {
      // The sidecar is gone; the tool call it was waiting on went with it.
    }
  };

  const execute = async (command: Command) => {
    // The user should see the AI work, so the panel opens first.
    if (!isPanelOpen()) {
      notify({ open: true, threadId: command.thread_id });
      await waitForPanelOpen(5000);
    }
    notify({ busy: true, threadId: command.thread_id, action: command.action });
    clearTimeout(frameOffTimer);
    const frameTab = command.action === "tabs" ? undefined : ensureActiveTab();
    if (frameTab && !frameTab.blank) void inPage(frameTab.view.webContents, "agent_frame", { on: true }).catch(() => undefined);
    try {
      const result = await run(command);
      await reply(command.id, { ok: true, result });
    } catch (err) {
      await reply(command.id, { ok: false, error: err instanceof Error ? err.message : String(err) });
    } finally {
      notify({ busy: false, threadId: command.thread_id });
      // Steps come a few seconds apart while the model thinks; the frame
      // stays up between them instead of flickering.
      frameOffTimer = setTimeout(() => {
        for (const tab of allTabs()) {
          if (!tab.blank) void inPage(tab.view.webContents, "agent_frame", { on: false }).catch(() => undefined);
        }
      }, 8000);
    }
  };

  void (async () => {
    for (;;) {
      let response: Response;
      try {
        response = await fetch(base, { headers });
      } catch {
        await sleep(RECONNECT_DELAY_MS);
        continue;
      }
      if (!response.ok || !response.body) {
        await sleep(RECONNECT_DELAY_MS);
        continue;
      }
      let buf = "";
      try {
        for await (const chunk of Readable.fromWeb(response.body as never)) {
          buf += (chunk as Buffer).toString("utf-8");
          let idx: number;
          while ((idx = buf.indexOf("\n\n")) !== -1) {
            const frame = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            for (const line of frame.split("\n")) {
              if (!line.startsWith("data: ")) continue;
              let command: Command;
              try {
                command = JSON.parse(line.slice("data: ".length)) as Command;
              } catch {
                continue;
              }
              // One step at a time, in order: two clicks racing on one
              // page would land unpredictably.
              queue = queue.then(() => execute(command));
            }
          }
        }
      } catch {
        // Dropped stream: reconnect below.
      }
      await sleep(RECONNECT_DELAY_MS);
    }
  })();
}
