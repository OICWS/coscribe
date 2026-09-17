import type { LogItem } from "../state/reducer";

type ToolOrApprovalItem = Extract<LogItem, { kind: "tool" | "approval" }>;
type UserItem = Extract<LogItem, { kind: "user" }>;
type AgentItem = Extract<LogItem, { kind: "agent" }>;

export interface Turn {
  kind: "turn";
  id: string;
  /** Every item from (and including) the triggering user message up to
   * but not including the next one -- the full user-message-to-next-
   * user-message span this app's own "turn" concept refers to (see
   * ROADMAP.md's Phase 8am item 7). Rendered via the exact same
   * groupToolRuns pass every item list already went through, just scoped
   * to one turn instead of the whole thread. */
  items: LogItem[];
  /** null only for a leading run of items with no preceding user message
   * at all -- doesn't happen in practice (every real thread starts with
   * a user message), handled anyway so this function stays total. */
  userItem: UserItem | null;
  /** The last agent reply in this turn, if the turn has produced one yet
   * -- what the per-turn copy button copies, and what marks the turn as
   * "done" (present and not still streaming) for the rewind/copy footer
   * to render at all. */
  finalAgentItem: AgentItem | null;
  /** An unresolved approval blocks both editing and (by the same real
   * backend rule -- see handle_edit_message's own check) rewinding. */
  hasPendingApproval: boolean;
}

/** Splits a flat LogItem[] into per-turn spans -- a coarser grouping than
 * groupToolRuns' consecutive-tool-call runs, used to place one copy
 * button and one rewind control at the bottom of each complete turn
 * instead of one copy button per agent message. */
export function groupTurns(items: LogItem[]): Turn[] {
  const turns: Turn[] = [];
  let current: LogItem[] = [];
  let currentUserItem: UserItem | null = null;

  const flush = () => {
    if (current.length === 0) return;
    let finalAgentItem: AgentItem | null = null;
    let hasPendingApproval = false;
    for (const item of current) {
      if (item.kind === "agent") finalAgentItem = item;
      if (item.kind === "approval" && item.status === "pending") hasPendingApproval = true;
    }
    turns.push({
      kind: "turn",
      id: currentUserItem ? `turn-${currentUserItem.id}` : `turn-lead-${current[0].id}`,
      items: current,
      userItem: currentUserItem,
      finalAgentItem,
      hasPendingApproval,
    });
    current = [];
    currentUserItem = null;
  };

  for (const item of items) {
    if (item.kind === "user") {
      flush();
      currentUserItem = item;
    }
    current.push(item);
  }
  flush();
  return turns;
}

export interface ToolRunGroup {
  kind: "tool_run";
  id: string;
  items: ToolOrApprovalItem[];
}

export type TranscriptEntry = LogItem | ToolRunGroup;

/** Render-time-only grouping pass -- no reducer/wire changes. Coalesces
 * every consecutive run of tool/approval items (a "run") between user/
 * agent/system messages into one ToolRunGroup, including a run of just
 * one -- always through ToolRunGroupView's own chevron/summary/expand
 * treatment, never ToolCallRow's standalone bordered-card look (real,
 * live-reported complaint: an isolated tool call between two agent
 * replies rendered as an inconsistent box next to every grouped run's
 * plain summary line, "完全不一致" against the reference UI, which
 * treats a single tool call exactly like a group of one). */
export function groupToolRuns(items: LogItem[]): TranscriptEntry[] {
  const result: TranscriptEntry[] = [];
  let current: ToolOrApprovalItem[] = [];

  const flush = () => {
    if (current.length === 0) return;
    result.push({ kind: "tool_run", id: `run-${current[0].id}`, items: current });
    current = [];
  };

  for (const item of items) {
    if (item.kind === "tool" || item.kind === "approval") {
      current.push(item);
    } else {
      flush();
      result.push(item);
    }
  }
  flush();
  return result;
}

/** A run is "live" (must render expanded, not collapsed) while it holds an
 * approval the user hasn't answered yet -- an approval-required action
 * must never be hidden behind a disclosure the user has to think to
 * open. */
export function groupHasPendingApproval(group: ToolRunGroup): boolean {
  return group.items.some((item) => item.kind === "approval" && item.status === "pending");
}

type ArgRecord = Record<string, unknown>;

function str(args: ArgRecord, key: string): string | undefined {
  const value = args[key];
  return typeof value === "string" && value ? value : undefined;
}

