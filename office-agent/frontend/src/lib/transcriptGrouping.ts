import type { LogItem } from "../state/reducer";

type ToolOrApprovalItem = Extract<LogItem, { kind: "tool" | "approval" }>;

export interface ToolRunGroup {
  kind: "tool_run";
  id: string;
  items: ToolOrApprovalItem[];
}

export type TranscriptEntry = LogItem | ToolRunGroup;

/** Render-time-only grouping pass -- no reducer/wire changes. Coalesces
 * consecutive tool/approval items (a "run") between user/agent/system
 * messages into one ToolRunGroup; a lone tool/approval item (a "run" of
 * one) passes through unwrapped, since there's nothing to group -- both
 * still render collapsed-by-default via ToolCallRow either way. */
export function groupToolRuns(items: LogItem[]): TranscriptEntry[] {
  const result: TranscriptEntry[] = [];
  let current: ToolOrApprovalItem[] = [];

  const flush = () => {
    if (current.length === 0) return;
    if (current.length === 1) {
      result.push(current[0]);
    } else {
      result.push({ kind: "tool_run", id: `run-${current[0].id}`, items: current });
    }
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
 * handful that read better as "Verb: object" (a query or question). */
export interface SummaryParts {
  verb: string;
  object: string | null;
  glue?: string;
}

/** Exact-name handlers for the common built-in tools -- gives Claude-Code-
 * style specificity ("Wrote deck.pptx", "Ran a command") for the tools a
 * user actually sees most. Anything not listed here (a less common
 * built-in, or a dynamically-named MCP connector tool like
 * "playwright_browser_navigate") falls through to summarizeToolNameParts's
 * generic prefix table below, not a hardcoded entry for all ~35+ tools. */
const TOOL_SUMMARIES: Record<string, (args: ArgRecord) => SummaryParts> = {
  run_python_script: () => ({ verb: "Ran a command", object: null }),
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
  if (item.kind === "approval" && item.status === "pending") {
    return { ...base, verb: `Approve: ${base.verb}` };
  }
  return base;
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
 * capped past a few items ("and 2 more"). No line-count/diff-stat badges
 * -- no tool response in this codebase returns that data, so none is
 * shown. */
export function summarizeGroupParts(items: ToolOrApprovalItem[]): GroupHeaderParts {
  const parts = items.map(partsFor);
  if (parts.length <= SUMMARY_CAP) return { shown: parts, more: 0 };
  return { shown: parts.slice(0, SUMMARY_CAP), more: parts.length - SUMMARY_CAP };
}
