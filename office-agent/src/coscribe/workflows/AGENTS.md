# workflows: fixed lists of low-autonomy steps, run by LangGraph

## Purpose
Owns how a workflow runs (`engine.py`; `model_step.py` gets a model to return a step's
fields; `agent_step.py` runs an agent step through an injected runner), what it may touch (`permissions.py`), the tool parameters its editor needs (`catalog.py`), drafting one from a
conversation (`solidify.py`) and a one-off test run (`testing.py`). Does not own the schedule
that fires a run or its stored history (`runtime_lg/scheduled_tasks.py`), the routes
(`web/routes/scheduled.py`) or any agent: an agent step (decision 0028) runs through
`StepContext.run_agent`, which `conversation` supplies; without one `preflight` refuses it.

## Public interface
`BranchStep`, `LoopStep`, `Workflow`, `parse_workflow`, `walk`, `workflow_error` (the definition, re-exported from `workflow_spec`, where it lives);
`AgentCall`, `AgentReply`, `AgentRequest`, `AgentRunner` (what an agent runner takes and returns);
`RunOutcome`, `StepContext`, `StepRecord`, `WorkflowNotRunnable`, `WorkflowRun` (a run);
`brings_outside_content`, `granted_by`, `sites_in`, `summarize`, `untrusted_agent_steps` (permissions); `describe_params`, `tool_description`
(catalog); `output_files`, `run_test`, `uses_browser` (test run); `DraftFailed`, `WorkflowDraft`,
`draft_workflow`, `revise_workflow` (drafting from a conversation: it takes the serialized
history, so this package does not import `runtime_lg`).

## Depends on
`coscribe.needs_permission`, `coscribe.tools`, `coscribe.workflow_spec`.

## Invariants
- Nothing here (or in `coscribe.workflow_spec`) imports `code_runtime`, `runtime_lg` or
  `coordinator` (decision 0028; the import contract "Workflows depend on no agent runtime"): an
  agent step's runner comes in through `StepContext.run_agent`.
- Saving is the consent: a site or folder outside what `permissions.granted_by` lists stops a run.
- An agent step's runner keeps one agent per `AgentRequest.key` and never redoes finished work;
  an answer counts only for the call it names (`call_id`), since a resume replays earlier answers.
- An agent step that reads outside content may only make READ calls, unless `trusted_input`.
- A bounded step's allowlist, declared folders and sites are its consent: a call beyond them
  waits for a person (`agent_step.decide`, `permissions.outside_declared`), never runs unasked.
- A test run stops before an approval step and before a step marked `irreversible` (unless
  `send_for_real`, refused when no one is watching; decision 0029), and writes no run history.
- `UNAVAILABLE_TOOLS` in `engine.py` only lists tools a run may not call; the import contract,
  not that list, is what keeps the code module out.

## Tests
`tests/test_workflow_spec.py`, `tests/test_workflow_engine.py`, `tests/test_workflow_agent_step.py`, `tests/test_workflow_permissions.py`,
`tests/test_workflow_catalog.py`, `tests/test_workflow_solidify.py`, `tests/test_workflow_testing.py`;
`tests/web/test_scheduled.py` covers runs through the server.

## Verify by hand
Drafting a workflow from a real conversation with a real model (`solidify`); a browser step
on a real Windows machine.

## Reviewed
2026-10-12 (pilot card; the sha is added when the card is first checked against a later change).
