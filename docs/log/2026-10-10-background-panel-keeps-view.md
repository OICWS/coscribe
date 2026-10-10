# The Background tasks panel keeps the task you are viewing (shipped)

Why: with a script's output open, starting another command could throw the view back to a
blank "Background task" page.

## Shipped
- `loadBackgroundEntries` keeps the previous entries of a kind whose read failed. The panel's
  two reads (sub-agents, scripts) are independent, and a failed or timed-out script read used
  to count as "no scripts", so the open task vanished from the list and the panel drew a
  script's id as a sub-agent transcript. The badge on the button uses the same keep.
- Reproduced live (failing the script-list request while a script's output was open): the
  header fell back to "Background task" and the output disappeared; after the change it stays,
  and recovers when the request does.
- Two scripts do run at the same time in one conversation: started two with
  `run_background_script` in one `asyncio.gather`, both ran and finished.

## Not verified
- Why the list read failed while the user started a command is not known; the failing request
  was simulated. If the view still resets with a real run, the cause is elsewhere (a sub-agent
  asking for approval moves the panel to it by design).

## Follow-ups
- `ensure_script_env` is not locked: two first-ever script runs both create the venv. It worked
  in the check above, but the race is real.
