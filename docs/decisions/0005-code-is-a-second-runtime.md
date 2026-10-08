# 0005: Code is a second runtime, never inside a fixed workflow

Status: accepted (recorded 2026-10-08). See `ARCHITECTURE.md`, "Code is a second runtime".

## Context
Programming work is a different job from office work and needs a coding agent's
own loop. Rebuilding one inside `runtime_lg` would duplicate a maintained project.

## Decision
Codex (`openai/codex`, Apache-2.0) runs as an external process over its
app-server JSON-RPC protocol (`code_runtime/`). The chat hands it a task
(`run_code_task`) that runs as a sub-agent; its commands and file changes come back
through coscribe's approvals as synthetic gated tools. It is optional and off by
default; the pinned binary is downloaded on first use, not bundled. Fixed workflows
never use it: a fixed run must not contain an autonomous agent.

## Consequences
- Enforced by an import contract (`[tool.importlinter]` in `pyproject.toml`): `workflows.engine`, `.spec`, `.refs`,
  `.permissions` may not import `code_runtime`, `runtime_lg` or `coordinator`.
- The code module gets no secret values (0003) and `http_request` is not given to it.
- Not yet run on real Windows.
