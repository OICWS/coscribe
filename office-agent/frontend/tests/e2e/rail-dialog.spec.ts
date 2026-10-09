import { expect, test } from "@playwright/test";
import { freshThreadPath, waitForConnected } from "./helpers";

test("a dialog opened from a hover-opened sidebar is not closed with the sidebar", async ({ page }) => {
  await page.goto(freshThreadPath("rail-dialog"));
  await waitForConnected(page);
  await page.locator("textarea").fill("Reply with just OK.");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 30_000 });

  // Not pinned: the pointer over the top-left corner opens it.
  await page.mouse.move(20, 20);
  const row = page.getByTestId("thread-row").first();
  await expect(row).toBeVisible();
  await row.click({ button: "right" });
  await page.getByRole("menuitem", { name: "Edit environment" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit environment" });
  await expect(dialog).toBeVisible();

  // Into the dialog, well clear of the sidebar, and longer than its delay.
  await page.mouse.move(700, 400);
  await page.waitForTimeout(1500);
  await expect(dialog).toBeVisible();

  // The window losing focus (the Windows screenshot tool, an alt-tab) collapses a
  // hover-opened sidebar too.
  await page.evaluate(() => window.dispatchEvent(new Event("blur")));
  await page.waitForTimeout(500);
  await expect(dialog).toBeVisible();

  // Closed, and the pointer still out of the sidebar: it goes with the next move.
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await page.mouse.move(720, 420);
  await expect(page.getByTestId("thread-row")).toHaveCount(0, { timeout: 3000 });
});

test("a click beside the Edit environment dialog keeps what was typed", async ({ page }) => {
  await page.goto(freshThreadPath("env-dirty"));
  await waitForConnected(page);
  await page.locator("textarea").fill("Reply with just OK.");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 30_000 });
  await page.getByTitle("Pin navigation open").click();
  await page.getByTestId("thread-row").first().click({ button: "right" });
  await page.getByRole("menuitem", { name: "Edit environment" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit environment" });

  // Nothing typed: a click beside it closes it.
  await page.mouse.click(1270, 650);
  await expect(dialog).toHaveCount(0);

  await page.getByTestId("thread-row").first().click({ button: "right" });
  await page.getByRole("menuitem", { name: "Edit environment" }).click();
  await dialog.getByRole("button", { name: "Add variable" }).click();
  await dialog.getByLabel("Variable name").fill("MODE");
  await page.mouse.click(1270, 650);
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Variable name")).toHaveValue("MODE");
});
