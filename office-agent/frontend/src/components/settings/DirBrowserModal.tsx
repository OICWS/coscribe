import { useEffect, useState } from "react";
import { browseDirs } from "../../lib/rest";
import type { BrowseDirsEntry } from "../../types/settings";
import { isDesktop, pickFolderNative } from "../../lib/desktop";

interface DirBrowserModalProps {
  onSelect: (path: string) => void;
  onClose: () => void;
}

/** Browser-mode-only in-app directory browser (see this file's own name)
 * -- inside a desktop shell (Tauri or Electron), this renders nothing
 * itself and instead opens the real OS folder picker via lib/desktop.ts's
 * pickFolderNative, calling onSelect/onClose with its result directly.
 * Falls back to the in-app browser below if that call fails for any
 * reason (ACL denied on Tauri, plugin not registered) -- see
 * pickFolderNative's own docstring for why that's a real, not just
 * theoretical, caught case. A plain browser tab (isDesktop() false)
 * always uses the in-app browser, same as before either native path
 * existed -- there is no OS-level picker a served-over-HTTP page could
 * call instead. */
export function DirBrowserModal({ onSelect, onClose }: DirBrowserModalProps) {
  const [useFallback, setUseFallback] = useState(!isDesktop());
  const [path, setPath] = useState<string | null>(null);
  const [parent, setParent] = useState<string | null>(null);
  const [directories, setDirectories] = useState<BrowseDirsEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isDesktop()) return;
    let cancelled = false;
    pickFolderNative()
      .then((picked) => {
        if (cancelled) return;
        if (picked) onSelect(picked);
        else onClose();
      })
      .catch(() => {
        if (!cancelled) setUseFallback(true);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!useFallback) return;
    let cancelled = false;
    browseDirs(path ?? undefined).then((res) => {
      if (cancelled) return;
      if ("error" in res) {
        setError(res.error);
        return;
      }
      setError(null);
      setPath(res.path);
      setParent(res.parent);
      setDirectories(res.directories);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [useFallback]);

  if (!useFallback) return null;

  const navigate = (target: string | null) => {
    browseDirs(target ?? undefined).then((res) => {
      if ("error" in res) {
        setError(res.error);
        return;
      }
      setError(null);
      setPath(res.path);
      setParent(res.parent);
      setDirectories(res.directories);
    });
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="flex h-[min(480px,90vh)] w-[min(480px,90vw)] flex-col rounded-xl bg-[var(--panel-bg)] p-4 shadow-[var(--shadow)]">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-medium">Browse directories</span>
          <button type="button" className="text-[var(--muted)]" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="mb-2 truncate text-xs text-[var(--muted)]">{path}</div>
        <div className="flex-1 overflow-y-auto rounded-lg border border-[var(--border)]">
          {parent !== null && (
            <div
              className="cursor-pointer px-3 py-2 text-sm hover:bg-[var(--card-bg)]"
              onClick={() => navigate(parent)}
            >
              .. (up)
            </div>
          )}
          {directories.map((dir) => (
            <div
              key={dir.path}
              className="cursor-pointer px-3 py-2 text-sm hover:bg-[var(--card-bg)]"
              onClick={() => navigate(dir.path)}
            >
              {dir.name}
            </div>
          ))}
        </div>
        {error && <div className="mt-2 text-xs text-red-500">{error}</div>}
        <div className="mt-3 flex justify-end gap-2">
          <button type="button" className="rounded-md border border-[var(--border)] px-3 py-1 text-sm" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="rounded-md bg-[var(--accent)] px-3 py-1 text-sm text-[var(--accent-fg)] disabled:opacity-40"
            disabled={!path}
            onClick={() => path && onSelect(path)}
          >
            Select this folder
          </button>
        </div>
      </div>
    </div>
  );
}
