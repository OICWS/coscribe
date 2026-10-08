import { expect, test } from "@playwright/test";

test("Discover lists plugins; a plugin can be previewed file by file before Add installs all its skills", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByText("Skills", { exact: true }).first().click();
  await page.getByRole("button", { name: "Discover", exact: true }).first().click();

  const card = page.getByRole("button", { name: /^Legal by Anthropic/ });
  await expect(card).toBeVisible();
  await card.click();

  // Nothing is added yet, but SKILL.md and the other files can be read.
  await expect(page.getByRole("button", { name: "Add", exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^Contents ·/ }).click();
  await expect(page.getByText(/^\/skills\/.+\/SKILL\.md$/)).toBeVisible();
  await expect(page.getByText("Loading...")).toHaveCount(0, { timeout: 20_000 });

  await page.getByRole("button", { name: /^Skills ·/ }).click();
  await expect(page.getByText("review-contract")).toBeVisible();

  await page.getByRole("button", { name: /^Connectors ·/ }).click();
  await expect(page.getByText("Slack")).toBeVisible();

  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText("Added", { exact: true })).toBeVisible({ timeout: 60_000 });

  await page.getByRole("button", { name: "Discover" }).first().click();
  await page.getByRole("button", { name: "Yours", exact: true }).click();
  await expect(page.getByText("review-contract").first()).toBeVisible();
});
