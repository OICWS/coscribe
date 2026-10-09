# In the desktop app the title bar stays usable under a dialog (shipped)

Why: with Settings open, the OS window buttons (minimise, maximise, close) still
worked but the page's own title-bar buttons (menu, sidebar, back, forward) and
the drag area did not: a dialog's backdrop covered the whole window.

## Shipped
- In the desktop shell a dialog's backdrop starts below the title bar row
  (`index.css`, set by `data-title-bar` on `<html>`), so the menu, the sidebar
  button, back/forward and the drag area work like the OS buttons.
- `TitleBarDim` lays the backdrops' dimming over that row without taking its
  clicks, so the row looks dimmed like the rest (as the OS buttons already are,
  by colour: `lib/titleBar.ts`).
- Settings no longer adds its own top padding for it.
- Spec: `tests/e2e/titlebar.spec.ts` (the shell is emulated by the page's
  `coscribeDesktop` object; it fails without the CSS rule).

## Not verified
- The real desktop app: window dragging with a dialog open (the drag area is
  still declared by the same element; only its covering changed), and Windows
  Window Controls Overlay placement.

## Superseded
Reverted the same day (`2026-10-09-title-bar-covered.md`): the maintainer wanted the page's own
title-bar buttons covered by a dialog too, leaving only the OS window buttons (minimise, maximise,
close) usable.
