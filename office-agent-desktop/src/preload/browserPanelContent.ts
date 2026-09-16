/**
 * Preload for the Browser panel's own `WebContentsView` (the page being
 * browsed), distinct from `preload/index.ts` (the main window's own
 * preload) -- wired into browserPanel.ts's `ensurePanelView()`. Re-
 * executes on every navigation of this view, like a browser-extension
 * content script -- whatever's here reaches every page the user
 * navigates to, not just the first one.
 *
 * Electron migration Phase 3: element-picking. The host window's React
 * DOM cannot draw on top of a natively-composited child view (a
 * `WebContentsView` is a separate OS-composited surface, not part of the
 * main window's own DOM) -- so the hover highlight and its label are
 * injected as real `<div>`s into the *live page's own* DOM instead of
 * being React-rendered overlays the way the old screencast
 * implementation's canvas path drew them. contextIsolation/sandbox stay
 * on (browserPanel.ts's own webPreferences) even though this file
 * injects DOM nodes -- this view loads arbitrary, untrusted third-party
 * sites, so nothing here exposes anything to that page's own JS via
 * contextBridge; it only manipulates the DOM from the isolated preload
 * context, which page scripts can't observe or interfere with.
 *
 * The actual element-lookup JS mirrors browser_panel.py's own
 * `_element_at()` exactly (elementFromPoint + getBoundingClientRect,
 * same truncated-innerText shape) -- kept in sync deliberately so a
 * future reader comparing the two paths sees the same logic, not two
 * independently-evolved implementations of "what element is under the
 * cursor."
 */

import { ipcRenderer } from "electron";

const SET_PICK_MODE_CHANNEL = "browser-panel-content:set-pick-mode";
const PICKED_CHANNEL = "browser-panel-content:picked";
const WHEEL_ZOOM_CHANNEL = "browser-panel-content:wheel-zoom";

const HIGHLIGHT_ID = "__coscribe_browser_panel_highlight__";
const LABEL_ID = "__coscribe_browser_panel_label__";

let pickModeActive = false;
let highlightEl: HTMLDivElement | null = null;
let labelEl: HTMLDivElement | null = null;

interface ElementInfo {
  tag: string;
  text: string;
  rect: { x: number; y: number; width: number; height: number };
}

/** Same lookup as browser_panel.py's `_element_at()`: elementFromPoint,
 * bail on a zero-sized box (nothing meaningfully there), tag/truncated-
 * innerText/rect in the same shape. */
function elementAt(x: number, y: number): ElementInfo | null {
  const el = document.elementFromPoint(x, y);
  if (!el) return null;
  const rect = el.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return null;
  return {
    tag: el.tagName.toLowerCase(),
    text: (el.textContent || "").trim().slice(0, 300),
    rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
  };
}

function ensureOverlayEls(): { highlight: HTMLDivElement; label: HTMLDivElement } {
  if (!highlightEl) {
    highlightEl = document.createElement("div");
    highlightEl.id = HIGHLIGHT_ID;
    // Fixed, not absolute -- rects from getBoundingClientRect are
    // viewport-relative, same coordinate space position:fixed uses,
    // independent of the page's own scroll position or layout.
    // 2147483647 (max signed 32-bit int) as z-index: this needs to sit
    // above literally anything the real page itself might stack -- a
    // real site's own modal/sticky-header z-index games are exactly the
    // kind of thing worth not losing to.
    highlightEl.style.cssText =
      "position:fixed;pointer-events:none;z-index:2147483647;" +
      "border:2px solid #ec3013;background:rgba(236,48,19,0.1);" +
      "box-sizing:border-box;transition:none;";
  }
  if (!labelEl) {
    labelEl = document.createElement("div");
    labelEl.id = LABEL_ID;
    labelEl.style.cssText =
      "position:fixed;pointer-events:none;z-index:2147483647;" +
      "background:#ec3013;color:#fff;font:10px/1.6 -apple-system,sans-serif;" +
      "padding:1px 6px;border-radius:3px;white-space:nowrap;";
  }
  if (!highlightEl.isConnected) document.body?.appendChild(highlightEl);
  if (!labelEl.isConnected) document.body?.appendChild(labelEl);
  return { highlight: highlightEl, label: labelEl };
}

function removeOverlayEls(): void {
  highlightEl?.remove();
  labelEl?.remove();
}

function drawHighlight(info: ElementInfo): void {
  const { highlight, label } = ensureOverlayEls();
  const { rect } = info;
  highlight.style.left = `${rect.x}px`;
  highlight.style.top = `${rect.y}px`;
  highlight.style.width = `${rect.width}px`;
  highlight.style.height = `${rect.height}px`;
  label.textContent = `${info.tag} ${Math.round(rect.width)}×${Math.round(rect.height)}`;
  // Same "flip below if too close to the top" rule the old canvas path's
  // labelStyle computation already used, ported directly rather than
  // reinvented -- a real, previously-fixed edge case (a full-page
  // element with no room "above" or "below" that isn't already
  // off-screen), not a new guess.
  const labelHeight = 20;
  const aboveTop = rect.y - labelHeight;
  const top = rect.y >= labelHeight ? aboveTop : rect.y + rect.height;
  label.style.left = `${Math.max(0, rect.x)}px`;
  label.style.top = `${Math.max(0, top)}px`;
}

function onMouseMove(e: MouseEvent): void {
  if (!pickModeActive) return;
  const info = elementAt(e.clientX, e.clientY);
  if (info) {
    drawHighlight(info);
  } else {
    removeOverlayEls();
  }
}

function onClick(e: MouseEvent): void {
  if (!pickModeActive) return;
  // A real click while picking must never actually activate whatever's
  // under the cursor (follow a link, submit a form) -- capturing on the
  // window in the capture phase (see addEventListener's third arg below)
  // intercepts before the page's own handlers ever see it.
  e.preventDefault();
  e.stopPropagation();
  const info = elementAt(e.clientX, e.clientY);
  if (info) ipcRenderer.send(PICKED_CHANNEL, info);
}

/** Real, confirmed gap (ROADMAP.md's own "Later" backlog, now fixed): a
 * bare `WebContentsView` has no browser-chrome zoom keybindings the way
 * a full Chrome window does (ctrl+scroll-to-zoom is Chrome's own `//chrome`
 * UI layer, not a core Blink/renderer behavior Electron ships) -- so
 * ctrl+wheel over the embedded page silently did nothing instead of
 * zooming. Electron's main-process `webContents` has no wheel event at
 * all (only `before-input-event`, keyboard-only -- see browserPanel.ts's
 * own before-input-event handler for the ctrl+plus/minus/0 half of this
 * fix), so this half has to go through the same DOM-listener-plus-IPC
 * relay element-picking above already established: intercept the page's
 * own `wheel` event here (capture phase, same as onClick above, so a
 * page that itself listens for wheel never sees a ctrl-held one),
 * preventDefault (stops the page's own scroll/pinch-zoom handling from
 * also firing), and hand `deltaY` to the main process, which owns the
 * actual `setZoomFactor` call (this preload has no zoom API of its own). */
function onWheel(e: WheelEvent): void {
  if (!e.ctrlKey) return;
  e.preventDefault();
  ipcRenderer.send(WHEEL_ZOOM_CHANNEL, e.deltaY);
}

window.addEventListener("mousemove", onMouseMove, true);
window.addEventListener("click", onClick, true);
window.addEventListener("wheel", onWheel, { capture: true, passive: false });

ipcRenderer.on(SET_PICK_MODE_CHANNEL, (_event, enabled: boolean) => {
  pickModeActive = enabled;
  if (!enabled) removeOverlayEls();
});
