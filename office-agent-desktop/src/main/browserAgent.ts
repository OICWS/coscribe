/**
 * Carries out coscribe's browser_* tool calls on the Browser panel's
 * tabs. Commands arrive over the sidecar's /internal/browser-host
 * Server-Sent Events stream (authenticated with the token this app
 * started the sidecar with) and each result is POSTed back; see the
 * sidecar's tools/browser.py. No remote-debugging port is opened: that
 * would let any local program drive the user's signed-in pages.
 *
 * Reading the page and locating elements run in the page and its frames
 * (browserAgentPage.ts, through browserCdp.ts); clicks and keystrokes are
 * sent as real input events, which pages can't tell from the user's.
 */

import { existsSync, mkdirSync } from "node:fs";
import { join, parse } from "node:path";
import { Readable } from "node:stream";
import { type BrowserWindow, type DownloadItem, type WebContents, app } from "electron";
import { pageAgent } from "./browserAgentPage";
import { type AgentFrame, type Download, type PageDialog, type TabCdp, cdpFor, existingCdp } from "./browserCdp";
import { isAllowed, requestPermission } from "./browserPermissions";
import {
  BROWSER_AGENT_EVENT,
  MAX_TABS,
  activeTab,
  allTabs,
  closeTab,
  createTab,
  ensureActiveTab,
  normalizeUrl,
  onTabDownload,
  selectTab,
  setViewHidden,
  tabInfo,
  tabLimitReached,
  waitForPanelOpen,
  isPanelOpen,
  type Tab,
} from "./browserPanel";


const RECONNECT_DELAY_MS = 2000;
// The agent's frame and click marks: its own world, separate from the
// page's scripts and from the content preload's (999).
const AGENT_WORLD_ID = 1337;
// An iframe shows at most this much of itself in a snapshot, and frames
// nest at most this deep: an embedded page shouldn't crowd out the page.
const FRAME_LINES = 80;
const FRAME_CHARS = 5000;
const MAX_FRAME_DEPTH = 2;
const CHOOSER_WAIT_MS = 5000;
// For CDP calls made while a page may be stuck behind a dialog, which
// blocks its renderer.
const BEST_EFFORT_MS = 1500;
const LOAD_TIMEOUT_MS = 45_000;
// How long a step waits for what it needs -- an element named by
// description, text, a download -- unless the step says otherwise; a
// saved workflow step can allow up to an hour for a slow export.
const DEFAULT_WAIT_S = 30;
const MAX_WAIT_S = 3600;
const REF_PATTERN = /^(f\d+)?e\d+$/;
// Downloads this soon after an AI step are the AI's; the user's own still
// get Electron's save dialog.
const DOWNLOAD_WINDOW_MS = 15_000;
// A quick download is reported by the step that started it; a slower one
// is reported as started, and browser_wait_for(download) waits it out.
const DOWNLOAD_QUICK_MS = 10_000;

/** The step running now, and whether its conversation was stopped. */
let running: { threadId: string; stop: boolean } | null = null;

function checkStop(): void {
  if (running?.stop) throw new Error("Stopped by the user.");
}

function waitSeconds(args: Record<string, unknown>): number {
  const seconds = Number(args.timeout);
  return Number.isFinite(seconds) && seconds > 0 ? Math.min(seconds, MAX_WAIT_S) : DEFAULT_WAIT_S;
}
const SETTLE_TIMEOUT_MS = 10_000;

