import { type ComponentType, useEffect, useMemo, useState } from "react";
import { getConfig, updateConfig } from "../../lib/rest";
import type { ConfigResponse } from "../../types/settings";
import {
  BookOpenIcon,
  FolderIcon,
  KeyIcon,
  LinkIcon,
  SearchIcon,
  SettingsIcon,
  TerminalIcon,
  ToolIcon,
  ZapIcon,
} from "../icons";
import { ConnectorsTab } from "./ConnectorsTab";
import {
  BACKGROUND_ON_CLOSE_KEY,
  GENERAL_FIELDS,
  READABLE_DIRS_KEY,
  WORKSPACE_FIELDS,
  WORKSPACE_ROOT_KEY,
  WRITABLE_DIRS_KEY,
  buildDirEntries,
  syncDirsToStrings,
  type DirEntry,
} from "./fields";
import { EnvironmentTab } from "./EnvironmentTab";
import { GeneralTab } from "./GeneralTab";
import { ProvidersTab } from "./ProvidersTab";
import { SkillsTab } from "./SkillsTab";
import { ToolsTab } from "./ToolsTab";
import { WorkflowsTab } from "./WorkflowsTab";
import { WorkspaceTab } from "./WorkspaceTab";

export type SettingsCategory =
  | "general"
  | "workspace"
  | "providers"
  | "tools"
  | "skills"
  | "connectors"
  | "workflows"
  | "environment";

interface CategoryDef {
  id: SettingsCategory;
  label: string;
  icon: ComponentType<{ className?: string }>;
}

// Scheduled Tasks no longer lives here -- moved to the Run mode's own
// "Scheduled" sub-tab (see RunPanel.tsx) as part of the "Nav rail,
// Create/Run split" design pass; Settings is reachable from the nav
// rail's own Settings icon now, not a top-bar gear (see NavRail.tsx).
//
// Grouped toward the "Settings modal" reference screenshot
// (docs/ui-references/settings-modal.png): a plain "Settings" group for
// the core config categories, then "Customize" pulled out specifically
// for Skills/Connectors -- the one thing the roadmap explicitly called
// out to align on. The reference's third "Platform" group (an "API
// keys" external link) and its "Plugins" Customize item both have no
// coscribe equivalent yet (no plugin system, no separate platform
// console) -- deliberately not added as empty/placeholder groups or
// nav items; add them for real once there's something behind them.
const GROUPS: { label: string; categories: CategoryDef[] }[] = [
  {
    label: "Settings",
    categories: [
      { id: "general", label: "General", icon: SettingsIcon },
      { id: "workspace", label: "Workspace", icon: FolderIcon },
      { id: "providers", label: "Providers", icon: KeyIcon },
      { id: "tools", label: "Tools", icon: ToolIcon },
      { id: "workflows", label: "Workflows", icon: ZapIcon },
      { id: "environment", label: "Environment", icon: TerminalIcon },
    ],
  },
  {
    label: "Customize",
    categories: [
      { id: "skills", label: "Skills", icon: BookOpenIcon },
      { id: "connectors", label: "Connectors", icon: LinkIcon },
    ],
  },
];

const CONFIG_KEYS = [
  ...GENERAL_FIELDS.map((f) => f.key),
  ...WORKSPACE_FIELDS.map((f) => f.key),
  WORKSPACE_ROOT_KEY,
  BACKGROUND_ON_CLOSE_KEY,
];

interface SettingsModalProps {
  open: boolean;
  initialCategory?: SettingsCategory;
  workflowEventTick: number;
  enabledSkills: string[];
  onToggleSkill: (name: string, enabled: boolean) => void;
  onCreateSkill: () => void;
  onClose: () => void;
}

