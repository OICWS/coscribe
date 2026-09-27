import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { CloseIcon, PlusIcon } from "./icons";
import { getAllowedSites, setAllowedSites } from "../lib/electron";

/** The sites coscribe's AI may use its browser on without asking. */
export function AllowedSitesDialog({ onClose }: { onClose: () => void }) {
  const [sites, setSites] = useState<string[] | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getAllowedSites().then(setSites);
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const save = async (next: string[]) => setSites(await setAllowedSites(next));

  const add = async () => {
    const text = draft.trim();
    if (!text) return;
    if (!/^(https?:\/\/)?(localhost|[\w-]+(\.[\w-]+)+)(:\d+)?([/?#]\S*)?$/i.test(text)) {
      setError("That doesn't look like a site address.");
      return;
    }
    await save([...(sites ?? []), text]);
    setDraft("");
  };

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="allowed-sites-title"
        className="flex w-[min(480px,100vw-2rem)] flex-col gap-5 rounded-2xl border border-[var(--border)] bg-[var(--panel-bg)] p-6 shadow-[var(--shadow)]"
      >
        <div className="flex items-start gap-4">
          <div className="flex-1">
            <h2 id="allowed-sites-title" className="text-lg font-semibold">
              Allowed sites
            </h2>
            <p className="mt-1.5 text-[15px] leading-snug text-[var(--muted)]">
              coscribe can use its browser tools on these sites without asking first. Remove a site and coscribe asks again
              the next time it needs that site.
            </p>
          </div>
          <button
            type="button"
            aria-label="Close"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
            onClick={onClose}
          >
            <CloseIcon className="h-4 w-4" />
          </button>
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="allowed-site-input" className="text-sm text-[var(--muted)]">
            Add URL
          </label>
          <div className="flex h-10 items-center rounded-xl border border-[var(--border)] bg-[var(--input-bg,var(--bg))] pl-3 pr-1.5 focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-500/15">
            <input
              id="allowed-site-input"
              autoFocus
              className="min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-[var(--muted)]"
              placeholder="example.com"
              spellCheck={false}
              value={draft}
              onChange={(e) => {
                setDraft(e.target.value);
                setError(null);
              }}
              onKeyDown={(e) => e.key === "Enter" && void add()}
            />
            <button
              type="button"
              aria-label="Add site"
              disabled={!draft.trim()}
              className="flex h-7 w-7 items-center justify-center rounded-md border border-[var(--border)] text-[var(--muted)] hover:text-[var(--fg)] disabled:opacity-40"
              onClick={() => void add()}
            >
              <PlusIcon className="h-3.5 w-3.5" />
            </button>
          </div>
          {error && <p className="text-sm text-red-500">{error}</p>}
        </div>

        {sites && sites.length > 0 ? (
          <ul className="flex max-h-60 flex-col overflow-y-auto" aria-label="Allowed sites">
            {sites.map((site) => (
              <li key={site} className="flex items-center justify-between border-b border-[var(--border)] py-2 text-[15px] last:border-b-0">
                <span className="truncate">{site}</span>
                <button
                  type="button"
                  aria-label={`Remove ${site}`}
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
                  onClick={() => void save(sites.filter((s) => s !== site))}
                >
                  <CloseIcon className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[15px] text-[var(--muted)]">{sites ? "No allowed sites yet." : "Loading…"}</p>
        )}

        <div className="flex items-center justify-between">
          <button
            type="button"
            disabled={!sites?.length}
            className="h-8 rounded-lg bg-red-600 px-3 text-sm font-medium text-white hover:bg-red-700 disabled:bg-red-600/35"
            onClick={() => void save([])}
          >
            Clear all
          </button>
          <button
            type="button"
            className="h-8 rounded-lg bg-[var(--primary)] px-3.5 text-sm font-medium text-[var(--primary-fg)] hover:bg-[var(--primary-hover)]"
            onClick={onClose}
          >
            Done
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
