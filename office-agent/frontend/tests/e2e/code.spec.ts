import { expect, test } from "@playwright/test";
import { freshThreadPath, waitForConnected } from "./helpers";

test("Settings has a Code section", async ({ page }) => {
  await page.goto(freshThreadPath("code-settings"));
  await waitForConnected(page);

  await page.getByTitle("Settings").click();
  await page.getByRole("button", { name: "Code", exact: true }).click();

  await expect(page.getByRole("heading", { name: "Code module" })).toBeVisible();
  await expect(page.getByLabel("Model for code")).toBeVisible();
  await expect(page.getByRole("switch", { name: "Chats can hand tasks to Code" })).toBeVisible();
});

test("The nav rail has no Code mode", async ({ page }) => {
  await page.goto(freshThreadPath("code-nav"));
  await waitForConnected(page);

  await page.getByTitle("Pin navigation open").click();
  await expect(page.getByTitle("Scheduled", { exact: true })).toBeVisible();
  await expect(page.getByTitle("Code", { exact: true })).toHaveCount(0);
});