function basename(path: string): string {
  return path.split(/[/\\]/).pop() || path;
}

function fileArg(args: ArgRecord): string | undefined {
  const path = str(args, "path") ?? str(args, "file_path");
  return path ? basename(path) : undefined;
}

/** A summary split into a plain verb phrase and an optional "object" (a
 * filename, query, or other identifier) the UI renders as an emphasized
 * inline chip -- e.g. verb "Wrote", object "deck.pptx" -- rather than one
 * flat string, so a row can visually distinguish "what happened" from
 * "what it happened to" the way Claude Code's own transcript rows do.
 * `glue` is the text between them when both are present -- a space for
 * the common "Verb object" phrasing ("Wrote deck.pptx"), ": " for the
 * handful that read better as "Verb: object" (a query or question).
 * `diffStat`, when present, is a real line-count from the tool's own
 * result (write_file/edit_file/edit_file_batch -- see files.py's
 * _line_diff_stat) -- never estimated client-side. */
export interface SummaryParts {
  verb: string;
  object: string | null;
  glue?: string;
  diffStat?: { added: number; removed: number };
  /** This label represents one call that itself errored (see `isFailure`
   * below) -- rendered as red text for the whole label, not just a
   * marker. */
  failed?: boolean;
  /** This label represents an *aggregated* run of several calls (see
   * summarizeGroupParts' command-run collapsing) -- how many of them
   * errored, rendered as a red "(N failed)" suffix. Mutually exclusive
   * with `failed` in practice: an aggregated clause is never also a
   * single failed item. */
  failedCount?: number;
}

/** The one generic, works-for-every-tool failure signal this app has --
 * straight off the checkpointed ToolMessage's own `.status` field (see
 * wire.ts's ToolResultEvent/HistoryEntry, set server-side by agent.py's
 * _CatchToolErrorsMiddleware whenever the tool's Python body raised).
 * Deliberately NOT inferred by sniffing each tool's own result shape
 * (e.g. a command's exit_code) -- that would only cover the tools this
 * file happens to special-case, where this covers all of them, including
 * a failed write_file/edit_file/read_file. `undefined` (not yet resolved,
 * or a denied approval that never ran) reads as "not a failure", same as
 * a still-pending item never showing a diff stat either. */
export function isFailure(item: ToolOrApprovalItem): boolean {
  return item.isError === true;
}

/** run_python_script/run_node_script are the two tools a user would call
 * "running a command" in the Claude-Code-web sense the reference UI this
 * matches uses -- run_background_script/check_background_task have their
 * own distinct "started"/"checked" semantics and aren't folded in here. */
const COMMAND_TOOL_NAMES = new Set(["run_python_script", "run_node_script"]);

/** Pulls `{lines_added, lines_removed}` off a tool result if present --
 * only write_file/edit_file/edit_file_batch's results carry these fields
 * today (see files.py), so this doubles as the "does this call have a
 * diff stat to show" check; no allowlist of tool names needed, any tool
 * whose result happens to carry both fields gets a badge for free. Null
 * when both counts are 0 (nothing actually changed -- e.g. write_file
 * writing back identical content) since a "+0-0" badge would just be
 * noise. */
function diffStatOf(result: unknown): { added: number; removed: number } | null {
  if (!result || typeof result !== "object") return null;
  const added = (result as Record<string, unknown>).lines_added;
  const removed = (result as Record<string, unknown>).lines_removed;
  if (typeof added !== "number" || typeof removed !== "number") return null;
  if (added === 0 && removed === 0) return null;
  return { added, removed };
}

/** Exact-name handlers for the common built-in tools -- gives Claude-Code-
 * style specificity ("Wrote deck.pptx", "Ran a command") for the tools a
 * user actually sees most. Anything not listed here (a less common
 * built-in, or a dynamically-named MCP connector tool like
 * "playwright_browser_navigate") falls through to summarizeToolNameParts's
 * generic prefix table below, not a hardcoded entry for all ~35+ tools. */
