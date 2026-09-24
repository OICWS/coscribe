import type { ScheduledRun } from "../types/settings";
import type { Workflow } from "../types/workflow";

export type ProgressStatus = "pending" | "in_progress" | "completed" | "failed";

export interface ProgressItem {
  id: string;
  content: string;
  status: ProgressStatus;
}

/** A workflow run as the side panel's progress row: one entry per step. */
export function workflowProgress(workflow: Workflow, run: ScheduledRun): ProgressItem[] {
  const records = new Map(run.steps.map((record) => [record.step_id, record]));
  return workflow.steps.map((step) => {
    const status = records.get(step.id)?.status;
    let progress: ProgressStatus = "pending";
    if (status === "done" || status === "skipped") progress = "completed";
    else if (status === "running" || status === "waiting") progress = "in_progress";
    else if (status === "failed") progress = "failed";
    return { id: step.id, content: step.title, status: progress };
  });
}

/** The run header's status, naming the step when it matters. */
export function workflowRunLabel(workflow: Workflow, run: ScheduledRun): string | null {
  const index = (status: string) =>
    workflow.steps.findIndex((step) => run.steps.some((r) => r.step_id === step.id && r.status === status));
  if (run.status === "failed") {
    const failed = index("failed");
    return failed >= 0 ? `Stopped at step ${failed + 1}` : "Stopped";
  }
  if (run.status === "needs_approval") {
    const waiting = index("waiting");
    return waiting >= 0 ? `Waiting at step ${waiting + 1}` : "Needs approval";
  }
  return null;
}
