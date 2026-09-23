/**
 * REST request/response shapes for the settings panel, hand-encoded from
 * coscribe/web/app.py the same way types/wire.ts encodes the WS
 * contract. Field names/optionality match the backend's actual dict
 * shapes exactly (see that file's endpoint handlers), not app.js's
 * assumptions about them.
 */

// ---------------------------------------------------------------------
// Config (General + Workspace tabs)
// ---------------------------------------------------------------------

export interface MaskedKey {
  set: boolean;
  masked: string | null;
}

export interface ConfigResponse {
  ANTHROPIC_API_KEY: MaskedKey;
  OPENAI_API_KEY: MaskedKey;
  GEMINI_API_KEY: MaskedKey;
  COSCRIBE_ANTHROPIC_DEFAULT_MODEL: string | null;
  COSCRIBE_OPENAI_DEFAULT_MODEL: string | null;
  COSCRIBE_GEMINI_DEFAULT_MODEL: string | null;
  COSCRIBE_DEFAULT_MODEL: string | null;
  COSCRIBE_WORKSPACE_ROOT: string | null;
  COSCRIBE_SKILLS_DIR: string | null;
  COSCRIBE_MEMORY_PATH: string | null;
  COSCRIBE_MCP_CONFIG_PATH: string | null;
  COSCRIBE_PROVIDERS_CONFIG_PATH: string | null;
  COSCRIBE_HOOKS_CONFIG_PATH: string | null;
  COSCRIBE_LOG_LEVEL: string | null;
  COSCRIBE_EXTRA_READABLE_DIRS: string | null;
  COSCRIBE_EXTRA_WRITABLE_DIRS: string | null;
  COSCRIBE_MAX_TURNS: string | null;
  /** Desktop-shell-only, Rust-consumed (see office-agent-desktop's
   * lib.rs) -- "false" opts out of the default hide-on-close/keep-
   * running-in-background behavior. Absent/null/anything but exactly
   * "false" means the default (keep running) is in effect. No-op in
   * browser mode, where there's no window to hide in the first place. */
  COSCRIBE_BACKGROUND_ON_CLOSE: string | null;
}

// ---------------------------------------------------------------------
// Memory (Global instructions editor, General tab)
// ---------------------------------------------------------------------

export interface MemoryResponse {
  content: string;
}

export interface ConfigUpdateResult {
  restart_required: boolean;
  rejected: Record<string, string>;
}

export interface BrowseDirsEntry {
  name: string;
  path: string;
}

export type BrowseDirsResponse =
  | { error: string }
  | { path: string; parent: string | null; directories: BrowseDirsEntry[] };

// ---------------------------------------------------------------------
// Tools tab
// ---------------------------------------------------------------------

export interface ToolInfo {
  name: string;
  category: string;
  risk_category: "READ" | "WRITE_LOCAL" | "EXEC" | "EXTERNAL";
  requires_approval: boolean;
  description: string;
}

export interface ToolsResponse {
  tools: ToolInfo[];
}

// ---------------------------------------------------------------------
// Skills tab
// ---------------------------------------------------------------------

export interface SkillInfo {
  name: string;
  description: string;
  source: "builtin" | "custom";
}

export type SkillsResponse = SkillInfo[];

export type UploadSkillResult = SkillInfo | { error: string };

/** GET /api/skills/{name}/files -- flat, relative posix paths under that
 * skill's own real directory (a skill is a folder: SKILL.md plus
 * whatever reference docs/scripts it needs). The frontend folds this
 * into a tree client-side, see SkillsTab.tsx's buildFileTree. */
export interface SkillFilesResponse {
  files: string[];
}

/** GET /api/skills/{name}/files/{path} -- one file's raw text content
 * for the Skills tab's own preview pane. `error` covers every rejection
 * shape that endpoint can return (missing file, path traversal, binary
 * content, too large) -- one discriminated field, not a different HTTP
 * status per case the frontend has to branch on separately. */
export type SkillFileContentResult = { path: string; content: string } | { error: string };

// ---------------------------------------------------------------------
// Connectors tab (MCP)
// ---------------------------------------------------------------------

export interface McpCatalogEntry {
  name: string;
  description: string;
  command: string;
  args: string[];
  env?: Record<string, string>;
  needs_browser_check?: boolean;
  needs_config?: boolean;
}

export interface McpServerInfo {
  command?: string;
  args?: string[];
  masked_env?: Record<string, string>;
  /** Present instead of command/args/masked_env for a remote
   * (streamable_http) entry, e.g. a hand-configured Custom-tab server. */
  server_url?: string;
  masked_headers?: Record<string, string>;
  /** Live signal, not derived from the static config the rest of this
   * entry comes from -- whether this server is currently connected in
   * the running coscribe-web process (see web/app.py's mcp_connections). */
  connected: boolean;
}

