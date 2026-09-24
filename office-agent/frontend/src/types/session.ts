/** REST shapes outside the settings panel: threads, slash commands, file
 * uploads. Hand-encoded from a direct read of coscribe/web/app.py, same
 * discipline as wire.ts and settings.ts. */

export interface ThreadSummary {
  thread_id: string;
  updated_at: string | null;
  message_count: number;
  preview: string;
  workspace_root: string;
}

export type ThreadsResponse = ThreadSummary[];

export type ThreadDeleteResult = { deleted: string } | { error: string };

export type ThreadRenameResult = { thread_id: string; title: string } | { error: string };

export interface CommandInfo {
  name: string;
  description: string;
}

export type CommandsResponse = CommandInfo[];

export type UploadResult = { path: string; bytes_written: number } | { error: string };

/** A background spawn_agent_background run -- mirrors tools/
 * subagent_tasks.py's SubAgentTask.to_dict() exactly. */
export interface SubAgentTask {
  task_id: string;
  thread_id: string;
  instructions: string;
  prompt: string;
  tool_names: string;
  description: string;
  status: "running" | "paused" | "blocked_on_approval" | "succeeded" | "failed";
  started_at: string;
  finished_at: string | null;
  result: string | null;
  error: string | null;
}

export type SubAgentTasksResponse = SubAgentTask[];

/** GET /api/subagents/{task_id}/transcript's response -- `entries` reuses
 * wire.ts's own HistoryEntry shape (imported by the one caller, the Sub
 * Agents panel, rather than re-exported here to avoid a wire.ts <->
 * session.ts import cycle for a single type). */
export interface SubAgentTranscriptResponse {
  task: SubAgentTask;
  entries: import("./wire").HistoryEntry[];
}

export type SubAgentActionResult = SubAgentTask | { error: string };

/** GET /api/threads/{id}/context-breakdown's response -- mirrors
 * web/context_usage.py's build_context_breakdown() return shape
 * exactly. `key` is a fixed, known set (see ContextBreakdownPanel.tsx's
 * own CATEGORY_COLOR map) -- typed as `string`, not a literal union,
 * since a frontend/backend version mismatch should degrade (an unknown
 * category falls back to a neutral color) rather than fail to compile. */
export interface ContextBreakdownCategory {
  key: string;
  label: string;
  tokens: number;
}

export interface ContextBreakdown {
  context_window: number;
  total_tokens: number;
  total_is_estimated: boolean;
  categories: ContextBreakdownCategory[];
}

// -- Side panel (GET /api/threads/{id}/activity) ------------------------------

export interface ActivityFile {
  /** Relative to the workspace (or absolute, for an extra directory). */
  path: string;
  name: string;
  exists: boolean;
  /** A document/media type the OS may open; anything else is reveal-only. */
  openable: boolean;
  modified_at: string | null;
}

export interface ActivityToolUse {
  name: string;
  count: number;
}

export interface PlanTask {
  id: string;
  content: string;
  status: "pending" | "in_progress" | "completed";
}

export interface ThreadActivity {
  tasks: PlanTask[];
  /** Most recently touched first. */
  outputs: ActivityFile[];
  references: ActivityFile[];
  tools: ActivityToolUse[];
  connectors: { server: string; tools: ActivityToolUse[] }[];
  skills: string[];
}