interface Command {
  id: string;
  thread_id: string;
  action: string;
  args: Record<string, unknown>;
  /** When the desktop app got it: a stop covers what arrived before it. */
  receivedAt?: number;
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
  await cdpFor(wc).idle();
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

// Input goes through the DevTools protocol rather than sendInputEvent,
// which hands every event to the tab's top frame: a click on an iframe
// from another site would never reach it. Coordinates are the page's CSS
// pixels, as the protocol takes them.
async function click(wc: WebContents, cdp: TabCdp, point: { x: number; y: number }, clickCount: number): Promise<void> {
  void inPage(wc, "show_cursor", point).catch(() => undefined);
  const { x, y } = point;
  await cdp.input("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
  for (let n = 1; n <= clickCount; n++) {
    await cdp.input("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: n });
    await cdp.input("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: n });
  }
}

interface KeySpec {
  key: string;
  code: string;
  keyCode: number;
  text?: string;
}

const NAMED_KEYS: Record<string, KeySpec> = {
  arrowdown: { key: "ArrowDown", code: "ArrowDown", keyCode: 40 },
  arrowup: { key: "ArrowUp", code: "ArrowUp", keyCode: 38 },
  arrowleft: { key: "ArrowLeft", code: "ArrowLeft", keyCode: 37 },
  arrowright: { key: "ArrowRight", code: "ArrowRight", keyCode: 39 },
  escape: { key: "Escape", code: "Escape", keyCode: 27 },
  enter: { key: "Enter", code: "Enter", keyCode: 13, text: "\r" },
  tab: { key: "Tab", code: "Tab", keyCode: 9 },
  backspace: { key: "Backspace", code: "Backspace", keyCode: 8 },
  delete: { key: "Delete", code: "Delete", keyCode: 46 },
  home: { key: "Home", code: "Home", keyCode: 36 },
  end: { key: "End", code: "End", keyCode: 35 },
  pageup: { key: "PageUp", code: "PageUp", keyCode: 33 },
  pagedown: { key: "PageDown", code: "PageDown", keyCode: 34 },
  space: { key: " ", code: "Space", keyCode: 32, text: " " },
  ...Object.fromEntries(
    Array.from({ length: 12 }, (_, i) => [`f${i + 1}`, { key: `F${i + 1}`, code: `F${i + 1}`, keyCode: 112 + i }]),
  ),
};
const KEY_ALIASES: Record<string, string> = { down: "arrowdown", up: "arrowup", left: "arrowleft", right: "arrowright", esc: "escape", return: "enter", " ": "space" };
// The protocol's modifier bits.
const MODIFIERS: Record<string, number> = { alt: 1, control: 2, ctrl: 2, meta: 4, cmd: 4, shift: 8 };

function keySpec(name: string): KeySpec {
  const lower = name.toLowerCase();
  const named = NAMED_KEYS[KEY_ALIASES[lower] ?? lower];
  if (named) return named;
  if (name.length !== 1) throw new Error(`Unknown key "${name}".`);
  const upper = name.toUpperCase();
  const code = /[A-Z]/.test(upper) ? `Key${upper}` : /[0-9]/.test(name) ? `Digit${name}` : "";
  return { key: name, code, keyCode: /[A-Z0-9]/.test(upper) ? upper.charCodeAt(0) : 0, text: name };
}

async function pressKey(cdp: TabCdp, combo: string): Promise<void> {
  const parts = combo.split("+").map((p) => p.trim());
  // "Control++" -- the key itself was "+".
  const keyPart = parts.pop() || (combo.endsWith("+") ? "+" : "");
  let modifiers = 0;
  for (const part of parts.filter(Boolean)) {
    const bit = MODIFIERS[part.toLowerCase()];
    if (!bit) throw new Error(`Unknown modifier "${part}" in "${combo}".`);
    modifiers |= bit;
  }
  const spec = keySpec(keyPart);
  // Text only when the key would type it: Control+a selects, it doesn't type "a".
  const text = modifiers & ~MODIFIERS.shift ? undefined : spec.text;
  const event = { key: spec.key, code: spec.code, windowsVirtualKeyCode: spec.keyCode, modifiers };
  await cdp.input("Input.dispatchKeyEvent", { type: text ? "keyDown" : "rawKeyDown", ...event, text, unmodifiedText: text });
  await cdp.input("Input.dispatchKeyEvent", { type: "keyUp", ...event });
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

function bestEffort(work: Promise<unknown>): Promise<unknown> {
  return Promise.race([work.catch(() => undefined), sleep(BEST_EFFORT_MS)]);
}

function describeDialog(dialog: PageDialog): string {
  const kind = /^[aeiou]/.test(dialog.type) ? `An ${dialog.type}` : `A ${dialog.type}`;
  return `${kind} dialog is open on the page: "${dialog.message}". Answer it with browser_handle_dialog before anything else.`;
}

/** "f2e7" -> frame "f2", element "e7"; "e7" is in the page itself. */
function splitRef(ref: unknown): { key: string; inner: string } {
  const text = String(ref ?? "").trim();
  const match = /^(f\d+)(e\d+)$/.exec(text);
  return match ? { key: match[1], inner: match[2] } : { key: "", inner: text };
}

/** A frame's snapshot lines, with each iframe it shows read in turn and
 * indented under it. */
async function snapshotFrame(cdp: TabCdp, frame: AgentFrame, depth: number): Promise<{ lines: string[]; url: string }> {
  const { lines, url } = await cdp.agent<{ lines: string[]; url: string }>(frame, "snapshot");
  const out: string[] = [];
  for (const raw of lines) {
    const line = frame.key ? raw.replace(/\[ref=e/g, `[ref=${frame.key}e`) : raw;
    const marker = /\s*@@frame:(\d+)@@$/.exec(line);
    if (!marker) {
      out.push(line);
      continue;
    }
    const head = line.slice(0, marker.index);
    const child = depth < MAX_FRAME_DEPTH ? await cdp.childFrame(frame, Number(marker[1])).catch(() => null) : null;
    if (!child) {
      out.push(`${head} (not readable)`);
      continue;
    }
    let inner: string[];
    let innerUrl: string;
    try {
      ({ lines: inner, url: innerUrl } = await snapshotFrame(cdp, child, depth + 1));
    } catch (err) {
      out.push(`${head} [frame=${child.key}] (couldn't be read: ${err instanceof Error ? err.message : String(err)})`);
      continue;
    }
    out.push(`${head} [frame=${child.key}]:`);
    let chars = 0;
    for (let i = 0; i < inner.length; i++) {
      if (i >= FRAME_LINES || chars + inner[i].length > FRAME_CHARS) {
        out.push(`  - (${inner.length - i} more lines in this frame not shown -- its page is ${innerUrl}, which browser_navigate can open)`);
        break;
      }
      out.push(`  ${inner[i]}`);
      chars += inner[i].length;
    }
    // A frame whose page hasn't arrived yet still holds its blank placeholder.
    if (inner.length === 0) out.push(innerUrl === "about:blank" ? "  - (still loading -- wait, then take a new snapshot)" : "  - (empty)");
  }
  return { lines: out, url };
}

/** Where to click element `ref`, in the tab's viewport: its spot in its
 * own frame plus the offset of each iframe it sits in. */
async function locate(cdp: TabCdp, ref: unknown): Promise<{ x: number; y: number; element: string; frame: AgentFrame; inner: string }> {
  const { key, inner } = splitRef(ref);
  const frame = cdp.frame(key);
  const point = await cdp.agent<{ x: number; y: number; element: string }>(frame, "locate", { ref: inner });
  let { x, y } = point;
  let bounds: { width: number; height: number } | null = null;
  for (let f: AgentFrame = frame; f.parent; f = f.parent) {
    const box = await cdp.agent<{ x: number; y: number; width: number; height: number }>(f.parent, "frame_box", { index: f.index });
    x += box.x;
    y += box.y;
    bounds = box;
  }
  if (bounds && (x < 0 || y < 0 || x > bounds.width || y > bounds.height)) {
    throw new Error(`${point.element} is outside the visible part of its frame -- scroll it into view first.`);
  }
  return { x, y, element: point.element, frame, inner };
}

async function upload(wc: WebContents, cdp: TabCdp, ref: unknown, files: string[]): Promise<string> {
  let chooser = cdp.chooser;
  let element = "the file chooser";
  if (ref) {
    const { key, inner } = splitRef(ref);
    const frame = cdp.frame(key);
    const field = await cdp.agent<{ file: boolean; multiple: boolean; element: string }>(frame, "is_file_input", { ref: inner });
    if (field.file) {
      if (files.length > 1 && !field.multiple) throw new Error(`${field.element} takes one file, not ${files.length}.`);
      const objectId = await cdp.element(frame, inner);
      await cdp.send("DOM.setFileInputFiles", { files, objectId }, frame.session);
      return field.element;
    }
    // Most sites hide the real file field behind a styled button: click
    // it, and catch the chooser it opens instead of letting it appear.
    cdp.chooser = null;
    await cdp.interceptFileChooser(true);
    try {
      const point = await locate(cdp, ref);
      element = point.element;
      await click(wc, cdp, point, 1);
      const deadline = Date.now() + CHOOSER_WAIT_MS;
      while (!cdp.chooser && !cdp.dialog && Date.now() < deadline) await sleep(100);
    } finally {
      await bestEffort(cdp.interceptFileChooser(false));
    }
    chooser = cdp.chooser;
    if (!chooser) throw new Error(`Clicking ${element} didn't open a file chooser -- pass the ref of the upload button or file field.`);
  }
  if (!chooser) throw new Error("No file chooser is open -- pass the ref of the upload button or file field.");
  if (files.length > 1 && !chooser.multiple) throw new Error(`This file chooser takes one file, not ${files.length}.`);
  await cdp.send("DOM.setFileInputFiles", { files, backendNodeId: chooser.backendNodeId }, chooser.session);
  cdp.chooser = null;
  return element;
}

/** A description as snapshots print elements -- 'checkbox "All items"',
 * optionally ' #2' for the second such -- turned into the element's ref
 * in a fresh snapshot, waiting for it to appear. A ref passes through. */
async function resolveTarget(cdp: TabCdp, target: string, waitS = DEFAULT_WAIT_S): Promise<string> {
  const text = target.trim();
  if (REF_PATTERN.test(text)) return text;
  const match = /^([a-z]+)(?:\s+"(.*)")?(?:\s+#(\d+))?$/is.exec(text);
  if (!match) throw new Error(`"${target}" is neither a ref like e12 nor an element like button "Save".`);
  const [, role, name, nth] = match;
  const prefix = name === undefined ? `- ${role.toLowerCase()} [ref=` : `- ${role.toLowerCase()} "${name}" `;
  const index = Math.max(Number(nth ?? 1), 1) - 1;
  const deadline = Date.now() + waitS * 1000;
  for (;;) {
    checkStop();
    const { lines } = await snapshotFrame(cdp, cdp.main, 0);
    const refsWhere = (hit: (line: string) => boolean) =>
      lines.flatMap((line) => {
        const trimmed = line.trimStart();
        const ref = hit(trimmed) ? /\[ref=((?:f\d+)?e\d+)\]/.exec(trimmed)?.[1] : undefined;
        return ref ? [ref] : [];
      });
    let found = refsWhere((line) => line.startsWith(prefix));
    if (!found.length) found = refsWhere((line) => line.toLowerCase().startsWith(prefix.toLowerCase()));
    if (found[index]) return found[index];
    if (cdp.dialog) throw new Error(describeDialog(cdp.dialog));
    if (Date.now() >= deadline) {
      throw new Error(`No ${text} appeared on the page within ${waitS}s -- take a browser_snapshot to see what's there.`);
    }
    await sleep(500);
  }
}

function uniquePath(dir: string, fileName: string): string {
  const { name, ext } = parse(fileName || "download");
  let path = join(dir, `${name}${ext}`);
  for (let n = 1; existsSync(path); n++) path = join(dir, `${name} (${n})${ext}`);
  return path;
}

function onDownload(item: DownloadItem, wc: WebContents): void {
  const cdp = existingCdp(wc);
  const aiStep = cdp && (cdp.stepEnded < cdp.stepStarted || Date.now() - cdp.stepEnded < DOWNLOAD_WINDOW_MS);
  if (!cdp || !aiStep) return;
  // The system save dialog would stop the AI (and a scheduled run) until
  // someone answers it. Into the conversation's own folder instead, where
  // the AI can read it and runs don't mix.
  const dir = cdp.downloadDir || app.getPath("downloads");
  try {
    mkdirSync(dir, { recursive: true });
  } catch {
    // setSavePath below then fails the download, which the step reports.
  }
  const download: Download = { path: uniquePath(dir, item.getFilename()), state: "progressing", announced: false, reported: false };
  item.setSavePath(download.path);
  cdp.downloads.push(download);
  item.once("done", (_event, state) => {
    download.state = state;
  });
}

/** What the model hasn't heard about the tab's downloads: finished ones,
 * and newly started ones still going after `waitMs`. */
async function newDownloads(cdp: TabCdp, waitMs: number): Promise<Array<{ path: string; state: string }>> {
  const deadline = Date.now() + waitMs;
  while (cdp.downloads.some((d) => d.state === "progressing") && Date.now() < deadline && !running?.stop) await sleep(200);
  const news: Array<{ path: string; state: string }> = [];
  for (const d of cdp.downloads) {
    if (d.state !== "progressing") {
      news.push({ path: d.path, state: d.state });
      d.reported = true;
    } else if (!d.announced) {
      news.push({ path: d.path, state: d.state });
      d.announced = true;
    }
  }
  cdp.downloads = cdp.downloads.filter((d) => !d.reported);
  return news;
}

/** Until a download has finished (one already under way, or one that
 * starts while waiting) and none is still going. */
async function waitForDownload(cdp: TabCdp, waitS: number): Promise<void> {
  const deadline = Date.now() + waitS * 1000;
  for (;;) {
    checkStop();
    const going = cdp.downloads.filter((d) => d.state === "progressing");
    if (!going.length && cdp.downloads.length) return;
    if (cdp.dialog) throw new Error(describeDialog(cdp.dialog));
    if (Date.now() >= deadline) {
      throw new Error(
        going.length
          ? `Still downloading ${going.map((d) => d.path).join(", ")} after ${waitS}s.`
          : `No download started within ${waitS}s.`,
      );
    }
    await sleep(500);
  }
}

function describeValue(value: unknown): string {
  if (value === undefined) return "undefined";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

async function run(command: Command): Promise<Record<string, unknown>> {
  let args = command.args ?? {};
  if (command.action === "tabs") {
    const op = String(args.op);
    if (op === "new") {
      if (tabLimitReached()) {
        throw new Error(`All ${MAX_TABS} tabs are in use -- close one with browser_tabs("close") or reuse the current tab.`);
      }
      const tab = createTab();
      selectTab(tab.id);
      if (args.url) {
        const cdp = cdpFor(tab.view.webContents);
        cdp.stepStarted = Date.now();
        if (typeof args.download_dir === "string") cdp.downloadDir = args.download_dir;
        await cdp.ensure();
        await load(tab.view.webContents, String(args.url));
        cdp.stepEnded = Date.now();
      }
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
  if (command.action !== "navigate" && tab.blank) {
    throw new Error("The current tab is empty -- open a page with browser_navigate first.");
  }
  const cdp = cdpFor(wc);
  cdp.stepStarted = Date.now();
  if (typeof args.download_dir === "string") cdp.downloadDir = args.download_dir;
  await cdp.ensure();
  if (cdp.dialog && command.action !== "handle_dialog") throw new Error(describeDialog(cdp.dialog));

  let result: Record<string, unknown>;
  try {
    if (typeof args.ref === "string" && args.ref.trim()) {
      args = { ...args, ref: await resolveTarget(cdp, args.ref, waitSeconds(args)) };
    }
    result = await act(command, tab, wc, cdp, args);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new Error(cdp.dialog ? `${message} ${describeDialog(cdp.dialog)}` : message);
  } finally {
    cdp.stepEnded = Date.now();
  }
  // What a step left open for the model to deal with next.
  if (cdp.dialog) result.dialog = describeDialog(cdp.dialog);
  if (cdp.chooser) result.file_chooser = true;
  const downloads = await newDownloads(cdp, DOWNLOAD_QUICK_MS);
  if (downloads.length) result.downloads = downloads;
  return result;
}

async function act(
  command: Command,
  tab: Tab,
  wc: WebContents,
  cdp: TabCdp,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
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
      const full = (await snapshotFrame(cdp, cdp.main, 0)).lines.join("\n");
      const start = Number(args.start) || 0;
      const max = Number(args.max_chars) || 12000;
      let end = Math.min(full.length, start + max);
      // Break at a line, not mid-element.
      if (end < full.length) {
        const cut = full.lastIndexOf("\n", end);
        if (cut > start) end = cut;
      }
      return withTab(tab, { text: full.slice(start, end), total_chars: full.length });
    }
    case "click": {
      const point = await locate(cdp, args.ref);
      // A click on an upload button would otherwise pop the system file
      // dialog up in front of the user; the model answers it instead.
      cdp.chooser = null;
      await bestEffort(cdp.interceptFileChooser(true));
      try {
        await click(wc, cdp, point, args.double ? 2 : 1);
        await settle(wc);
      } finally {
        await bestEffort(cdp.interceptFileChooser(false));
      }
      return withTab(tab, { element: point.element });
    }
    case "hover": {
      const point = await locate(cdp, args.ref);
      await cdp.input("Input.dispatchMouseEvent", { type: "mouseMoved", x: point.x, y: point.y });
      await sleep(300);
      return withTab(tab, { element: point.element });
    }
    case "type": {
      const point = await locate(cdp, args.ref);
      await click(wc, cdp, point, 1);
      await sleep(100);
      await cdp.agent(point.frame, "select_all_in_focus");
      const text = String(args.text ?? "");
      if (text) await cdp.input("Input.insertText", { text });
      else await pressKey(cdp, "Backspace");
      if (args.submit) {
        await sleep(100);
        await pressKey(cdp, "Enter");
      }
      await settle(wc);
      return withTab(tab, { element: point.element });
    }
    case "press_key":
      await pressKey(cdp, String(args.key));
      await settle(wc);
      return withTab(tab);
    case "select_option": {
      const { key, inner } = splitRef(args.ref);
      const result = await cdp.agent<Record<string, unknown>>(cdp.frame(key), "select_option", { ...args, ref: inner });
      await settle(wc);
      return withTab(tab, result);
    }
    case "scroll": {
      if (args.ref) {
        const point = await locate(cdp, args.ref);
        return withTab(tab, { note: `Scrolled ${point.element} into view.` });
      }
      return withTab(tab, await cdp.agent<Record<string, unknown>>(cdp.main, "scroll", { direction: args.direction }));
    }
    case "wait_for": {
      const waitS = waitSeconds(args);
      if (args.download) {
        await waitForDownload(cdp, waitS);
        return withTab(tab);
      }
      if (args.element) {
        const ref = await resolveTarget(cdp, String(args.element), waitS);
        return withTab(tab, { element: ref });
      }
      const text = String(args.text || args.gone || "");
      if (!text) {
        const deadline = Date.now() + Math.min(Number(args.seconds) || 0, waitS) * 1000;
        while (Date.now() < deadline) {
          checkStop();
          await sleep(Math.min(500, Math.max(0, deadline - Date.now())));
        }
        return withTab(tab);
      }
      const wantGone = !args.text;
      const deadline = Date.now() + waitS * 1000;
      for (;;) {
        checkStop();
        const { found } = await cdp
          .agent<{ found: boolean }>(cdp.main, "has_text", { text })
          .catch(() => ({ found: false }));
        if (found !== wantGone) return withTab(tab, { found });
        if (cdp.dialog) throw new Error(describeDialog(cdp.dialog));
        if (Date.now() >= deadline) {
          throw new Error(`"${text}" ${wantGone ? "was still on the page" : "didn't appear"} after ${waitS}s.`);
        }
        await sleep(500);
      }
    }
    case "handle_dialog": {
      const accept = args.accept !== false;
      const dialog = await cdp.handleDialog(accept);
      await settle(wc);
      return withTab(tab, { dialog_type: dialog.type, message: dialog.message, accepted: accept });
    }
    case "upload": {
      const files = (Array.isArray(args.paths) ? args.paths : []).map(String);
      if (!files.length) throw new Error("No files to upload.");
      const element = await upload(wc, cdp, args.ref, files);
      await settle(wc);
      return withTab(tab, { element });
    }
    case "evaluate": {
      const { value, opened } = await cdp.evaluateInPage(String(args.expression ?? ""));
      await settle(wc);
      return withTab(tab, { value: opened ? "(the script opened a dialog before it finished)" : describeValue(value) });
    }
    default:
      throw new Error(`Unknown browser action ${command.action}.`);
  }
}

/** The site a command would touch: where it navigates to, or the page
 * it acts on. None for tab bookkeeping and empty tabs. */
function siteFor(command: Command): string | null {
  const args = command.args ?? {};
  let url: string;
  if (command.action === "navigate" || (command.action === "tabs" && args.op === "new" && args.url)) {
    url = normalizeUrl(String(args.url));
  } else if (command.action === "tabs") {
    return null;
  } else {
    const tab = activeTab();
    if (!tab || tab.blank) return null;
    url = tab.view.webContents.getURL();
  }
  try {
    const { protocol, hostname } = new URL(url);
    return protocol === "http:" || protocol === "https:" ? hostname : null;
  } catch {
    return null;
  }
}

/** Starts the connection loop for the life of the app. */
export function startBrowserAgent(port: number, token: string, getWindow: () => BrowserWindow | undefined): void {
  const base = `http://127.0.0.1:${port}/internal/browser-host`;
  const headers = { "x-coscribe-browser-token": token };
  let queue: Promise<void> = Promise.resolve();
  let frameOffTimer: ReturnType<typeof setTimeout> | undefined;
  onTabDownload(onDownload);

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

  // Conversations stopped, and when: their steps that arrived before then
  // are dropped rather than carried out.
  const stoppedAt = new Map<string, number>();

  const execute = async (command: Command) => {
    if ((stoppedAt.get(command.thread_id) ?? 0) >= (command.receivedAt ?? 0)) {
      await reply(command.id, { ok: false, error: "Stopped by the user." });
      return;
    }
    // The user should see the AI work, so the panel opens first.
    if (!isPanelOpen()) {
      notify({ open: true, threadId: command.thread_id });
      await waitForPanelOpen(5000);
    }
    try {
      const site = siteFor(command);
      if (site && !isAllowed(site, command.thread_id)) await requestPermission(site, command.thread_id, getWindow());
    } catch (err) {
      await reply(command.id, { ok: false, error: err instanceof Error ? err.message : String(err) });
      return;
    }
    // The AI's steps land on the live page, so an annotation in progress
    // gives way (the panel drops its drawing when it sees "busy").
    setViewHidden(false);
    notify({ busy: true, threadId: command.thread_id, action: command.action });
    clearTimeout(frameOffTimer);
    const frameTab = command.action === "tabs" ? undefined : ensureActiveTab();
    if (frameTab && !frameTab.blank) void inPage(frameTab.view.webContents, "agent_frame", { on: true }).catch(() => undefined);
    running = { threadId: command.thread_id, stop: false };
    try {
      const result = await run(command);
      await reply(command.id, { ok: true, result });
    } catch (err) {
      await reply(command.id, { ok: false, error: err instanceof Error ? err.message : String(err) });
    } finally {
      running = null;
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
              // A stop can't queue behind the step it is stopping.
              if (command.action === "cancel") {
                stoppedAt.set(command.thread_id, Date.now());
                if (running?.threadId === command.thread_id) running.stop = true;
                continue;
              }
              command.receivedAt = Date.now();
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
