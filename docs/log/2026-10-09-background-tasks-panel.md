# The Sub Agents panel is the Background tasks panel and lists scripts too (shipped)

Why: a script started in the background could only be followed through model
tools, and a sub-agent is the same kind of thing for the person watching: work
that runs on without blocking the conversation and reports when it ends.

## Shipped
- The header button, panel and badge say "Background tasks". One list holds
  sub-agents and scripts started with `run_background_script`, grouped Running /
  Finished like before; each row says what it is (Agent, Python, Node).
- A script's card shows its time and a Stop button; open it for its output (the
  end of the log, refreshed every 2 s while it runs, kept at the end unless the
  reader scrolls up). A finished script says how it ended: Finished, Failed (exit
  N), Timed out, Stopped, Interrupted.
- Clear finished and the badge count cover both kinds. The server's
  `background_tasks_changed` refreshes the panel at once.
- Files: `components/BackgroundTasksPanel.tsx`, `lib/backgroundTasks.ts`,
  `lib/useRunningBackgroundTasks.ts` (renamed from the sub-agent ones),
  `lib/rest.ts`, `types/session.ts`, `types/wire.ts`.

## Not verified
- In the desktop app, and with a script started by a real model turn: checked
  against a local server with one running and three finished scripts seeded.
- The follow-up chain split between Running and Finished is still shown as two
  separate notes (unchanged).

## Follow-ups
- `run_background_command` for shell commands (`docs/plan.md`).
