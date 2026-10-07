import { expect, test } from "@playwright/test";

test("Discover lists hosted connectors with their icons, and a page shows tools and who makes it", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByText("Connectors", { exact: true }).first().click();

  // Yours holds only what was added.
  await expect(page.getByRole("button", { name: "Open Canva" })).toHaveCount(0);

  await page.getByRole("button", { name: "Discover", exact: true }).first().click();
  for (const name of ["Canva", "Notion", "Atlassian", "ClickUp", "Miro", "monday.com", "Zapier", "Microsoft Learn"]) {
    const card = page.getByRole("button", { name: `Open ${name}` });
    await expect(card).toBeVisible();
    // The service's own icon, not a letter.
    await expect(card.locator("img")).toHaveCount(1);
  }

  // Every connector has its own icon, none a letter.
  const cards = page.getByRole("button", { name: /^Open / });
  expect(await cards.count()).toBeGreaterThan(20);
  expect(await cards.locator("img").count()).toBe(await cards.count());

  await page.getByRole("button", { name: "Open Canva" }).click();
  await expect(page.getByRole("heading", { name: "Tools" })).toBeVisible();
  await expect(page.getByText("autofill-design", { exact: true })).toBeVisible();
  await expect(page.getByText("export-design", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Show all 46" }).click();
  await expect(page.getByText("export-design", { exact: true })).toBeVisible();
  await expect(page.getByText("https://mcp.canva.com/mcp")).toBeVisible();
  await expect(page.getByText("Made by")).toBeVisible();
  await expect(page.getByRole("link", { name: "Privacy policy" })).toBeVisible();
});
