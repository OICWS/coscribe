# Connectors can use a secret from Settings > Secrets (shipped, backend)

Why: a connector's key was typed into its add form and kept in the keychain
under that connector. A key the user already has in Settings > Secrets had to be
entered again, in a second place.

## Shipped
- `{{secret:NAME}}` in a connector's `env` or `headers` value (the same
  placeholder `http_request` uses) is replaced by the secret's value when the
  connector connects: at startup, on add, on reconnect and on a version bump
  (`tools/mcp.py`: `with_secrets`, `prepare_for_connect`). `mcp.json` only ever
  holds the placeholder text.
- A **header** goes to the connector's own server, so the secret must be allowed
  for that host (`api.example.com`, `*.example.com`), checked when the connector
  is added and again when it connects. An **env** value goes to the local command
  the user chose, which has no host, so there is no host check; only the user
  adds connectors (there is no model tool for it).
- Adding a connector that names an unknown secret, or one not allowed for its
  host, is rejected with a message naming the connector and the secret.
- One connector whose secret cannot be found at connect time is skipped with a
  logged warning; the others still load.
- The Connectors list shows a value that uses a secret as written, other text
  hidden, never the value or its last characters.
- Deleting a secret a connector uses is refused (409, naming the connectors).
- Reconnecting from the saved config now reads keychain references back too; the
  reconnect paths had passed them on unread.
- Tests in `tests/test_connector_secrets.py`, mutation-checked on the host check;
  a secret's value that looks like a placeholder is not expanded again.
- The Connectors list says why a connector has no secret (`secret_error`).
- A connector setting that can't be read from the keychain blocks deleting a
  secret (503) rather than counting as "uses nothing".

## Not verified
- A real remote connector with a key from Settings > Secrets, and on Windows.
- The add form does not offer the secrets yet: the placeholder is typed.

## Follow-ups
- The connector add form picks a secret (frontend; `docs/plan.md`).
