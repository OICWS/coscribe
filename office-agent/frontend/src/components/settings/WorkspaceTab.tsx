import { useState } from "react";
import { DirBrowserModal } from "./DirBrowserModal";
import { SettingRow, SettingRowInput } from "./SettingRow";
import { WORKSPACE_FIELDS, WORKSPACE_ROOT_KEY, type DirEntry, type DirPermission } from "./fields";

interface WorkspaceTabProps {
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
  dirEntries: DirEntry[];
  onDirEntriesChange: (entries: DirEntry[]) => void;
}

export function WorkspaceTab({ values, onChange, dirEntries, onDirEntriesChange }: WorkspaceTabProps) {
  const [addValue, setAddValue] = useState("");
  const [browserOpen, setBrowserOpen] = useState(false);
  const [browserTarget, setBrowserTarget] = useState<"root" | "extra">("extra");

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

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col divide-y divide-[var(--border)]">
        <SettingRow
          label="Default Workspace"
          description="Used for any session that doesn't pick its own folder at start -- each session can have its own workspace (see the folder picker on New Session)."
          control={
            <div className="flex items-center gap-2">
              <SettingRowInput
                value={values[WORKSPACE_ROOT_KEY] ?? ""}
                placeholder="./workspace"
                onChange={(v) => onChange(WORKSPACE_ROOT_KEY, v)}
              />
              <button
                type="button"
                className="rounded-md border border-[var(--border)] px-3 py-1 text-sm hover:bg-[var(--card-bg)]"
                onClick={() => {
                  setBrowserTarget("root");
                  setBrowserOpen(true);
                }}
              >
                Browse...
              </button>
            </div>
          }
        />
        {WORKSPACE_FIELDS.map((field) => (
          <SettingRow
            key={field.key}
            label={field.label}
            description={field.description}
            control={
              <SettingRowInput
                value={values[field.key] ?? ""}
                placeholder={field.placeholder}
                onChange={(v) => onChange(field.key, v)}
              />
            }
          />
        ))}
      </div>

      <div>
        <h4 className="text-sm text-[var(--fg)]">Other Directories</h4>
        <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">
          The workspace above is always readable and writable. Give coscribe access to more folders here, each
          with its own Read only / Read &amp; Write permission.
        </p>
        <div className="mt-3 flex flex-col overflow-hidden rounded-lg border border-[var(--border)]">
          <div className="flex items-center gap-2 bg-[var(--card-bg)] px-3 py-2 text-sm">
            <span className="flex-1 truncate">{values[WORKSPACE_ROOT_KEY] || "(unset)"}</span>
            <span className="text-xs text-[var(--muted)]">Read &amp; Write · root</span>
          </div>
          {dirEntries.length === 0 ? (
            <p className="px-3 py-3 text-xs text-[var(--muted)]">No other directories added.</p>
          ) : (
            dirEntries.map((entry) => (
              <div
                key={entry.path}
                className="flex items-center gap-2 border-t border-[var(--border)] px-3 py-2 text-sm"
              >
                <span className="flex-1 truncate">{entry.path}</span>
                <select
                  className="rounded-md border border-[var(--border)] bg-transparent px-1.5 py-0.5 text-xs outline-none"
                  value={entry.permission}
                  onChange={(e) => setPermission(entry.path, e.target.value as DirPermission)}
                >
                  <option value="read">Read only</option>
                  <option value="read-write">Read &amp; Write</option>
                </select>
                <button
                  type="button"
                  className="text-xs text-[var(--muted)] hover:text-red-500"
                  onClick={() => removeEntry(entry.path)}
                >
                  Remove
                </button>
              </div>
            ))
          )}
        </div>
        <div className="mt-2 flex gap-2">
          <input
            className="flex-1 rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none focus:border-[var(--accent)]"
            placeholder="/path/to/dir, /another/path"
            value={addValue}
            onChange={(e) => setAddValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") addPaths(addValue);
            }}
          />
          <button
            type="button"
            className="rounded-md border border-[var(--border)] px-3 py-1 text-sm hover:bg-[var(--card-bg)]"
            onClick={() => addPaths(addValue)}
          >
            Add
          </button>
          <button
            type="button"
            className="rounded-md border border-[var(--border)] px-3 py-1 text-sm hover:bg-[var(--card-bg)]"
            onClick={() => {
              setBrowserTarget("extra");
              setBrowserOpen(true);
            }}
          >
            Browse...
          </button>
        </div>
      </div>

      {browserOpen && (
        <DirBrowserModal
          onClose={() => setBrowserOpen(false)}
          onSelect={(path) => {
            if (browserTarget === "root") {
              onChange(WORKSPACE_ROOT_KEY, path);
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
