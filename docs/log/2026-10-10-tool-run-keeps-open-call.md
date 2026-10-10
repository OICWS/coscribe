# An opened command stays open when the next one arrives (shipped)

Why: in a run of commands ("Ran 2 commands"), opening one to read its result and then having
the next command arrive closed it again.

## Shipped
- `ToolRunGroupView` (`ChatLog.tsx`) holds which calls are opened, instead of each
  `ToolStepRow` holding its own. A row is unmounted when a second call joins a run of one (one
  call shows its detail directly, several show a list) and when an approval takes the group
  over, so its own state was lost. A lone call's detail counts as that call being opened.
- Reproduced live with a scripted model that makes three `read_file` calls five seconds apart:
  after the second arrived, one disclosure was open before this change (the group), two after
  (the group and the first call).

## Not verified
- The approval case (a later call asking for approval while an earlier one is open) uses the
  same state but was not run live.
- The Background tasks panel draws sub-agent transcripts with the same components; not checked.
