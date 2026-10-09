# Format-Hex counts as a read in the code module (shipped)

Why: With Manual mode and the code module on, Codex checked a file's bytes with `Format-Hex -LiteralPath <file in the folder>` and every such call asked, though it only reads.

## Shipped
- `Format-Hex` and its alias `fhx` join the PowerShell read cmdlets in `code_runtime/thread.py`. The same folder, device-name and syntax checks apply; a path outside the folder still asks.
- Tests: two accepted forms, plus a path outside the folder that must still ask.

## Not verified
- On Windows against a live Codex run.
