import { expect, test } from "@playwright/test";
import { freshThreadPath, waitForConnected } from "./helpers";

// The desktop shell's own title bar, as the page sees it (lib/electron.ts).
test("under a dialog the title bar's buttons stay usable", async ({ page }) => {
  await page.addInitScript(() => {
    (window as unknown as Record<string, unknown>).coscribeDesktop = {
      platform: "win32",
      setTitleBarColors() {},
      onBrowserAgent: () => () => {},
    };
  });
  await page.goto(freshThreadPath("titlebar"));
  await waitForConnected(page);

  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.getByRole("button", { name: "Close settings" })).toBeVisible();

  const topmostAt = (x: number, y: number) =>
    page.evaluate(([px, py]) => document.elementFromPoint(px, py)?.closest("[title]")?.getAttribute("title"), [x, y]);
  expect(await topmostAt(60, 20)).toBe("Pin navigation open");
  expect(await topmostAt(20, 20)).toBe("Menu");

  await page.getByTitle("Pin navigation open").click();
  await expect(page.getByTitle("Unpin navigation")).toBeVisible();
  await expect(page.getByRole("button", { name: "Close settings" })).toBeVisible();
});
