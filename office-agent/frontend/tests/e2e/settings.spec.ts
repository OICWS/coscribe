import { expect, test } from "@playwright/test";
import { freshThreadPath, waitForConnected } from "./helpers";

test("General tab: editing a field enables Save, saving persists it", async ({ page }) => {
  // COSCRIBE_LOG_LEVEL rejects blank server-side, so capture whatever
  // was already configured and restore it afterwards rather than clearing
  // the field -- avoids polluting the dev .env across runs.
  const before = await page.request.get("/api/config").then((r) => r.json());
  const original = before.COSCRIBE_LOG_LEVEL ?? "";

  await page.goto(freshThreadPath("settings"));
  await waitForConnected(page);
  const configLoaded = page.waitForResponse((r) => r.url().includes("/api/config") && r.status() === 200);
  await page.getByRole("button", { name: "Settings" }).click();
  // Wait for the actual GET /api/config fetch to resolve before typing --
  // SettingsModal populates its fields from that response asynchronously,
  // and typing before it lands gets silently clobbered when it arrives.
  await configLoaded;

  const logLevelInput = page.locator("input[placeholder='INFO']");
  await logLevelInput.waitFor();
  const saveButton = page.getByRole("button", { name: "Save" });
  await expect(saveButton).toBeDisabled();

  await logLevelInput.fill("DEBUG");
  await expect(saveButton).toBeEnabled();
  await saveButton.click();

  await expect(page.getByText(/^Saved/)).toBeVisible();

  const config = await page.evaluate(() => fetch("/api/config").then((r) => r.json()));
  expect(config.COSCRIBE_LOG_LEVEL).toBe("DEBUG");

  // COSCRIBE_LOG_LEVEL can't be unset once written (blank is rejected),
  // so an originally-unset value restores to a sensible default instead.
  await logLevelInput.fill(original || "INFO");
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
