import { useEffect, useState, type MouseEvent } from "react";
import { showAppMenu } from "../lib/electron";
import { MAC_TITLE_BAR } from "../lib/titleBar";
import { ArrowLeftIcon, ArrowRightIcon, MenuIcon } from "./icons";

const iconButton =
  "flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)] disabled:opacity-35 disabled:hover:bg-transparent disabled:hover:text-[var(--muted)]";

interface NavigationLike extends EventTarget {
  canGoBack: boolean;
  canGoForward: boolean;
}

/** Chromium's Navigation API, which knows whether Back/Forward lead
 * anywhere; the History API doesn't. */
function navigationApi(): NavigationLike | null {
  return (window as unknown as { navigation?: NavigationLike }).navigation ?? null;
}

function readAvailability(): { back: boolean; forward: boolean } {
  const nav = navigationApi();
  return { back: nav?.canGoBack ?? true, forward: nav?.canGoForward ?? true };
}

function useHistoryAvailability(): { back: boolean; forward: boolean } {
  const [state, setState] = useState(readAvailability);
  useEffect(() => {
    const nav = navigationApi();
    if (!nav) return;
    const update = () => setState(readAvailability());
    nav.addEventListener("currententrychange", update);
    return () => nav.removeEventListener("currententrychange", update);
  }, []);
  return state;
}

/** The application menu (File/Edit/View/Go/Window/Help); the OS menu bar is hidden. */
export function AppMenuButton() {
  if (MAC_TITLE_BAR) return null;
  const open = (event: MouseEvent<HTMLButtonElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    showAppMenu(rect.left, rect.bottom + 4);
  };
  return (
    <button type="button" title="Menu" aria-label="Menu" className={iconButton} onClick={open}>
      <MenuIcon className="h-[18px] w-[18px]" />
    </button>
  );
}

/** Back and Forward through the pages visited in this window. */
export function HistoryButtons() {
  const { back, forward } = useHistoryAvailability();
  return (
    <>
      <button
        type="button"
        title="Back"
        aria-label="Back"
        disabled={!back}
        className={iconButton}
        onClick={() => window.history.back()}
      >
        <ArrowLeftIcon className="h-[18px] w-[18px]" />
      </button>
      <button
        type="button"
        title="Forward"
        aria-label="Forward"
        disabled={!forward}
        className={iconButton}
        onClick={() => window.history.forward()}
      >
        <ArrowRightIcon className="h-[18px] w-[18px]" />
      </button>
    </>
  );
}
