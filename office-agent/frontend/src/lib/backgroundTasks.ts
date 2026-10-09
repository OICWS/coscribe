import type { BackgroundScriptTask, SubAgentTask } from "../types/session";
import { getBackgroundScripts, getSubAgentTasks } from "./rest";

/** One row of the Background tasks panel: a delegated sub-agent or a script
 * started with run_background_script. */
export type BackgroundEntry =
  | { kind: "agent"; task: SubAgentTask }
  | { kind: "script"; task: BackgroundScriptTask };

export const entryId = (entry: BackgroundEntry) => entry.task.task_id;

export const isEntryActive = (entry: BackgroundEntry) =>
  entry.task.status === "running" || entry.task.status === "needs_approval";

/** What an entry is, in a word: "Agent", "Python", "Node". */
export function entryLabel(entry: BackgroundEntry): string {
  if (entry.kind === "agent") return "Agent";
  return entry.task.language === "node" ? "Node" : "Python";
}

/** How a finished script ended, in a few words. */
export function scriptOutcome(task: BackgroundScriptTask): string {
  switch (task.status) {
    case "succeeded":
      return "Finished";
    case "failed":
      return task.exit_code === null ? "Failed" : `Failed (exit ${task.exit_code})`;
    case "timed_out":
      return "Timed out";
    case "killed":
      return "Stopped";
    case "interrupted":
      return "Interrupted";
    default:
      return "Running";
  }
}

/** This conversation's sub-agents and background scripts, oldest first. The
 * two lists are read apart so that a failing one (an older server has no
 * script endpoint) leaves the other on screen. */
export async function loadBackgroundEntries(threadId: string): Promise<BackgroundEntry[]> {
  const [agents, scripts] = await Promise.all([
    getSubAgentTasks(threadId).catch(() => [] as SubAgentTask[]),
    getBackgroundScripts(threadId).catch(() => [] as BackgroundScriptTask[]),
  ]);
  const entries: BackgroundEntry[] = [
    ...agents.map((task) => ({ kind: "agent" as const, task })),
    ...scripts.map((task) => ({ kind: "script" as const, task })),
  ];
  return entries.sort((a, b) => a.task.started_at.localeCompare(b.task.started_at));
}
