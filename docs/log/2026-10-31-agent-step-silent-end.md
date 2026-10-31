# An agent step that ends without a word no longer passes as done (#719; shipped)

Why: in a live run on `deepseek:deepseek-flash`, a whole-task agent step was asked to write an
action-item tracker to Excel. Its `write_xlsx_cells` call came back with an error. The model
then ended its turn with an empty message and no call. The runner took the empty text as the
step's report, so the step and the run showed as completed, though the file was never
written.

## Shipped
- `runtime_lg/workflow_agent.py`: an agent that ends with no text and no call is told once
  that its reply was empty. It is asked to deal with a failed call and finish, then to say
  what is done and what isn't. If it ends silently again, the step fails with "The agent
  stopped without saying whether the task was done."
- The nudge is a message in the agent's own checkpoint, so it is sent at most once across a
  pause or a restart.
- Tests in `tests/test_workflow_agent_runner.py`: the agent recovers after the nudge, or
  stays silent and fails the step.

## Not verified
- On Windows, and again live with the model that produced the empty reply (the scripted
  model in the tests reproduces its shape).

## Follow-ups
- A step whose fields or report the run can't check (a whole-task step) still relies on the
  agent's own word. A check step after it is what verifies the work; drafting (P3) should add
  one.
