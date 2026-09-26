import { useRef, useState } from "react";
import { readStored, writeStored } from "../lib/storage";
import { useClickOutside } from "../lib/useClickOutside";
import { CheckIcon, FolderIcon, PlusIcon } from "./icons";
import { DirBrowserModal } from "./settings/DirBrowserModal";

const RECENT_KEY = "coscribe.recentFolders";
const MAX_RECENT = 6;

function folderName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

function buttonLabel(folders: string[]): string {
  if (folders.length === 0) return "Add folder";
  const main = folderName(folders[0]);
  return folders.length === 1 ? main : `${main} +${folders.length - 1}`;
}

function readRecent(): string[] {
  try {
    const saved: unknown = JSON.parse(readStored(RECENT_KEY) ?? "[]");
    return Array.isArray(saved) ? saved.filter((p): p is string => typeof p === "string") : [];
  } catch {
    return [];
  }
}

function remember(paths: string[]) {
  const merged = [...paths, ...readRecent().filter((p) => !paths.includes(p))];
  writeStored(RECENT_KEY, JSON.stringify(merged.slice(0, MAX_RECENT)));
}

interface FolderPickerProps {
  /** Attached folders, the main one first. */
  folders: string[];
  disabled: boolean;
  onChange: (folders: string[]) => void;
}

/** The folders this conversation reads and writes in. A checked row is
 * attached; clicking toggles it. Recently used folders stay listed so
 * one removed by mistake is a click away. */
export function FolderPicker({ folders, disabled, onChange }: FolderPickerProps) {
  const [open, setOpen] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  useClickOutside(rootRef, () => setOpen(false), open);

  const recent = open ? readRecent().filter((p) => !folders.includes(p)) : [];
  const rows = [...folders, ...recent];

  const apply = (next: string[]) => {
    remember(next);
    onChange(next);
  };
  const toggle = (path: string) =>
    apply(folders.includes(path) ? folders.filter((p) => p !== path) : [...folders, path]);

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        disabled={disabled}
        title={folders.length > 0 ? folders.join("\n") : "Let the agent work in a folder on this computer"}
        aria-expanded={open}
        className="flex h-8 max-w-48 items-center rounded-lg px-2 text-sm text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)] disabled:opacity-50 disabled:hover:bg-transparent"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="truncate">{buttonLabel(folders)}</span>
      </button>
      {open && (
        <div className="absolute bottom-full left-0 z-20 mb-1 w-72 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] p-1 shadow-[var(--shadow)]">
          {rows.length > 0 && (
            <ul className="max-h-64 overflow-y-auto">
              {rows.map((path) => {
                const attached = folders.includes(path);
                return (
                  <li key={path}>
                    <button
                      type="button"
                      role="menuitemcheckbox"
                      aria-checked={attached}
                      title={path}
                      className="flex w-full min-w-0 items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm hover:bg-[var(--card-bg)]"
                      onClick={() => toggle(path)}
                    >
                      <FolderIcon className="h-4 w-4 shrink-0 text-[var(--muted)]" />
                      <span className="flex min-w-0 flex-1 flex-col">
                        <span className="truncate">{folderName(path)}</span>
                        <span className="truncate text-xs text-[var(--muted)]">{path}</span>
                      </span>
                      {attached && <CheckIcon className="h-4 w-4 shrink-0 text-[var(--fg)]" />}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {rows.length > 0 && <div className="mx-1 my-1 border-t border-[var(--border)]" />}
          <button
            type="button"
            className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm hover:bg-[var(--card-bg)]"
            onClick={() => {
              setOpen(false);
              setBrowsing(true);
            }}
          >
            <PlusIcon className="h-4 w-4 shrink-0 text-[var(--muted)]" />
            Add folder…
          </button>
        </div>
      )}
      {browsing && (
        <DirBrowserModal
          onClose={() => setBrowsing(false)}
          onSelect={(path) => {
            setBrowsing(false);
            if (!folders.includes(path)) apply([...folders, path]);
          }}
        />
      )}
    </div>
  );
}
