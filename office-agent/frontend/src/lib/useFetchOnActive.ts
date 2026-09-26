import { useCallback, useEffect, useState } from "react";

export type FetchOnActiveStatus = "idle" | "loading" | "error" | "success";

/** Fetch once the first time a settings tab becomes active, and never
 * silently again -- the bug this replaces (a bare `getX().then(setX)` in
 * a `useEffect` keyed on `active`, no `.catch`, no loading state): a
 * transient failure (a real, live-hit case on a fresh Windows install --
 * the packaged sidecar's cold start racing the Settings modal's first
 * open) left the tab permanently showing an empty list with no
 * indication anything went wrong, indistinguishable from "genuinely
 * nothing here" (e.g. ConnectorsTab's "No connectors match" -- read as
 * "you searched and there's zero", not "this never loaded"). Re-opening
 * the tab re-ran the same unguarded fetch, which could hit the same
 * transient failure again just as silently.
 *
 * Modeled on GlobalInstructionsSection's own hand-rolled `loaded` guard
 * (already correct, just not shared) -- fetches once per mount on first
 * activation, exposes `status`/`error` so a caller can render a real
 * "couldn't load -- Retry" state instead of an empty list, and `retry()`
 * to re-run it on demand (the *only* other time a fetch happens -- no
 * automatic refetch on every re-activation, since that's what let a
 * transient failure quietly blank an already-successfully-loaded list). */
export function useFetchOnActive<T>(active: boolean, fetcher: () => Promise<T>, initial: T) {
  const [data, setData] = useState<T>(initial);
  const [status, setStatus] = useState<FetchOnActiveStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(() => {
    setStatus("loading");
    fetcher()
      .then((result) => {
        setData(result);
        setError(null);
        setStatus("success");
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : null);
        setStatus("error");
      });
    // fetcher is expected to be stable enough not to need tracking as a
    // dependency (same assumption every existing fetch-on-active tab this
    // replaces already made, just implicitly) -- re-running run() itself
    // is always explicit (below), never re-created just because a caller
    // re-renders with a fresh closure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (active && status === "idle") run();
  }, [active, status, run]);

  return { data, status, error, retry: run };
}
