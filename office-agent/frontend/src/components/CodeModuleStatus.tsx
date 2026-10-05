import type { useCodeStatus } from "../lib/useCodeStatus";

function megabytes(bytes: number): string {
  return `${Math.round(bytes / 1_000_000)} MB`;
}

/** Whether Codex is downloaded, with Download or Remove. */
export function CodeDownload({ code, compact = false }: { code: ReturnType<typeof useCodeStatus>; compact?: boolean }) {
  const { status, error, preparing } = code;
  if (!status) return null;
  const size = status.size === null ? null : megabytes(status.size);
  let text: string;
  if (preparing) {
    text =
      status.progress === null
        ? "Getting the code module ready..."
        : `Downloading Codex ${status.version}... ${Math.round(status.progress * 100)}%`;
  } else if (status.installed) {
    text = `Codex ${status.version} is downloaded${size ? ` (${size} on disk)` : ""}.`;
  } else if (status.size === null) {
    text = "The code module has no Codex build for this computer.";
  } else {
    text = `Codex ${status.version} downloads the first time it's used, ${size} on disk.`;
  }
  let action: string | null = null;
  if (!status.installed && status.size !== null) action = "Download now";
  else if (status.installed && !compact) action = "Remove";
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-3">
        <span className="min-w-0 flex-1 text-sm text-[var(--muted)]">{text}</span>
        {action && (
          <button
            type="button"
            disabled={preparing}
            className="shrink-0 rounded-md border border-[var(--border)] px-3 py-1 text-sm hover:bg-[var(--card-bg)] disabled:opacity-50"
            onClick={status.installed ? code.remove : code.install}
          >
            {action}
          </button>
        )}
      </div>
      {preparing && status.progress !== null && (
        <div className="h-1 overflow-hidden rounded-full bg-[var(--border)]">
          <div className="h-full bg-[var(--primary)]" style={{ width: `${Math.round(status.progress * 100)}%` }} />
        </div>
      )}
      {error && <span className="text-sm text-[var(--danger)]">{error}</span>}
    </div>
  );
}
