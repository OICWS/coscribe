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
  McpReconnectResult,
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
  RunNowResult,
  TaskNotesResult,
  ScriptEnvInstallResult,
  ScriptEnvInterpreterInfo,
  ScriptEnvPackage,
  SkillFileContentResult,
  SkillFilesResponse,
  SkillsResponse,
  ToolsResponse,
  UploadSkillResult,
} from "../types/settings";
import type {
  CommandsResponse,
  ContextBreakdown,
  SubAgentActionResult,
  SubAgentTasksResponse,
  SubAgentTranscriptResponse,
  ThreadDeleteResult,
  ThreadRenameResult,
  ThreadActivity,
  ThreadsResponse,
  UploadResult,
} from "../types/session";

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

async function putJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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

export const getSkillFiles = (name: string) =>
  getJson<SkillFilesResponse>(`/api/skills/${encodeURIComponent(name)}/files`);

/** A missing/too-large/binary/path-traversal-rejected file is a real,
 * expected outcome the preview pane renders inline -- same "read the
 * body regardless of status" pattern uploadSkill above already uses,
 * not `getJson`'s throw-on-non-2xx (that's for failures a tab has no
 * sane in-UI response to, see checkOk's own comment). `path` already
 * contains "/" for a nested file -- encodeURIComponent would escape
 * those slashes into "%2F", which the backend's `{path:path}` route
 * matcher wouldn't split back into its own segments, so only each
 * path *segment* is encoded, joined back with real "/"s. */
export async function getSkillFileContent(name: string, path: string): Promise<SkillFileContentResult> {
  const encodedPath = path.split("/").map(encodeURIComponent).join("/");
  const res = await fetch(`/api/skills/${encodeURIComponent(name)}/files/${encodedPath}`);
  return res.json() as Promise<SkillFileContentResult>;
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
export const reconnectMcpServer = (name: string) =>
  postJson<McpReconnectResult>(`/api/mcp/servers/${encodeURIComponent(name)}/reconnect`, {});
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
export const updateScheduledTask = (triggerId: string, payload: CreateScheduledTaskPayload) =>
  putJson<ScheduledTaskResult>(`/api/scheduled-tasks/${encodeURIComponent(triggerId)}`, payload);
export const runScheduledTaskNow = (triggerId: string) =>
  postJson<RunNowResult>(`/api/scheduled-tasks/${encodeURIComponent(triggerId)}/run`, {});
export const getTaskNotes = (triggerId: string) =>
  getJson<TaskNotesResult>(`/api/scheduled-tasks/${encodeURIComponent(triggerId)}/notes`);
export const saveTaskNotes = (triggerId: string, notes: string) =>
  putJson<TaskNotesResult>(`/api/scheduled-tasks/${encodeURIComponent(triggerId)}/notes`, { notes });

// -- Threads --------------------------------------------------------------

export const getThreads = () => getJson<ThreadsResponse>("/api/threads");
export const deleteThread = (id: string) => del<ThreadDeleteResult>(`/api/threads/${encodeURIComponent(id)}`);
export const renameThread = (id: string, title: string) =>
  postJson<ThreadRenameResult>(`/api/threads/${encodeURIComponent(id)}/rename`, { title });

// -- Sub Agents (background spawn_agent_background runs) --------------------

export const getThreadActivity = (threadId: string) =>
  getJson<ThreadActivity>(`/api/threads/${encodeURIComponent(threadId)}/activity`);

/** Resolves to an error message, or null once the file was handed to the OS. */
export async function openThreadFile(threadId: string, path: string, reveal: boolean): Promise<string | null> {
  const res = await fetch(`/api/threads/${encodeURIComponent(threadId)}/files/open`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, reveal }),
  });
  if (res.ok) return null;
  const body = (await res.json().catch(() => null)) as { error?: string } | null;
  return body?.error ?? `Couldn't open ${path} (${res.status})`;
}

export const threadFileDownloadUrl = (threadId: string, path: string) =>
  `/api/threads/${encodeURIComponent(threadId)}/files/download?path=${encodeURIComponent(path)}`;

export const getSubAgentTasks = (threadId: string) =>
  getJson<SubAgentTasksResponse>(`/api/threads/${encodeURIComponent(threadId)}/subagents`);
export const getSubAgentTranscript = (taskId: string) =>
  getJson<SubAgentTranscriptResponse>(`/api/subagents/${encodeURIComponent(taskId)}/transcript`);
export const pauseSubAgentTask = (taskId: string) =>
  postJson<SubAgentActionResult>(`/api/subagents/${encodeURIComponent(taskId)}/pause`, {});
export const resumeSubAgentTask = (taskId: string) =>
  postJson<SubAgentActionResult>(`/api/subagents/${encodeURIComponent(taskId)}/resume`, {});

// -- Context-window breakdown -------------------------------------------

export const getContextBreakdown = (threadId: string) =>
  getJson<ContextBreakdown>(`/api/threads/${encodeURIComponent(threadId)}/context-breakdown`);

// -- Slash commands -----------------------------------------------------

export const getCommands = () => getJson<CommandsResponse>("/api/commands");

// -- Uploads --------------------------------------------------------------

export async function uploadFile(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: form });
  return res.json() as Promise<UploadResult>;
}
