import type { ThreadStatus } from "../types/session";

export const STATUS_LABEL: Record<ThreadStatus, string> = {
  working: "Working",
  needs_input: "Waiting for you",
  ready: "Ready for review",
  idle: "Completed",
};

export type ThreadFilter = "all" | ThreadStatus | "archived";

/** In the order the filter menu lists them; "idle" is what the menu calls
 * Completed. */
export const FILTERS: { value: ThreadFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "needs_input", label: "Needs input" },
  { value: "ready", label: "Ready for review" },
  { value: "working", label: "Working" },
  { value: "idle", label: "Completed" },
  { value: "archived", label: "Archived" },
];

interface Filterable {
  status: ThreadStatus;
  archived: boolean;
  preview: string;
  thread_id: string;
}

/** Archived conversations show only under Archived; every other filter
 * leaves them out. */
export function matchesFilter(thread: Filterable, filter: ThreadFilter, query: string): boolean {
  if (filter === "archived" ? !thread.archived : thread.archived) return false;
  if (filter !== "all" && filter !== "archived" && thread.status !== filter) return false;
  const text = query.trim().toLowerCase();
  return !text || (thread.preview || thread.thread_id).toLowerCase().includes(text);
}

export interface ThreadSection<T> {
  /** null is the ungrouped section. */
  group: string | null;
  threads: T[];
}

/** Named groups in their saved order, then the ungrouped. Under a filter
 * or search an empty group isn't worth showing. */
export function sectionThreads<T extends { group: string | null }>(
  threads: T[],
  groups: string[],
  hideEmpty: boolean,
): ThreadSection<T>[] {
  const known = new Set(groups);
  // A group a conversation names but the list lost (deleted elsewhere)
  // still shows, rather than hiding its conversations.
  const extra = [...new Set(threads.map((t) => t.group).filter((g): g is string => !!g && !known.has(g)))];
  const sections: ThreadSection<T>[] = [...groups, ...extra].map((group) => ({
    group,
    threads: threads.filter((t) => t.group === group),
  }));
  sections.push({ group: null, threads: threads.filter((t) => !t.group) });
  return hideEmpty ? sections.filter((s) => s.threads.length > 0) : sections;
}
