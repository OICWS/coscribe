import { useState } from "react";
import { CloseIcon, FolderIcon } from "./icons";
import { DirBrowserModal } from "./settings/DirBrowserModal";

function folderName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

/** The folder a task's runs read and write in; empty is the app's
 * default workspace. */
export function WorkspacePicker({ value, onChange }: { value: string; onChange: (path: string) => void }) {
  const [browsing, setBrowsing] = useState(false);
  return (
    <span className="inline-flex min-w-0 items-center gap-1">
      <button
        type="button"
        title={value || "Runs use the default workspace"}
        className="flex min-w-0 items-center gap-1.5 rounded-md px-1 py-0.5 text-[var(--muted)] hover:text-[var(--fg)]"
        onClick={() => setBrowsing(true)}
      >
        <FolderIcon className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">{value ? folderName(value) : "Default workspace"}</span>
      </button>
      {value && (
        <button
          type="button"
          aria-label="Use the default workspace"
          title="Use the default workspace"
          className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
          onClick={() => onChange("")}
        >
          <CloseIcon className="h-3 w-3" />
        </button>
      )}
      {browsing && (
        <DirBrowserModal
          onClose={() => setBrowsing(false)}
          onSelect={(path) => {
            setBrowsing(false);
            onChange(path);
          }}
        />
      )}
    </span>
  );
}
