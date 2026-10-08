import { expect, test } from "@playwright/test";

test("a connector that needs its own app walks through setup, saves the credentials, then offers Connect", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByText("Connectors", { exact: true }).first().click();
  await page.getByRole("button", { name: "Discover", exact: true }).first().click();

  const card = page.getByRole("button", { name: "Open HubSpot" });
  await expect(card.getByText("Needs setup")).toBeVisible();
  // The + opens the setup page rather than failing to connect.
  await page.getByRole("button", { name: "Add HubSpot" }).click();

  await expect(page.getByRole("heading", { name: "Set up HubSpot" })).toBeVisible();
  await expect(page.getByText("http://localhost:47821/api/mcp/oauth/callback").first()).toBeVisible();
  await expect(page.getByRole("link", { name: /Open MCP Connectors/ })).toHaveAttribute("href", /app\.hubspot\.com/);
  await expect(page.getByRole("button", { name: "Connect", exact: true })).toHaveCount(0);

  await page.getByLabel("Client ID").fill("has a space");
  await page.getByLabel("Client secret").fill("secret-value");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("spaces");

  await page.getByLabel("Client ID").fill("client-id-123");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("HubSpot app saved")).toBeVisible();
  await expect(page.getByRole("button", { name: "Connect", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Remove app" }).click();
  await expect(page.getByRole("heading", { name: "Set up HubSpot" })).toBeVisible();
});
