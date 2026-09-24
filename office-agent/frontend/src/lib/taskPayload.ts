import type { CreateScheduledTaskPayload, ScheduledTask } from "../types/settings";

/** A task's current settings as an update payload, with `changes` applied.
 * Every partial edit (a toggle, one workflow step) goes through this, so
 * none of them can drop a field the others own -- the workflow above all,
 * which the PUT replaces wholesale. */
export function taskPayload(
  task: ScheduledTask,
  changes: Partial<CreateScheduledTaskPayload> = {},
): CreateScheduledTaskPayload {
  const { schedule } = task;
  return {
    name: task.name,
    kind: schedule.kind,
    at: schedule.at,
    prompt: task.prompt,
    ...(schedule.weekday !== null ? { weekday: schedule.weekday } : {}),
    ...(schedule.day_of_month !== null ? { day_of_month: schedule.day_of_month } : {}),
    ...(schedule.start_date ? { start_date: schedule.start_date } : {}),
    ...(task.model ? { model: task.model } : {}),
    approval_mode: task.approval_mode,
    notes_enabled: task.notes_enabled,
    workflow: task.workflow,
    workspace: task.workspace ?? null,
    ...changes,
  };
}
