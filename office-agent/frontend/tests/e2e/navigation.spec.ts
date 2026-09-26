import { expect, test } from "@playwright/test";
import { freshThreadPath, waitForConnected } from "./helpers";

test("Back and Forward move between the conversation and the Scheduled pages", async ({ page }) => {
  await page.goto(freshThreadPath("nav"));
  await waitForConnected(page);

  await page.getByTitle("Pin navigation open").click();
  await page.getByTitle("Scheduled", { exact: true }).click();
  const heading = page.getByRole("heading", { name: "Scheduled tasks" });
  await expect(heading).toBeVisible();
  expect(page.url()).toContain("view=scheduled");

  await page.goBack();
  await expect(heading).not.toBeVisible();
  await expect(page.locator("textarea")).toBeVisible();

  await page.goForward();
  await expect(heading).toBeVisible();

  await page.reload();
  await expect(heading).toBeVisible();
});
