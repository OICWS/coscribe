# Background scripts can be listed, read, stopped and cleared from the UI (partly shipped: backend)

Why: a script started with `run_background_script` could only be followed through
model tools. The Sub Agents panel is to become one Background tasks panel for both
kinds (the frontend part follows), so a script task needs the same actions over REST.

## Shipped
- `GET /api/threads/{id}/background-tasks`, `GET /api/background-tasks/{id}/log`,
  `POST /api/background-tasks/{id}/stop`, `DELETE /api/threads/{id}/background-tasks`
  (`web/app.py`), over `tools/background_tasks.py` (`stop_background_task`,
  `read_background_log`, `forget_finished_background_tasks`). The model's
  `kill_background_task` uses the same function.
- A task that is "running" on disk but has no live handle in this process
  (the server restarted) is now settled as `interrupted`, with a note that the
  script may still be running. Before, it stayed "running" for ever and a
  `wake_on_task` waiting on it never fired.
- The open tab is told when a script starts or ends (`background_tasks_changed`
  over the WebSocket), so the panel need not wait for its poll.
- A task id from a URL cannot name a file outside the store.
- Tests: `tests/test_background_tasks_panel.py` (settling, forgetting, the log, an id
  that leaves the store, a real process stopped, the four endpoints).

## Not verified
- The WebSocket notice in a real browser tab (the panel that listens for it is the
  next PR).
- A script still running after a restart: only the record is settled, the process
  is not looked for.

## Follow-ups
- Frontend: the Sub Agents panel becomes Background tasks and lists both kinds
  (`docs/plan.md`).
