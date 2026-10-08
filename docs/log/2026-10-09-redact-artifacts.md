# Secret values are blanked from a tool result's attachments too (shipped)

Why: the blanking of secret values (2026-10-08, secrets part 2) covered a tool
result's text and text blocks but not `ToolMessage.artifact`, where MCP tools
put attachments. No tool produced one yet; connectors with resources would.

## Shipped
- `_RedactToolResultsMiddleware` blanks every string in a result's content
  blocks and its `artifact`, however deeply nested in lists, tuples and dicts
  (keys are left as they are).
- An object that is not JSON-shaped (a model class, bytes) passes through
  unchanged: it cannot be walked without knowing its type.
- Test: a nested content block and an artifact, with the value in a key's value
  and in a list; the artifact line fails the test when removed.

## Not verified
- A real MCP tool that returns an artifact.