export function SettingsModal({
  open,
  initialCategory,
  workflowEventTick,
  enabledSkills,
  onToggleSkill,
  onCreateSkill,
  onClose,
}: SettingsModalProps) {
  const [category, setCategory] = useState<SettingsCategory>(initialCategory ?? "general");
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [dirEntries, setDirEntries] = useState<DirEntry[]>([]);
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null);
  const [saving, setSaving] = useState(false);
  const [search, setSearch] = useState("");

  // v1: filters by category label only (e.g. "skill" narrows the list to
  // Skills) -- not a deep search into each tab's own field labels, which
  // the reference screenshot's search bar may or may not do (can't tell
  // from a static image, and it's a meaningfully bigger feature).
  const filteredGroups = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return GROUPS;
    return GROUPS.map((g) => ({ ...g, categories: g.categories.filter((c) => c.label.toLowerCase().includes(q)) })).filter(
      (g) => g.categories.length > 0,
    );
  }, [search]);

  useEffect(() => {
    if (!open) return;
    setCategory(initialCategory ?? "general");
    setStatus(null);
    setSearch("");
    getConfig().then((cfg) => {
      setConfig(cfg);
      const initial: Record<string, string> = {};
      for (const key of CONFIG_KEYS) initial[key] = (cfg as unknown as Record<string, string | null>)[key] ?? "";
      setValues(initial);
      setDirEntries(buildDirEntries(cfg.COSCRIBE_EXTRA_READABLE_DIRS, cfg.COSCRIBE_EXTRA_WRITABLE_DIRS));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  const dirs = syncDirsToStrings(dirEntries);
  const originalDirs = config
    ? { readable: config.COSCRIBE_EXTRA_READABLE_DIRS ?? "", writable: config.COSCRIBE_EXTRA_WRITABLE_DIRS ?? "" }
    : { readable: "", writable: "" };

  const dirty: Record<string, string> = {};
  if (config) {
    for (const key of CONFIG_KEYS) {
      const original = (config as unknown as Record<string, string | null>)[key] ?? "";
      if (values[key] !== original) dirty[key] = values[key];
    }
    if (dirs.readable !== originalDirs.readable) dirty[READABLE_DIRS_KEY] = dirs.readable;
    if (dirs.writable !== originalDirs.writable) dirty[WRITABLE_DIRS_KEY] = dirs.writable;
  }
  const isDirty = Object.keys(dirty).length > 0;

  const save = async () => {
    setSaving(true);
    setStatus(null);
    try {
      const result = await updateConfig(dirty);
      const rejectedEntries = Object.entries(result.rejected);
      if (rejectedEntries.length > 0) {
        setStatus({ text: `Not saved -- ${rejectedEntries[0][1]}`, error: true });
        return;
      }
      setStatus({ text: result.restart_required ? "Saved. Restart coscribe-web to apply." : "Saved.", error: false });
      const refreshed = await getConfig();
      setConfig(refreshed);
    } finally {
      setSaving(false);
    }
  };

  const showSaveBar = category === "general" || category === "workspace";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="relative flex h-[min(680px,100vh-2rem)] w-[min(920px,100vw-2rem)] overflow-hidden rounded-[14px] border border-[var(--border)] bg-[var(--panel-bg)] shadow-[var(--shadow)]">
        <button
          type="button"
          aria-label="Close settings"
          className="absolute right-3 top-3 z-10 text-[var(--muted)] hover:text-[var(--fg)]"
          onClick={onClose}
        >
          ✕
        </button>
        <nav className="flex w-[220px] shrink-0 flex-col gap-3 overflow-y-auto border-r border-[var(--border)] p-3">
          <div className="flex items-center gap-2 rounded-md border border-[var(--border)] bg-[var(--card-bg)] px-2.5 py-1.5">
            <SearchIcon className="h-3.5 w-3.5 shrink-0 text-[var(--muted)]" />
            <input
              className="w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-[var(--muted)]"
              placeholder="Search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          {filteredGroups.length === 0 && <div className="px-2 py-1 text-sm text-[var(--muted)]">No matches.</div>}
          {filteredGroups.map((group) => (
            <div key={group.label} className="flex flex-col gap-0.5">
              <div className="mb-0.5 px-2 text-xs font-medium tracking-wide text-[var(--muted)]">{group.label}</div>
              {group.categories.map((cat) => (
                <button
                  key={cat.id}
                  type="button"
                  className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${
                    category === cat.id
                      ? "bg-[var(--card-bg)] font-medium text-[var(--fg)]"
                      : "text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
                  }`}
                  onClick={() => setCategory(cat.id)}
                >
                  <cat.icon className="h-4 w-4 shrink-0" />
                  {cat.label}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="flex flex-1 flex-col overflow-hidden">
          <div className="flex-1 overflow-y-auto p-4">
            {category === "general" && <GeneralTab values={values} onChange={(k, v) => setValues({ ...values, [k]: v })} />}
            {category === "workspace" && (
              <WorkspaceTab
                values={values}
                onChange={(k, v) => setValues({ ...values, [k]: v })}
                dirEntries={dirEntries}
                onDirEntriesChange={setDirEntries}
              />
            )}
            {category === "providers" && <ProvidersTab active={category === "providers"} />}
            {category === "tools" && <ToolsTab active={category === "tools"} />}
            {category === "skills" && (
              <SkillsTab
                active={category === "skills"}
                enabledSkills={enabledSkills}
                onToggle={onToggleSkill}
                onCreateSkill={onCreateSkill}
              />
            )}
            {category === "connectors" && <ConnectorsTab active={category === "connectors"} />}
            {category === "workflows" && (
              <WorkflowsTab active={category === "workflows"} workflowEventTick={workflowEventTick} />
            )}
            {category === "environment" && <EnvironmentTab active={category === "environment"} />}
          </div>
          {showSaveBar && (
            <div className="flex items-center gap-3 border-t border-[var(--border)] px-4 py-3">
              <button
                type="button"
                className="rounded-md bg-[var(--accent)] px-4 py-1.5 text-sm text-[var(--accent-fg)] disabled:opacity-40"
                disabled={!isDirty || saving}
                onClick={save}
              >
                Save
              </button>
              {status && (
                <span className={`text-sm ${status.error ? "text-red-500" : "text-[var(--muted)]"}`}>{status.text}</span>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
