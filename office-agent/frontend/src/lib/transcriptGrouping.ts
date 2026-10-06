import type { LogItem } from "../state/reducer";
import type { Workflow } from "../types/workflow";

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

// Excludes "tool"/"approval" from the LogItem side, not just LogItem |
// ToolRunGroup -- groupToolRuns below now always wraps every tool/
// approval run into a ToolRunGroup (even a run of one), so a bare
// tool/approval item can genuinely never appear in its output anymore.
// This is what lets ChatLog.tsx's own render switch narrow down to
// LogItemView's stricter "user" | "agent" | "system" prop type after
// excluding "tool_run" and "question" -- real build failure caught this
// the first time around: `tsc --noEmit` alone didn't catch it (a looser
// invocation than this project's real build command), but `tsc -b`
// (what `npm run build` actually runs) correctly rejected the type
// mismatch once TranscriptEntry's own type no longer reflected the
// runtime invariant.
/** A workflow the model drafted with draft_workflow, shown as a card to
 * review instead of as a tool call. */
export interface WorkflowDraftEntry {
  kind: "workflow_draft";
  id: string;
  name: string;
  workflow: Workflow;
  notes: string[];
  workspace: string | null;
  /** A revision of this saved task's workflow, not a new one. */
  triggerId: string | null;
  /** What the revision changes, one line each. */
  changes: string[];
  /** The id test_workflow and revise_workflow know it by. */
  draftId: string | null;
}

export interface WorkflowTestStep {
  id: string;
  title: string;
  /** A StepRecord status, or "not reached". */
  status: string;
  seconds: number | null;
  output: string | null;
  error: string | null;
}

export interface WorkflowTestFile {
  path: string;
  size: number;
  rows?: number;
  columns?: number;
}

/** What test_workflow reported. */
export interface WorkflowTestResult {
  status: "passed" | "failed" | "stopped_at_approval";
  seconds: number;
  steps: WorkflowTestStep[];
  files: WorkflowTestFile[];
  failed_step: string | null;
  error: string | null;
  /** A file under GET /api/screenshots/: the page the test ended on. */
  screenshot?: string | null;
}

/** A draft's latest test: its result, or running with none yet. */
export interface DraftTest {
  result: WorkflowTestResult | null;
  running: boolean;
}

function parsedResult(result: unknown): Record<string, unknown> | null {
  let value = result;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return null;
    }
  }
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : null;
}

/** A script the folder guard stopped from writing: the path it tried, and
 * the folder to offer adding (none when that would be a whole drive). */
export interface BlockedWrite {
  path: string;
  folder: string | null;
}

export function blockedWriteOf(item: ToolOrApprovalItem): BlockedWrite | null {
  if (item.result === undefined) return null;
  const data = parsedResult(item.result);
  if (data === null || typeof data.blocked_write !== "string") return null;
  return { path: data.blocked_write, folder: typeof data.blocked_folder === "string" ? data.blocked_folder : null };
}

/** Each draft's latest test_workflow call among `items`, by draft id. A
 * call with no result is running only while a turn is: Stop cancels it
 * without one. */
export function workflowTests(items: LogItem[], turnInFlight: boolean): Map<string, DraftTest> {
  const tests = new Map<string, DraftTest>();
  const lastUser = items.map((i) => i.kind).lastIndexOf("user");
  for (const [index, item] of items.entries()) {
    if ((item.kind !== "tool" && item.kind !== "approval") || item.toolName !== "test_workflow") continue;
    const draftId = item.arguments.draft_id;
    if (typeof draftId !== "string" || !draftId) continue;
    if (item.result === undefined) {
      const running = turnInFlight && index > lastUser && (item.kind === "tool" || item.status === "approved");
      tests.set(draftId, { result: null, running });
      continue;
    }
    const data = parsedResult(item.result);
    const ran = data !== null && Array.isArray(data.steps) && typeof data.status === "string";
    tests.set(draftId, {
      result: ran ? ({ files: [], ...data } as unknown as WorkflowTestResult) : null,
      running: false,
    });
  }
  return tests;
}

