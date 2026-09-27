import { type ComponentType, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { getConfig, updateConfig } from "../../lib/rest";
import type { ConfigResponse } from "../../types/settings";
import {
  BookOpenIcon,
  CloseIcon,
  FolderIcon,
  KeyIcon,
  LinkIcon,
  SearchIcon,
  SettingsIcon,
  TerminalIcon,
  ToolIcon,
} from "../icons";
import { ConnectorsTab } from "./ConnectorsTab";
import {
  BACKGROUND_ON_CLOSE_KEY,
  GENERAL_FIELDS,
  LOG_LEVEL_KEY,
  PERMISSION_MODE_KEY,
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
import { primaryButtonClass } from "./SettingRow";
import { ProvidersTab } from "./ProvidersTab";
import { SkillsTab } from "./SkillsTab";
import { ToolsTab } from "./ToolsTab";
import { WorkspaceTab } from "./WorkspaceTab";

export type SettingsCategory =
  | "general"
  | "workspace"
  | "providers"
  | "tools"
  | "skills"
  | "connectors"
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

// Every key a tab edits must be here, or Save never sees the change.
const CONFIG_KEYS = [
  ...GENERAL_FIELDS.map((f) => f.key),
  LOG_LEVEL_KEY,
  PERMISSION_MODE_KEY,
  ...WORKSPACE_FIELDS.map((f) => f.key),
  WORKSPACE_ROOT_KEY,
  BACKGROUND_ON_CLOSE_KEY,
];

interface SettingsModalProps {
  open: boolean;
  initialCategory?: SettingsCategory;
  onCreateSkill: () => void;
  onClose: () => void;
}

export function SettingsModal({
  open,
  initialCategory,
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

  // Portaled straight to document.body -- real, reproduced bug: rendered
  // in place (a descendant of App's own root, a sibling of BrowserPanel),
  // this modal's `fixed inset-0 z-50` backdrop visually failed to dim
  // BrowserPanel's own content even though z-index math said it should
  // (confirmed the click-blocking half of that *was* correct -- clicks
  // into BrowserPanel were genuinely blocked -- but the paint clearly
  // wasn't matching). A portal to body removes the ambiguity by
  // construction: nothing in App's own tree (BrowserPanel's canvas-based
  // remote-page view included) can end up compositing above a dialog
  // that isn't a descendant of it in the first place.
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="relative flex h-[min(840px,100vh-2rem)] w-[min(1100px,100vw-2rem)] overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--panel-bg)] shadow-[var(--shadow)]">
        <button
          type="button"
          aria-label="Close settings"
          className="absolute right-4 top-4 z-10 flex h-8 w-8 items-center justify-center rounded-lg text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
          onClick={onClose}
        >
          <CloseIcon className="h-[18px] w-[18px]" />
        </button>
        <nav className="flex w-[240px] shrink-0 flex-col border-r border-[var(--border)]">
          <div className="p-4 pb-3">
            <div className="flex h-10 items-center gap-2.5 rounded-xl border border-[var(--border)] bg-[var(--field-bg)] px-3 focus-within:border-[var(--focus)] focus-within:ring-2 focus-within:ring-[var(--focus)]/15">
              <SearchIcon className="h-[18px] w-[18px] shrink-0 text-[var(--muted)]" />
              <input
                aria-label="Search settings"
                className="w-full min-w-0 bg-transparent text-[15px] outline-none placeholder:text-[var(--muted)]"
                placeholder="Search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>
          {/* Scrolls on its own, below the search box, so its scrollbar
           * never runs up beside it. */}
          <div className="mb-3 flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-4 pb-2 pt-2">
            {filteredGroups.length === 0 && <div className="px-3 py-1 text-sm text-[var(--muted)]">No matches.</div>}
            {filteredGroups.map((group) => (
              <div key={group.label} className="flex flex-col gap-0.5">
                <div className="mb-1 px-3 text-[13px] text-[var(--muted)]">{group.label}</div>
                {group.categories.map((cat) => (
                  <button
                    key={cat.id}
                    type="button"
                    aria-current={category === cat.id ? "page" : undefined}
                    className={`flex h-9 items-center gap-3 rounded-lg px-3 text-left text-[15px] ${
                      category === cat.id
                        ? "bg-[var(--card-bg)] font-medium text-[var(--fg)]"
                        : "text-[var(--fg)]/80 hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
                    }`}
                    onClick={() => setCategory(cat.id)}
                  >
                    <cat.icon className="h-[18px] w-[18px] shrink-0" />
                    {cat.label}
                  </button>
                ))}
              </div>
            ))}
          </div>
        </nav>
        <div className="flex min-w-0 flex-1 flex-col">
          {/* Starts below the close button and stops short of the bottom
           * edge, so the scrollbar keeps clear of both. */}
          <div className="mb-3 mr-1.5 mt-14 min-h-0 flex-1 overflow-y-auto pl-10 pr-8">
            <div className="pb-8">
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
                onCreateSkill={onCreateSkill}
              />
            )}
            {category === "connectors" && <ConnectorsTab active={category === "connectors"} />}
            {category === "environment" && <EnvironmentTab active={category === "environment"} />}
            </div>
          </div>
          {showSaveBar && (
            <div className="flex items-center justify-end gap-3 border-t border-[var(--border)] px-8 py-3">
              {status && (
                <span className={`mr-auto text-sm ${status.error ? "text-red-500" : "text-[var(--muted)]"}`}>{status.text}</span>
              )}
              <button type="button" className={primaryButtonClass} disabled={!isDirty || saving} onClick={save}>
                Save changes
              </button>
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
