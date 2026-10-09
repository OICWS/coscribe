# The sidebar closes 0.6 s after the pointer leaves; the context ring sits under Send (shipped)

Why: two small things that felt off in daily use.

## Shipped
- A sidebar opened by hover closes 0.6 s after the pointer is out of it (it used
  to wait until the pointer was 48 px beyond it). Coming back within that time
  keeps it open. The position is still read from mouse moves, not `mouseleave`:
  the desktop title bar's drag area swallows mouse events (`NavRail.tsx`).
- The context ring under the input box is centred under the Send button instead of
  at the box's right edge (`ContextRing.tsx`).

## Not verified
- The desktop app (title bar drag area, window blur), where the 0.6 s was only
  checked in the browser.