/** The newest workflow draft among `items`, as its card shows it. */
export function latestWorkflowDraft(items: LogItem[]): WorkflowDraftEntry | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    const draft = item.kind === "tool" ? workflowDraftOf(item) : null;
    if (draft) return draft;
  }
  return null;
}

export type TranscriptEntry = Exclude<LogItem, ToolOrApprovalItem> | ToolRunGroup | WorkflowDraftEntry;

/** The id of the newest workflow draft among `items` -- a card for any
 * earlier one is superseded. */
export function latestWorkflowDraftId(items: LogItem[]): string | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.kind === "tool" && workflowDraftOf(item)) return item.id;
  }
  return null;
}

function workflowDraftOf(item: ToolOrApprovalItem): WorkflowDraftEntry | null {
  if (item.kind !== "tool" || item.result === undefined) return null;
  if (item.toolName !== "draft_workflow" && item.toolName !== "revise_workflow") return null;
  const draft = parsedResult(item.result);
  if (draft === null) return null;
  if (draft.status !== "drafted" || typeof draft.workflow !== "object" || draft.workflow === null) return null;
  return {
    kind: "workflow_draft",
    id: item.id,
    name: typeof draft.name === "string" ? draft.name : "Untitled workflow",
    workflow: draft.workflow as Workflow,
    notes: Array.isArray(draft.notes) ? draft.notes.map(String) : [],
    workspace: typeof draft.workspace === "string" ? draft.workspace : null,
    triggerId: typeof draft.trigger_id === "string" ? draft.trigger_id : null,
    changes: Array.isArray(draft.changes) ? draft.changes.map(String) : [],
    draftId: typeof draft.draft_id === "string" ? draft.draft_id : null,
  };
}

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
    const drafted = item.kind === "tool" ? workflowDraftOf(item) : null;
    if (drafted) {
      flush();
      result.push(drafted);
    } else if (item.kind === "tool" || item.kind === "approval") {
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

function firstLine(text: string | undefined): string | null {
  const line = text?.split("\n").find((l) => l.trim());
  return line ? line.trim() : null;
}

/** ask_user_question's first question; an older call asked one directly. */
function firstQuestion(args: ArgRecord): string | undefined {
  const questions = args.questions;
  if (!Array.isArray(questions)) return str(args, "question");
  const first: unknown = questions[0];
  const text = first && typeof first === "object" ? (first as Record<string, unknown>).question : undefined;
  if (typeof text !== "string" || !text) return undefined;
  return questions.length > 1 ? `${text} (+${questions.length - 1} more)` : text;
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
  /** Still running: the verb is in the present tense ("Reading"). */
  running?: boolean;
  /** Still running: the UI animates it. */
  shimmer?: boolean;
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
const COMMAND_TOOL_NAMES = new Set(["run_python_script", "run_node_script", "run_code_command"]);

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
  // description is a required argument on both -- "one sentence, plain
  // language, what this script does" (see scripts.py's own docstring)
  // -- real, live-reported complaint: without it, every command in a
  // group read as a bare "Ran a command" with zero way to tell them
  // apart short of expanding each one, unlike the reference UI's own
  // Background Tasks panel, which always shows what a command was for.
  run_python_script: (a) => ({ verb: "Ran a command", object: str(a, "description") ?? null, glue: ": " }),
  run_node_script: (a) => ({ verb: "Ran a command", object: str(a, "description") ?? null, glue: ": " }),
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
  add_docx_chart: () => ({ verb: "Added a chart", object: null }),
  add_docx_image: () => ({ verb: "Added an image", object: null }),
  format_xlsx_cells: () => ({ verb: "Formatted cells", object: null }),
  recalc_xlsx: (a) => ({ verb: "Recalculated", object: fileArg(a) ?? "a spreadsheet" }),
  task_create: (a) => ({ verb: "Added a task", object: str(a, "content") ?? null, glue: ": " }),
  task_update: () => ({ verb: "Updated a task", object: null }),
  task_list: () => ({ verb: "Listed tasks", object: null }),
  remember: () => ({ verb: "Saved a memory", object: null }),
  ask_user_question: (a) => ({ verb: "Asked", object: firstQuestion(a) ?? "a question", glue: ": " }),
  spawn_agent: (a) => ({ verb: "Delegated to a sub-agent", object: str(a, "description") ?? null, glue: ": " }),
  run_code_task: (a) => ({ verb: "Handed a coding task to the code module", object: str(a, "description") ?? null, glue: ": " }),
  // The code module's own commands carry the script in place of a
  // description, so the row names it by its first line.
  run_code_command: (a) => ({ verb: "Ran a command", object: firstLine(str(a, "description") || str(a, "script")), glue: ": " }),
  apply_code_change: (a) => {
    const paths = Array.isArray(a.paths) ? a.paths.filter((p): p is string => typeof p === "string") : [];
    return paths.length > 0 ? { verb: "Changed", object: paths.map(basename).join(", ") } : { verb: "Changed files", object: null };
  },
  spawn_agent_background: (a) => ({
    verb: "Started a sub-agent",
    object: str(a, "description") ?? null,
    glue: ": ",
  }),
  get_file_info: (a) => ({ verb: "Checked", object: fileArg(a) ?? "a file" }),
  sleep_until: () => ({ verb: "Scheduled a wake-up", object: null }),
  sleep_for: () => ({ verb: "Scheduled a wake-up", object: null }),
  wake_on_subagent: () => ({ verb: "Set to resume when the sub-agent finishes", object: null }),
  wake_on_event: (a) => ({ verb: "Set to resume on", object: str(a, "event_key") ?? "an event" }),
  signal_event: (a) => ({ verb: "Signaled", object: str(a, "event_key") ?? "an event" }),
  check_subagent_task: () => ({ verb: "Checked on a sub-agent", object: null }),
  list_subagent_tasks: () => ({ verb: "Listed sub-agents", object: null }),
  stop_subagent: () => ({ verb: "Stopped a sub-agent", object: null }),
  review_work: () => ({ verb: "Asked a reviewer to check the work", object: null }),
  load_skill: (a) => ({ verb: "Loaded skill", object: str(a, "name") ?? null }),
  draft_workflow: () => ({ verb: "Drafted a workflow", object: null }),
  revise_workflow: () => ({ verb: "Revised a workflow", object: null }),
  test_workflow: () => ({ verb: "Tested a workflow", object: null }),
  edit_scheduled_task: () => ({ verb: "Proposed changes to a task", object: null }),
  search_tools: (a) => ({ verb: "Searched tools", object: str(a, "query") ?? null, glue: ": " }),
  read_web_page: (a) => ({ verb: "Read", object: str(a, "url") ?? "a web page" }),
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

const PRESENT_TENSE: Record<string, string> = {
  Wrote: "Writing",
  Read: "Reading",
  Edited: "Editing",
  Listed: "Listing",
  Searched: "Searching",
  Downloaded: "Downloading",
  Added: "Adding",
  Updated: "Updating",
  Deleted: "Deleting",
  Removed: "Removing",
  Installed: "Installing",
  Uninstalled: "Uninstalling",
  Created: "Creating",
  Ran: "Running",
  Started: "Starting",
  Checked: "Checking",
  Killed: "Stopping",
  Formatted: "Formatting",
  Recalculated: "Recalculating",
  Saved: "Saving",
  Asked: "Asking",
  Delegated: "Delegating",
  Loaded: "Loading",
  Set: "Setting",
  Tested: "Testing",
  Opened: "Opening",
  Fetched: "Fetching",
  Drafted: "Drafting",
  Revised: "Revising",
  Proposed: "Proposing",
};

/** "Wrote" -> "Writing"; a verb with no known present form stays as is. */
export function presentTense(verb: string): string {
  const space = verb.indexOf(" ");
  const first = space === -1 ? verb : verb.slice(0, space);
  const present = PRESENT_TENSE[first];
  return present ? present + verb.slice(first.length) : verb;
}

/** A call counts as running while a live turn hasn't returned its result;
 * a denied or still-pending approval never ran. */
export function isRunning(item: ToolOrApprovalItem, live: boolean): boolean {
  if (!live || item.result !== undefined || item.isError !== undefined) return false;
  return item.kind === "tool" || item.status === "approved";
}

function partsFor(item: ToolOrApprovalItem, running = false): SummaryParts {
  const summarized =
    TOOL_SUMMARIES[item.toolName]?.(item.arguments) ?? summarizeToolNameParts(item.toolName, item.arguments);
  const base = running
    ? { ...summarized, verb: presentTense(summarized.verb), running: true, shimmer: true }
    : summarized;
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
export function summarizeItemParts(item: ToolOrApprovalItem, running = false): SummaryParts {
  return partsFor(item, running);
}

/** Flat-string form of summarizeItemParts, for places that just need
 * plain text (a tooltip, a title attribute) with no emphasis to render. */
export function summarizeItem(item: ToolOrApprovalItem): string {
  const { verb, object, glue = " " } = partsFor(item);
  return object ? `${verb}${glue}${object}` : verb;
}

const SUMMARY_CAP = 3;

type Category = "command" | "write" | "edit" | "read" | "search" | "fetch" | "subagent" | "connector" | "other";

const SUBAGENT_TOOL_NAMES = new Set(["spawn_agent", "review_work", "run_code_task"]);
const FETCH_TOOL_NAMES = new Set(["read_web_page", "download_image", "browser_navigate"]);
// The office-document tools that change a file; their reading, listing
// and checking siblings share the _docx/_pptx/_xlsx infix.
const OFFICE_FILE = /_(docx|pptx|xlsx)(_|$)/;
const NOT_AN_EDIT = /^(read|write|list|check|render|extract)_/;

function categoryOf(toolName: string, connectorTools: ReadonlySet<string>): Category {
  if (connectorTools.has(toolName)) return "connector";
  if (COMMAND_TOOL_NAMES.has(toolName) || toolName === "run_background_script") return "command";
  if (SUBAGENT_TOOL_NAMES.has(toolName)) return "subagent";
  if (FETCH_TOOL_NAMES.has(toolName)) return "fetch";
  if (toolName === "web_search" || (toolName.startsWith("search_") && toolName !== "search_tools")) return "search";
  if (toolName.startsWith("write_") || toolName === "convert_office_file") return "write";
  if (toolName === "edit_file" || toolName === "edit_file_batch" || toolName === "apply_code_change") return "edit";
  if (OFFICE_FILE.test(toolName) && !NOT_AN_EDIT.test(toolName)) return "edit";
  if (toolName === "list_files" || toolName === "get_file_info") return "read";
  if (toolName.startsWith("read_") && toolName !== "read_skill_file") return "read";
  return "other";
}

/** Past and present verb, and the noun counted, for each category's
 * clause ("Ran 3 commands" / "Running 3 commands"). */
const CATEGORY_WORDS: Record<Exclude<Category, "other">, [past: string, present: string, noun: string]> = {
  command: ["Ran", "Running", "command"],
  write: ["Wrote", "Writing", "file"],
  edit: ["Edited", "Editing", "file"],
  read: ["Read", "Reading", "file"],
  search: ["Searched", "Searching", "time"],
  fetch: ["Fetched", "Fetching", "page"],
  subagent: ["Finished", "Running", "sub-agent task"],
  connector: ["Used", "Using", "tool"],
};
const FILE_CATEGORIES = new Set<Category>(["write", "edit", "read"]);

/** The files a call touched: its path, or the code module's list of them. */
function filePathsArg(args: ArgRecord): string[] {
  const path = str(args, "path") ?? str(args, "file_path");
  if (path) return [path];
  return Array.isArray(args.paths) ? args.paths.filter((p): p is string => typeof p === "string" && p !== "") : [];
}

function categoryClause(category: Exclude<Category, "other">, items: ToolOrApprovalItem[], live: boolean): SummaryParts {
  if (items.length === 1) return partsFor(items[0], isRunning(items[0], live));
  const [past, present, noun] = CATEGORY_WORDS[category];
  const running = items.some((item) => isRunning(item, live));
  const failedCount = items.filter(isFailure).length;
  const stats = items.map((item) => diffStatOf(item.result)).filter((stat) => stat !== null);
  const diffStat =
    stats.length > 0
      ? { added: stats.reduce((sum, s) => sum + s.added, 0), removed: stats.reduce((sum, s) => sum + s.removed, 0) }
      : undefined;
  // A file category counts files, not calls: five edits to one deck are
  // one edited file. A call without a path counts on its own.
  let count = items.length;
  let onlyFile: string | undefined;
  if (FILE_CATEGORIES.has(category)) {
    const keys = items.flatMap((item, index) => {
      const paths = filePathsArg(item.arguments);
      return paths.length > 0 ? paths : [`#${index}`];
    });
    const paths = new Set(keys);
    count = paths.size;
    if (count === 1 && !keys[0].startsWith("#")) onlyFile = basename(keys[0]);
  }
  const verb = running ? present : past;
  const counted = `${verb} ${count} ${count === 1 ? noun : `${noun}s`}`;
  return {
    verb: onlyFile ? verb : counted,
    object: onlyFile ?? null,
    ...(diffStat && { diffStat }),
    ...(failedCount > 0 && { failedCount }),
    ...(running && { running: true, shimmer: true }),
  };
}

export interface GroupHeaderParts {
  /** The clauses to show, at most SUMMARY_CAP -- render joined by ", "
   * with each object emphasized, same as a single row's own label. */
  shown: SummaryParts[];
  /** Calls not covered by `shown` ("and 9 more actions"), 0 if none. */
  more: number;
  /** The latest call, while it's still running -- always shown, after the
   * capped clauses, whatever the cap hides. */
  active: SummaryParts | null;
}

/** Structured headline for a ToolRunGroup's collapsed header: one clause
 * per kind of work, in the order each first happened -- "Ran 117 commands
 * (2 failed), wrote 4 files, read 5 files, and 9 more actions". A kind
 * done once keeps the call's own wording ("Wrote `deck.pptx`"). Calls that
 * fit no kind, and kinds past the cap, are counted in `more`; a run made
 * only of such calls is listed call by call instead. The expanded list
 * (ToolRunGroupView) still shows every call. */
export function summarizeGroupParts(
  items: ToolOrApprovalItem[],
  live = false,
  connectorTools: ReadonlySet<string> = new Set(),
): GroupHeaderParts {
  const last = items[items.length - 1];
  const active = last && isRunning(last, live) ? partsFor(last, true) : null;
  if (active) items = items.slice(0, -1);

  const byCategory = new Map<Category, ToolOrApprovalItem[]>();
  for (const item of items) {
    const category = categoryOf(item.toolName, connectorTools);
    const bucket = byCategory.get(category);
    if (bucket) bucket.push(item);
    else byCategory.set(category, [item]);
  }
  const others = byCategory.get("other") ?? [];
  byCategory.delete("other");

  if (byCategory.size === 0) {
    const clauses = others.map((item) => partsFor(item, isRunning(item, live)));
    return { shown: clauses.slice(0, SUMMARY_CAP), more: Math.max(clauses.length - SUMMARY_CAP, 0), active };
  }
  const categories = [...byCategory.entries()] as [Exclude<Category, "other">, ToolOrApprovalItem[]][];
  const shown = categories.slice(0, SUMMARY_CAP).map(([category, its]) => categoryClause(category, its, live));
  const hidden = categories.slice(SUMMARY_CAP).reduce((sum, [, its]) => sum + its.length, 0);
  return { shown, more: others.length + hidden, active };
}
