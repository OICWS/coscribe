import type {
  BrowseDirsResponse,
  BrowserCheckResponse,
  ConfigResponse,
  ConfigUpdateResult,
  CreateScheduledTaskPayload,
  InstallBrowserResponse,
  McpCatalogEntry,
  McpServerUpdateResult,
  McpServersResponse,
  McpVersionBumpResult,
  MemoryResponse,
  NpmLatestVersionResponse,
  ProviderCatalogEntry,
  ProviderDeleteResult,
  ProviderUpdateResult,
  ProvidersResponse,
  ScheduledTask,
  ScheduledTaskDeleteResult,
  ScheduledTaskResult,
  ScriptEnvInstallResult,
  ScriptEnvInterpreterInfo,
  ScriptEnvPackage,
  SkillsResponse,
  ToolsResponse,
  UploadSkillResult,
  Workflow,
  WorkflowDeleteResult,
  WorkflowRunDeleteResult,
} from "../types/settings";
import type {
  CommandsResponse,
  ThreadDeleteResult,
  ThreadRenameResult,
  ThreadsResponse,
  UploadResult,
} from "../types/session";
// wire.ts's WorkflowRun (mode: "chain"|"agent" literal union) is the
// stricter of the two structurally-near-identical WorkflowRun types --
// settings.ts's own copy (mode: string) exists for the settings-panel
// REST types file to stay self-contained, but the app-level reducer
// state (workflowRuns, hydrated via this same endpoint) needs the wire.ts
// one, so this function returns that instead of settings.ts's.
import type { WorkflowRun } from "../types/wire";

