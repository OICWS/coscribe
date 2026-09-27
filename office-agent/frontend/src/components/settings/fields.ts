export interface SettingsField {
  key: string;
  label: string;
  placeholder: string;
  /** Shown under the label, SettingRow-style -- what this actually does
   * and its consequence, not a restatement of the label. The old
   * layout crammed this into the input's own placeholder text (see e.g.
   * Max Turns' old "20 (caps a single agent loop's tool-calling
   * turns)"), which meant it vanished the moment you typed a value. */
  description: string;
}

/** Mirrors app.js's GENERAL_FIELDS/WORKSPACE_FIELDS exactly -- these key
 * names are also what POST /api/config expects and what BLANK_UNSAFE_ENV_VARS
 * validates server-side (see coscribe/web/app.py). */
export const GENERAL_FIELDS: SettingsField[] = [
  {
    key: "COSCRIBE_DEFAULT_MODEL",
    label: "Default model",
    placeholder: "provider:model",
    description:
      "What a new conversation starts with. Each conversation can switch from its own model picker.",
  },
  {
    key: "COSCRIBE_MAX_TURNS",
    label: "Max turns",
    placeholder: "20",
    description: "The most tool steps one reply can take before it stops.",
  },
];

// Log Level's real values are this fixed, small set (Python's own logging
// module level names) -- rendered as a SegmentedControl instead of free
// text, see GeneralTab.tsx. Not in GENERAL_FIELDS above: that array feeds
// a generic free-text-field loop, and this isn't one.
export const LOG_LEVEL_KEY = "COSCRIBE_LOG_LEVEL";
export const LOG_LEVELS = [
  { value: "DEBUG", label: "Debug" },
  { value: "INFO", label: "Info" },
  { value: "WARNING", label: "Warning" },
  { value: "ERROR", label: "Error" },
] as const;

export const PERMISSION_MODE_KEY = "COSCRIBE_DEFAULT_PERMISSION_MODE";
export const PERMISSION_MODES = [
  { value: "manual", label: "Manual" },
  { value: "accept-edits", label: "Accept Edits" },
  { value: "plan", label: "Plan" },
  { value: "auto", label: "Auto" },
] as const;

// Skills Directory is a real folder (unlike the file-path fields below it in
// this array), so it's the one WORKSPACE_FIELDS entry GeneralTab/WorkspaceTab
// pairs with a DirBrowserModal "Browse..." button -- see WorkspaceTab.tsx.
export const SKILLS_DIR_KEY = "COSCRIBE_SKILLS_DIR";

export const WORKSPACE_FIELDS: SettingsField[] = [
  {
    key: SKILLS_DIR_KEY,
    label: "Skills folder",
    placeholder: "./skills",
    description: "Where your own skills are kept, alongside the built-in ones.",
  },
  {
    key: "COSCRIBE_MEMORY_PATH",
    label: "Memory file",
    placeholder: "./MEMORY.md",
    description: "Where your instructions and anything coscribe is asked to remember are kept.",
  },
  {
    key: "COSCRIBE_MCP_CONFIG_PATH",
    label: "Connectors config file",
    placeholder: "(none)",
    description: "An MCP server config file loaded alongside the Connectors page.",
  },
  {
    key: "COSCRIBE_PROVIDERS_CONFIG_PATH",
    label: "Providers config file",
    placeholder: "(none)",
    description: "A custom-provider config file loaded alongside the Providers page.",
  },
  {
    key: "COSCRIBE_HOOKS_CONFIG_PATH",
    label: "Hooks config file",
    placeholder: "(none)",
    description: "Commands that run on tool calls and other conversation events.",
  },
];

/** Desktop-shell-only toggle -- see ConfigResponse's own doc in
 * types/settings.ts and office-agent-desktop's lib.rs
 * (should_keep_running_in_background). Not in GENERAL_FIELDS: it's a
 * ToggleSwitch, not a text SettingsField, and deliberately excluded
 * from restart_required server-side (see web/app.py's
 * DESKTOP_ENV_VARS), unlike everything in that list. */
export const BACKGROUND_ON_CLOSE_KEY = "COSCRIBE_BACKGROUND_ON_CLOSE";

export const WORKSPACE_ROOT_KEY = "COSCRIBE_WORKSPACE_ROOT";
export const READABLE_DIRS_KEY = "COSCRIBE_EXTRA_READABLE_DIRS";
export const WRITABLE_DIRS_KEY = "COSCRIBE_EXTRA_WRITABLE_DIRS";

export type DirPermission = "read" | "read-write";

export interface DirEntry {
  path: string;
  permission: DirPermission;
}

function parseDirsList(value: string | null | undefined): string[] {
  return (value ?? "")
    .split(",")
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0);
}

/** A writable directory takes precedence over a readable one with the same
 * path, matching app.js's initDirsField dedup. */
export function buildDirEntries(readable: string | null, writable: string | null): DirEntry[] {
  const writableSet = new Set(parseDirsList(writable));
  const entries = new Map<string, DirPermission>();
  for (const path of parseDirsList(readable)) entries.set(path, "read");
  for (const path of writableSet) entries.set(path, "read-write");
  return [...entries.entries()].map(([path, permission]) => ({ path, permission }));
}

/** Comma+space join -- must exactly match buildDirEntries' parse format or
 * every settings-open shows a spurious "dirty" diff. */
export function syncDirsToStrings(entries: DirEntry[]): { readable: string; writable: string } {
  const readable = entries.filter((e) => e.permission === "read").map((e) => e.path);
  const writable = entries.filter((e) => e.permission === "read-write").map((e) => e.path);
  return { readable: readable.join(", "), writable: writable.join(", ") };
}
