# Scheduled tasks choose an environment (shipped)

Why: after conversations moved to named environments, a scheduled task still had a folder of its
own and ran with Default's network, secrets and switches. Allen asked for tasks to choose an
environment too, the same picker as below the message box (#588, step 3b).

## Shipped
- A task holds `environment` (an id, "default" for Default) instead of `workspace`. A run's
  conversation is put in that environment when it starts, so its network, secrets, variables and
  what may run come from it; its folders are the environment's, then the ones its workflow names
  or the person allowed while a run waited.
- The task form's folder picker became the environment picker (search, gear to edit, New
  environment). A task saved from a conversation starts with that conversation's environment;
  `draft_workflow`/`revise_workflow` report `environment` instead of `workspace`.
- Deleting an environment moves its tasks to Default as well. `GET /api/environments` counts the
  tasks using each one, and the dialog says so ("Used by 2 conversations and 1 scheduled task").
- Every start, a task saved with a folder moves into an environment with only that folder, named
  after the task; tasks with the same folder share one (`web/environment_migration.py`).

## Not verified
- On Windows: the task form's picker and the move of real tasks were not clicked through on a
  Windows machine; the backend tests run in CI on Linux.
