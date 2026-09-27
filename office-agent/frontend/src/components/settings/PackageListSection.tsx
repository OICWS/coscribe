import { useState } from "react";
import { useFetchOnActive } from "../../lib/useFetchOnActive";
import type { ScriptEnvInstallResult, ScriptEnvPackage } from "../../types/settings";
import { FetchRetry } from "./FetchRetry";
import { CloseIcon } from "../icons";
import { fieldClass, secondaryButtonClass } from "./SettingRow";

// Extracted from what used to be EnvironmentTab.tsx's entire body, so the
// same add/list/remove list can be rendered twice (Python packages for
// run_python_script, Node.js packages for run_node_script) without
// duplicating ~150 lines of near-identical JSX. Same design philosophy
// as before: a plain add/remove package list, not a freeform script
// textarea -- coscribe is an office assistant used by non-technical
// people, and a shell-script box assumes exactly the knowledge this
// audience shouldn't need. "Type a package name, click a button" is the
// whole interaction.
interface PackageListSectionProps {
  title: string;
  description: string;
  placeholder: string;
  emptyStateText: string;
  active: boolean;
  getPackages: () => Promise<ScriptEnvPackage[]>;
  installPackage: (name: string) => Promise<ScriptEnvInstallResult>;
  removePackage: (name: string) => Promise<ScriptEnvInstallResult>;
}

export function PackageListSection({
  title,
  description,
  placeholder,
  emptyStateText,
  active,
  getPackages,
  installPackage,
  removePackage,
}: PackageListSectionProps) {
  const { data: packages, status: loadStatus, error: loadError, retry: refresh } = useFetchOnActive(
    active,
    getPackages,
    [] as ScriptEnvPackage[],
  );
  const [query, setQuery] = useState("");
  const [installing, setInstalling] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null);

  const install = async () => {
    const name = query.trim();
    if (!name) return;
    setInstalling(true);
    setStatus(null);
    try {
      const result = await installPackage(name);
      if (!result.success) {
        setStatus({ text: result.error || `Could not add ${name}.`, error: true });
        return;
      }
      setStatus({ text: `Added ${name}.`, error: false });
      setQuery("");
      refresh();
    } catch (err) {
      // installPackage/removePackage only return {success:false} for an
      // *expected* pip failure (bad name, no network); an unexpected one
      // -- the venv itself failing to create, the server erroring, a
      // dropped connection -- surfaces as a thrown Error from rest.ts's
      // checkOk() instead. Without this catch that exception used to
      // become a silent unhandled rejection: the spinner would stop and
      // nothing else would happen, with the real error message never
      // reaching the user (see the script-env 500 report this was
      // debugged from).
      setStatus({ text: err instanceof Error ? err.message : `Could not add ${name}.`, error: true });
    } finally {
      setInstalling(false);
    }
  };

  const remove = async (name: string) => {
    setRemoving(name);
    setStatus(null);
    try {
      const result = await removePackage(name);
      if (!result.success) {
        setStatus({ text: result.error || `Could not remove ${name}.`, error: true });
        return;
      }
      refresh();
    } catch (err) {
      setStatus({ text: err instanceof Error ? err.message : `Could not remove ${name}.`, error: true });
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div className="flex flex-col gap-3 pt-5">
      <div>
        <div className="text-[15px]">{title}</div>
        <p className="mt-1 text-[13px] leading-snug text-[var(--muted)]">{description}</p>
      </div>

      <div className="flex items-center gap-2">
        <input
          aria-label={placeholder}
          className={`${fieldClass} min-w-0 flex-1`}
          placeholder={placeholder}
          value={query}
          disabled={installing}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && install()}
        />
        <button type="button" className={secondaryButtonClass} disabled={installing || !query.trim()} onClick={install}>
          {installing ? (
            <span className="flex items-center gap-1.5">
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-[var(--muted)] border-t-transparent" />
              Adding
            </span>
          ) : (
            "Add"
          )}
        </button>
      </div>

      {status && <p className={`text-[13px] ${status.error ? "text-red-500" : "text-[var(--muted)]"}`}>{status.text}</p>}

      <FetchRetry status={loadStatus} error={loadError} onRetry={refresh} />

      {packages.length > 0 && (
        <div className="flex flex-col divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]">
          {packages.map((pkg) => (
            <div key={pkg.name} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
              <div className="flex min-w-0 items-baseline gap-2">
                <span className="truncate font-mono">{pkg.name}</span>
                <span className="shrink-0 text-xs text-[var(--muted)]">{pkg.version}</span>
              </div>
              <button
                type="button"
                aria-label={`Remove ${pkg.name}`}
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-red-500 disabled:opacity-40"
                disabled={removing === pkg.name}
                onClick={() => remove(pkg.name)}
              >
                {removing === pkg.name ? "…" : <CloseIcon className="h-3.5 w-3.5" />}
              </button>
            </div>
          ))}
        </div>
      )}
      {loadStatus === "success" && packages.length === 0 && <p className="text-[13px] text-[var(--muted)]">{emptyStateText}</p>}
    </div>
  );
}
