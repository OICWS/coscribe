# Under a dialog only the OS window buttons stay usable (shipped)

Why: `2026-10-09-title-bar-under-dialogs.md` made the page's own title-bar buttons (menu, sidebar,
back, forward, Settings) work under a dialog, to match the OS window buttons, which the page cannot
cover. What was wanted is the other way round: nothing of the page usable (no click, no hover
highlight), only the window buttons.

## Shipped
- That change is reverted: a dialog's backdrop covers the whole window again, the title-bar row
  included, and is the layer under the pointer there.
- `tests/e2e/titlebar.spec.ts` (new) checks it, with the desktop shell emulated: with Settings open,
  the element under the menu, sidebar and Settings buttons is the backdrop, not the button.

## Not verified
- The real desktop app: dragging the window by its title bar with a dialog open (the drag area is
  declared on an element the backdrop now covers again; the OS decides drag from the declared area).
