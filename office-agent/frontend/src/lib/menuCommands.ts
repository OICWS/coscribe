/** The desktop app's File and Edit menu items, which the page carries out
 * (office-agent-desktop/src/main/windowChrome.ts dispatches them), and
 * the two of them that reach into the composer. */

import { useEffect, useRef } from "react";

export type MenuCommand =
  | "new-session"
  | "open-file"
  | "open-folder"
  | "settings"
  | "close-session"
  | "find"
  | "find-next"
  | "find-previous";

const MENU_EVENT = "coscribe:menu-command";
const COMPOSER_EVENT = "coscribe:composer-action";
const FIND_STEP_EVENT = "coscribe:find-step";

export type ComposerAction = "open-file" | "open-folder";

export function onMenuCommand(handler: (command: MenuCommand) => void): () => void {
  const listener = (event: Event) => handler((event as CustomEvent<MenuCommand>).detail);
  window.addEventListener(MENU_EVENT, listener);
  return () => window.removeEventListener(MENU_EVENT, listener);
}

export function requestComposerAction(action: ComposerAction): void {
  window.dispatchEvent(new CustomEvent(COMPOSER_EVENT, { detail: action }));
}

/** Runs `handler` when the menu asks the composer for `action`. */
export function useComposerAction(action: ComposerAction, handler: () => void): void {
  const latest = useRef(handler);
  latest.current = handler;
  useEffect(() => {
    const listener = (event: Event) => {
      if ((event as CustomEvent<ComposerAction>).detail === action) latest.current();
    };
    window.addEventListener(COMPOSER_EVENT, listener);
    return () => window.removeEventListener(COMPOSER_EVENT, listener);
  }, [action]);
}

/** Find Next / Find Previous, for the open find bar. */
export function requestFindStep(by: 1 | -1): void {
  window.dispatchEvent(new CustomEvent(FIND_STEP_EVENT, { detail: by }));
}

export function onFindStep(handler: (by: 1 | -1) => void): () => void {
  const listener = (event: Event) => handler((event as CustomEvent<1 | -1>).detail);
  window.addEventListener(FIND_STEP_EVENT, listener);
  return () => window.removeEventListener(FIND_STEP_EVENT, listener);
}
