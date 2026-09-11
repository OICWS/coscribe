import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { freshThreadPath, waitForConnected } from "./helpers";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

test("attaching a file shows a chip and folds a workspace-note into the outgoing message", async ({ page }) => {
  await page.goto(freshThreadPath("attach"));
  await waitForConnected(page);

  const filePath = path.join(__dirname, "fixtures", "sample.txt");
  await page.locator("input[type=file]").setInputFiles(filePath);

  await expect(page.getByText("sample.txt")).toBeVisible();

  await page.locator("textarea").click();
  await page.locator("textarea").fill("please summarize this");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByText("please summarize this")).toBeVisible();
  // Chip clears once the message actually sends.
  await expect(page.getByText("sample.txt")).not.toBeVisible();
});
