# A workflow test run stops before a step that can't be taken back (in review)

Why: in the workflow drift eval (#754) every repair filed 2 or 3 real records on the site while
`test_workflow` checked it, and an unattended investigation pre-approves that tool. Decision 0029.

## Shipped
- **`workflow_spec`**: tool and agent steps take `irreversible`.
- **`workflows/testing.py`**: `run_test` puts an approval step in front of each marked step,
  so a test stops there and reports `stopped_before_irreversible` with the step it stopped
  before. `send_for_real` runs them.
- **`conversation/workflow_drafting.py`**: `test_workflow(send_for_real)`. It is refused when
  `test_workflow` is pre-approved (an unattended investigation). The result's `next` says to ask
  the person before sending for real.
- **Prompts**: the drafter marks a final submit, a send or a payment (`solidify.STEP_FORMAT`).
  The coordinator and the investigation prompt say a test stops there.
- **Frontend**:
  - a "Can't be undone" switch on tool and agent steps;
  - "· can't be undone" in a step's summary;
  - the draft card's "Tested up to …, which a test doesn't send";
  - a new step for a non-browser tool that acts outside the machine starts marked.

## Not verified
- On Windows.
- Whether a real model marks the right steps when drafting from a real conversation; the
  workflow eval's fixed workflows set the mark by hand.

## Follow-ups
- Mark the submit step in `evals/workflow` and score repairs with no real submissions (#754).
