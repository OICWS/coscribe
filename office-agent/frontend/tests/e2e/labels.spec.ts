import { expect, test } from "@playwright/test";

// The frontend's own modules are served by the dev server these specs run against.
test("a long first message becomes a short name", async ({ page }) => {
  await page.goto("/");
  const label = (text: string) =>
    page.evaluate(async (t) => (await import("/static/src/lib/threadStatus.ts")).shortLabel(t), text);

  expect(await label("Summarise the Q3 sales figures")).toBe("Summarise the Q3 sales figures");
  const long = await label(
    "Please read the attached contract and tell me every clause that mentions termination, notice periods or penalties",
  );
  expect(long.endsWith("…")).toBe(true);
  expect(long.length).toBeLessThanOrEqual(41);
  // The first line only, with its whitespace collapsed.
  expect(await label("  \n\n  first   line \nsecond line")).toBe("first line");
  // Chinese counts double, and there is no space to cut at.
  const chinese = await label("请帮我把第三季度的销售数据汇总成一张表格并且按地区分组然后做成图表发给我");
  expect(chinese.endsWith("…")).toBe(true);
  expect([...chinese].length).toBeLessThanOrEqual(21);
});