// Every settings tab does `someGetter().then(setState)` with no .catch --
// a rejected promise there just leaves that tab's state at its initial
// empty array forever, with nothing in the UI to say why (see the
// session-switching bug report: "settings里所有设置消失，看不到skill,
// connector"). Throwing a real Error here (instead of letting a non-JSON
// error body reach `res.json()` and reject with an opaque "Unexpected
// token" SyntaxError) at least makes the eventual unhandled-rejection
// console message point at the actual HTTP failure, not a JSON parse
// artifact -- callers that want a visible in-UI message still need their
// own .catch, this only makes the underlying failure legible.
async function checkOk(res: Response): Promise<void> {
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} from ${res.url}${body ? `: ${body.slice(0, 200)}` : ""}`);
  }
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  await checkOk(res);
  return res.json() as Promise<T>;
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  await checkOk(res);
  return res.json() as Promise<T>;
}

async function del<T>(url: string): Promise<T> {
  const res = await fetch(url, { method: "DELETE" });
  await checkOk(res);
  return res.json() as Promise<T>;
}

// -- Config (General + Workspace) -------------------------------------

export const getConfig = () => getJson<ConfigResponse>("/api/config");
export const updateConfig = (updates: Record<string, string>) =>
  postJson<ConfigUpdateResult>("/api/config", { updates });
export const browseDirs = (path?: string) =>
  getJson<BrowseDirsResponse>(`/api/browse-dirs${path ? `?path=${encodeURIComponent(path)}` : ""}`);

// -- Memory (Global instructions) ---------------------------------------

export const getMemory = () => getJson<MemoryResponse>("/api/memory");
export const updateMemory = (content: string) =>
  postJson<{ status: string }>("/api/memory", { content });

// -- Tools --------------------------------------------------------------

export const getTools = () => getJson<ToolsResponse>("/api/tools");

// -- Skills ---------------------------------------------------------------

export const getSkills = () => getJson<SkillsResponse>("/api/skills");

export async function uploadSkill(file: File): Promise<UploadSkillResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/skills/upload", { method: "POST", body: form });
  return res.json() as Promise<UploadSkillResult>;
}

// -- Connectors (MCP) -----------------------------------------------------

export const getMcpCatalog = () => getJson<McpCatalogEntry[]>("/api/mcp/catalog");
export const getMcpServers = () => getJson<McpServersResponse>("/api/mcp/servers");
export const addMcpServer = (
  name: string,
  server:
    | { command: string; args: string[]; env?: Record<string, string> }
    | { server_url: string; headers?: Record<string, string> },
) => postJson<McpServerUpdateResult>("/api/mcp/servers", { name, ...server });
export const removeMcpServer = (name: string) => del<Record<string, never>>(`/api/mcp/servers/${encodeURIComponent(name)}`);
export const checkBrowser = () => getJson<BrowserCheckResponse>("/api/mcp/browser-check");
export const installBrowser = () => postJson<InstallBrowserResponse>("/api/mcp/install-browser", {});
export const getNpmLatestVersion = (pkg: string) =>
  getJson<NpmLatestVersionResponse>(`/api/mcp/npm-latest-version?package=${encodeURIComponent(pkg)}`);
export const bumpMcpVersion = (name: string, pkg: string, version: string) =>
  postJson<McpVersionBumpResult>(`/api/mcp/servers/${encodeURIComponent(name)}/bump-version`, {
    package: pkg,
    version,
  });
// -- Providers ------------------------------------------------------------

export const getProvidersCatalog = () => getJson<ProviderCatalogEntry[]>("/api/providers/catalog");
export const getProviders = () => getJson<ProvidersResponse>("/api/providers");
export const addProvider = (name: string, baseUrl: string, apiKey: string, defaultModel: string) =>
  postJson<ProviderUpdateResult>("/api/providers", {
    name,
    base_url: baseUrl,
    api_key: apiKey,
    default_model: defaultModel,
  });
export const removeProvider = (name: string) => del<ProviderDeleteResult>(`/api/providers/${encodeURIComponent(name)}`);

// -- Environment (script-env packages) -------------------------------------

export const getScriptEnvPackages = () => getJson<ScriptEnvPackage[]>("/api/script-env/packages");
export const installScriptEnvPackage = (packageName: string) =>
  postJson<ScriptEnvInstallResult>("/api/script-env/packages", { package: packageName });
export const removeScriptEnvPackage = (packageName: string) =>
  del<ScriptEnvInstallResult>(`/api/script-env/packages/${encodeURIComponent(packageName)}`);

export const getScriptEnvInterpreter = () => getJson<ScriptEnvInterpreterInfo>("/api/script-env/interpreter");
// `path: ""` clears the override server-side, reverting to auto-detection.
export const setScriptEnvInterpreter = (path: string) =>
  postJson<ScriptEnvInstallResult>("/api/script-env/interpreter", { path });

// Mirrors the script-env functions above exactly -- backed by
// run_node_script's node-env directory instead (see tools/node_env.py).
export const getNodeEnvPackages = () => getJson<ScriptEnvPackage[]>("/api/node-env/packages");
export const installNodeEnvPackage = (packageName: string) =>
  postJson<ScriptEnvInstallResult>("/api/node-env/packages", { package: packageName });
export const removeNodeEnvPackage = (packageName: string) =>
  del<ScriptEnvInstallResult>(`/api/node-env/packages/${encodeURIComponent(packageName)}`);

// -- Workflows --------------------------------------------------------------

export const getWorkflows = () => getJson<Workflow[]>("/api/workflows");
export const deleteWorkflow = (name: string) => del<WorkflowDeleteResult>(`/api/workflows/${encodeURIComponent(name)}`);
export const getWorkflowRuns = (limit = 20) => getJson<WorkflowRun[]>(`/api/workflow-runs?limit=${limit}`);
export const deleteWorkflowRun = (runId: string) => del<WorkflowRunDeleteResult>(`/api/workflow-runs/${encodeURIComponent(runId)}`);

// -- Scheduled Tasks --------------------------------------------------------

export const getScheduledTasks = () => getJson<ScheduledTask[]>("/api/scheduled-tasks");
export const createScheduledTask = (payload: CreateScheduledTaskPayload) =>
  postJson<ScheduledTaskResult>("/api/scheduled-tasks", payload);
export const pauseScheduledTask = (triggerId: string) =>
  postJson<ScheduledTaskResult>(`/api/scheduled-tasks/${encodeURIComponent(triggerId)}/pause`, {});
export const resumeScheduledTask = (triggerId: string) =>
  postJson<ScheduledTaskResult>(`/api/scheduled-tasks/${encodeURIComponent(triggerId)}/resume`, {});
export const deleteScheduledTask = (triggerId: string) =>
  del<ScheduledTaskDeleteResult>(`/api/scheduled-tasks/${encodeURIComponent(triggerId)}`);

// -- Threads --------------------------------------------------------------

export const getThreads = () => getJson<ThreadsResponse>("/api/threads");
export const deleteThread = (id: string) => del<ThreadDeleteResult>(`/api/threads/${encodeURIComponent(id)}`);
export const renameThread = (id: string, title: string) =>
  postJson<ThreadRenameResult>(`/api/threads/${encodeURIComponent(id)}/rename`, { title });

// -- Slash commands -----------------------------------------------------

export const getCommands = () => getJson<CommandsResponse>("/api/commands");

// -- Uploads --------------------------------------------------------------

export async function uploadFile(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: form });
  return res.json() as Promise<UploadResult>;
}
