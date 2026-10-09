# Edit environment no longer vanishes, and looks like the rest of the app (shipped)

Why: the dialog closed when the window lost focus (a click outside the app, the Windows
screenshot tool, an alt-tab), losing what was typed; and it looked unfinished next to the
Settings pages.

## Shipped
- The dialog is held by `NavRail`, not by the conversation list inside it. The list goes when
  a hover-opened sidebar collapses, and a window losing focus collapses it, so the dialog went
  with it (`NavRail.tsx`, `ThreadList.tsx`). The earlier fix only covered the pointer leaving.
- A click beside the dialog closes it only when nothing in it has been changed; Cancel, the
  close button and Escape still close it.
- A plainer layout (`EnvironmentDialog.tsx`): a header with a rule under it and the
  conversation named, column headings over the variables, empty states for no variables and no
  secrets, each secret a bordered card with its hosts as small chips (the ticked ones outlined
  in the accent colour), and a footer with a rule above the buttons.
- Specs: `tests/e2e/rail-dialog.spec.ts` (the window losing focus; a click beside the dialog
  with and without an edit).

## Not verified
- The desktop app with the real Windows screenshot shortcut; the window's `blur` event is sent
  by the test.
