# Security

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's **Report a vulnerability**
button on the repository's Security tab (private vulnerability reporting), or,
if it is not available, contact the maintainer [@OICWS](https://github.com/OICWS)
through their GitHub profile. Say what you found, how to reproduce it and what
it affects. You will get an answer as soon as the maintainer can give one.

## What the project does and does not promise

coscribe runs on your own computer.

- **There is no sandbox.** Scripts and commands the assistant runs, after you
  approve them, run as you and can read any file you can and use the network.
  The approval prompts, permission modes and workspace folders reduce mistakes;
  they are not a security boundary.
- **Secrets** (provider keys, connector tokens, the global secrets): values go
  to the operating system's keychain where there is one. The promise is that
  the *model* cannot see a value in its tool output, the transcript, approval
  cards or logs. It is **not** a promise against code that goes digging, since
  such code runs as you and could read the keychain itself. Redaction of
  responses covers common encodings only, and a host you allow a secret to be
  sent to could echo it back.
- **Connectors** sign in through the service's own page in your browser; their
  tokens are kept in the keychain (or a 0600 file when there is none).
- **Skills and connectors from elsewhere** run with the access you give them;
  read what you add.

## Supported versions

Only the latest `main` is maintained.
