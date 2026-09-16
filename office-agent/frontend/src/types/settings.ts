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
}

export type McpServersResponse = Record<string, McpServerInfo>;

export interface McpServerUpdateResult {
  rejected: Record<string, string>;
  connected: boolean;
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

export type McpVersionBumpResult = { connected: boolean } | { error: string; connected: false };

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
// Workflows tab
// ---------------------------------------------------------------------

export interface WorkflowStep {
  tool_name: string;
  arguments: Record<string, unknown>;
  expect_contains: string | null;
}

export interface Workflow {
  name: string;
  mode: "chain" | "agent";
  summary: string;
  steps: WorkflowStep[];
  source_thread_id: string;
  created_at: string;
  updated_at: string;
  last_run_at: string | null;
  last_run_status: string | null;
}

export type WorkflowDeleteResult = { deleted: string } | { error: string };

export interface WorkflowRunStepStatus {
  index: number;
  tool_name: string;
  status: "pending" | "running" | "done" | "failed" | "stopped";
  detail: string | null;
}

export interface WorkflowRun {
  run_id: string;
  workflow_name: string;
  mode: string;
  status: "running" | "completed" | "failed" | "stopped";
  started_at: string;
  finished_at: string | null;
  steps: WorkflowRunStepStatus[];
  error: string | null;
}

export type WorkflowRunDeleteResult = { deleted: string } | { error: string };

// ---------------------------------------------------------------------
// Scheduled Tasks tab
// ---------------------------------------------------------------------

export interface ScheduleRule {
  kind: "once" | "daily" | "weekly" | "monthly";
  at: string;
  weekday: number | null;
  day_of_month: number | null;
}

export interface ScheduledTask {
  trigger_id: string;
  name: string;
  thread_id: string;
  schedule: ScheduleRule;
  enabled: boolean;
  created_at: string;
  next_run_at: string | null;
  workflow_name: string | null;
  prompt: string | null;
  last_run_at: string | null;
  last_run_status: string | null;
}

export interface CreateScheduledTaskPayload {
  name: string;
  kind: ScheduleRule["kind"];
  at: string;
  prompt?: string;
  workflow_name?: string;
  weekday?: number;
  day_of_month?: number;
}

export type ScheduledTaskResult = ScheduledTask | { error: string };

export type ScheduledTaskDeleteResult = { deleted: string } | { error: string };
