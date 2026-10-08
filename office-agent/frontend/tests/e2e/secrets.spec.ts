import { expect, test, type Page } from "@playwright/test";
import { freshThreadPath, waitForConnected } from "./helpers";

// Needs a backend with a working keychain (CI and dev machines have one; the
// live-test setup points keyring at a file backend).

async function openSecrets(page: Page) {
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByText("Secrets", { exact: true }).first().click();
}

test("a secret is added, kept write-only, given to a conversation and deleted", async ({ page, request }) => {
  const name = `E2E_KEY_${Math.random().toString(36).slice(2, 7).toUpperCase()}`;
  const threadPath = freshThreadPath("secrets");
  const threadId = new URL(threadPath, "http://x").searchParams.get("thread") ?? "";
  await page.goto(threadPath);
  await waitForConnected(page);
  await page.locator("textarea").fill("Reply with just OK.");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 30_000 });

  try {
    await openSecrets(page);
    await expect(page.getByText("never sees its")).toBeVisible();
    await expect(page.getByText("not a defence")).toBeVisible();

    // The server's own wording for a bad host is shown as it is.
    await page.getByRole("button", { name: "Add secret" }).click();
    await page.getByLabel("Name").fill(name);
    await page.getByLabel("Value").fill("sk-live-abcdef123456");
    await page.getByLabel("Hosts it may be sent to").fill("https://api.example.com/path");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("isn't a host name");

    await page.getByLabel("Hosts it may be sent to").fill("api.example.com\n*.cdn.example.com");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    const row = page.getByRole("listitem").filter({ hasText: name });
    await expect(row).toBeVisible();
    await expect(row.getByText("api.example.com", { exact: true })).toBeVisible();
    await expect(row.getByText("*.cdn.example.com")).toBeVisible();
    // The value never comes back to the page.
    expect(await page.content()).not.toContain("sk-live-abcdef123456");

    await row.getByRole("button", { name: "Edit hosts" }).click();
    await page.getByLabel("Hosts it may be sent to").fill("api.other.example.com");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(row.getByText("api.other.example.com")).toBeVisible();
    await expect(row.getByText("*.cdn.example.com")).toHaveCount(0);

    await page.keyboard.press("Escape");
    await page.getByTitle("Pin navigation open").click();
    await page.getByTestId("thread-row").first().click({ button: "right" });
    await page.getByRole("menuitem", { name: "Edit environment" }).click();

    const dialog = page.getByRole("dialog", { name: "Edit environment" });
    await dialog.getByRole("button", { name: "Add variable" }).click();
    await dialog.getByLabel("Variable name").fill("MODE");
    await dialog.getByLabel("Variable value").fill("fast");
    await dialog.getByRole("checkbox").first().check();
    await dialog.getByRole("button", { name: "Save", exact: true }).click();
    await expect(dialog).toHaveCount(0);

    const saved = await (await request.get(`/api/threads/${threadId}/environment`)).json();
    expect(saved).toEqual({ variables: { MODE: "fast" }, secrets: [name] });

    // Deleting the secret also takes it out of the conversation.
    await openSecrets(page);
    await row.getByRole("button", { name: "Delete" }).click();
    await page.getByRole("button", { name: "Delete", exact: true }).last().click();
    await expect(row).toHaveCount(0);
    const after = await (await request.get(`/api/threads/${threadId}/environment`)).json();
    expect(after.secrets).toEqual([]);
  } finally {
    await request.delete(`/api/secrets/${name}`);
    await request.delete(`/api/threads/${threadId}`);
  }
});
