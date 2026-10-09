import { expect, test } from "@playwright/test";
import { freshThreadPath, waitForConnected } from "./helpers";

// The desktop shell's own title bar, as the page sees it (lib/electron.ts).
test("under a dialog the page's title-bar buttons can't be used", async ({ page }) => {
  await page.addInitScript(() => {
    (window as unknown as Record<string, unknown>).coscribeDesktop = {
      platform: "win32",
      setTitleBarColors() {},
      onBrowserAgent: () => () => {},
    };
  });
  await page.goto(freshThreadPath("titlebar"));
  await waitForConnected(page);

  const centreOf = async (title: string) => {
    const box = await page.getByTitle(title, { exact: true }).first().boundingBox();
    if (!box) throw new Error(`no ${title} button`);
    return [box.x + box.width / 2, box.y + box.height / 2] as const;
  };
  const buttons = [await centreOf("Pin navigation open"), await centreOf("Menu"), await centreOf("Settings")];

  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.getByRole("button", { name: "Close settings" })).toBeVisible();

  for (const [x, y] of buttons) {
    const covered = await page.evaluate(
      ([px, py]) => !document.elementFromPoint(px, py)?.closest("button"),
      [x, y],
    );
    expect(covered).toBe(true);
  }
});
