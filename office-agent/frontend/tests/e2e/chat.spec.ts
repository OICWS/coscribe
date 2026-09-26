import { expect, test } from "@playwright/test";
import { freshThreadPath, waitForConnected } from "./helpers";

test("chat round trip: send a message, get a streamed then finalized reply", async ({ page }) => {
  await page.goto(freshThreadPath("chat"));
  await waitForConnected(page);

  const textarea = page.locator("textarea");
  await textarea.click();
  await textarea.fill("What is 2+2? Answer in one short sentence.");
  await page.keyboard.press("Enter");

  // User bubble appears immediately (optimistic, no round trip needed).
  await expect(page.getByText("What is 2+2? Answer in one short sentence.")).toBeVisible();

  // Real backend reply -- generous timeout for a real LLM call. Scoped to
  // the chat log specifically: the usage bar's "4.9k / 1.0M" text also
  // contains a bare "4", which would otherwise ambiguously match too.
  await expect(page.getByTestId("chat-log").getByText(/4|four/i)).toBeVisible({ timeout: 20_000 });

  // turnInFlight clears once agent_message finalizes -- Send button returns.
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();
});

test("/clear wipes the chat log", async ({ page }) => {
  await page.goto(freshThreadPath("clear"));
  await waitForConnected(page);
  const textarea = page.locator("textarea");
  await textarea.click();
  await textarea.fill("hello");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("chat-log").getByText("hello", { exact: true })).toBeVisible();

  // /clear respects the turn lock (only /stop bypasses it, per
  // web/session.py's request_stop() docstring) -- if it's sent while
  // "hello"'s turn is still in flight, it just queues behind it instead
  // of running immediately. Wait for that turn to actually finish first,
  // same as a real user would naturally do.
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 20_000 });

  await textarea.click();
  await textarea.fill("/clear");
  await page.keyboard.press("Enter");

  await expect(page.getByText("Cleared this thread's conversation history.")).toBeVisible();
  // Scoped to the chat log, not the page: the header keeps showing the
  // thread's own derived title ("hello", from its first message) after
  // /clear -- that's correct, unrelated product behavior, not something
  // /clear is supposed to wipe.
  await expect(page.getByTestId("chat-log").getByText("hello", { exact: true })).not.toBeVisible();
});
