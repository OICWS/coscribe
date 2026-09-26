import type { FetchOnActiveStatus } from "../../lib/useFetchOnActive";

/** Shared "couldn't load -- Retry" row for a useFetchOnActive-backed list
 * -- renders nothing for "idle"/"success" (the caller's own content covers
 * "success"; "idle" means the tab isn't active yet, nothing to show). */
export function FetchRetry({
  status,
  error,
  onRetry,
}: {
  status: FetchOnActiveStatus;
  error?: string | null;
  onRetry: () => void;
}) {
  if (status === "loading") {
    return <p className="text-sm text-[var(--muted)]">Loading&hellip;</p>;
  }
  if (status === "error") {
    return (
      <p className="text-sm text-red-500">
        Couldn&apos;t load{error ? `: ${error}` : ""} --{" "}
        <button type="button" className="underline" onClick={onRetry}>
          Retry
        </button>
      </p>
    );
  }
  return null;
}
