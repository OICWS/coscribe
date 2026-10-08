# 0003: Secrets: the model cannot see a value; nothing more is promised

Status: accepted (2026-10-08). Implementation: `runtime/secret_store.py`, `tools/http_request.py`.

## Context
Users want to give the assistant API keys without the model ever knowing them.
Masking printed values is weak: code can print a value encoded or write it to a
file. With no sandbox (0002) a script runs as the user and could read the
keychain itself.

## Decision
A secret is a name, a value in the OS keychain, and the hosts it may be sent to.
The model writes `{{secret:NAME}}` in an `http_request`; coscribe substitutes the
value at the moment of the request, only for an allowed host, only over https,
without following redirects, and blanks every stored value and its common
encodings out of every tool result at one place (outermost middleware) before the
model, transcript, UI or audit log read it. No keychain means no save: never a
plain-file fallback. A value is at least 8 characters. Scripts and Codex never
receive a value.

## Consequences
- The promise, worded the same on the Secrets page, in `SECURITY.md` and here:
  the model cannot SEE a value; the app is not defended against code that goes
  digging. Redaction covers common encodings only and an allowed host could echo a
  value back.
- A secret's names and hosts reach the model through `list_secrets`, never
  through tool schemas or the system prompt (0001).
- Reject any change that returns a value from an API or puts one in an
  environment a script inherits.