const TOOL_SUMMARIES: Record<string, (args: ArgRecord) => SummaryParts> = {
  run_python_script: () => ({ verb: "Ran a command", object: null }),
  run_node_script: () => ({ verb: "Ran a command", object: null }),
  run_background_script: (a) => ({
    verb: "Started a background script",
    object: str(a, "description") ?? null,
    glue: ": ",
  }),
  check_background_task: () => ({ verb: "Checked a background task", object: null }),
  kill_background_task: () => ({ verb: "Killed a background task", object: null }),
  wake_on_task: (a) => ({
    verb: "Waiting on a background task",
    object: str(a, "reason") ?? null,
    glue: ": ",
  }),
  write_docx: (a) => ({ verb: "Wrote", object: fileArg(a) ?? "a document" }),
  write_pdf: (a) => ({ verb: "Wrote", object: fileArg(a) ?? "a PDF" }),
  write_xlsx: (a) => ({ verb: "Wrote", object: fileArg(a) ?? "a spreadsheet" }),
  write_pptx: (a) => ({ verb: "Wrote", object: fileArg(a) ?? "a deck" }),
  write_file: (a) => ({ verb: "Wrote", object: fileArg(a) ?? "a file" }),
  edit_file: (a) => ({ verb: "Edited", object: fileArg(a) ?? "a file" }),
  edit_file_batch: (a) => ({ verb: "Edited", object: fileArg(a) ?? "a file" }),
  read_docx: (a) => ({ verb: "Read", object: fileArg(a) ?? "a document" }),
  read_pdf: (a) => ({ verb: "Read", object: fileArg(a) ?? "a PDF" }),
  read_xlsx: (a) => ({ verb: "Read", object: fileArg(a) ?? "a spreadsheet" }),
  read_pptx: (a) => ({ verb: "Read", object: fileArg(a) ?? "a deck" }),
  read_file: (a) => ({ verb: "Read", object: fileArg(a) ?? "a file" }),
  list_files: () => ({ verb: "Listed files", object: null }),
  search_files: (a) => ({ verb: "Searched files", object: str(a, "query") ?? null, glue: ": " }),
  search_pdf: (a) => ({ verb: "Searched PDF", object: str(a, "query") ?? null, glue: ": " }),
  search_images: (a) => ({ verb: "Searched images", object: str(a, "query") ?? null, glue: ": " }),
  web_search: (a) => ({ verb: "Searched the web", object: str(a, "query") ?? null, glue: ": " }),
  download_image: (a) => ({ verb: "Downloaded", object: fileArg(a) ?? "an image" }),
  set_pptx_background_image: () => ({ verb: "Set a slide background image", object: null }),
  add_pptx_scrim: () => ({ verb: "Added a text-legibility overlay", object: null }),
  add_pptx_chart: () => ({ verb: "Added a chart", object: null }),
  add_pptx_image: () => ({ verb: "Added an image", object: null }),
  add_xlsx_chart: () => ({ verb: "Added a chart", object: null }),
  format_xlsx_cells: () => ({ verb: "Formatted cells", object: null }),
  recalc_xlsx: (a) => ({ verb: "Recalculated", object: fileArg(a) ?? "a spreadsheet" }),
  task_create: (a) => ({ verb: "Added a task", object: str(a, "content") ?? null, glue: ": " }),
  task_update: () => ({ verb: "Updated a task", object: null }),
  task_list: () => ({ verb: "Listed tasks", object: null }),
  remember: () => ({ verb: "Saved a memory", object: null }),
  ask_user_question: (a) => ({ verb: "Asked", object: str(a, "question") ?? "a question", glue: ": " }),
  spawn_agent: () => ({ verb: "Delegated to a sub-agent", object: null }),
  review_work: () => ({ verb: "Asked a reviewer to check the work", object: null }),
  load_skill: (a) => ({ verb: "Loaded skill", object: str(a, "name") ?? null }),
  run_workflow: (a) => ({ verb: "Ran workflow", object: str(a, "name") ?? null }),
};

const PREFIX_VERBS: [prefix: string, verb: string][] = [
  ["write_", "Wrote"],
  ["read_", "Read"],
  ["search_", "Searched"],
  ["download_", "Downloaded"],
  ["list_", "Listed"],
  ["add_", "Added"],
  ["set_", "Updated"],
  ["delete_", "Deleted"],
  ["remove_", "Removed"],
  ["install_", "Installed"],
  ["uninstall_", "Uninstalled"],
  ["create_", "Created"],
  ["update_", "Updated"],
  ["run_", "Ran"],
];

/** Generic fallback for any tool not in TOOL_SUMMARIES -- a small prefix
 * table (covers most naming conventions used across this codebase's
 * ~35+ built-ins) plus a safe last resort: the bare tool name, with no
 * object to emphasize. Also handles MCP connector tools, whose names are
 * dynamic (server-provided) and can't be enumerated ahead of time. */
