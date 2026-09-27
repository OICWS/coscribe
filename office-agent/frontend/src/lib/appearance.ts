/** Settings > General > Appearance: theme, chat font and motion. Applied
 * as attributes on <html> (index.css keys its tokens off them) and saved
 * to the server's .env, since the desktop app serves this page from a new
 * port each launch and browser storage doesn't survive that. A copy in
 * localStorage only spares the first frame a wrong theme. */

import { useSyncExternalStore } from "react";
import { setThemeSource } from "./electron";
import { getConfig, updateConfig } from "./rest";

export type ThemeChoice = "system" | "light" | "dark";
export type ChatFontChoice = "sans" | "serif" | "system";
export type MotionChoice = "system" | "reduced";

export interface Appearance {
  theme: ThemeChoice;
  chatFont: ChatFontChoice;
  motion: MotionChoice;
}

const OPTIONS: { [K in keyof Appearance]: readonly Appearance[K][] } = {
  theme: ["system", "light", "dark"],
  chatFont: ["sans", "serif", "system"],
  motion: ["system", "reduced"],
};

const CONFIG_KEYS: { [K in keyof Appearance]: string } = {
  theme: "COSCRIBE_THEME",
  chatFont: "COSCRIBE_CHAT_FONT",
  motion: "COSCRIBE_MOTION",
};

const DEFAULTS: Appearance = { theme: "system", chatFont: "sans", motion: "system" };
const HINT_KEY = "coscribe-appearance";

function valid(raw: Partial<Record<keyof Appearance, unknown>>): Partial<Appearance> {
  const out: Partial<Appearance> = {};
  for (const key of Object.keys(OPTIONS) as (keyof Appearance)[]) {
    const value = raw[key];
    if ((OPTIONS[key] as readonly unknown[]).includes(value)) (out as Record<string, unknown>)[key] = value;
  }
  return out;
}

function readHint(): Appearance {
  try {
    return { ...DEFAULTS, ...valid(JSON.parse(localStorage.getItem(HINT_KEY) ?? "{}")) };
  } catch {
    return { ...DEFAULTS };
  }
}

function saveHint(): void {
  try {
    localStorage.setItem(HINT_KEY, JSON.stringify(current));
  } catch {
    // Only a first-paint nicety.
  }
}

let current: Appearance = readHint();
const listeners = new Set<() => void>();
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
const reduceQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

function apply(): void {
  const root = document.documentElement;
  root.dataset.theme = current.theme === "system" ? (darkQuery.matches ? "dark" : "light") : current.theme;
  root.dataset.motion = current.motion === "reduced" || reduceQuery.matches ? "reduced" : "full";
  root.dataset.chatFont = current.chatFont;
  // Native menus and dialogs follow along.
  setThemeSource(current.theme);
  for (const listener of listeners) listener();
}

/** Applies what's known now, follows the system while "system" is chosen,
 * then settles on the saved choice. Called once, before the first render. */
export function startAppearance(): void {
  apply();
  darkQuery.addEventListener("change", apply);
  reduceQuery.addEventListener("change", apply);
  getConfig()
    .then((config) => {
      const saved = config as unknown as Record<string, unknown>;
      current = {
        ...DEFAULTS,
        ...valid({
          theme: saved[CONFIG_KEYS.theme],
          chatFont: saved[CONFIG_KEYS.chatFont],
          motion: saved[CONFIG_KEYS.motion],
        }),
      };
      saveHint();
      apply();
    })
    .catch(() => {});
}

/** Takes effect at once, and is saved in the background. */
export async function setAppearance<K extends keyof Appearance>(key: K, value: Appearance[K]): Promise<void> {
  current = { ...current, [key]: value };
  saveHint();
  apply();
  await updateConfig({ [CONFIG_KEYS[key]]: value });
}

export function useAppearance(): Appearance {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => current,
  );
}