export type McpServersResponse = Record<string, McpServerInfo>;

export interface McpServerUpdateResult {
  rejected: Record<string, string>;
  connected: boolean;
  /** Human-readable reason a connect attempt failed, or null on success
   * or when `rejected` already explains the outcome (a local validation
   * failure, caught before any connect was attempted). */
  error: string | null;
}

export interface BrowserCheckResponse {
  checked: boolean;
  path: string | null;
}

export interface InstallBrowserResponse {
  success: boolean;
  error?: string;
}

export type NpmLatestVersionResponse = { package: string; latest: string } | { error: string };

// Both bump-version and reconnect return this same shape -- backend's
// web/app.py builds both from the same {"connected": ..., "error": ...}
// pattern (error non-null covers a thrown validate_mcp_config failure or
// "not found" too, not just a failed connect attempt).
export type McpVersionBumpResult = { connected: boolean; error: string | null };
export type McpReconnectResult = { connected: boolean; error: string | null };

// ---------------------------------------------------------------------
// Providers tab
// ---------------------------------------------------------------------

export interface ProviderCatalogEntry {
  name: string;
  description: string;
  base_url: string;
  default_model: string;
  builtin: boolean;
}

export interface ProviderInfo {
  base_url: string | null;
  default_model: string;
  masked_key: string | null;
  builtin: boolean;
}

export type ProvidersResponse = Record<string, ProviderInfo>;

export interface ProviderUpdateResult {
  restart_required: boolean;
  rejected: Record<string, string>;
}

export interface ProviderDeleteResult {
  restart_required: boolean;
}

// ---------------------------------------------------------------------
// Environment tab (script-env packages for run_python_script)
// ---------------------------------------------------------------------

export interface ScriptEnvPackage {
  name: string;
  version: string;
}

export interface ScriptEnvInstallResult {
  success: boolean;
  error: string | null;
}

export interface ScriptEnvInterpreterInfo {
  /** The user's manually-chosen interpreter path, or null if they haven't
   * set one -- auto-detection (`auto_detected`) is what's actually used. */
  configured: string | null;
  /** What auto-detection alone would try, in order (sys.executable first,
   * then py/python3/python found on PATH) -- shown so the UI can tell
   * "found automatically" apart from "your manual choice". */
  auto_detected: string[];
}

// ---------------------------------------------------------------------
// Scheduled Tasks tab
// ---------------------------------------------------------------------

// "once" predates the six-value Manual/Hourly/Daily/Weekdays/Weekly/Monthly
// frequency picker the Edit/Create modal offers -- kept in the union so an
// already-created "once" trigger's schedule still type-checks, but the
// modal never lets you pick it for a new/edited task.
export type ScheduleKind = "manual" | "once" | "hourly" | "daily" | "weekdays" | "weekly" | "monthly";

export type ApprovalMode = "manual" | "auto" | "skip";

export interface ScheduleRule {
  kind: ScheduleKind;
  at: string;
  weekday: number | null;
  day_of_month: number | null;
  start_date: string | null;
}

export type RunStatus = "running" | "completed" | "failed" | "needs_approval" | "stopped";

/** One execution of a task, in its own conversation (thread_id). */
export interface ScheduledRun {
  run_id: string;
  thread_id: string;
  started_at: string;
  source: "manual" | "scheduled";
  status: RunStatus;
  finished_at: string | null;
  error: string | null;
}

export interface ScheduledTask {
  trigger_id: string;
  name: string;
  schedule: ScheduleRule;
  enabled: boolean;
  created_at: string;
  next_run_at: string | null;
  prompt: string;
  last_run_at: string | null;
  last_run_status: string | null;
  model: string | null;
  approval_mode: ApprovalMode;
  notes_enabled: boolean;
  /** Oldest first. */
  runs: ScheduledRun[];
}

export interface CreateScheduledTaskPayload {
  name: string;
  kind: ScheduleKind;
  at: string;
  prompt: string;
  weekday?: number;
  day_of_month?: number;
  start_date?: string;
  model?: string;
  approval_mode?: ApprovalMode;
  notes_enabled?: boolean;
}

export type ScheduledTaskResult = ScheduledTask | { error: string };

export type RunNowResult = { task: ScheduledTask; run: ScheduledRun } | { error: string };

export type TaskNotesResult = { notes: string } | { error: string };

export type ScheduledTaskDeleteResult = { deleted: string } | { error: string };
