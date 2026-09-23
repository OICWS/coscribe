import type { RunStatus, ScheduledRun, ScheduledTask } from "../types/settings";
import { formatElapsed } from "./format";
import { SCHEDULED_THREAD_PREFIX } from "./nav";

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  needs_approval: "Needs approval",
  stopped: "Stopped",
};

export function latestRun(task: ScheduledTask): ScheduledRun | null {
  return task.runs.length > 0 ? task.runs[task.runs.length - 1] : null;
}

/** The task a run conversation belongs to -- also matches a task from
 * before per-run conversations, whose single shared thread shows up as
 * one run. */
export function taskForThread(tasks: ScheduledTask[], threadId: string): ScheduledTask | null {
  if (!threadId.startsWith(SCHEDULED_THREAD_PREFIX)) return null;
  return tasks.find((t) => t.runs.some((r) => r.thread_id === threadId)) ?? null;
}

function isSameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** "today at 1:59 PM", "yesterday at 9:00 AM", "Sep 21 at 9:00 AM". */
export function formatRunTime(iso: string): string {
  const date = new Date(iso);
  const time = date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (isSameDay(date, now)) return `today at ${time}`;
  if (isSameDay(date, yesterday)) return `yesterday at ${time}`;
  const day = date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(date.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}),
  });
  return `${day} at ${time}`;
}

export function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function runDuration(run: ScheduledRun): string | null {
  if (!run.finished_at) return null;
  return formatElapsed(new Date(run.finished_at).getTime() - new Date(run.started_at).getTime());
}

export function runSourceLabel(run: ScheduledRun): string {
  return run.source === "manual" ? "Manual" : "Scheduled";
}
