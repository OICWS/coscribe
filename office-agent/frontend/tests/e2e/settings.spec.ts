import { expect, test } from "@playwright/test";
import { freshThreadPath, waitForConnected } from "./helpers";

test("General tab: editing a field enables Save, saving persists it", async ({ page }) => {
  // Restores whatever level was selected before, so the dev .env isn't
  // left changed across runs.
  await page.goto(freshThreadPath("settings"));
  await waitForConnected(page);
  const configLoaded = page.waitForResponse((r) => r.url().includes("/api/config") && r.status() === 200);
  await page.getByRole("button", { name: "Settings" }).click();
  // Wait for the actual GET /api/config fetch to resolve before typing --
  // SettingsModal populates its fields from that response asynchronously,
  // and typing before it lands gets silently clobbered when it arrives.
  await configLoaded;

  const saveButton = page.getByRole("button", { name: "Save" });
  await expect(saveButton).toBeDisabled();

  const levels = ["Debug", "Info", "Warning", "Error"];
  const pressed = await page.locator("button[aria-pressed=true]").allInnerTexts();
  const original = levels.find((level) => pressed.includes(level)) ?? "Info";
  const target = original === "Debug" ? "Warning" : "Debug";

  await page.getByRole("button", { name: target, exact: true }).click();
  await expect(saveButton).toBeEnabled();
  await saveButton.click();

  await expect(page.getByText(/^Saved/)).toBeVisible();
  const config = await page.evaluate(() => fetch("/api/config").then((r) => r.json()));
  expect(JSON.stringify(config)).toContain(target.toUpperCase());

  await page.getByRole("button", { name: original, exact: true }).click();
  await saveButton.click();
  await expect(page.getByText(/^Saved/)).toBeVisible();
});

test("General tab: Default Mode saves and applies to the next conversation", async ({ page }) => {
  await page.goto(freshThreadPath("settings-mode"));
  await waitForConnected(page);
  const configLoaded = page.waitForResponse((r) => r.url().includes("/api/config") && r.status() === 200);
  await page.getByRole("button", { name: "Settings" }).click();
  await configLoaded;

  const saveButton = page.getByRole("button", { name: "Save" });
  await page.getByRole("button", { name: "Plan", exact: true }).click();
  await expect(saveButton).toBeEnabled();
  await saveButton.click();
  await expect(page.getByText(/^Saved/)).toBeVisible();

  await page.goto(freshThreadPath("settings-mode-after"));
  await waitForConnected(page);
  await expect(page.getByRole("button", { name: "Plan", exact: true })).toBeVisible();

  const restoreLoaded = page.waitForResponse((r) => r.url().includes("/api/config") && r.status() === 200);
  await page.getByRole("button", { name: "Settings" }).click();
  await restoreLoaded;
  await page.getByRole("button", { name: "Auto", exact: true }).click();
  await saveButton.click();
  await expect(page.getByText(/^Saved/)).toBeVisible();
});

test("Tools tab: lists built-in tools grouped by category", async ({ page }) => {
  await page.goto(freshThreadPath("settings-tools"));
  await waitForConnected(page);
  await page.getByRole("button", { name: "Settings" }).click();
  const toolsLoaded = page.waitForResponse((r) => r.url().includes("/api/tools") && r.status() === 200);
  await page.getByRole("button", { name: "Tools" }).click();
  await toolsLoaded;

  await expect(page.getByText("list_files")).toBeVisible();
  await expect(page.getByText("requires approval").first()).toBeVisible();
});
