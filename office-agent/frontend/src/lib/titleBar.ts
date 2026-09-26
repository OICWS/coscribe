import { useEffect } from "react";
import { setTitleBarColors, titleBarPlatform } from "./electron";

/** The desktop shell hides the OS title bar and leaves the top row to the
 * page (office-agent-desktop's main/windowChrome.ts). */
export const TITLE_BAR_PLATFORM = titleBarPlatform();
export const DRAWS_TITLE_BAR = TITLE_BAR_PLATFORM !== null;

/** Width of the top-left cluster (menu, sidebar toggle, back, forward)
 * while the sidebar is collapsed: 36px buttons, 2px gaps, 6px padding. */
export const COLLAPSED_CLUSTER_WIDTH = DRAWS_TITLE_BAR ? 4 * 38 + 10 : 48;

type Rgb = [number, number, number];

function hexToRgb(hex: string): Rgb | null {
  const match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim());
  return match ? [parseInt(match[1], 16), parseInt(match[2], 16), parseInt(match[3], 16)] : null;
}

function rgbToHex(rgb: Rgb): string {
  return `#${rgb.map((c) => Math.round(c).toString(16).padStart(2, "0")).join("")}`;
}

let probe: CanvasRenderingContext2D | null = null;

/** Any CSS color as RGB plus opacity. Computed colors come back in
 * whatever space the stylesheet used (Tailwind's `bg-black/60` is
 * `oklab(0 0 0 / 0.6)`), so a pixel painted in it is read back instead. */
function toRgba(color: string): { rgb: Rgb; alpha: number } | null {
  if (!probe) {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    probe = canvas.getContext("2d", { willReadFrequently: true });
  }
  if (!probe) return null;
  probe.clearRect(0, 0, 1, 1);
  probe.fillStyle = color;
  probe.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = probe.getImageData(0, 0, 1, 1).data;
  return { rgb: [r, g, b], alpha: a / 255 };
}

/** The translucent full-window backdrops currently open (a dialog's dimmed
 * layer), bottom-most first. */
function openBackdrops(): { rgb: Rgb; alpha: number }[] {
  const layers: { rgb: Rgb; alpha: number }[] = [];
  for (const el of document.querySelectorAll<HTMLElement>(".fixed.inset-0")) {
    const layer = toRgba(getComputedStyle(el).backgroundColor);
    if (layer && layer.alpha > 0) layers.push(layer);
  }
  return layers;
}

function blend(base: Rgb, layers: { rgb: Rgb; alpha: number }[]): Rgb {
  return layers.reduce<Rgb>(
    (color, { rgb, alpha }) => [0, 1, 2].map((i) => color[i] * (1 - alpha) + rgb[i] * alpha) as Rgb,
    base,
  );
}

// The OS draws the window buttons outside the page, so a dialog's dimmed
// backdrop can't cover them: they get the same dimming as a color.
function sendThemeColors() {
  const styles = getComputedStyle(document.documentElement);
  const bg = hexToRgb(styles.getPropertyValue("--bg"));
  const muted = hexToRgb(styles.getPropertyValue("--muted"));
  if (!bg || !muted) return;
  const layers = openBackdrops();
  setTitleBarColors(rgbToHex(blend(bg, layers)), rgbToHex(blend(muted, layers)));
}

let lastSent = "";

function sendIfChanged() {
  const key = `${getComputedStyle(document.documentElement).getPropertyValue("--bg")}|${openBackdrops()
    .map((layer) => `${layer.rgb}/${layer.alpha}`)
    .join(",")}`;
  if (key === lastSent) return;
  lastSent = key;
  sendThemeColors();
}

/** Keeps the OS window buttons on the page's own background, in both
 * themes and under an open dialog's backdrop. */
export function useTitleBarColors() {
  useEffect(() => {
    if (!DRAWS_TITLE_BAR) return;
    sendIfChanged();
    const scheme = window.matchMedia("(prefers-color-scheme: dark)");
    scheme.addEventListener("change", sendIfChanged);
    let frame = 0;
    const observer = new MutationObserver(() => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        sendIfChanged();
      });
    });
    observer.observe(document.body, { childList: true, subtree: true });
    return () => {
      scheme.removeEventListener("change", sendIfChanged);
      observer.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);
}
