import type { ScheduledRun } from "../types/settings";
import type { StepRecord, StepStatus, Workflow, WorkflowStep } from "../types/workflow";
import { loopProgress } from "./workflowProgress";
import { placeSteps } from "./workflowTree";

/** A step's state on the run's canvas. "not_taken" is a step in a branch arm
 * the run went past; "stopped" a step that was running when the run stopped. */
export type NodeStatus = StepStatus | "not_reached" | "not_taken" | "stopped";

export interface NodeRunState {
  status: NodeStatus;
  /** For a loop, how many of its items are through; for a step inside one,
   * how many of its passes finished, of how many the loops around it hold
   * (null while that isn't known yet). */
  passes?: { done: number; total: number | null };
  /** For a branch, the arms its finished passes took. */
  arms?: string[];
}

// The order a step with several passes is shown in: what needs the person
// first, then what is still going, then how the rest went.
const WORST: NodeStatus[] = ["failed", "waiting", "stopped", "running", "done", "skipped"];

function worst(statuses: NodeStatus[]): NodeStatus {
  return WORST.find((status) => statuses.includes(status)) ?? "not_reached";
}

function armOf(record: StepRecord): string | null {
  const arm = (record.output as { arm?: unknown } | null | undefined)?.arm;
  return record.status === "done" && typeof arm === "string" ? arm : null;
}

/** Steps under an arm no pass of its branch took, so the run can't reach
 * them any more. Inside a loop each pass can take either arm, so that is
 * only known once every pass of the loop is through. */
function notTaken(workflow: Workflow, byStep: Map<string, StepRecord[]>): Set<string> {
  const skipped = new Set<string>();
  const finished = (loop: string | null) =>
    loop === null || (byStep.get(loop) ?? []).every((record) => record.status === "done");
  const visit = (steps: WorkflowStep[], loop: string | null) => {
    for (const step of steps) {
      if (step.kind === "loop") visit(step.steps, step.id);
      if (step.kind !== "branch") continue;
      const records = byStep.get(step.id) ?? [];
      const taken = new Set(records.map(armOf).filter((arm) => arm !== null));
      const decided = records.some((record) => record.status === "done") && finished(loop);
      for (const [arm, list] of [
        ["then", step.then],
        ["otherwise", step.otherwise],
      ] as const) {
        if (decided && !taken.has(arm)) {
          for (const placed of placeSteps(list)) skipped.add(placed.step.id);
        } else {
          visit(list, loop);
        }
      }
    }
  };
  visit(workflow.steps, null);
  return skipped;
}

/** A loop's items through, over all its passes. */
function loopItems(records: StepRecord[]): NodeRunState["passes"] {
  let done = 0;
  let total = 0;
  for (const progress of records.map(loopProgress)) {
    done += progress?.done ?? 0;
    total += progress?.total ?? 0;
  }
  return { done, total };
}

/** A step inside a loop: its passes finished, of all the passes the
 * innermost loop's records hold between them (one record per pass of the
 * loops around that one). A step under a branch in the loop runs only on
 * the passes that take its arm, so it has no such total. */
function passesInside(statuses: NodeStatus[], loopRecords: StepRecord[], direct: boolean): NodeRunState["passes"] {
  let total: number | null = direct && loopRecords.length > 0 ? 0 : null;
  for (const record of loopRecords) {
    const items = loopProgress(record)?.total;
    total = total === null || items === undefined ? null : total + items;
  }
  return { done: statuses.filter((s) => s === "done" || s === "skipped").length, total };
}

/** Each step's state in `run`, for drawing the run on the workflow's canvas.
 * A step inside a loop gets the state of its passes taken together. */
export function runMap(workflow: Workflow, run: ScheduledRun): Map<string, NodeRunState> {
  const byStep = new Map<string, StepRecord[]>();
  for (const record of run.steps) byStep.set(record.step_id, [...(byStep.get(record.step_id) ?? []), record]);
  const going = run.status === "running";
  const unreachable = notTaken(workflow, byStep);
  const states = new Map<string, NodeRunState>();
  for (const { step, loops, list } of placeSteps(workflow.steps)) {
    const records = byStep.get(step.id) ?? [];
    const statuses = records.map(
      (record): NodeStatus => (record.status === "running" && !going ? "stopped" : record.status),
    );
    const status: NodeStatus =
      records.length > 0 ? worst(statuses) : unreachable.has(step.id) ? "not_taken" : "not_reached";
    const state: NodeRunState = { status };
    const innermost = loops[loops.length - 1];
    if (step.kind === "branch")
      state.arms = ["then", "otherwise"].filter((arm) => records.some((r) => armOf(r) === arm));
    if (step.kind === "loop" && records.length > 0) state.passes = loopItems(records);
    else if (innermost) state.passes = passesInside(statuses, byStep.get(innermost) ?? [], list.parent === innermost);
    states.set(step.id, state);
  }
  return states;
}

/** The pass of the loop at `path` to show so that `stepId` is in view: the
 * last one it ran in, or null when it never ran under this loop. */
export function passShowing(run: ScheduledRun, stepId: string, path: number[]): number | null {
  let found: number | null = null;
  for (const record of run.steps) {
    const { iteration } = record;
    if (record.step_id !== stepId || iteration.length <= path.length) continue;
    if (path.every((index, depth) => iteration[depth] === index)) found = Math.max(found ?? 0, iteration[path.length]);
  }
  return found;
}
