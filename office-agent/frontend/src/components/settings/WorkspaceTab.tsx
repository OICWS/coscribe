import { useState } from "react";
import { DirBrowserModal } from "./DirBrowserModal";
import { CloseIcon, FolderIcon } from "../icons";
import { SettingRow, SettingRowInput, SettingRows, SettingsSection, fieldClass, secondaryButtonClass } from "./SettingRow";
import { SKILLS_DIR_KEY, WORKSPACE_FIELDS, WORKSPACE_ROOT_KEY, type DirEntry, type DirPermission } from "./fields";

interface WorkspaceTabProps {
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
  dirEntries: DirEntry[];
  onDirEntriesChange: (entries: DirEntry[]) => void;
}

export function WorkspaceTab({ values, onChange, dirEntries, onDirEntriesChange }: WorkspaceTabProps) {
  const [addValue, setAddValue] = useState("");
  const [browserOpen, setBrowserOpen] = useState(false);
  const [browserTarget, setBrowserTarget] = useState<"root" | "skills" | "extra">("extra");

  const setPermission = (path: string, permission: DirPermission) => {
    onDirEntriesChange(dirEntries.map((e) => (e.path === path ? { ...e, permission } : e)));
  };

  const removeEntry = (path: string) => {
    onDirEntriesChange(dirEntries.filter((e) => e.path !== path));
  };

  const addPaths = (raw: string) => {
    const paths = raw
      .split(",")
      .map((p) => p.trim())
      .filter((p) => p.length > 0);
    if (paths.length === 0) return;
    const existing = new Set(dirEntries.map((e) => e.path));
    const additions = paths.filter((p) => !existing.has(p)).map((path) => ({ path, permission: "read" as const }));
    if (additions.length > 0) onDirEntriesChange([...dirEntries, ...additions]);
    setAddValue("");
  };

  const browse = (target: "root" | "skills" | "extra") => {
    setBrowserTarget(target);
    setBrowserOpen(true);
  };
  const pathRow = (key: string, label: string, description: string, placeholder: string, target?: "root" | "skills") => (
    <SettingRow
      key={key}
      label={label}
      description={description}
      control={
        <div className="flex items-center gap-2">
          <SettingRowInput value={values[key] ?? ""} placeholder={placeholder} onChange={(v) => onChange(key, v)} />
          {target && (
            <button type="button" className={secondaryButtonClass} onClick={() => browse(target)}>
              Browse…
            </button>
          )}
        </div>
      }
    />
  );
  const field = (key: string) => WORKSPACE_FIELDS.find((f) => f.key === key)!;
  const fieldRow = (key: string, target?: "skills") =>
    pathRow(key, field(key).label, field(key).description, field(key).placeholder, target);

  return (
    <div className="flex flex-col gap-10">
      <SettingsSection title="Folders">
        <SettingRows>
          {pathRow(
            WORKSPACE_ROOT_KEY,
            "Default workspace",
            "Where a conversation works when you don't pick a folder for it.",
            "./workspace",
            "root",
          )}
          {fieldRow(SKILLS_DIR_KEY, "skills")}
        </SettingRows>
      </SettingsSection>

      <SettingsSection
        title="Other folders"
        description="coscribe can always read and change the workspace. Give it more folders here, each read-only or read and write."
      >
        <div className="mt-3 flex flex-col overflow-hidden rounded-xl border border-[var(--border)]">
          <div className="flex items-center gap-3 px-4 py-3 text-sm">
            <FolderIcon className="h-4 w-4 shrink-0 text-[var(--muted)]" />
            <span className="min-w-0 flex-1 truncate">{values[WORKSPACE_ROOT_KEY] || "(not set)"}</span>
            <span className="text-[13px] text-[var(--muted)]">Workspace · read and write</span>
          </div>
          {dirEntries.map((entry) => (
            <div key={entry.path} className="flex items-center gap-3 border-t border-[var(--border)] px-4 py-2.5 text-sm">
              <FolderIcon className="h-4 w-4 shrink-0 text-[var(--muted)]" />
              <span className="min-w-0 flex-1 truncate">{entry.path}</span>
              <select
                aria-label={`Access to ${entry.path}`}
                className={`${fieldClass} h-8 px-2 text-[13px]`}
                value={entry.permission}
                onChange={(e) => setPermission(entry.path, e.target.value as DirPermission)}
              >
                <option value="read">Read only</option>
                <option value="read-write">Read and write</option>
              </select>
              <button
                type="button"
                aria-label={`Remove ${entry.path}`}
                className="flex h-7 w-7 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-red-500"
                onClick={() => removeEntry(entry.path)}
              >
                <CloseIcon className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
        <div className="mt-3 flex gap-2">
          <input
            aria-label="Folder to add"
            className={`${fieldClass} min-w-0 flex-1`}
            placeholder="Paste a folder path, or several separated by commas"
            value={addValue}
            onChange={(e) => setAddValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") addPaths(addValue);
            }}
          />
          <button type="button" className={secondaryButtonClass} disabled={!addValue.trim()} onClick={() => addPaths(addValue)}>
            Add
          </button>
          <button type="button" className={secondaryButtonClass} onClick={() => browse("extra")}>
            Browse…
          </button>
        </div>
      </SettingsSection>

      <SettingsSection title="Files">
        <SettingRows>{fieldRow("COSCRIBE_MEMORY_PATH")}</SettingRows>
      </SettingsSection>

      <SettingsSection title="Advanced">
        <SettingRows>
          {fieldRow("COSCRIBE_MCP_CONFIG_PATH")}
          {fieldRow("COSCRIBE_PROVIDERS_CONFIG_PATH")}
          {fieldRow("COSCRIBE_HOOKS_CONFIG_PATH")}
        </SettingRows>
      </SettingsSection>

      {browserOpen && (
        <DirBrowserModal
          onClose={() => setBrowserOpen(false)}
          onSelect={(path) => {
            if (browserTarget === "root") {
              onChange(WORKSPACE_ROOT_KEY, path);
            } else if (browserTarget === "skills") {
              onChange(SKILLS_DIR_KEY, path);
            } else {
              addPaths(path);
            }
            setBrowserOpen(false);
          }}
        />
      )}
    </div>
  );
}
