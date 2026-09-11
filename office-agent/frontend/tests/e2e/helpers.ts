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
 * The initial render gate used to be a "Sessions" button -- removed by
 * the Nav rail/Create-Run-split redesign (NavRail.tsx), which replaced
 * it with an icon-only "Toggle navigation" button (no text content, so
 * its accessible name comes from its `title` attribute). Waiting on
 * that instead of jumping straight to the model-pill check keeps the
 * same "app has rendered at all" gate this helper always had. */
export async function waitForConnected(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Toggle navigation" }).waitFor();
  await expect(page.getByTestId("model-pill")).not.toHaveText("Select model", { timeout: 10_000 });
}
