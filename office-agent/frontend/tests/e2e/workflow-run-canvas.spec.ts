import { expect, test } from "@playwright/test";

// No model involved: a run's Overview draws how the run went on the
// workflow's canvas -- what ran, the arm a branch went past, where it failed
// and what it never reached.

test("a run's overview shows each step's state, and a step on it opens in the list", async ({ page }) => {
  const created = await page.request.post("/api/scheduled-tasks", {
    data: {
      name: "e2e workflow run canvas",
      kind: "manual",
      at: "",
      workflow: {
        steps: [
          {
            id: "list",
            kind: "tool",
            title: "List the workspace",
            tool: "list_files",
            args: { path: "." },
            save_as: "found",
          },
          {
            id: "seen",
            kind: "branch",
            title: "Did it count them?",
            condition: { left: { ref: "found.total_count" }, op: "ge", right: { value: 0 } },
            then: [],
            otherwise: [{ id: "never", kind: "tool", title: "List again", tool: "list_files", args: { path: "." } }],
          },
          // Fails whatever the workspace holds: CI's has over 200 files, so
          // the listing there is cut short.
          {
            id: "cut",
            kind: "check",
            title: "The count is negative",
            conditions: [{ left: { ref: "found.total_count" }, op: "lt", right: { value: 0 } }],
          },
          { id: "ok", kind: "approval", title: "Look it over", message: "Check the list." },
        ],
      },
    },
  });
  const task = await created.json();
  try {
    const started = await page.request.post(`/api/scheduled-tasks/${task.trigger_id}/run`, { data: {} });
    const run = (await started.json()).run;
    await page.goto(`/?thread=${run.thread_id}`);
    await expect(page.getByTestId("run-banner")).toContainText("Failed");

    await page.getByRole("button", { name: "Overview" }).click();
    const canvas = page.getByTestId("workflow-canvas");
    const node = (id: string) => canvas.locator(`[data-canvas-step="${id}"]`);
    await expect(node("list")).toHaveAttribute("data-run-status", "done");
    await expect(node("seen")).toContainText("Took Then");
    await expect(node("never")).toHaveAttribute("data-run-status", "not_taken");
    await expect(node("never")).toContainText("Not taken");
    await expect(node("cut")).toHaveAttribute("data-run-status", "failed");
    await expect(node("cut")).toContainText("Failed here");
    await expect(node("ok")).toHaveAttribute("data-run-status", "not_reached");

    await node("cut").click();
    await expect(page.getByRole("button", { name: "Steps" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator('[data-run-step="cut"]')).toBeInViewport();
  } finally {
    await page.request.delete(`/api/scheduled-tasks/${task.trigger_id}`);
  }
});
