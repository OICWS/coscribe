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

// Characters of room, counting a Chinese, Japanese or Korean one as two.
const LABEL_WIDTH = 40;

const isWide = (char: string) => /[\u1100-\u11ff\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\uff00-\uff60]/.test(char);

/** A name for a conversation that has no title yet: the first line of its
 * first message, cut short, so that a long message doesn't become a long name. */
export function shortLabel(text: string): string {
  const line = (text.split("\n").find((l) => l.trim() !== "") ?? "").replace(/\s+/g, " ").trim();
  let width = 0;
  let end = 0;
  const chars = [...line];
  for (const char of chars) {
    width += isWide(char) ? 2 : 1;
    if (width > LABEL_WIDTH) break;
    end += 1;
  }
  if (end === chars.length) return line;
  const cut = chars.slice(0, end).join("");
  const space = cut.lastIndexOf(" ");
  // At a word, unless that would throw most of it away.
  return `${(space > cut.length * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

/** What a conversation is called in the sidebar, its header and its dialogs. */
export const threadLabel = (thread: { preview: string; thread_id: string }) =>
  shortLabel(thread.preview) || thread.thread_id;

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
