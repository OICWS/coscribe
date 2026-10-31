# An agent step says whether it finished, and a stop fails the step with the agent's reason (in review)

Why: in the workflow drift eval (#754) an agent refused a checkbox the row gave no ground for,
said so in prose, and ended with `{"reference": ""}`. An empty text field passes the step's
check, so the run reported completed with a row unfiled. An agent that stopped with no JSON at
all failed with "reference must be text, got NoneType" instead of its reason.

## Shipped
- `workflows/agent_step.py`: every agent step's task asks it to stop rather than guess when it
  can't finish, and to end with `outcome` ("done" or "stopped") and `stopped_because` beside the
  step's fields. "stopped" fails the step with "The agent stopped: <reason>". A report without
  `outcome` is read by the model call that already recovers missing fields, which now also
  judges whether the report says the work was finished. A step without fields whose report has
  no JSON keeps the prose as before.
- The report also lists `assumed`: what the agent decided that the task didn't say. It is kept
  on the step's run record (`agent.assumed`), for the run report planned under #754.
- `workflow_spec`: `AGENT_OUTCOME_FIELDS`; a step field can't take those names.

## Not verified
- On Windows. Checked live on Linux with `deepseek:deepseek-flash` against the eval's sites.
