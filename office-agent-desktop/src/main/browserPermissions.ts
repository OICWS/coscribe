/**
 * The built-in browser's own settings (kept by the desktop app, not the
 * sidecar: they only mean anything where the browser lives), and the
 * per-site permission the AI needs before its browser tools touch a site.
 *
 * A site on the allowed list, or allowed for the current conversation,
 * goes ahead; any other gets a prompt in the Browser panel. Pre-allowing
 * sites is how an unattended scheduled task gets through without one.
 */

import { app, ipcMain, type BrowserWindow } from "electron";
import { readFileSync } from "node:fs";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";

export const BROWSER_PERMISSION_EVENT = "browser-panel:permission";
const PERMISSION_ANSWER_CHANNEL = "browser-panel:permission-answer";
const PENDING_PERMISSION_CHANNEL = "browser-panel:pending-permission";
const GET_ALLOWED_SITES_CHANNEL = "browser-panel:get-allowed-sites";
const SET_ALLOWED_SITES_CHANNEL = "browser-panel:set-allowed-sites";

const PROMPT_TIMEOUT_MS = 3 * 60_000;

export type PermissionAnswer = "once" | "always" | "deny";

interface BrowserSettings {
  openLinksInBuiltIn: boolean;
  allowedSites: string[];
}

interface PendingPrompt {
  requestId: number;
  host: string;
  threadId: string;
  resolve: (answer: PermissionAnswer) => void;
}

const DEFAULTS: BrowserSettings = { openLinksInBuiltIn: true, allowedSites: [] };
let cached: BrowserSettings | undefined;
let pending: PendingPrompt | null = null;
let nextRequestId = 1;
const allowedForThread = new Map<string, Set<string>>();

function settingsPath(): string {
  return join(app.getPath("userData"), "browser-settings.json");
}

export function browserSettings(): BrowserSettings {
  if (cached) return cached;
  try {
    const raw = JSON.parse(readFileSync(settingsPath(), "utf-8")) as Partial<BrowserSettings>;
    cached = {
      openLinksInBuiltIn: typeof raw.openLinksInBuiltIn === "boolean" ? raw.openLinksInBuiltIn : DEFAULTS.openLinksInBuiltIn,
      allowedSites: Array.isArray(raw.allowedSites) ? raw.allowedSites.flatMap((s) => normalizeSite(String(s)) ?? []) : [],
    };
  } catch {
    cached = { ...DEFAULTS };
  }
  return cached;
}

export async function updateBrowserSettings(patch: Partial<BrowserSettings>): Promise<BrowserSettings> {
  cached = { ...browserSettings(), ...patch };
  await writeFile(settingsPath(), JSON.stringify(cached, null, 2), "utf-8");
  return cached;
}

/** "https://www.Example.com/a?b" -> "example.com": a site is a host, and
 * "www." adds nothing (the bare domain already covers its subdomains). */
export function normalizeSite(input: string): string | null {
  const text = input.trim().toLowerCase();
  if (!text) return null;
  let host: string;
  try {
    host = new URL(/^[a-z][a-z0-9+.-]*:\/\//.test(text) ? text : `https://${text}`).hostname;
  } catch {
    return null;
  }
  host = host.replace(/^www\./, "").replace(/\.$/, "");
  return /^[a-z0-9.-]+$|^\[[0-9a-f:]+\]$/.test(host) && host.length > 0 ? host : null;
}

function covers(site: string, host: string): boolean {
  return host === site || host.endsWith(`.${site}`);
}

export function isAllowed(host: string, threadId: string): boolean {
  const site = normalizeSite(host);
  if (!site) return false;
  if (browserSettings().allowedSites.some((allowed) => covers(allowed, site))) return true;
  return [...(allowedForThread.get(threadId) ?? [])].some((allowed) => covers(allowed, site));
}

function sendPending(win: BrowserWindow | undefined): void {
  if (!pending || !win || win.isDestroyed()) return;
  win.webContents.send(BROWSER_PERMISSION_EVENT, { requestId: pending.requestId, host: pending.host, threadId: pending.threadId });
}

/** Asks in the Browser panel whether the AI may use `host`; throws if the
 * user says no or doesn't answer. One prompt at a time: the AI's steps
 * already run one at a time. */
export async function requestPermission(host: string, threadId: string, win: BrowserWindow | undefined): Promise<void> {
  const site = normalizeSite(host) ?? host;
  const answer = await new Promise<PermissionAnswer>((resolve) => {
    const requestId = nextRequestId++;
    const timer = setTimeout(() => settle("deny"), PROMPT_TIMEOUT_MS);
    function settle(value: PermissionAnswer) {
      clearTimeout(timer);
      if (pending?.requestId === requestId) pending = null;
      resolve(value);
    }
    pending = { requestId, host: site, threadId, resolve: settle };
    sendPending(win);
  });
  if (answer === "once") {
    const sites = allowedForThread.get(threadId) ?? new Set<string>();
    sites.add(site);
    allowedForThread.set(threadId, sites);
  } else if (answer === "always") {
    const sites = browserSettings().allowedSites;
    if (!sites.includes(site)) await updateBrowserSettings({ allowedSites: [...sites, site] });
  } else {
    throw new Error(
      `The user didn't allow coscribe's browser to use ${site}. Don't use it again unless they ask; ` +
        "they can allow it from the Browser panel.",
    );
  }
}

export function registerBrowserPermissionHandlers(win: BrowserWindow): void {
  ipcMain.handle(PERMISSION_ANSWER_CHANNEL, (_event, requestId: number, answer: PermissionAnswer) => {
    if (pending?.requestId === Number(requestId) && ["once", "always", "deny"].includes(answer)) pending.resolve(answer);
  });
  ipcMain.handle(PENDING_PERMISSION_CHANNEL, () => {
    sendPending(win);
  });
  ipcMain.handle(GET_ALLOWED_SITES_CHANNEL, () => browserSettings().allowedSites);
  ipcMain.handle(SET_ALLOWED_SITES_CHANNEL, async (_event, sites: unknown) => {
    const list = Array.isArray(sites) ? [...new Set(sites.flatMap((s) => normalizeSite(String(s)) ?? []))] : [];
    return (await updateBrowserSettings({ allowedSites: list })).allowedSites;
  });
}
