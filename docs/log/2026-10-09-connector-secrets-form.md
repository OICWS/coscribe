# The connector form takes a header and picks a secret (shipped)

Why: connectors could already use `{{secret:NAME}}` in a header or an env value
(`2026-10-08-connector-secrets.md`), but the add form had no header field for a remote
server and no way to pick a secret, so the placeholder had to be typed by hand.

## Shipped
- The custom-connector form has a Headers field for a remote server
  (`Name: value`, one per line) and an "Insert a secret" menu under it and under the
  local command's environment field; the menu puts `{{secret:NAME}}` at the caret
  (`ConnectorsTab.tsx`).
- It says, before anything is sent, when a header's secret doesn't exist or isn't
  allowed for the server's host (same rule as the server's `host_allowed`); the
  server still checks when the connector is added. An env value has no host check.
- A connector whose secret can't be filled in shows "Secret problem" in the list and
  the server's wording on its page (`secret_error`).
- The Environment dialog no longer says scripts and the code module don't get the
  variables, and now says that a name like PATH, PYTHONPATH, NODE_OPTIONS or a proxy
  variable changes how they run (`EnvironmentDialog.tsx`).
- Deleting a secret a connector uses already shows the server's message naming the
  connector (checked); nothing more was needed there.
- Spec: `tests/e2e/secrets.spec.ts` (the form, the host check, no value in the page).

## Not verified
- A real remote connector with a key from Settings > Secrets, and on Windows.
- The "Secret problem" cell: reproducing it needs a secret removed behind the
  connector's back; checked in the type and the markup only.
