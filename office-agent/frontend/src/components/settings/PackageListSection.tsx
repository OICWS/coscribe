import { useEffect, useState } from "react";
import type { ScriptEnvInstallResult, ScriptEnvPackage } from "../../types/settings";

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
  note: string;
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
  note,
  placeholder,
  emptyStateText,
  active,
  getPackages,
  installPackage,
  removePackage,
}: PackageListSectionProps) {
  const [packages, setPackages] = useState<ScriptEnvPackage[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [query, setQuery] = useState("");
  const [installing, setInstalling] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null);

  const refresh = () => {
    getPackages().then((res) => {
      setPackages(res);
      setLoaded(true);
    });
  };

  useEffect(() => {
    if (active) refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

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
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-lg border border-[var(--border)] bg-[var(--card-bg)] p-3">
        <p className="text-sm font-medium">{title}</p>
        <p className="mt-1 text-sm">{description}</p>
        <p className="mt-1.5 text-xs text-[var(--muted)]">{note}</p>
      </div>

      <div className="flex items-center gap-2">
        <input
          className="min-w-0 flex-1 rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:border-[var(--accent)]"
          placeholder={placeholder}
          value={query}
          disabled={installing}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && install()}
        />
        <button
          type="button"
          className="shrink-0 rounded-md bg-[var(--accent)] px-3 py-1.5 text-sm font-medium text-[var(--accent-fg)] disabled:opacity-40"
          disabled={installing || !query.trim()}
          onClick={install}
        >
          {installing ? (
            <span className="flex items-center gap-1.5">
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-[var(--accent-fg)] border-t-transparent" />
              Adding
            </span>
          ) : (
            "Add"
          )}
        </button>
      </div>

      {status && (
        <div className={`rounded-md px-3 py-2 text-xs ${status.error ? "bg-red-500/10 text-red-500" : "bg-[var(--accent)]/10 text-[var(--accent)]"}`}>
          {status.text}
        </div>
      )}

      <div className="flex flex-col gap-1">
        {packages.map((pkg) => (
          <div
            key={pkg.name}
            className="flex items-center justify-between rounded-lg border border-[var(--border)] px-3 py-2 text-sm"
          >
            <div className="flex min-w-0 items-baseline gap-2">
              <span className="truncate font-mono font-medium">{pkg.name}</span>
              <span className="shrink-0 text-xs text-[var(--muted)]">{pkg.version}</span>
            </div>
            <button
              type="button"
              className="shrink-0 rounded-md px-2 py-0.5 text-[var(--muted)] hover:bg-red-500/10 hover:text-red-500 disabled:opacity-40"
              disabled={removing === pkg.name}
              onClick={() => remove(pkg.name)}
              title={`Remove ${pkg.name}`}
            >
              {removing === pkg.name ? "…" : "×"}
            </button>
          </div>
        ))}
        {loaded && packages.length === 0 && (
          <div className="rounded-lg border border-dashed border-[var(--border)] px-3 py-4 text-center text-sm text-[var(--muted)]">
            {emptyStateText}
          </div>
        )}
      </div>
    </div>
  );
}
