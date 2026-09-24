import type { ScheduledRun } from "../types/settings";
import type { LoopStep, StepRecord, StepStatus, Workflow, WorkflowStep } from "../types/workflow";
import { isBlock, placeSteps } from "./workflowTree";

export type ProgressStatus = "pending" | "in_progress" | "completed" | "failed";

export interface ProgressItem {
  id: string;
  content: string;
  status: ProgressStatus;
}

// -- Records ----------------------------------------------------------------------

/** A step's record is one per pass of the loops around it. */
export function recordKey(stepId: string, iteration: number[] = []): string {
  return `${stepId}@${iteration.join(".")}`;
}

export function recordMap(run: ScheduledRun): Map<string, StepRecord> {
  return new Map(run.steps.map((record) => [recordKey(record.step_id, record.iteration), record]));
}

export interface LoopProgress {
  done: number;
  total: number;
  /** A short name per item, in order. */
  items: string[];
  collected?: unknown;
}

export function loopProgress(record: StepRecord | undefined): LoopProgress | null {
  const output = record?.output;
  if (typeof output !== "object" || output === null || !("total" in output)) return null;
  const { done, total, items, collected } = output as Record<string, unknown>;
  return {
    done: typeof done === "number" ? done : 0,
    total: typeof total === "number" ? total : 0,
    items: Array.isArray(items) ? items.map(String) : [],
    collected,
  };
}

function descendants(step: WorkflowStep): WorkflowStep[] {
  return placeSteps([step])
    .slice(1)
    .map((placed) => placed.step);
}

/** How one pass of a loop went, from the records of the steps inside it. */
export function passStatus(
  loop: LoopStep,
  path: number[],
  records: Map<string, StepRecord>,
): StepStatus | "not_reached" {
  const statuses: StepStatus[] = [];
  for (const placed of placeSteps(loop.steps)) {
    // Only the steps directly in this pass; deeper loops add their own index.
    const depth = placed.loops.length;
    const record = depth === 0 ? records.get(recordKey(placed.step.id, path)) : undefined;
    if (record) statuses.push(record.status);
  }
  if (statuses.length === 0) return "not_reached";
  for (const status of ["failed", "waiting", "running"] as const) {
    if (statuses.includes(status)) return status;
  }
  return "done";
}

/** A block whose own record says failed or waiting because of a step
 * inside it -- that step shows the problem, not the block. */
export function stoppedInside(step: WorkflowStep, run: ScheduledRun): boolean {
  if (!isBlock(step)) return false;
  const inside = new Set(descendants(step).map((s) => s.id));
  return run.steps.some((r) => inside.has(r.step_id) && (r.status === "failed" || r.status === "waiting"));
}

// -- Side panel and header ----------------------------------------------------------

function progressOf(status: StepStatus | undefined): ProgressStatus {
  if (status === "done" || status === "skipped") return "completed";
  if (status === "running" || status === "waiting") return "in_progress";
  if (status === "failed") return "failed";
  return "pending";
}

/** A workflow run as the side panel's progress row: one entry per step
 * that runs once -- a loop is one entry counting its items, and only the
 * arm a branch took is listed. */
export function workflowProgress(workflow: Workflow, run: ScheduledRun): ProgressItem[] {
  const records = recordMap(run);
  const items: ProgressItem[] = [];
  const visit = (steps: WorkflowStep[]) => {
    for (const step of steps) {
      const record = records.get(recordKey(step.id));
      if (step.kind === "loop") {
        const progress = loopProgress(record);
        const counted = progress ? ` · ${progress.done} of ${progress.total}` : "";
        items.push({ id: step.id, content: `${step.title}${counted}`, status: progressOf(record?.status) });
        continue;
      }
      items.push({ id: step.id, content: step.title, status: progressOf(record?.status) });
      if (step.kind === "branch" && record?.status === "done") {
        const arm = (record.output as { arm?: string } | null)?.arm;
        visit(arm === "then" ? step.then : step.otherwise);
      }
    }
  };
  visit(workflow.steps);
  return items;
}

/** The run header's status, naming the step when it matters. */
export function workflowRunLabel(workflow: Workflow, run: ScheduledRun): string | null {
  const placed = placeSteps(workflow.steps);
  const where = (status: StepStatus) => {
    const record = [...run.steps]
      .reverse()
      .find((r) => r.status === status && !placed.find((p) => p.step.id === r.step_id && isBlock(p.step)));
    const spot = record ? placed.find((p) => p.step.id === record.step_id) : undefined;
    if (!record || !spot) return null;
    const pass = record.iteration?.length ? `, item ${record.iteration[record.iteration.length - 1] + 1}` : "";
    return `step ${spot.number}${pass}`;
  };
  if (run.status === "failed") {
    const at = where("failed");
    return at ? `Stopped at ${at}` : "Stopped";
  }
  if (run.status === "needs_approval") {
    const at = where("waiting");
    return at ? `Waiting at ${at}` : "Needs approval";
  }
  return null;
}
