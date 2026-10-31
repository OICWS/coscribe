# workflow_spec: what a workflow is, validated, and the references between its steps

## Purpose
Owns the definition of a workflow (`spec.py`: the step kinds, `parse_workflow`, the checks that
every reference points at an earlier step, `workflow_error`) and how a reference or a condition
is read (`refs.py`). Pure data and string handling with no I/O, so `tools` (the schedule store
validates a workflow before saving it) and `workflows` (which runs it) both use it and neither
imports the other. Does not own running a workflow, what it may touch or drafting one: those are
`workflows`.

## Public interface
Definition: `Workflow`, `parse_workflow`, `workflow_error`, `walk`, `Placed`, `step_output`,
`Step`, `ToolStep`, `ScriptStep`, `LLMStep`, `AgentStep`, `CheckStep`, `ApprovalStep`, `BranchStep`,
`LoopStep`, `OutputField`, `AGENT_OUTCOME_FIELDS`, `AGENT_REPORT_FIELD`, `child_lists`, `step_references`. References: `resolve`, `evaluate`, `render_text`, `render_value`. Import these from
`coscribe.workflow_spec`, not a submodule.

## Depends on
No other coscribe package.

## Invariants
- It imports no LangChain, no agent and no tool (import contract "A fixed workflow run never
  reaches an autonomous agent"). An agent step (decision 0028) is data here: what it may call and
  touch, and the fields it returns.
- A reference is a lookup, never an expression: nothing in `refs.py` evaluates code.
- Steps are capped (`MAX_STEPS`, `MAX_NESTING`, `MAX_LOOP_ITEMS`, `MAX_AGENT_STEPS`) so a saved
  workflow stays small and an agent step's run stays bounded.
- An agent step needs `version: 2`; a version 1 workflow behaves as when it was saved.

## Tests
`tests/test_workflow_spec.py`; the engine, permission, drafting and test-run tests use it too.

## Verify by hand
Saving a workflow with a mistaken reference in the scheduled-task editor and reading the message.

## Reviewed
2026-10-18 (first card).
