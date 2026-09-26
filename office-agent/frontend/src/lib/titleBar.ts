import { useEffect } from "react";
import { setTitleBarColors, titleBarPlatform } from "./electron";

/** The desktop shell hides the OS title bar and leaves the top row to the
 * page (office-agent-desktop's main/windowChrome.ts). */
export const TITLE_BAR_PLATFORM = titleBarPlatform();
export const DRAWS_TITLE_BAR = TITLE_BAR_PLATFORM !== null;

/** Width of the top-left cluster (menu, sidebar toggle, back, forward)
 * while the sidebar is collapsed: 36px buttons, 2px gaps, 6px padding. */
export const COLLAPSED_CLUSTER_WIDTH = DRAWS_TITLE_BAR ? 4 * 38 + 10 : 48;

function sendThemeColors() {
  const styles = getComputedStyle(document.documentElement);
  const bg = styles.getPropertyValue("--bg").trim();
  const muted = styles.getPropertyValue("--muted").trim();
  if (/^#[0-9a-f]{6}$/i.test(bg) && /^#[0-9a-f]{6}$/i.test(muted)) setTitleBarColors(bg, muted);
}

/** Keeps the OS window buttons on the page's own background, in both themes. */
export function useTitleBarColors() {
  useEffect(() => {
    if (!DRAWS_TITLE_BAR) return;
    sendThemeColors();
    const scheme = window.matchMedia("(prefers-color-scheme: dark)");
    scheme.addEventListener("change", sendThemeColors);
    return () => scheme.removeEventListener("change", sendThemeColors);
  }, []);
}