function summarizeToolNameParts(toolName: string, args: ArgRecord): SummaryParts {
  for (const [prefix, verb] of PREFIX_VERBS) {
    if (toolName.startsWith(prefix)) {
      const object = fileArg(args) ?? str(args, "query") ?? str(args, "name");
      if (object) return { verb, object };
      const suffix = toolName.slice(prefix.length).replace(/_/g, " ");
      return { verb: `${verb} ${suffix}`, object: null };
    }
  }
  return { verb: toolName, object: null };
}

function partsFor(item: ToolOrApprovalItem): SummaryParts {
  const base = TOOL_SUMMARIES[item.toolName]?.(item.arguments) ?? summarizeToolNameParts(item.toolName, item.arguments);
  const diffStat = diffStatOf(item.result);
  let parts = diffStat ? { ...base, diffStat } : base;
  if (isFailure(item)) {
    // "Failed to run" specifically for the command tools, matching the
    // reference UI's own wording exactly -- every other tool keeps its
    // normal verb ("Wrote ROADMAP.md", "Read a file", ...), just rendered
    // in red (`failed: true` below) rather than rewritten per tool, since
    // a bespoke failure verb for all ~35+ built-ins isn't worth it for a
    // case the reference doesn't actually show.
    parts = {
      ...parts,
      verb: COMMAND_TOOL_NAMES.has(item.toolName) ? "Failed to run" : parts.verb,
      failed: true,
    };
  }
  if (item.kind === "approval" && item.status === "pending") {
    return { ...parts, verb: `Approve: ${parts.verb}` };
  }
  return parts;
}

/** Structured form -- verb plus an optional emphasized object -- for a
 * caller (ToolCallRow's collapsed label, a ToolRunGroup's header) that
 * wants to render the object as a distinct inline chip rather than plain
 * text. */
export function summarizeItemParts(item: ToolOrApprovalItem): SummaryParts {
  return partsFor(item);
}

/** Flat-string form of summarizeItemParts, for places that just need
 * plain text (a tooltip, a title attribute) with no emphasis to render. */
export function summarizeItem(item: ToolOrApprovalItem): string {
  const { verb, object, glue = " " } = partsFor(item);
  return object ? `${verb}${glue}${object}` : verb;
}

const SUMMARY_CAP = 3;

export interface GroupHeaderParts {
  /** Each shown item's parts, capped at SUMMARY_CAP -- render joined by
   * ", " with each object emphasized, same as a single row's own label. */
  shown: SummaryParts[];
  /** Count of additional items past the cap, 0 if none ("and 2 more"). */
  more: number;
}

/** Structured headline for a ToolRunGroup's collapsed header -- the same
 * per-item verb/object split ToolCallRow uses, so the header can also
 * emphasize each object inline ("Wrote `deck.pptx`, searched `images`"),
 * each with its own diff-stat/failure styling, capped past a few clauses
 * ("and 2 more"). A run of two or more consecutive command calls
 * (run_python_script/run_node_script -- see COMMAND_TOOL_NAMES) collapses
 * into one "Ran N commands" clause with a "(M failed)" suffix if any of
 * them errored, matching the reference UI's own "Ran 3 commands (1
 * failed)" convention -- a single command among non-command items still
 * gets its own normal "Ran a command"/"Failed to run" clause, same as
 * before. The *expanded* per-step list (ToolRunGroupView) is unaffected
 * -- it always renders every individual item, never this aggregation. */
export function summarizeGroupParts(items: ToolOrApprovalItem[]): GroupHeaderParts {
  const clauses: SummaryParts[] = [];
  let run: ToolOrApprovalItem[] = [];

  const flushRun = () => {
    if (run.length === 0) return;
    if (run.length === 1) {
      clauses.push(partsFor(run[0]));
    } else {
      const failedCount = run.filter(isFailure).length;
      clauses.push({
        verb: `Ran ${run.length} commands`,
        object: null,
        failedCount: failedCount > 0 ? failedCount : undefined,
      });
    }
    run = [];
  };

  for (const item of items) {
    if (COMMAND_TOOL_NAMES.has(item.toolName)) {
      run.push(item);
    } else {
      flushRun();
      clauses.push(partsFor(item));
    }
  }
  flushRun();

  if (clauses.length <= SUMMARY_CAP) return { shown: clauses, more: 0 };
  return { shown: clauses.slice(0, SUMMARY_CAP), more: clauses.length - SUMMARY_CAP };
}
