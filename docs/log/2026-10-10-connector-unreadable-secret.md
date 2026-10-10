# One unreadable keychain value no longer stops the server from starting (shipped)

Why: The main session saw the server fail to start when one connector's keychain value could not be read back (entry gone, keychain locked or missing). `load_mcp_server_configs` read those values before the `try` that skips a connector with a missing secret, and caught only `SecretError`, while the keychain read raises `RuntimeError`.

## Shipped
- `tools/mcp.py: load_mcp_server_configs` reads the `env` and `headers` values inside the guarded block and skips that one connector on `SecretError` or `RuntimeError`, with a logged warning; the others load.
- When connectors are only listed (`fill_secrets=False`), an unreadable value shows as empty and the connector stays in the list, so it can be removed or fixed.
- A test with a keychain reference that is not in the keychain, loaded and listed.

## Not verified
- A real locked keychain on Windows or macOS; the test uses a keychain whose entry is missing.
