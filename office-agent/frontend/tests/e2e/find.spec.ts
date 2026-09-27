import { expect, test } from "@playwright/test";
import { freshThreadPath, waitForConnected } from "./helpers";

test("Ctrl+F finds text in the conversation and steps through matches", async ({ page }) => {
  await page.goto(freshThreadPath("find"));
  await waitForConnected(page);
  const textarea = page.locator("textarea");
  await textarea.click();
  await textarea.fill("Reply with exactly: marmalade marmalade");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 30_000 });

  await page.getByTestId("chat-log").click();
  await page.keyboard.press("Control+f");
  const find = page.getByLabel("Find in page");
  await expect(find).toBeFocused();
  await find.fill("marmalade");
  const search = page.getByRole("search");
  await expect(search).toContainText(/1\/[2-9]/);
  await page.keyboard.press("Enter");
  await expect(search).toContainText(/2\/[2-9]/);
  await page.keyboard.press("Shift+Enter");
  await expect(search).toContainText(/1\/[2-9]/);
  expect(await page.evaluate(() => CSS.highlights.has("coscribe-find"))).toBe(true);

  await page.keyboard.press("Escape");
  await expect(find).toHaveCount(0);
  expect(await page.evaluate(() => CSS.highlights.size)).toBe(0);
});
