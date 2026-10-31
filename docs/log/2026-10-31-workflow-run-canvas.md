# A run can be watched on the workflow's canvas (P2c of #719; shipped)

Why: the run view lists a run step by step, and a loop shows one item's pass at a time, so
how a whole run went (which arm a branch took, how far a loop got, where it stopped) was
hard to see at a glance. The canvas from P2b already draws the workflow; this draws the run
on it.

## Shipped
- `lib/workflowRunMap.ts`: `runMap` gives each step its state in a run: done, running,
  waiting, failed, skipped, stopped, not reached, or not taken (an arm the branch went past).
  - A step inside a loop takes its passes together. It shows the one that needs the person
    first, then the one still running, then how the rest went. It also counts how many items
    it has been done for.
  - Inside a loop an arm counts as not taken only once every pass is through, since each pass
    can take either arm.
  - `passShowing` finds the pass of a loop a step last ran in.
- `WorkflowCanvas.tsx` takes an optional `run`.
  - Each card gets a status mark and a line such as "Done for 2 items", "Stopped at item 1 of
    4" or "Took Then".
  - A failing, waiting or running card gets a ring. A card not reached or not taken fades.
  - A taken arm's label is coloured, and Done fades until the run completes.
- `WorkflowRunView.tsx`: "Steps" (the list, as before) or "Overview" (the canvas).
  - Clicking a card goes back to the list at that step, with each loop open on a pass the step
    ran in.
  - `StatusDot` moved to its own file, and the toggle (`ViewToggle.tsx`) is shared with the
    workflow page.
- A bounded agent step that asks about a call now says why. Either the tool isn't on the step's
  list, or the call reaches outside the folders or sites the step names.
- Tests:
  - `lib/workflowRunMap.test.ts` (7);
  - `tests/e2e/workflow-run-canvas.spec.ts`, which covers states, a not-taken arm, a failed
    check and a click back to the list. It is added to the e2e job's list.

## Checked live
Three runs on `deepseek:deepseek-flash` against four regional sales CSVs:
- a loop totalling each region, with a branch suggesting a recovery action for regions under
  target;
- a bounded agent writing the weekly brief;
- a check;
- an approval.

The canvas followed each run live. One run went through, and the brief's figures were right
(452,140 in total). In another, the agent stepped outside its one tool and asked first. In
the third, a check inside the loop failed on its first item.

## Not verified
- On Windows and in the desktop app (checked in Chromium).

## Follow-ups
- A bounded agent step used about 70k to 107k tokens to write one short file, so a cost cap
  (open under P1) matters.
