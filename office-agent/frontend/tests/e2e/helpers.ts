import { expect, type Page } from "@playwright/test";

/** A fresh, random thread id per test -- avoids collisions between test
 * runs and with any real conversation history sitting in the dev
 * backend's state dir. */
export function freshThreadPath(prefix: string): string {
  const id = `${prefix}-${Math.random().toString(36).slice(2, 8)}`;
  return `/?thread=${id}`;
}

/** React 18 StrictMode double-invokes effects in dev (see frontend
 * README's "known dev-mode-only artifact" note): the first WebSocket
 * connect() call opens, then is immediately torn down by the first
 * effect cleanup, and a second call opens the socket that actually
 * stays alive. Interacting before that second socket's "state" event
 * lands risks racing the teardown of the first one -- wait for the
 * model pill to show a real model (not the "Select model" placeholder)
 * as a reliable signal the surviving connection is up.
 *
 * The initial render gate used to be a "Sessions" button, then a
 * "Toggle navigation" button -- both removed by later NavRail.tsx
 * changes. It's now a pin/unpin toggle whose accessible name (from its
 * `title` attribute -- see NavRail.tsx) flips between "Pin navigation
 * open" and "Unpin navigation" depending on pinned state, so this
 * matches on the shared "navigation" substring rather than the exact
 * label -- a real, live-caught staleness bug: the exact-label match
 * above silently timed out on every spec in this suite once the label
 * changed, none of them actually exercising anything by that point.
 * Waiting on it instead of jumping straight to the model-pill check
 * keeps the same "app has rendered at all" gate this helper always had. */
export async function waitForConnected(page: Page): Promise<void> {
  await page.getByRole("button", { name: /navigation/i }).waitFor();
  await expect(page.getByTestId("model-pill")).not.toHaveText("Select model", { timeout: 10_000 });
}
