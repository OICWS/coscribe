import { expect, test } from "@playwright/test";
import { freshThreadPath, waitForConnected } from "./helpers";

test("General tab: a change saves itself", async ({ page }) => {
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

  await expect(page.getByRole("button", { name: "Save changes" })).toHaveCount(0);

  const levels = ["Debug", "Info", "Warning", "Error"];
  const pressed = await page.locator("button[aria-pressed=true]").allInnerTexts();
  const original = levels.find((level) => pressed.includes(level)) ?? "Info";
  const target = original === "Debug" ? "Warning" : "Debug";

  await page.getByRole("button", { name: target, exact: true }).click();
  await expect(page.getByRole("status")).toHaveText(/^Saved/);
  const config = await page.evaluate(() => fetch("/api/config").then((r) => r.json()));
  expect(JSON.stringify(config)).toContain(target.toUpperCase());

  await page.getByRole("button", { name: original, exact: true }).click();
  await expect(page.getByRole("status")).toHaveText(/^Saved/);
});

test("General tab: Default Mode saves", async ({ page }) => {
  await page.goto(freshThreadPath("settings-mode"));
  await waitForConnected(page);
  const configLoaded = page.waitForResponse((r) => r.url().includes("/api/config") && r.status() === 200);
  await page.getByRole("button", { name: "Settings" }).click();
  await configLoaded;

  await page.getByRole("button", { name: "Plan", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText(/^Saved/);

  const config = await page.evaluate(() => fetch("/api/config").then((r) => r.json()));
  expect(config.COSCRIBE_DEFAULT_PERMISSION_MODE).toBe("plan");

  await page.getByRole("button", { name: "Auto", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText(/^Saved/);
});

test("Tools tab: categories start collapsed and open to list their tools", async ({ page }) => {
  await page.goto(freshThreadPath("settings-tools"));
  await waitForConnected(page);
  await page.getByRole("button", { name: "Settings" }).click();
  const toolsLoaded = page.waitForResponse((r) => r.url().includes("/api/tools") && r.status() === 200);
  await page.getByRole("button", { name: "Tools" }).click();
  await toolsLoaded;

  await expect(page.getByText("list_files")).toHaveCount(0);
  const filesystem = page.getByRole("button", { name: /^Filesystem/ });
  await expect(filesystem).toHaveAttribute("aria-expanded", "false");
  await filesystem.click();
  await expect(page.getByText("list_files")).toBeVisible();
  await expect(page.getByText("Needs approval").first()).toBeVisible();
});

test("Skills tab: a skill switched off from its menu shows Off, and back on", async ({ page }) => {
  await page.goto(freshThreadPath("settings-skills"));
  await waitForConnected(page);
  await page.getByRole("button", { name: "Settings" }).click();
  const skillsLoaded = page.waitForResponse((r) => r.url().endsWith("/api/skills") && r.status() === 200);
  await page.getByRole("button", { name: "Skills", exact: true }).click();
  await skillsLoaded;

  const row = page.getByRole("button", { name: "Open Word Documents" });
  const options = page.getByRole("button", { name: "Options for Word Documents", exact: true });
  await options.click();
  await page.getByRole("menuitem", { name: "Turn off" }).click();
  await expect(row.getByText("Off", { exact: true })).toBeVisible();
  // Built-in skills can only be switched off, never removed.
  await options.click();
  await expect(page.getByRole("menuitem", { name: "Remove" })).toHaveCount(0);
  await page.getByRole("menuitem", { name: "Turn on" }).click();
  await expect(row.getByText("Off", { exact: true })).toHaveCount(0);
});
