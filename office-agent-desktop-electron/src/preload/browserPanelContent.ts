/**
 * Preload for the Browser panel's own `WebContentsView` (the page being
 * browsed), distinct from `preload/index.ts` (the main window's own
 * preload). Deliberately empty for now -- wired into browserPanel.ts's
 * `ensurePanelView()` in Phase 2 specifically so Phase 3 (element-picking:
 * hover-highlight + click-to-screenshot, reusing browser_panel.py's own
 * `_element_at()` DOM-query JS) doesn't need a second real-hardware round
 * just to add a preload that wasn't wired in yet. Re-executes on every
 * navigation of this view, like a browser-extension content script --
 * whatever Phase 3 adds here reaches every page the user navigates to,
 * not just the first one.
 *
 * contextIsolation/sandbox stay on (browserPanel.ts's own webPreferences)
 * even though this file does nothing yet -- this view loads arbitrary,
 * untrusted third-party sites, unlike the main window's preload, which
 * only ever loads this app's own splash page and its own sidecar's
 * origin.
 */
export {};
