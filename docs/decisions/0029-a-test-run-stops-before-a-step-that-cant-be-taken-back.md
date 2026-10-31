# 0029: A test run stops before a step that can't be taken back

Status: proposed (2026-10-31, kurtosisOC; needs the maintainer's acceptance). Builds on 0028
(the repair layers, P4). Issue #757.

## Context
`test_workflow` runs every step once for real. That is how the assistant checks a workflow it
drafted and how a run's investigation checks its repair. In the workflow drift eval (#754) every
repair filed 2 or 3 real records on the site while it was being checked. An unattended
investigation pre-approves `test_workflow`, so no one even saw them go. A finance task can't allow
that.

## Decision
- **The mark.** A tool step or an agent step can be marked `irreversible`: it submits, sends, pays
  or deletes something outside this machine that can't be taken back.
  - The drafter marks such steps.
  - A person can set or clear the mark in the editor; the step's summary shows it.
  - A new step for a tool that acts outside the machine starts marked, except a browser click,
    which as often only moves to the next page.
- **A test stops there.** A test run stops before the first marked step and reports
  `stopped_before_irreversible`, naming that step. `run_test` does this by putting an approval
  step in front of each marked step, so the engine is unchanged.
- **Sending for real.** `test_workflow(send_for_real=true)` runs those steps too.
  - The tool tells the assistant to use it only when the person agreed.
  - It is refused when `test_workflow` is pre-approved, which means no one is watching.
- **The mark is a declaration, not a detector.** Nothing checks that every submit is marked. The
  review card is where a missing mark is caught.

## Consequences
- **The part after a submit isn't checked by a test.** That covers the confirmation page and the
  reference. The person can agree to one real test, or the first real run checks it.
- **An agent step is marked as a whole.** A test stops before the whole step, even when only its
  last click submits.
- The autonomy setting and the run report (#754) can use the same mark, for example to list the
  irreversible actions a run took.
