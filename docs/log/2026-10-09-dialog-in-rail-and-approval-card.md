# A dialog from the hover-opened sidebar stays; an approval card fits the Background tasks panel (shipped)

Why: the code/secrets session found both while testing.

## Shipped
- The hover-opened sidebar no longer closes while a dialog is open. "Edit environment"
  is mounted inside the sidebar, so moving the pointer into the dialog closed the
  sidebar and took the dialog with it (only a pinned sidebar was safe). When the dialog
  is gone and the pointer is still outside, the next mouse move starts the 0.6 s delay
  (`NavRail.tsx`). Spec: `tests/e2e/rail-dialog.spec.ts` (fails without it).
- A pending approval no longer runs past the right edge of a narrow panel: its wrapper is
  limited to the panel's width, so the "isn't sandboxed" line wraps and the code block
  wraps inside the card (`ChatLog.tsx`, `ToolRunGroupView`).

## Not verified
- The approval card has no spec: a sub-agent waiting for approval needs a server-side
  fixture. Checked by hand against a seeded task in a 460 px panel.
- Windows (where the clipping was seen); the fix is layout only.
