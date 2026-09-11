import { expect, test } from "@playwright/test";
import { freshThreadPath, waitForConnected } from "./helpers";

test("approval-gated tool call: approve card appears, approving runs the tool and finishes the turn", async ({
  page,
}) => {
  await page.goto(freshThreadPath("approval"));
  await waitForConnected(page);
  const textarea = page.locator("textarea");
  await textarea.click();
  await textarea.fill(
    "Please create a file named e2e-approval-test.txt in the workspace with the text 'hi' using your file tools.",
  );
  await page.keyboard.press("Enter");

  await expect(page.getByText("Approve: Wrote", { exact: false })).toBeVisible({ timeout: 30_000 });
  // exact: true -- the collapsed summary toggle's own accessible name is
  // "Approve: Wrote e2e-approval-test.txt", a substring match away from
  // colliding with the real action button below it.
  await page.getByRole("button", { name: "Approve", exact: true }).click();

  await expect(page.getByText("Approved")).toBeVisible();
  await expect(page.getByText(/bytes_written/)).toBeVisible({ timeout: 20_000 });
  // reducer.ts's tool_result case merges an approved call's result onto
  // the *same* approval item rather than pushing a second "tool" one --
  // a real bug found live: it used to push a second item describing the
  // same edit, so a collapsed tool-run group's summary read "Wrote
  // e2e-approval-test.txt, Wrote e2e-approval-test.txt". Asserting
  // against the page's own full text rather than a specific locator's
  // count, since the exact element nesting SummaryLabel produces isn't
  // this test's concern -- only that the doubled phrase never appears.
  await expect(page.getByText("Wrote e2e-approval-test.txt, Wrote e2e-approval-test.txt")).not.toBeVisible();
  // The tool result still needs a second real model turn (the follow-up
  // summary) before turnInFlight clears -- two chained LLM calls, longer
  // timeout than a single-turn reply.
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 30_000 });
});
