# A connector added as `python` starts even when the app's PATH has no usable Python (shipped)

Why: A local connector added with the command `python` showed "Connection closed" on a Windows machine where the packaged app's PATH had no working Python (the user's was in conda). The message gave nothing to act on, and the fix was to type the interpreter's full path.

## Shipped
- `tools/script_env.py: resolve_python_command`, applied through `tools/mcp.py: with_interpreter` when a connector's config is prepared to connect (startup and every reconnect): a bare `python`, `python3` or `py` that runs is left as it is; one that doesn't (missing, or the Windows Store stub) is replaced by the interpreter chosen in Settings > Environment if it runs, else the first detected one.
- The saved connector keeps the command it was added with; the replacement is never written to `mcp.json`.
- Four tests in `tests/test_connector_interpreter.py`; with the replacement disabled, two fail.

## Not verified
- On Windows with a real Store stub or a conda-only Python.

## Follow-ups
- The add-connector form could offer the detected interpreters (`GET /api/script-env/interpreter` already lists them) next to the Command field, for `npx` and `uvx` too; frontend, and `npx`/`uvx` have no detection yet.
