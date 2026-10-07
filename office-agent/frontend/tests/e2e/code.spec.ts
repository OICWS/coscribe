import { expect, test } from "@playwright/test";
import { freshThreadPath, waitForConnected } from "./helpers";

test("Code is a third mode with its own sessions, and Chat leads back", async ({ page }) => {
  await page.goto(freshThreadPath("code-nav"));
  await waitForConnected(page);
  const chatThread = new URL(page.url()).searchParams.get("thread");

  await page.getByTitle("Pin navigation open").click();
  await page.getByTitle("Code", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "What should we build?" })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("thread")).toMatch(/^code-/);
  await expect(page.getByRole("button", { name: "New code session" })).toBeVisible();
  await expect(page.locator("textarea")).toHaveAttribute("placeholder", "Describe what to build, run or fix");
  // What only the chat's own agent uses isn't offered here.
  await expect(page.getByTitle("Browser")).toHaveCount(0);
  await expect(page.getByTitle("Sub Agents")).toHaveCount(0);

  await page.getByTitle("Chat", { exact: true }).click();
  await expect(page.getByRole("button", { name: "New session" })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("thread")).not.toMatch(/^code-/);
  expect(chatThread).not.toBeNull();
});

test("Settings has a Code section", async ({ page }) => {
  await page.goto(freshThreadPath("code-settings"));
  await waitForConnected(page);

  await page.getByTitle("Settings").click();
  await page.getByRole("button", { name: "Code", exact: true }).click();

  await expect(page.getByRole("heading", { name: "Code module" })).toBeVisible();
  await expect(page.getByLabel("Model for code")).toBeVisible();
  await expect(page.getByRole("switch", { name: "Chats can hand tasks to Code" })).toBeVisible();
  await expect(page.getByRole("switch", { name: "Use your instructions and memory" })).toHaveAttribute(
    "aria-checked",
    "true",
  );
});

test("Settings > Code can allow commands and file changes for good", async ({ page }) => {
  await page.goto(freshThreadPath("code-approvals"));
  await waitForConnected(page);

  await page.getByTitle("Settings").click();
  await page.getByRole("button", { name: "Code", exact: true }).click();

  const commands = page.getByRole("switch", { name: "Run commands without asking" });
  await expect(commands).toHaveAttribute("aria-checked", "false");
  await commands.click();
  await expect(commands).toHaveAttribute("aria-checked", "true");
  await commands.click();
  await expect(commands).toHaveAttribute("aria-checked", "false");
});
