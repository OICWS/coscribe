import { useContext } from "react";
import type { BlockedWrite } from "../lib/transcriptGrouping";
import { FolderActionsContext } from "./folderActions";

function shortName(path: string): string {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

/** Shown where a script was refused a write: says where, and puts the fix
 * one click away instead of leaving the model to guess. */
export function BlockedFolderCard({ blocked }: { blocked: BlockedWrite }) {
  const actions = useContext(FolderActionsContext);
  if (!actions) return null;
  const { folder } = blocked;
  const added = folder !== null && actions.folders.includes(folder);
  return (
    <div
      className="mt-2 flex max-w-full flex-col gap-2 rounded-xl border border-[var(--border)] bg-[var(--card-bg)] px-3 py-2.5 text-sm"
      data-testid="blocked-folder"
    >
      <div>
        <div className="font-medium">The script couldn’t save to a folder this conversation doesn’t have</div>
        <div className="mt-0.5 break-all text-[13px] text-[var(--muted)]">{blocked.path}</div>
      </div>
      {folder === null ? (
        <div className="text-[13px] text-[var(--muted)]">
          Use Add folder under the message box to pick the folder, then ask it to try again.
        </div>
      ) : added ? (
        <div className="flex items-center gap-3">
          <span className="text-[13px] text-[var(--success)]">Added {shortName(folder)}</span>
          <button
            type="button"
            disabled={actions.busy}
            className="rounded-md border border-[var(--border)] px-2.5 py-1 text-[13px] hover:bg-[var(--bg)] disabled:opacity-50"
            onClick={actions.onTryAgain}
          >
            Try again
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={actions.busy}
            title={folder}
            className="rounded-md bg-[var(--accent)] px-2.5 py-1 text-[13px] text-[var(--accent-fg)] disabled:opacity-50"
            onClick={() => actions.onAddFolder(folder)}
          >
            Add “{shortName(folder)}”
          </button>
          <span className="text-[13px] text-[var(--muted)]">
            The AI can then read and change files in it. Anything you don’t add stays out of reach of scripts’ writes.
          </span>
        </div>
      )}
    </div>
  );
}
