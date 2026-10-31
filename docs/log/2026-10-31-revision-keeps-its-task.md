# A fix revised twice is still a change to its task (#719; shipped)

Why: in a live test, a saved browser workflow failed after its site was redesigned and the
person asked the AI to look into it. The AI revised the task, tested the draft, and revised
that draft again after the test showed a second problem. The second draft had lost its link
to the task. The card offered "Review and save", and the draft page offered "Save as task…",
which makes a second task while the broken one keeps running.

## Shipped
- `conversation/workflow_drafting.py`: `revise_workflow` on a draft that changes a saved task
  keeps that task, so the result carries its `trigger_id` and the person saves the changes
  into it.
- Test in `tests/web/test_scheduled.py`: a revision of a revision still names the task.

## Not verified
- On Windows. The case was found and the fix's shape checked live on Linux with
  `deepseek:deepseek-flash`.
